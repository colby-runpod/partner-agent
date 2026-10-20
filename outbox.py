"""Notion outbox transport for Team Partner Agent; no model and no external recipients.

A durable sending fence deliberately holds ambiguous Slack outcomes for reconciliation.
Never retry a timeout as a new post. One dedicated outbox worker owns this ledger, alongside the existing review worker.
"""
import datetime as dt
import json
import os
import re
import sqlite3
import urllib.request
import urllib.error
import uuid
from zoneinfo import ZoneInfo

SOURCE = 'b9f3d371-6c3b-47f5-880a-47342ae69456'
INTAKE = 'ffecfb43-625c-42c6-a6af-1f8951641bba'
COLBY = 'U0BEHPEJA6L'
NOTION_COLBY = '38dd872b-594c-8152-9d06-0002630c4d98'
BOT = 'U0C7372ET45'
CHANNELS = {'C0CAL8C08RK', 'C0CAKA6PW8K', 'C0C7HG8KYFL'}
ACTIONS = {'outbox_approve': 'Approve', 'outbox_decline': 'Decline', 'outbox_call': 'Call first'}
KINDS = {'supply_lead', 'event_request', 'priority_alert', 'daily_digest', 'invitation_queue', 'other'}

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def rich(value):
    return {'rich_text': [{'type': 'text', 'text': {'content': value[i:i+1800]}} for i in range(0, len(value), 1800)]}

def prop(page, key):
    p = page.get('properties', {}).get(key, {})
    t = p.get('type')
    if t in ('rich_text', 'title'):
        return ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in p.get(t, []))
    if t == 'select':
        return (p.get(t) or {}).get('name', '')
    if t == 'date':
        return (p.get(t) or {}).get('start', '')
    if t == 'relation':
        if p.get('has_more'):
            raise ValueError('Too many intake relations; split into individual rows')
        return [x['id'] for x in p[t]]
    return ''

def row(page):
    return dict(id=page['id'], channel=prop(page,'Channel ID'), body=prop(page,'Body'),
                kind=prop(page,'Kind'), mentions=prop(page,'Mention IDs'), thread=prop(page,'Thread TS'),
                ts=prop(page,'Message TS'), status=prop(page,'Status'), before=prop(page,'Not Before'),
                related=prop(page,'Related Intake') or [])

def payload(r):
    if r['channel'] not in CHANNELS:
        raise ValueError('channel not allowed')
    if r['kind'] not in KINDS or not r['body'] or len(r['body']) > 35000:
        raise ValueError('Invalid kind or message body')
    ids = [x.strip() for x in r['mentions'].split(',') if x.strip()]
    if any(not re.fullmatch(r'[UW][A-Z0-9]+', x) for x in ids):
        raise ValueError('Invalid mention ID')
    if re.search(r'(?i)http://|(?<![\w/])www\.', r['body']):
        raise ValueError('Use explicit https:// links; body was not changed')
    if r['thread'] and not re.fullmatch(r'\d+\.\d+', r['thread']):
        raise ValueError('Invalid thread timestamp')
    text = (' '.join('<@%s>' % x for x in ids)+'\n' if ids else '') + r['body']
    p = dict(channel=r['channel'], text=text, mrkdwn=True, unfurl_links=False, unfurl_media=False,
             client_msg_id=str(uuid.uuid5(uuid.NAMESPACE_URL, 'partner-outbox:'+r['id'])))
    if r['thread']:
        p['thread_ts'] = r['thread']
    if r['kind'] == 'invitation_queue' and r['related']:
        if r['channel'] != 'C0C7HG8KYFL':
            raise ValueError('Invitations must be in ask-partners')
        chunks = []
        for line in text.splitlines(keepends=True):
            if len(line) > 2800:
                raise ValueError('Invitation line too long for a Slack block; shorten in outbox')
            if chunks and len(chunks[-1]) + len(line) <= 2800:
                chunks[-1] += line
            else:
                chunks.append(line)
        p['blocks'] = [{'type':'section','text':{'type':'mrkdwn','text':chunk}}
                       for chunk in chunks] + [{'type':'actions','elements':[
            {'type':'button','text':{'type':'plain_text','text':label},'action_id':key,'value':r['id']}
            for key,label in ACTIONS.items()]}]
    return p

