"""B research execution evidence. No GitHub writes; no approval fabrication."""
import json
import math
import re
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from .contracts import sha, clock
from upbit_b.feature_contracts import digest, dumps
from . import b_prospective_v11 as B

CONTRACT_HASH='c38c83913c6a96ca7e59e8b243f311f783f7e7ff1007c4cc828017bf86565e7a'
SCHEMA='b-prospective-activation-evidence-1.2'
REPO='nh1018/upbit-scanner'
ISSUE='https://api.github.com/repos/'+REPO+'/issues/37'
API='https://api.github.com/repos/'+REPO
OWNER='nh1018'
LEAD_MS=60000
CLOCK_BOUND_MS=2000


def millis(s):
    d=datetime.fromisoformat(s.replace('Z','+00:00'))
    if d.utcoffset() is None or d.utcoffset().total_seconds()!=0:raise ValueError('explicit UTC required')
    return int(d.timestamp()*1000)


def utc(ms):return datetime.fromtimestamp(clock(ms)/1000,timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def now():return time.time_ns()//1000000


def validate_contract(c,raw):
    if c!=B.contract(raw) or c['contract_sha256']!=CONTRACT_HASH:raise ValueError('pinned V1.1 contract mismatch')


def record_text(commit):return dumps({'purpose':'B_PROSPECTIVE_CONTRACT_V12','contract_sha256':CONTRACT_HASH,'git_commit_sha':commit})
def approval_text(commit,boundary,attempts,interval):
    return dumps({'purpose':'APPROVE_B_PROSPECTIVE_V12','contract_sha256':CONTRACT_HASH,'git_commit_sha':commit,
                  'activation_time_utc':utc(boundary),'max_attempts':attempts,'retry_interval_ms':interval})
def witness_text(record_sha):return dumps({'purpose':'B_PROSPECTIVE_ACTIVATION_WITNESS_V12','activation_file_sha256':record_sha})


def comment(c,expected):
    if (type(c.get('id')) is not int or c['id']<=0 or c.get('issue_url')!=ISSUE or c.get('user',{}).get('login')!=OWNER
        or c.get('user',{}).get('type')!='User' or c.get('body')!=expected
        or c.get('updated_at')!=c.get('created_at')):raise ValueError('missing/edited/wrong-author approval evidence')
    return millis(c['created_at'])


def server_clock(e):
    """TLS GitHub HTTP Date is a bounded operational check, not authenticated NTP."""
    for k in ('request_started_ms','response_received_ms','server_ms'):clock(e[k])
    rtt=e['elapsed_ms']
    if not isinstance(rtt,(int,float)) or rtt<0 or rtt>1000:raise ValueError('server clock delay/monotonic regression')
    start,end=e['request_started_ms'],e['response_received_ms']
    if end<start or abs((end-start)-rtt)>250:raise ValueError('system clock jump')
    # HTTP Date has one-second resolution; account for RTT and truncation.
    low=e['server_ms']-end;high=e['server_ms']+1000-start
    if max(abs(low),abs(high))>CLOCK_BOUND_MS:raise ValueError('UTC clock uncertainty exceeds bound')
    return max(end,e['server_ms']+1000+int(rtt))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args):return None


class GitHub:
    def __init__(self,token=None,opener=None):self.token,self.opener=token,opener or urllib.request.build_opener(NoRedirect).open
    def get(self,path):
        if not path.startswith('/') or '..' in path:raise ValueError('invalid GitHub path')
        headers={'Accept':'application/vnd.github+json','User-Agent':'b-prospective-manual-v12','Cache-Control':'no-cache'}
        if self.token:headers['Authorization']='Bearer '+self.token
        start=now();mono=time.monotonic()
        try:
            with self.opener(urllib.request.Request(API+path,headers=headers),timeout=15) as r:
                if r.geturl()!=API+path:raise ValueError('GitHub redirect refused')
                raw=r.read();date=r.headers.get('Date')
        except urllib.error.HTTPError as e:raise ValueError('GITHUB_HTTP_'+str(e.code)) from None
        except OSError:raise ValueError('GITHUB_NETWORK_ERROR') from None
        end=now();elapsed=math.ceil((time.monotonic()-mono)*1000)
        if not date:raise ValueError('GitHub server Date unavailable')
        server=parsedate_to_datetime(date)
        if server.utcoffset() is None:raise ValueError('GitHub Date timezone unavailable')
        evidence={'request_started_ms':start,'response_received_ms':end,'elapsed_ms':elapsed,'server_ms':int(server.timestamp()*1000),'response_sha256':sha(raw),'url':API+path}
        server_clock(evidence)
        return json.loads(raw),evidence
    def comment(self,identifier):
        if type(identifier) is not int or identifier<=0:raise ValueError('valid comment ID required')
        return self.get('/issues/comments/'+str(identifier))


def prepare(c,raw,commit,boundary,record,approval,clock_evidence,max_attempts=3,retry_interval_ms=3600000):
    """Only called after separate user approval exists; never posts comments."""
    validate_contract(c,raw)
    if not re.fullmatch('[0-9a-f]{40}',commit):raise ValueError('pinned code SHA required')
    if type(max_attempts) is not int or not 1<=max_attempts<=10 or type(retry_interval_ms) is not int or retry_interval_ms<60000:raise ValueError('invalid approved retry policy')
    current=server_clock(clock_evidence)
    recorded=comment(record,record_text(commit));approved=comment(approval,approval_text(commit,boundary,max_attempts,retry_interval_ms))
    if not recorded<=approved<=current or boundary<=current+LEAD_MS:raise ValueError('retroactive/too-close activation')
    body={'schema_version':SCHEMA,'state':'APPROVED_ACTIVATION','contract_sha256':CONTRACT_HASH,
          'approval_reference':approval['html_url'],'approval_original':approval['body'],'approval_comment':approval,
          'contract_comment':record,'contract_recorded_at_ms':recorded,'approved_at_ms':approved,'activation_time_ms':clock(boundary),
          'git_commit_sha':commit,'prepared_at_upper_bound_ms':current,'server_clock_evidence':clock_evidence,
          'execution_policy':{'max_attempts':max_attempts,'retry_interval_ms':retry_interval_ms},'repository':REPO}
    return dict(body,event_id=digest(body))


def encoded(a):return (dumps(a)+'\n').encode()


def validate(a,c,raw,github,*,require_started=True):
    validate_contract(c,raw);B.activation(a,c,raw)
    if a.get('schema_version')!=SCHEMA or a.get('repository')!=REPO:raise ValueError('V1.2 activation evidence required')
    policy=a['execution_policy'];record,re=github.comment(a['contract_comment']['id']);approval,ae=github.comment(a['approval_comment']['id'])
    if record!=a['contract_comment'] or approval!=a['approval_comment']:raise ValueError('approval evidence changed')
    rebuilt=prepare(c,raw,a['git_commit_sha'],a['activation_time_ms'],record,approval,a['server_clock_evidence'],policy['max_attempts'],policy['retry_interval_ms'])
    if rebuilt!=a:raise ValueError('activation replay mismatch')
    witness,we=github.comment(github.witness_id)
    observed=comment(witness,witness_text(sha(encoded(a))))
    if not a['prepared_at_upper_bound_ms']<=observed<a['activation_time_ms']:raise ValueError('activation witness must precede boundary')
    current=server_clock(we)
    if require_started and we['server_ms']<a['activation_time_ms']:raise ValueError('activation boundary not reached')
    return {'current_upper_ms':current,'server_lower_ms':we['server_ms'],'witness':witness,'server_evidence':we}
