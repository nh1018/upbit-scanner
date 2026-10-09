"""Manual Actions adapter: verified parent restore, new records only, failure evidence."""
import argparse
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path
from .research_segments import WORKFLOW, restore_chain, export_segment
from .research_runner import main as run_research


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def github_loader(repository, token):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('invalid repository')
    def api(path):
        req=urllib.request.Request('https://api.github.com/repos/'+repository+'/'+path,
            headers={'Authorization':'Bearer '+token,'Accept':'application/vnd.github+json'})
        try:
            with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError('GitHub metadata HTTP '+str(exc.code)) from None
    def load(run_id):
        if not re.fullmatch(r'[1-9][0-9]*',str(run_id)):
            raise ValueError('invalid parent run ID')
        run=api('actions/runs/'+run_id)
        if run['path'] != WORKFLOW or run['conclusion'] != 'success':
            raise ValueError('parent run not successful same-workflow research')
        name='upbit-c-research-increment-'+str(run['run_attempt'])
        artifacts=[]
        for page in range(1,100):
            objects=api(f'actions/runs/{run_id}/artifacts?per_page=100&page={page}')['artifacts']
            artifacts.extend(objects)
            if len(objects)<100:break
        matches=[x for x in artifacts if x['name']==name]
        if len(matches)!=1 or matches[0]['expired']:
            raise ValueError('missing/duplicate/expired parent artifact; lineage cannot restart silently')
        art=matches[0]
        req=urllib.request.Request(f'https://api.github.com/repos/{repository}/actions/artifacts/{art["id"]}/zip',headers={'Authorization':'Bearer '+token})
        try:
            with urllib.request.build_opener(NoRedirect).open(req,timeout=60) as response:raw=response.read()
        except urllib.error.HTTPError as exc:
            if exc.code not in (301,302,303,307,308):
                raise ValueError('GitHub artifact download HTTP '+str(exc.code)) from None
            location=exc.headers.get('Location','')
            if not location.startswith('https://'):
                raise ValueError('unsafe artifact redirect')
            with urllib.request.urlopen(location,timeout=120) as response:raw=response.read()
        digest=art.get('digest')
        return raw,{'workflow_path':run['path'],'conclusion':run['conclusion'],
            'expired':art['expired'],'expires_at':art['expires_at'],
            'archive_sha256':digest.removeprefix('sha256:') if digest else None}
    return load


def manual_mode(label, parent, outcomes_only, evaluate_history, checkpoint):
    """Only explicit owner-label modes; no dynamic code or historical reconstruction."""
    import re
    if not label:
        return parent, outcomes_only, evaluate_history, checkpoint
    if label == 'c-research-run-root':
        return '', False, False, False
    match=re.fullmatch(r'c-research-(scan|evaluate|checkpoint|failproof)-([1-9][0-9]*)',label)
    if not match:
        raise ValueError('invalid explicit research label')
    mode, parent=match.groups()
    return parent, mode != 'scan', mode == 'scan', mode == 'checkpoint'


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state',type=Path,required=True)
    p.add_argument('--segment',type=Path,required=True)
    p.add_argument('--summary',type=Path,required=True)
    p.add_argument('--run-id',required=True)
    p.add_argument('--previous-run-id',default='')
    p.add_argument('--manual-label',default='')
    p.add_argument('--checkpoint',action='store_true')
    p.add_argument('--outcomes-only',action='store_true')
    p.add_argument('--evaluate-history',action='store_true')
    p.add_argument('--batch-count',default='1')
    p.add_argument('--batch-index',default='0')
    a=p.parse_args(argv)
    protected={'.git','.github','data','data_market','metadata_features','market_data_v1','btc_anytime','upbit_b','upbit_c','chat_analysis','diagnostics','tests'}
    if any(part.lower() in protected or part.lower().startswith('output') for path in (a.state,a.segment,a.summary) for part in path.resolve().parts):
        p.error('research state/segment/summary must be outside protected production namespaces')
    summary={'schema_version':'upbit-c-research-operation-11','activation':'RESEARCH_ONLY',
        'run_id':a.run_id,'previous_run_id':a.previous_run_id or None,'status':'STARTED',
        'checkpoint_requested':a.checkpoint,'production_files_created':0}
    parent=None; restored=False
    try:
        a.previous_run_id,a.outcomes_only,a.evaluate_history,a.checkpoint=manual_mode(a.manual_label,a.previous_run_id,a.outcomes_only,a.evaluate_history,a.checkpoint)
        summary.update(previous_run_id=a.previous_run_id or None,checkpoint_requested=a.checkpoint)
        run_attempt=int(os.environ.get('GITHUB_RUN_ATTEMPT','1'))
        if run_attempt>1:
            # Native reruns can invalidate earlier run artifacts before this job starts.
            # Never rescan or claim a replay against unavailable prior evidence.
            raise ValueError('UNSAFE_NATIVE_RERUN: use a new manual execution with an explicit successful parent; preserve a verified checkpoint/backup before rerunning any ancestor')
        if a.previous_run_id:
            parent,verification=restore_chain(a.previous_run_id,github_loader(os.environ['GITHUB_REPOSITORY'],os.environ['GH_TOKEN']),a.state,checkpoint=a.checkpoint)
            summary['parent_verification']=verification
        elif a.outcomes_only:
            raise ValueError('outcomes-only operation requires an explicit parent')
        restored=True
        args=['--output',str(a.state),'--batch-count',a.batch_count,'--batch-index',a.batch_index]
        if a.outcomes_only:args.append('--outcomes-only')
        elif a.evaluate_history:args.append('--evaluate-history')
        summary['research']=run_research(args)
        summary['status']='SUCCESS'
    except Exception as exc:
        summary['status']='FAILED'
        summary['error_type']=type(exc).__name__
        summary['error']=str(exc) if isinstance(exc,ValueError) else 'operation failed; see Actions step/class'
        print('C research operation failed: '+type(exc).__name__,file=sys.stderr)
    finally:
        if restored:
            try:
                manifest=export_segment(a.state,a.segment,a.run_id,parent,a.checkpoint,source_revision=os.environ.get('SOURCE_REVISION',os.environ.get('GITHUB_SHA')))
                summary['segment']={k:manifest[k] for k in ('manifest_sha256','inventory_sha256','new_records','exported_records','exported_record_bytes')}
            except Exception as exc:
                summary['status']='FAILED';summary['export_error_type']=type(exc).__name__
                summary['export_error']=str(exc) if isinstance(exc,ValueError) else 'segment export failed'
        a.summary.parent.mkdir(parents=True,exist_ok=True)
        a.summary.write_text(json.dumps(summary,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,sort_keys=True))
    return 0 if summary['status']=='SUCCESS' else 1


if __name__=='__main__':
    raise SystemExit(main())