class Rejected(Exception):
    """Definitive API rejection: Slack says no message was accepted."""

class Remote:
    def call(self, base, path, method, data, token, notion=False):
        headers = {'Authorization':'Bearer '+token,'Content-Type':'application/json'}
        if notion:
            headers['Notion-Version']='2025-09-03'
        req=urllib.request.Request(base+path, data=json.dumps(data).encode() if data is not None else None,
                                   headers=headers,method=method)
        try:
            with urllib.request.urlopen(req,timeout=20) as response:
                result=json.load(response)
        except urllib.error.HTTPError as e:
            # An HTTP error or network timeout may follow an accepted Slack post.
            raise RuntimeError('Remote HTTP %s; outcome may be unknown' % e.code) from None
        if not notion and not result.get('ok'):
            raise Rejected(re.sub(r'[^a-zA-Z0-9_]', '', str(result.get('error','slack_error')))[:100])
        return result
    def slack(self, method, data):
        return self.call('https://slack.com/api/',method,'POST',data,os.environ['SLACK_BOT_TOKEN'])
    def notion(self, method, path, data=None):
        return self.call('https://api.notion.com/v1/',path,method,data,os.environ['NOTION_TOKEN'],True)
    def query(self, filter):
        cursor=None
        while True:
            data={'filter':filter,'sorts':[{'timestamp':'created_time','direction':'ascending'}],'page_size':100}
            if cursor: data['start_cursor']=cursor
            result=self.notion('POST','data_sources/'+SOURCE+'/query',data)
            yield from result['results']
            if not result.get('has_more'): return
            cursor=result['next_cursor']
    def patch(self, id, props):
        return self.notion('PATCH','pages/'+str(uuid.UUID(id)),{'properties':props})
    def get(self, id):
        return self.notion('GET','pages/'+str(uuid.UUID(id)))

