"""Isolated read-only connectivity check; emits no tokens or message contents."""
import json
import os
import re
import urllib.request
import psycopg

FIXTURE='402ff732-fc34-81d8-85e3-f6c01f1b2fdd'
SOURCE='b9f3d371-6c3b-47f5-880a-47342ae69456'
CHANNEL='C0C7R970C5T'


def request(url,token,data=None,notion=False):
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'}
    if notion: headers['Notion-Version']='2025-09-03'
    req=urllib.request.Request(url,data=None if data is None else json.dumps(data).encode(),headers=headers)
    with urllib.request.urlopen(req,timeout=20) as response: return json.load(response)


def main():
    results={}
    try:
        token=os.environ['SLACK_BOT_TOKEN']
        if not token.startswith('xoxb-') or token.strip()!=token: raise ValueError()
        identity=request('https://slack.com/api/auth.test',token,{})
        if not identity.get('ok') or identity.get('user_id')!='U0C7372ET45': raise ValueError()
        results['slack_identity']='PASS'
        results['slack_bot_username']=identity.get('user','')
        results['slack_bot_user_id']=identity.get('user_id','')
        channel=request('https://slack.com/api/conversations.info?channel='+CHANNEL,token)
        results['test_channel_membership']='PASS' if channel.get('ok') and channel['channel'].get('is_member') else 'UNVERIFIED'
        if not channel.get('ok'):
            error=channel.get('error')
            needed=channel.get('needed','').split(',')
            results['token_granted_scopes']=[x for x in channel.get('provided','').split(',') if re.fullmatch(r'[a-z_]+(?::[a-z_]+)*',x)]
            results['channel_scopes_needed']=[x for x in needed if x in ('channels:read','groups:read','im:read','mpim:read')]
            results['channel_check_reason']=error if isinstance(error,str) and re.fullmatch(r'[a-z_]{1,80}',error) else 'other_api_error'
        elif not channel['channel'].get('is_member'):
            results['channel_check_reason']='bot_not_member'
    except Exception:
        results['slack_identity']='FAIL: check token and bot identity'
    try:
        token=os.environ['NOTION_TOKEN']
        if not token.startswith(('ntn_','secret_')) or token.strip()!=token: raise ValueError()
        page=request('https://api.notion.com/v1/pages/'+FIXTURE,token,notion=True)
        props=page['properties']
        target=''.join(x.get('plain_text','') for x in props['Channel ID']['rich_text'])
        if page['parent'].get('data_source_id')!=SOURCE or props['Status']['select']['name']!='Cancelled' or target!=CHANNEL:
            raise ValueError()
        results['notion_cancelled_fixture']='PASS'
    except Exception:
        results['notion_cancelled_fixture']='FAIL: check token, integration access and fixture'
    try:
        credential=json.loads(os.environ['RUNTIME_CREDENTIAL'])
        if credential['username']!='agent_runtime': raise ValueError()
        with psycopg.connect(host=os.environ['DB_HOST'],dbname='partneragent',user=credential['username'],
             password=credential['password'],sslmode='verify-full',sslrootcert='/app/rds-ca.pem',connect_timeout=10) as db:
            db.execute('SET TRANSACTION READ ONLY')
            assert db.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0]
            tables=[r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='partner_agent'")]
            required={'jobs','reviews','review_meta','feedback','outbox_delivery','outbox_events','worker_ownership'}
            results['database_tls_runtime']='PASS'
            results['schema_missing']=sorted(required-set(tables))
    except Exception:
        results['database_tls_runtime']='FAIL: check runtime credentials or network'
    print(json.dumps(results,sort_keys=True),flush=True)
    return 0 if all(results.get(k)=='PASS' for k in ('slack_identity','test_channel_membership','notion_cancelled_fixture','database_tls_runtime')) and not results.get('schema_missing') else 2


if __name__=='__main__':
    raise SystemExit(main())
