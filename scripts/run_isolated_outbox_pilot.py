"""One fixture only. Separate pilot namespace; never enables production workers."""
import json
import os
import urllib.parse
from outbox import Outbox, Remote, row, prop, rich, SOURCE, BOT
from postgres_store import PostgresStore
from postgres_worker import worker_session

FIXTURE='402ff732-fc34-81d8-85e3-f6c01f1b2fdd'
CHANNEL='C0C7R970C5T'
BODY='*Runpod agent AWS test*\nSynthetic delivery check only. No partner request or invitation decision is attached.\nTest reference: TEST-AWS-FRESH-20261023\nExpected result: one message, one saved receipt, no duplicate on the next poll.'

class IsolatedRemote(Remote):
    posts=0
    def get(self,id):
        assert id==FIXTURE
        return super().get(id)
    def patch(self,id,props):
        assert id==FIXTURE
        return super().patch(id,props)
    def query(self,filter):
        assert filter=={'property':'Status','select':{'equals':'Queued'}}
        page=self.get(FIXTURE)
        if prop(page,'Status')=='Queued': yield page
    def slack(self,method,data):
        assert method in {'auth.test','conversations.info','chat.postMessage','chat.getPermalink','conversations.history'}
        if method!='auth.test': assert data['channel']==CHANNEL
        if method=='chat.postMessage':
            assert self.posts==0 and data['text']==BODY and not data.get('thread_ts') and not data.get('blocks')
            self.posts+=1
        if method in {'conversations.info','chat.getPermalink','conversations.history'}:
            return self.call('https://slack.com/api/',method+'?'+urllib.parse.urlencode(data),'GET',None,os.environ['SLACK_BOT_TOKEN'])
        return super().slack(method,data)

def main():
    remote=IsolatedRemote()
    identity=remote.slack('auth.test',{})
    assert identity['user_id']==BOT
    os.environ['SLACK_TEAM_ID']=identity['team_id']
    os.environ['OUTBOX_TEST_IDS']=FIXTURE
    channel=remote.slack('conversations.info',{'channel':CHANNEL})
    assert channel['channel']['is_member']
    page=remote.get(FIXTURE); r=row(page)
    assert page['parent']['data_source_id']==SOURCE and not page.get('archived') and not page.get('in_trash')
    assert r['body'] in (BODY,BODY.replace('*','')) and r['channel']==CHANNEL and r['kind']=='other'
    assert not any(r[k] for k in ('mentions','thread','related','ts','before')) and r['status']=='Cancelled'
    credential=json.loads(os.environ['RUNTIME_CREDENTIAL'])
    assert credential['username']=='agent_runtime'
    source=PostgresStore(dict(host=os.environ['DB_HOST'],dbname='partneragent',user='agent_runtime',password=credential['password'],sslmode='verify-full',sslrootcert='/app/rds-ca.pem'),identity['team_id']+':pilot-OUT22-20261023')
    assert source.delivery(FIXTURE) is None, 'Existing pilot receipt requires review; no automatic reset'
    with worker_session(source,'outbox') as owned:
        box=Outbox(None,remote,owned)
        remote.patch(FIXTURE,{'Body':rich(BODY),'Status':{'select':{'name':'Queued'}}})
        box.poll()
        receipt=box.get(FIXTURE)
        assert receipt and receipt['state']=='posted' and receipt['ts'], 'Inspect held receipt before retry'
        ts=receipt['ts']
    assert remote.posts==1
    # New worker session proves the persisted fence survives a worker restart.
    with worker_session(source,'outbox') as owned:
        box=Outbox(None,remote,owned)
        box.poll()
        box.post(page | {'properties': dict(page['properties'],Status={'type':'select','select':{'name':'Queued'}})})
        assert box.get(FIXTURE)['ts']==ts
    assert remote.posts==1
    final=remote.get(FIXTURE)
    assert prop(final,'Status')=='Posted' and prop(final,'Message TS')==ts
    messages=remote.slack('conversations.history',{'channel':CHANNEL,'oldest':ts,'latest':ts,'inclusive':'true','limit':10})['messages']
    assert len(messages)==1 and messages[0]['ts']==ts and messages[0]['text']==BODY and messages[0]['user']==BOT
    print(json.dumps({'result':'PASS','post_calls':remote.posts,'restart_poll_duplicate_calls':0,'notion_status':'Posted','message_ts':ts,'permalink':final['properties']['Permalink']['url']}),flush=True)

if __name__=='__main__':
    try: main()
    except BaseException as error:
        print(json.dumps({'result':'STOPPED','error_type':type(error).__name__,'instruction':'Inspect persisted pilot receipt; do not reset or repost'}),flush=True)
        raise SystemExit(2)