class Outbox:
    def __init__(self, path, remote=None):
        self.path=path; self.remote=remote or Remote(); self.verified=False
        raw = os.environ.get('OUTBOX_TEST_IDS')
        self.test_ids = None if raw is None else {str(uuid.UUID(x.strip())) for x in raw.split(',')}
        # A present but empty/malformed restriction fails closed during startup.
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS outbox_delivery (id TEXT PRIMARY KEY, snapshot TEXT NOT NULL, state TEXT NOT NULL, ts TEXT, decision TEXT, actor TEXT, at TEXT, error TEXT)')
            db.execute('CREATE INDEX IF NOT EXISTS outbox_ts ON outbox_delivery(ts)')
            db.execute('CREATE TABLE IF NOT EXISTS outbox_events (key TEXT PRIMARY KEY, payload TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)')
    def selected(self, id):
        return self.test_ids is None or str(uuid.UUID(id)) in self.test_ids
    def verify(self):
        if not self.verified:
            identity = self.remote.slack('auth.test', {})
            if identity.get('user_id') != BOT or identity.get('team_id') != os.environ.get('SLACK_TEAM_ID'):
                raise ValueError('Outbox token must belong to Team Partner Agent in the configured workspace')
            self.verified = True
    def db(self):
        db=sqlite3.connect(self.path,timeout=10); db.row_factory=sqlite3.Row; return db
    def get(self,id):
        with self.db() as db:
            r=db.execute('SELECT * FROM outbox_delivery WHERE id=?',(id,)).fetchone()
            return dict(r) if r else None
    def save(self,id,**fields):
        if not fields.keys() <= {'state','ts','decision','actor','at','error'}: raise ValueError('Invalid ledger field')
        with self.db() as db:
            db.execute('UPDATE outbox_delivery SET '+','.join(k+'=?' for k in fields)+' WHERE id=?',[*fields.values(),id])
    def sync(self,d):
        if not self.selected(d['id']): return
        r=json.loads(d['snapshot']); ts=d['ts']
        if not ts: return
        current=self.remote.get(r['id'])
        if current.get('archived') or current.get('in_trash') or prop(current,'Status')=='Cancelled':
            self.save(r['id'],state='cancelled'); return
        props={'Message TS':rich(ts),'Permalink':{'url':self.remote.slack('chat.getPermalink',{'channel':r['channel'],'message_ts':ts})['permalink']},'Error':rich('')}
        if d['decision']:
            decision=d['decision']
            if r['kind']=='invitation_queue':
                # Only a persisted, authorized button decision can reach this path.
                if d['actor'] != COLBY: raise ValueError('Invalid invitation actor')
                if decision in ('Approve','Decline'):
                    for id in r['related']:
                        target=self.remote.get(id)
                        if target.get('parent',{}).get('data_source_id') != INTAKE:
                            raise ValueError('Related intake is outside the approved source')
                    for id in r['related']:
                        self.remote.patch(id,{'Invitation Decision':{'select':{'name':'Approved' if decision=='Approve' else 'Declined'}},
                            'Invitation Approved By':{'people':[{'object':'user','id':NOTION_COLBY}]},
                            'Invitation Approved Date':{'date':{'start':dt.datetime.fromisoformat(d['at']).astimezone(ZoneInfo('America/Denver')).date().isoformat()}}})
                props['Decision']={'select':{'name':decision}}
                label={'Approve':'Approved','Decline':'Declined','Call first':'Call first'}[decision]
                stamp=dt.datetime.fromisoformat(d['at']).astimezone(ZoneInfo('America/Denver')).strftime('%m/%d')
                self.remote.slack('chat.update',{'channel':r['channel'],'ts':ts,'text':payload(r)['text']+'\n\n'+label+' by Colby, '+stamp,'blocks':[]})
            props.update({'Status':{'select':{'name':'Declined' if decision=='Declined' else 'Confirmed'}},'Confirmed By':rich(d['actor']),'Confirmed At':{'date':{'start':d['at']}}})
        else:
            # Do not erase a terminal decision on a delayed publication writeback.
            if prop(current,'Status') not in ('Confirmed','Declined'):
                props['Status']={'select':{'name':'Posted'}}
            props['Posted At']={'date':{'start':d['at']}}
        self.remote.patch(r['id'],props)
        self.save(r['id'],state='settled' if d['decision'] else 'posted',error='')
    def poll(self):
        self.verify()
        # Resume writebacks/decisions without re-posting messages.
        with self.db() as db:
            pending=[dict(x) for x in db.execute("SELECT * FROM outbox_delivery WHERE state IN ('sync','deciding')")]
        for d in pending:
            try: self.sync(d)
            except Exception: self.save(d['id'],error='Writeback pending; inspect permissions or remote availability')
        with self.db() as db:
            events=[dict(x) for x in db.execute('SELECT * FROM outbox_events WHERE done=0 ORDER BY rowid LIMIT 100')]
        for e in events:
            try:
                self.event(json.loads(e['payload']))
                with self.db() as db: db.execute('UPDATE outbox_events SET done=1 WHERE key=?',(e['key'],))
            except Exception:
                print('Outbox pickup/decision pending; retrying next poll.',flush=True)
        for page in self.remote.query({'property':'Status','select':{'equals':'Queued'}}):
            try: self.post(page)
            except Exception:
                print('Outbox row processing failed; retained for reconciliation.',flush=True)
    def post(self,page):
        if not self.selected(page['id']): return
        self.verify()
        r=row(page)
        if page.get('archived') or page.get('in_trash') or r['status']!='Queued': return
        d=self.get(r['id'])
        if d:
            if d['state']=='failed':
                self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich(d['error'] or 'Delivery rejected')})
            if d['state']=='sending':
                self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich('Ambiguous Slack delivery; reconcile before any retry. No automatic repost.')})
            return
        if r['ts']: return  # Never adopt or edit a message not in our own ledger.
        if r['before']:
            try:
                due=dt.datetime.fromisoformat(r['before'].replace('Z','+00:00'))
                if due.tzinfo is None: raise ValueError()
            except ValueError:
                self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich('Not Before must be a valid date-time with timezone')});return
            if due > dt.datetime.now(dt.timezone.utc): return
        if r['channel'] not in CHANNELS:
            self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich('channel not allowed')});return
        if r['thread'].startswith('parent:'):
            match=re.fullmatch(r'parent:OUT-(\d+)',r['thread'])
            if not match:
                self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich('Invalid parent reference')});return
            parents=list(self.remote.query({'property':'Post ID','unique_id':{'equals':int(match[1])}}))
            if len(parents)!=1: return
            p=row(parents[0])
            if p['status'] not in ('Posted','Confirmed','Declined') or not p['ts']: return
            if p['channel']!=r['channel']:
                self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich('Parent channel differs')});return
            r['thread']=p['ts']
        try: message=payload(r)
        except ValueError as e:
            self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich(str(e))});return
        # Recheck cancellation immediately before taking the irreversible send fence.
        fresh = self.remote.get(r['id'])
        if fresh.get('archived') or fresh.get('in_trash') or prop(fresh,'Status')!='Queued' or prop(fresh,'Message TS'): return
        with self.db() as db:
            if not db.execute('INSERT OR IGNORE INTO outbox_delivery(id,snapshot,state,at) VALUES (?,?,?,?)',(r['id'],json.dumps(r),'sending',now())).rowcount: return
        for attempt in range(2):
            try:
                result=self.remote.slack('chat.postMessage',message)
                if not re.fullmatch(r'\d+\.\d+',result.get('ts','')): raise RuntimeError('Missing Slack receipt')
                self.save(r['id'],ts=result['ts'],state='sync')
                self.sync(self.get(r['id']));return
            except Rejected as e:
                # Only retry a definitive rejection, not a success followed by a writeback failure.
                if self.get(r['id'])['ts']: raise
                if attempt==0: continue
                self.save(r['id'],state='failed',error=str(e))
                self.remote.patch(r['id'],{'Status':{'select':{'name':'Failed'}},'Error':rich(str(e))});return
            except Exception:
                # Preserve sync state if Slack succeeded; otherwise sending is a durable uncertainty fence.
                raise
    def enqueue_event(self,e):
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO outbox_events(key,payload) VALUES (?,?)',(e['key'],json.dumps(e)))
        # Poll owns retry and keeps decisions durable even during a remote outage.
    def event(self,e):
        self.verify()
        with self.db() as db:
            matches=[dict(x) for x in db.execute('SELECT * FROM outbox_delivery WHERE ts=?',(e['message_ts'],))]
        d=next((x for x in matches if json.loads(x['snapshot'])['channel']==e['channel']),None)
        if not d or d['decision'] or not self.selected(d['id']): return
        r=json.loads(d['snapshot'])
        status = prop(self.remote.get(r['id']),'Status')
        if status == 'Queued' and d['state'] == 'sync':
            raise RuntimeError('Wait for publication writeback before pickup')
        if status != 'Posted': return
        if e['kind']=='outbox_action':
            if r['kind']!='invitation_queue' or not r['related'] or e['row_id']!=r['id']: return
            if e['user']!=COLBY:
                self.remote.slack('chat.postEphemeral',{'channel':e['channel'],'user':e['user'],'text':'Only Colby can decide invitations'});return
            decision=ACTIONS[e['action']]
        else:
            if r['kind'] not in ('supply_lead','event_request') or e['user']==BOT: return
            user=self.remote.slack('users.info',{'user':e['user']})['user']
            if user.get('is_bot') or user.get('is_app_user') or user.get('deleted'): return
            if e.get('reply') and e['user']==COLBY: return
            decision='Declined' if e.get('reaction')=='x' else 'Confirmed'
        with self.db() as db:
            db.execute("UPDATE outbox_delivery SET decision=?,actor=?,at=?,state='deciding' WHERE id=? AND decision IS NULL",(decision,e['user'],now(),r['id']))
        self.sync(self.get(r['id']))

