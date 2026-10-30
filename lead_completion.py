"""Lead-level completion follows a Notion receipt, never a Q&A completion emoji."""
import datetime as dt
import json
import re
import urllib.parse
import os
import partner_sources
import lead_commands
import question_progress
from intake import api


def accept(body,cfg):
    e=body.get('event',{});item=e.get('item',{})
    if not cfg.get('review_enabled') or body.get('team_id')!=cfg['team_id'] or body.get('api_app_id')!=cfg['app_id']:return None
    if not body.get('event_id') or e.get('type')!='reaction_added' or e.get('user')!=partner_sources.COLBY or e.get('reaction')!='white_check_mark':return None
    channels=cfg.get('review_channels',set()) | ({partner_sources.FORWARD_CHANNEL} if cfg.get('forward_enabled') else set())
    if item.get('type')!='message' or item.get('channel') not in channels:return None
    if not re.fullmatch(r'\d+\.\d+',item.get('ts','')):return None
    return dict(kind='lead_completion',key='lead-completion:'+body['event_id'],event_id=body['event_id'],
                channel=item['channel'],ts=item['ts'],actor=e['user'],cause='manual_check')


def enqueue_handoff(store,handoff,source,route):
    if not source.get('key') or not source.get('channel') or not source.get('thread_ts'):return
    try:receipt=store.get(handoff['key'])
    except (TypeError,KeyError):return
    if not receipt or not receipt.get('ref') or not receipt.get('result_url') or receipt.get('error'):return
    key='lead-completion:'+source['key']+':'+handoff['key']
    store.enqueue(dict(kind='lead_completion',key=key,event_id=key,channel=source['channel'],ts=source['thread_ts'],
        review_key=source['key'],cause='handoff',handoff_key=handoff['key'],route=route,actor='agent'))


def delivered(store,data):
    out=[]
    source=data.get('source_event') or {}
    for route,_ in lead_commands.DESTINATIONS.values():
        key=partner_sources.handoff_key(data,source,route)
        try:r=store.get(key)
        except (KeyError,TypeError):continue
        if r and r.get('ref') and r.get('result_url') and not r.get('error'):out.append((route,r['result_url']))
    return out


def process(store,job,clients,ledger,cfg,notion):
    e=json.loads(job['payload']);key=job['key']
    if job['state']=='writing':
        store.update(key,state='done',error='completion_reconciliation_required');return
    if job['state']!='pending':return
    try:
        row=ledger.review(e['review_key']) if e.get('review_key') else (
            ledger.find(channel=e['channel'],card_ts=e['ts']) or ledger.find(channel=e['channel'],thread_ts=e['ts']))
        # A reaction on a Q&A reply/draft/request is not a lead-closing signal.
        if not row or row.get('outcome')!='ok' or row['channel']!=e['channel']:
            store.update(key,state='done');return
        if e['cause']=='manual_check':
            msg=api('https://slack.com/api/reactions.get?'+urllib.parse.urlencode(dict(channel=e['channel'],timestamp=e['ts'])),os.environ['SLACK_BOT_TOKEN'])['message']
            if not any(r['name']=='white_check_mark' and partner_sources.COLBY in r.get('users',[]) for r in msg.get('reactions',[])):
                store.update(key,state='done');return
    except Exception:
        store.update(key,state='done',error='completion_context_unavailable');return
    if not cfg.get('notion_write') or notion is None:
        store.update(key,state='done',error='completion_notion_disabled');return
    store.update(key,state='writing')
    try:
        data=json.loads(row.get('data') or '{}');routes=delivered(store,data)
        source_ts=(data.get('source_event') or {}).get('thread_ts') or row['ts']
        if e['cause']=='handoff':
            receipt=store.get(e['handoff_key'])
            if not receipt.get('ref') or not receipt.get('result_url') or receipt.get('error'):raise ValueError('Unconfirmed handoff')
            routes=[(e['route'],receipt['result_url'])]
        url,page_id=lead_commands.ensure_record(notion,ledger,row,data)
        page=notion._req('GET','pages/'+page_id)
        current=((page.get('properties') or {}).get('Status') or {}).get('select') or {}
        # A handoff completes intake work, not the receiving team's qualification or deal.
        status='Closed' if current.get('name')=='Closed' else 'Actioned'
        props={'Status':{'select':{'name':status}}}
        if routes:
            props['Intake Stage']={'select':{'name':'Routed Internally'}}
            if len(routes)==1:props['Forward To']={'select':{'name':'Startup Program' if routes[0][0]=='VC/Incubator' else routes[0][0]}}
        stamp=dt.datetime.now(dt.timezone.utc).isoformat()
        note=('Partnerships intake action completed. Status: '+status+'\nActor: '+e['actor']+'\nRecorded: '+stamp+
            '\nSource: https://slack.com/archives/'+row['channel']+'/p'+source_ts.replace('.','')+
            '\nReason: '+('Confirmed specialist handoff' if routes else 'Colby explicitly marked the lead actioned with a check reaction')+
            '\nHandoffs: '+('; '.join(r+': '+u for r,u in routes) or 'None recorded; no delivery inferred')+
            '\nOriginal score and qualification retained. This is not partner approval, a won deal, or confirmation of receiving-team follow-up.')
        lead_commands.append_activity(notion,page_id,key+':completion',note)
        notion._req('PATCH','pages/'+page_id,{'properties':props})
        verified=notion._req('GET','pages/'+page_id)['properties']
        if (verified.get('Status',{}).get('select') or {}).get('name')!=status:raise ValueError('Notion status not confirmed')
        if routes and (verified.get('Intake Stage',{}).get('select') or {}).get('name')!='Routed Internally':raise ValueError('Notion stage not confirmed')
        store.update(key,ref=json.dumps(dict(notion_id=page_id,status=status,routes=routes)),result_url=url)
        marked=True
        for ts in dict.fromkeys(filter(None,(source_ts,row.get('card_ts')))):
            marked=question_progress.change(dict(progress=True,channel=row['channel'],ts=ts),'add','white_check_mark') and marked
        store.update(key,state='done',error='' if marked else 'completion_reaction_unconfirmed')
    except Exception:
        store.update(key,state='done',error='completion_reconciliation_required')
