"""Explicit manual B research runner. No schedules, no strategy calculations."""
import argparse
from contextlib import contextmanager
from functools import wraps
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import urllib.parse
from dataclasses import replace
from . import b_prospective_v11 as B
from . import b_activation_evidence as E
from .contracts import sha,HOUR
from .engine import evaluate as engine_evaluate
from .storage import append as append_outcome
from .aggregate import latest
from upbit_b.feature_contracts import dumps
from upbit_b.market_data import normalize,UPBIT,BINANCE,iso
from upbit_c.research_archive import isolated
from upbit_c.research_release import no_symlinks


def git(repo,*args):
    p=subprocess.run(['git','-c','safe.directory='+str(Path(repo).resolve()),'-C',str(repo),*args],capture_output=True,check=True)
    return p.stdout


def sources(repo,revision):
    if not re.fullmatch('[0-9a-f]{40}',revision):raise ValueError('exact journal Git revision required')
    names=git(repo,'ls-tree','-r','--name-only',revision,'output_upbit_b/v1/history').decode().splitlines()
    result=[]
    for name in sorted(names):
        if not re.fullmatch(r'output_upbit_b/v1/history/\d{4}-\d{2}-\d{2}/\d{2}\.jsonl',name):raise ValueError('unexpected journal path')
        raw=git(repo,'show',revision+':'+name);result.append((raw,sha(raw),name))
    if not result:raise ValueError('full journal lineage required')
    return result


def root_path(root,repo):
    if root is None:raise ValueError('explicit isolated --record-root required')
    original=Path(root);no_symlinks(original);root=isolated(original)
    repo=Path(repo).resolve()
    if root==repo or repo in root.parents:raise ValueError('repository protected')
    if any(p.lower().startswith(('a-publication','coin-trading-backup')) for p in root.parts):raise ValueError('A baseline/backup protected')
    return root