def owns(channel,ts):
    path = os.environ.get('CHAT_DB', '/data/chat.sqlite3')
    try:
        with sqlite3.connect('file:'+path+'?mode=ro', uri=True, timeout=2) as db:
            return any(json.loads(x[0])['channel']==channel for x in db.execute('SELECT snapshot FROM outbox_delivery WHERE ts=?',(ts,)))
    except sqlite3.Error:
        return False

def accept(body,cfg):
    if not cfg.get('outbox_enabled') or body.get('team_id')!=cfg['team_id'] or body.get('api_app_id')!=cfg['app_id']: return
    e=body.get('event',{}); item=e.get('item',{})
    if not body.get('event_id') or not e.get('user') or e.get('bot_id') or e.get('user')==BOT: return
    if e.get('type')=='reaction_added' and item.get('type')=='message' and e.get('reaction') in ('white_check_mark','eyes','x'):
        channel,ts=item.get('channel'),item.get('ts')
    elif e.get('type')=='message' and not e.get('subtype') and e.get('thread_ts') and e['thread_ts']!=e.get('ts'):
        channel,ts=e.get('channel'),e['thread_ts']
    else: return
    if channel not in CHANNELS or not re.fullmatch(r'\d+\.\d+',ts or ''): return
    if not owns(channel,ts): return
    key='outbox:'+body['event_id']
    return dict(kind='outbox_event',key=key,event_id=key,channel=channel,message_ts=ts,user=e['user'],reaction=e.get('reaction'),reply=e.get('type')=='message')

def action(p,cfg):
    if not cfg.get('outbox_enabled') or p.get('type')!='block_actions' or p.get('team',{}).get('id')!=cfg['team_id'] or p.get('api_app_id')!=cfg['app_id']: return
    a=(p.get('actions') or [{}])[0]; c=p.get('channel',{}).get('id'); ts=p.get('message',{}).get('ts',''); u=p.get('user',{}).get('id')
    if not re.fullmatch(r'\d+\.\d+', str(a.get('action_ts',''))): return
    if a.get('action_id') not in ACTIONS or c!='C0C7HG8KYFL' or not u or not re.fullmatch(r'\d+\.\d+',ts): return
    try: id=str(uuid.UUID(a.get('value','')))
    except (ValueError,AttributeError): return
    key='outbox-action:'+str(a.get('action_ts'))+':'+u
    return dict(kind='outbox_action',key=key,event_id=key,channel=c,message_ts=ts,user=u,row_id=id,action=a['action_id'])


def check():
    """Read-only runtime preflight. Never prints token values or message bodies."""
    remote = Remote()
    identity = remote.slack('auth.test', {})
    if identity.get('user_id') != BOT or identity.get('team_id') != os.environ.get('SLACK_TEAM_ID'):
        raise ValueError('Wrong Slack bot or workspace')
    for source in (SOURCE, INTAKE):
        remote.notion('GET', 'data_sources/' + source)
    count = sum(1 for _ in remote.query({'property':'Status','select':{'equals':'Queued'}}))
    print('Bot identity and both Notion sources readable. Queued rows: %d. No messages sent. Write permissions and channel membership still require live acceptance.' % count)


def worker():
    import fcntl
    import time
    path = os.environ.get('CHAT_DB', '/data/chat.sqlite3')
    if os.environ.get('OUTBOX_ENABLED') != 'true':
        raise SystemExit('OUTBOX_ENABLED must be true')
    # Open after the receiver has initialized CHAT_DB; Outbox also creates its own tables.
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path + '.outbox.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        box = Outbox(path)
        while True:
            try:
                box.poll()
            except Exception:
                print('Outbox poll failed; verify bot identity and Notion access. Retrying in 180 seconds.', flush=True)
            time.sleep(180)


if __name__ == '__main__':
    import sys
    if sys.argv[1:] == ['check']:
        check()
    elif sys.argv[1:] == ['worker']:
        worker()
    else:
        raise SystemExit('Usage: python outbox.py check|worker')