def write_once(path,raw):
    no_symlinks(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as f:
            tmp=f.name;f.write(raw);f.flush();os.fsync(f.fileno())
        try:os.link(tmp,path)
        except FileExistsError:
            if path.read_bytes()!=raw:raise ValueError('append-only byte conflict')
            return 'REPLAY_NOOP'
    finally:
        if tmp:os.unlink(tmp)
    return 'CREATED'


def load_events(root,sub):
    directory=Path(root)/sub;no_symlinks(directory)
    return [json.loads(p.read_bytes()) for p in sorted(directory.rglob('*.json'))]


class RawMarket:
    """One attempt per request; retries belong to persisted attempt ledger."""
    def __init__(self,opener=None,clock=E.now,sleep=time.sleep):
        self.opener,self.clock,self.sleep=opener or urllib.request.build_opener(E.NoRedirect).open,clock,sleep;self.records=[]
    def get(self,base,path,params):
        if (base,path) not in ((UPBIT,'/v1/candles/minutes/60'),(BINANCE,'/api/v3/klines')):raise ValueError('unapproved market endpoint')
        url=base+path+'?'+urllib.parse.urlencode(params);self.sleep(.25);started=self.clock()
        try:
            with self.opener(urllib.request.Request(url,headers={'User-Agent':'b-prospective-manual-v12'}),timeout=15) as r:
                if r.geturl()!=url:raise ValueError('market redirect refused')
                raw=r.read()
        except urllib.error.HTTPError as e:
            raw=e.read();received=self.clock();self.records.append((raw,{'url':url,'response_sha256':sha(raw),'received_at_utc':iso(received)},started))
            raise ValueError('MARKET_HTTP_'+str(e.code)) from None
        except OSError:raise ValueError('MARKET_NETWORK_ERROR') from None
        received=self.clock()
        if received<started:raise ValueError('clock regression')
        ev={'url':url,'response_sha256':sha(raw),'received_at_utc':iso(received)};self.records.append((raw,ev,started))
        return B.V1.verify_response(raw,ev),ev


class FixtureMarket:
    """Dry-run only: memory reads get SIMULATED receipt clocks, never API evidence."""
    def __init__(self,bundle,clock=E.now):
        if bundle.get('kind')!='OFFLINE_FIXTURE_ONLY':raise ValueError('explicit fixture label required')
        self.responses=bundle['responses'];self.records=[];self.clock=clock
    def get(self,base,path,params):
        url=base+path+'?'+urllib.parse.urlencode(params)
        if url not in self.responses:raise ValueError('FIXTURE_RESPONSE_MISSING')
        item=self.responses[url];raw=base64.b64decode(item['raw_base64'],validate=True)
        if sha(raw)!=item['sha256']:raise ValueError('fixture response hash mismatch')
        t=self.clock();ev={'url':url,'response_sha256':sha(raw),'received_at_utc':iso(t),'simulation_only':True}
        self.records.append((raw,ev,t));return B.V1.verify_response(raw,ev),ev


def decode(rows,ev,provider,instrument):
    if not isinstance(rows,list):raise ValueError('candle list required')
    cs=[normalize(provider,instrument,'1h',r) for r in rows];received=B.V1._clock_ms(ev['received_at_utc'])
    return [replace(c,completed=c.close_ms<=received) for c in cs],dict(ev,source_row_open_times_ms=[c.open_ms for c in cs])


@contextmanager
def writer_lock(root):
    root.mkdir(parents=True,exist_ok=True);no_symlinks(root)
    lock=root/'.manual-writer-lock'
    try:lock.mkdir()
    except FileExistsError:raise ValueError('manual writer busy/stale lock; inspect, never auto-reset') from None
    try:yield
    finally:lock.rmdir()


def locked(method):
    @wraps(method)
    def call(self,*args,**kw):
        # A dry-run must not even create a lock directory.
        if kw.get('dry') or method.__name__=='report' and not kw.get('save',False):return method(self,*args,**kw)
        self.authorize();self.check_local_activation()
        with writer_lock(self.root):return method(self,*args,**kw)
    return call


class Runner:
    def __init__(self,repo,root,c,discovery,a,github,journal_sources,market=None,clock=E.now,journal_revision=None):
        self.repo=Path(repo);self.root=root_path(root,repo);self.c,self.raw,self.a=c,discovery,a
        self.github,self.sources,self.market,self.clock=github,list(journal_sources),market or RawMarket(),clock
        self.memory_events=[];self.memory_outcomes=[];self.memory_population=False;self.journal_revision=journal_revision
    def authorize(self):
        if git(self.repo,'rev-parse','HEAD').decode().strip()!=self.a['git_commit_sha']:raise ValueError('code revision differs from approved commit')
        if git(self.repo,'diff','HEAD','--','strategy_evaluation','upbit_b','upbit_c'):raise ValueError('dirty execution code')
        return E.validate(self.a,self.c,self.raw,self.github)
    def members(self):return B.population(self.sources,self.c,self.raw,self.a)
    def check_local_activation(self):
        p=self.root/'activation.json'
        if not p.is_file() or p.read_bytes()!=E.encoded(self.a):raise ValueError('pinned activation missing from research root')
    @locked
    def population(self,dry=False):
        authorization=self.authorize()
        if not dry:self.check_local_activation()
        members=self.members()
        if dry:
            existing=load_events(self.root,'events')
            self.memory_events=list(members)+[e for e in existing if e.get('kind')=='ATTEMPT'];self.memory_population=True
            return {'eligible':len(members),'writes':0}
        write_once(self.root/'activation_witness'/(str(authorization['witness']['id'])+'.json'),(dumps(authorization['witness'])+'\n').encode())
        snapshot=[]
        for raw,expected,ref in self.sources:
            if sha(raw)!=expected:raise ValueError('journal source hash changed')
            write_once(self.root/'journal_sources'/(expected+'.bin'),raw)
            snapshot.append({'reference':ref,'sha256':expected,'size':len(raw)})
        manifest={'journal_revision':self.journal_revision,'files':snapshot,'activation_event_id':self.a['event_id']}
        write_once(self.root/'journal_manifests'/(B.V1.digest(manifest)+'.json'),(dumps(manifest)+'\n').encode())
        result=[B.append(self.root/'events',m,self.c,self.raw,self.a) for m in members]
        return {'eligible':len(members),'created':result.count('CREATED'),'replay':result.count('REPLAY_NOOP')}
    def registered(self):
        members=self.members();events=self.memory_events if self.memory_population else load_events(self.root,'events')
        existing=[e for e in events if e.get('kind')=='POPULATION']
        if sorted(existing,key=lambda e:(e['signal']['signal_observed_at'],e['signal']['signal_id']))!=members:raise ValueError('register entire population first')
        attempts=[e for e in events if e.get('kind')=='ATTEMPT']
        if not self.memory_events:self.verify_retained(attempts)
        return members,attempts
    def verify_retained(self,attempts):
        for e in attempts:
            for ev in e['responses']:
                p=self.root/'raw_responses'/(ev['response_sha256']+'.bin');no_symlinks(p)
                if not p.is_file() or sha(p.read_bytes())!=ev['response_sha256']:raise ValueError('missing/corrupt retained response')
            x=e['context']
            if not x:continue
            parts={}
            for name,provider,instrument in (('upbit','UPBIT',x['signal_contract']['market']),('btc','BINANCE_SPOT','BTCUSDT')):
                ev=x['source_inputs'][name]
                if not ev:parts[name]=([],{});continue
                p=self.root/'raw_responses'/(ev['response_sha256']+'.bin')
                rows=B.V1.verify_response(p.read_bytes(),ev);parts[name]=decode(rows,ev,provider,instrument)
            up,ue=parts['upbit'];btc,be=parts['btc']
            if B.V1.digest(B.context(x['signal_contract'],up,ue,btc,be,x['registered_at_ms']))!=B.V1.digest(x):raise ValueError('context raw binding mismatch')
    def verify_outcomes(self,events):
        from .engine import validate_evaluation
        for e in events:
            validate_evaluation(e)
            if e['status']!='MATURED':continue
            candles=[]
            for ev in e['data_evidence']:
                p=self.root/'raw_responses'/(ev['response_sha256']+'.bin');no_symlinks(p)
                rows=B.V1.verify_response(p.read_bytes(),ev)
                cs,_=decode(rows,ev,'UPBIT',e['market']);candles.extend(cs)
            replay=engine_evaluate(e['signal_contract'],e['horizon']['days'],candles,e['data_evidence'],e['as_of_ms'])
            if B.V1.digest(replay)!=B.V1.digest(e):raise ValueError('outcome raw binding mismatch')
    def retain(self):
        for raw,ev,_ in self.market.records:
            if sha(raw)!=ev['response_sha256']:raise ValueError('raw response changed')
            write_once(self.root/'raw_responses'/(sha(raw)+'.bin'),raw)
            write_once(self.root/'response_receipts'/(B.V1.digest(ev)+'.json'),(dumps(ev)+'\n').encode())
    @locked
    def collect(self,dry=False):
        if not dry and isinstance(self.market,FixtureMarket):raise ValueError('fixture transport forbidden in real collection')
        self.authorize()
        if not dry:self.check_local_activation()
        members,attempts=self.registered();results=[]
        for member in members:
            sid=member['signal']['signal_id'];prior=B.validate_attempts(member,[e for e in attempts if e['signal_id']==sid])
            if any(e['status']=='SUCCESS' for e in prior):results.append({'signal_id':sid,'state':'FROZEN_CONTEXT_NOOP'});continue
            policy=self.a['execution_policy'];started=self.clock()
            if len(prior)>=policy['max_attempts']:results.append({'signal_id':sid,'state':'MAX_ATTEMPTS'});continue
            if prior and started<prior[-1]['completed_at_ms']+policy['retry_interval_ms']:results.append({'signal_id':sid,'state':'RETRY_NOT_DUE'});continue
            key=B.V1.digest([self.a['event_id'],member['event_id'],len(prior)])
            start_path=self.root/'attempt_starts'/(key+'.json')
            if not dry and start_path.exists():
                start=json.loads(start_path.read_bytes())
                if start['population_event_id']!=member['event_id'] or start['retry_attempt']!=len(prior):raise ValueError('interrupted attempt identity mismatch')
                failure=B.attempt(member,prior,start['request_started_ms'],None,started,[],error='INTERRUPTED_ATTEMPT_NO_COMPLETE_RESPONSE')
                B.append(self.root/'events',failure,self.c,self.raw,self.a,member);attempts.append(failure)
                results.append({'signal_id':sid,'state':'FAILED_INTERRUPTED_RECOVERED'});continue
            if not dry:write_once(start_path,(dumps({'population_event_id':member['event_id'],'retry_attempt':len(prior),'request_started_ms':started})+'\n').encode())
            s=member['signal'];cutoff=s['source_cutoff']//HOUR*HOUR;up=[];btc=[];ue={};be={};errors=[];self.market.records=[]
            for provider,base,path,params in (
                ('UPBIT',UPBIT,'/v1/candles/minutes/60',{'market':s['market'],'count':25,'to':iso(cutoff)}),
                ('BINANCE_SPOT',BINANCE,'/api/v3/klines',{'symbol':'BTCUSDT','interval':'1h','startTime':cutoff-25*HOUR,'endTime':cutoff-1,'limit':25})):
                try:
                    rows,ev=self.market.get(base,path,params)
                    cs,evidence=decode(rows,ev,provider,s['market'] if provider=='UPBIT' else 'BTCUSDT')
                    if provider=='UPBIT':up,ue=cs,evidence
                    else:btc,be=cs,evidence
                except (ValueError,KeyError,TypeError,OSError) as e:errors.append(provider+':'+type(e).__name__+':'+str(e)[:120])
            completed=self.clock();ctx=B.context(s,up,ue,btc,be,completed)
            responses=[ev for _,ev,_ in self.market.records]
            # Use normalized row references without losing raw response hashes.
            responses=[ue if ue and e['response_sha256']==ue['response_sha256'] else be if be and e['response_sha256']==be['response_sha256'] else e for e in responses]
            received=max((B.V1._clock_ms(e['received_at_utc']) for e in responses),default=None)
            attempt=B.attempt(member,prior,started,received,completed,responses,ctx if responses else None,';'.join(errors) or None)
            if not dry:
                self.authorize();self.retain();B.append(self.root/'events',attempt,self.c,self.raw,self.a,member)
            if dry:self.memory_events.append(attempt)
            attempts.append(attempt);results.append({'signal_id':sid,'state':attempt['status'],'event_id':attempt['event_id']})
        return results
    @locked
    def evaluate(self,dry=False):
        if not dry and isinstance(self.market,FixtureMarket):raise ValueError('fixture transport forbidden in real evaluation')
        if not dry:self.check_local_activation()
        evidence=self.authorize();members,_=self.registered();events=load_events(self.root,'outcomes')+self.memory_outcomes
        if not self.memory_outcomes:self.verify_outcomes(events)
        terminal={(e['signal_id'],e['horizon']['days']) for e in latest(events) if e['status']=='MATURED'};results=[]
        from .runner import fetch_evaluation
        for member in members:
            s=member['signal']
            for days in (1,3,7):
                if (s['signal_id'],days) in terminal:continue
                self.market.records=[]
                # Shared helper avoids HTTP entirely until the exact horizon matures.
                as_of=min(self.clock(),evidence['server_lower_ms'])
                outcome=fetch_evaluation(self.market,s,days,as_of)
                if not dry:
                    self.authorize();self.retain();append_outcome(self.root/'outcomes',outcome)
                if dry:self.memory_outcomes.append(outcome)
                results.append(outcome)
        return results
    @locked
    def report(self,save=False):
        if save:self.check_local_activation()
        self.authorize();members,attempts=self.registered();events=load_events(self.root,'outcomes')+self.memory_outcomes
        if not self.memory_outcomes:self.verify_outcomes(events)
        result=B.report(members,attempts,events,self.c,self.raw,self.a,sources=self.sources)
        if save:write_once(self.root/'reports'/(B.V1.digest(result)+'.json'),(dumps(result)+'\n').encode())
        return result


def cli(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=('status','dry-run','prepare-activation','population','collect','evaluate','report'))
    p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[1]);p.add_argument('--record-root',type=Path)
    p.add_argument('--journal-revision');p.add_argument('--activation',type=Path);p.add_argument('--activation-sha256');p.add_argument('--witness-comment',type=int)
    p.add_argument('--contract-comment',type=int);p.add_argument('--approval-comment',type=int);p.add_argument('--activation-utc')
    p.add_argument('--max-attempts',type=int,default=3);p.add_argument('--retry-interval-seconds',type=int,default=3600);p.add_argument('--save-report',action='store_true')
    p.add_argument('--fixture-bundle',type=Path);p.add_argument('--fixture-sha256')
    args=p.parse_args(argv)
    if args.fixture_bundle and args.command!='dry-run':p.error('fixture data is forbidden in real commands')
    directory=Path(__file__).resolve().parent/'research';raw=(directory/'b_discovery16_v1.json').read_bytes();c=json.loads((directory/'b_prospective_contract_v11.json').read_bytes());E.validate_contract(c,raw)
    commit=git(args.repo,'rev-parse','HEAD').decode().strip()
    if args.command in ('status','dry-run') and args.activation is None:
        result={'state':'NOT_ACTIVATED','contract_sha256':E.CONTRACT_HASH,'code_commit':commit,'writes':0,'market_requests':0,'missing':['separate user approval','activation and preboundary GitHub witness','explicit isolated root']}
        if args.journal_revision:
            src=sources(args.repo,args.journal_revision);B.verify_discovery_sources(json.loads(raw),src);result['verified_journals']=len(src)
        print(dumps(result));return result
    github=E.GitHub(os.environ.get('GITHUB_TOKEN'))
    if args.command=='prepare-activation':
        if not all((args.contract_comment,args.approval_comment,args.activation_utc)):p.error('existing user contract/approval comments and future UTC boundary required')
        if git(args.repo,'diff','HEAD','--','strategy_evaluation','upbit_b','upbit_c'):raise ValueError('dirty execution code')
        root=root_path(args.record_root,args.repo);record,_=github.comment(args.contract_comment);approval,ce=github.comment(args.approval_comment)
        path=root/'activation.json'
        if path.exists():
            no_symlinks(path);saved=path.read_bytes();a=json.loads(saved)
            expected=E.prepare(c,raw,commit,E.millis(args.activation_utc),record,approval,a['server_clock_evidence'],args.max_attempts,args.retry_interval_seconds*1000)
            if a!=expected or saved!=E.encoded(expected):raise ValueError('existing activation conflict')
        else:
            a=E.prepare(c,raw,commit,E.millis(args.activation_utc),record,approval,ce,args.max_attempts,args.retry_interval_seconds*1000)
            write_once(path,E.encoded(a))
        result={'state':'PREPARED_NOT_EFFECTIVE_WITHOUT_WITNESS','path':str(path),'sha256':sha(E.encoded(a)),'required_user_witness':E.witness_text(sha(E.encoded(a)))}
        print(dumps(result));return result
    if not args.activation or not args.activation_sha256 or not args.witness_comment:p.error('pinned activation bytes and GitHub witness required')
    activation_raw=args.activation.read_bytes()
    if sha(activation_raw)!=args.activation_sha256:raise ValueError('activation file SHA256 mismatch')
    a=json.loads(activation_raw)
    if activation_raw!=E.encoded(a):raise ValueError('canonical activation bytes required')
    github.witness_id=args.witness_comment
    ev=E.validate(a,c,raw,github,require_started=args.command not in ('status','dry-run'))
    if args.command=='status':result={'state':'EVIDENCE_VERIFIED','contract_sha256':E.CONTRACT_HASH,'activation_sha256':sha(activation_raw),'evidence':ev,'writes':0}
    else:
        if not args.journal_revision:p.error('exact --journal-revision required')
        runner=Runner(args.repo,args.record_root,c,raw,a,github,sources(args.repo,args.journal_revision),journal_revision=args.journal_revision)
        if args.command=='dry-run' and args.fixture_bundle:
            bundle_raw=args.fixture_bundle.read_bytes()
            if sha(bundle_raw)!=args.fixture_sha256:raise ValueError('fixture bundle SHA256 mismatch')
            runner.market=FixtureMarket(json.loads(bundle_raw));runner.population(dry=True)
            collection=runner.collect(dry=True);evaluation=runner.evaluate(dry=True);report=runner.report()
            result={'state':'OFFLINE_FIXTURE_ONLY_NOT_OPERATIONAL','writes':0,'market_requests':0,'collection':collection,'evaluation':evaluation,'report':report}
        elif args.command=='dry-run':
            members=runner.members();result={'state':'PLAN_VALIDATED','eligible':len(members),'writes':0,'market_requests':0,'stages':['population','collect','evaluate','report'],'limitation':'No live collection simulation; complete fixture lifecycle covered by offline tests'}
        elif args.command=='population':result=runner.population()
        elif args.command=='collect':result=runner.collect()
        elif args.command=='evaluate':result=runner.evaluate()
        else:result=runner.report(save=args.save_report)
    print(dumps(result));return result


if __name__=='__main__':
    try:cli()
    except (ValueError,OSError,KeyError,TypeError,subprocess.CalledProcessError) as e:
        print(dumps({'status':'FAIL_CLOSED','error_type':type(e).__name__,'reason':str(e) if isinstance(e,ValueError) else 'execution failed; protected records retained'}));raise SystemExit(1)
