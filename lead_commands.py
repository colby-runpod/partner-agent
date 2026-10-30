"""Explicit Colby-requested specialist handoffs with Notion activity receipts."""
import datetime as dt
import html
import json
import re
import partner_sources as routes

DESTINATIONS = {
    'supply': ('Supply Team', 'C0CAL8C08RK'),
    'events': ('Events/Sponsorship', 'C0CAKA6PW8K'),
    'startup': ('VC/Incubator', routes.VC_CHANNEL),
    'research': ('Research/University', routes.RESEARCH_CHANNEL),
    'affiliate': ('Affiliate Program', routes.AFFILIATE_CHANNEL),
}
ALIASES = {'supply':'supply','supply leads':'supply','feed-partner-supply-leads':'supply',
    'event':'events','events':'events','event leads':'events','events leads':'events',
    'feed-partner-event-requests':'events','startup':'startup','startup leads':'startup',
    'vc':'startup','vc/incubator':'startup','feed-partner-startup-leads':'startup',
    'research':'research','research/edu':'research','research leads':'research',
    'research/edu leads':'research','feed-partner-research-edu-leads':'research',
    'affiliate':'affiliate','affiliate leads':'affiliate','feed-partner-affiliate-creator-leads':'affiliate'}
PREFIX = re.compile(r'^(?:(?:can|could|would|will) you\s+)?(?:please\s+)?(?:route|forward|send|share|pass along)\b',re.I)


def destination(text):
    match=re.fullmatch(r'(?:(?:can|could|would|will) you\s+)?(?:please\s+)?'
        r'(?:route|forward|send|share|pass along)\s+(?:(?:this|it|this lead|this submission|this one)\s+)?'
        r'(?:to|with)\s+(?:the\s+)?(.+?)[?.!]*',text.strip(),re.I)
    if not match:return None
    target=match[1].strip().lower()
    target=re.sub(r'\s+(?:channel|cahnnel)$','',target).lstrip('#')
    for name,(_,channel) in DESTINATIONS.items():
        if re.fullmatch(r'<#'+channel.lower()+r'(?:\|[^<>]+)?>',target):return name
    return ALIASES.get(target)


def accept(body,cfg):
    e=body.get('event',{})
    if not cfg.get('review_enabled') or body.get('team_id')!=cfg['team_id'] or body.get('api_app_id')!=cfg['app_id']:return None
    if e.get('type')!='app_mention' or e.get('subtype') or e.get('bot_id') or e.get('user')!=routes.COLBY:return None
    if e.get('channel') not in cfg.get('review_channels',set()):return None
    ts,thread=e.get('ts',''),e.get('thread_ts','')
    if not body.get('event_id') or not all(re.fullmatch(r'\d+\.\d+',v) for v in (ts,thread)) or ts==thread:return None
    mention='<@'+cfg['bot_user']+'>'
    if mention not in e.get('text',''):return None
    text=e['text'].replace(mention,'').strip()
    if not PREFIX.match(text):return None
    return dict(kind='lead_command',key='lead-command:'+e['channel']+':'+ts,event_id=body['event_id'],
        channel=e['channel'],ts=ts,thread_ts=thread,user=e['user'],request=text[:4000],destination=destination(text))


class NeedsReview(Exception):
    pass


def link(e):return 'https://slack.com/archives/%s/p%s'%(e['channel'],e['ts'].replace('.',''))


def paragraph(text):return {'object':'block','type':'paragraph','paragraph':{'rich_text':[
    {'type':'text','text':{'content':text[i:i+1800]}} for i in range(0,len(text),1800)]}}


def append_activity(notion,page_id,marker,text):
    # Reconcile a known receipt before appending. Never automatically retry an uncertain append.
    cursor=''
    for _ in range(30):
        path='blocks/'+page_id+'/children?page_size=100'
        if cursor:
            import urllib.parse
            path+='&start_cursor='+urllib.parse.quote(cursor,safe='')
        result=notion._req('GET',path)
        for b in result.get('results',[]):
            p=b.get('paragraph',{})
            existing=''.join(x.get('plain_text',x.get('text',{}).get('content','')) for x in p.get('rich_text',[]))
            if existing==marker+'\n'+text:return
            if existing.startswith(marker+'\n'):raise ValueError('Conflicting activity receipt')
        if not result.get('has_more'):break
        cursor=result.get('next_cursor')
        if not cursor:raise ValueError('Incomplete activity history')
    else:raise ValueError('Activity history exceeds bound')
    notion._req('PATCH','blocks/'+page_id+'/children',{'children':[paragraph(marker+'\n'+text)]})


def ensure_record(notion,ledger,row,data):
    if row.get('notion_id') and row.get('notion_url'):return row['notion_url'],row['notion_id']
    ref='tpa:%s:%s'%(row['channel'],row['ts'])
    # Exact source first, then a unique email match. Never pick an arbitrary first row.
    for predicate in ([{'property':'Intake Ref','rich_text':{'equals':ref}}]+
                      ([{'property':'Email','email':{'equals':data['email']}}] if data.get('email') else [])):
        result=notion._req('POST','data_sources/'+notion.cfg['notion_intake']+'/query',{'filter':predicate,'page_size':100})
        rows=result.get('results',[])
        if result.get('has_more') or len(rows)>1:raise ValueError('Ambiguous Notion record')
        if rows:
            page=rows[0];break
    else:
        page=notion._req('POST','pages',{'parent':{'type':'data_source_id','data_source_id':notion.cfg['notion_intake']},
                                      'properties':notion.properties(data,ref)})
    ledger.mark(row['key'],notion_id=page['id'],notion_url=page['url'])
    return page['url'],page['id']


def process(store,job,clients,ledger,cfg,notion):
    event=json.loads(job['payload']);key=job['key']
    if job['state']=='writing':
        # A process died between side effects. Hold rather than repeat a post/create/append.
        store.update(key,state='done',error='lead_command_reconciliation_required',reply=
            'This routing request was interrupted. Its delivery or Notion log needs reconciliation before retrying; I will not send it again automatically.')
    elif job['state']=='pending':
        store.update(key,state='writing')
        reply='';receipt={}
        try:
            if not event.get('destination'):
                reply='Which destination should I use: supply, events, startup/VC, research/edu or affiliate? Please request one destination per message.'
            elif not cfg.get('routing_enabled') or not cfg.get('notion_write') or notion is None:
                reply='Routing and its Notion activity log must both be enabled before I can send this lead. Nothing was forwarded.'
            else:
                row=ledger.find(channel=event['channel'],thread_ts=event['thread_ts'])
                if not row:raise NeedsReview()
                data=json.loads(row.get('data') or '{}')
                if not data.get('source_event') or not isinstance(data.get('signals'),dict):raise NeedsReview()
                target,channel=DESTINATIONS[event['destination']]
                handoff=routes.handoff(data,data['source_event'],cfg,requested_route=target,requested_by=routes.COLBY)
                if not handoff:
                    reply='This lead is marked as spam, so I have not forwarded it. Correct that classification with a new review first.'
                else:
                    url,page_id=ensure_record(notion,ledger,row,data)
                    receipt.update(notion_url=url,notion_id=page_id,handoff_key=handoff['key'])
                    store.update(key,ref=json.dumps(receipt))
                    stamp=dt.datetime.fromtimestamp(float(event['ts']),dt.timezone.utc).isoformat()
                    activity=('Requested by Colby Henderson ('+event['user']+') at '+stamp+'\nRequest: '+event['request']+
                        '\nRequest link: '+link(event)+'\nDestination: '+target+' ('+channel+')'+
                        '\nOriginal score: '+str(data.get('score'))+'; assessment: '+routes.assessment(data)+
                        '\nOriginal signals: '+json.dumps(data.get('signals'),ensure_ascii=False)+
                        '\nForwarded for specialist validation at Colby’s request; not qualification, approval or outreach.')
                    append_activity(notion,page_id,key+':requested',activity)
                    store.enqueue(handoff)
                    delivered=store.get(handoff['key'])
                    already=bool(delivered.get('result_url'))
                    if delivered['state']=='pending':
                        routes.process_handoff(store,delivered,clients);delivered=store.get(handoff['key'])
                    result=delivered.get('result_url') if delivered.get('ref') else None
                    receipt.update(delivery_url=result or '',delivery_state='delivered' if result else 'unconfirmed')
                    store.update(key,ref=json.dumps(receipt),result_url=result or '')
                    outcome=('Already delivered' if already else 'Delivered')+' to '+target+': '+result if result else 'Slack delivery unconfirmed; held for reconciliation. Do not resend.'
                    append_activity(notion,page_id,key+':result',outcome+'\nRecorded at: '+dt.datetime.now(dt.timezone.utc).isoformat()+'\nRequest: '+link(event))
                    reply=(('Already forwarded' if already else 'Forwarded')+' to '+target+' for team validation: <'+result+'|Open handoff>.' if result else outcome)
                    reply+=' Original score and warnings retained. Activity logged: <'+url+'|Notion Biz Dev record>.'
        except NeedsReview:
            reply='I could not find a saved review with routing details for this thread. Please mention me with “review this” first, then request the handoff. Nothing was forwarded.'
        except Exception:
            saved=json.loads(store.get(key).get('ref') or '{}')
            reply=('The handoff is delivered: <'+saved['delivery_url']+'|Open handoff>. Its Notion activity log is incomplete; reconciliation is required.'
                   if saved.get('delivery_url') else 'I could not confirm both the handoff and its Notion activity log. The request is held for reconciliation; please do not resend it yet.')
            store.update(key,error='lead_command_reconciliation_required')
        store.update(key,state='done',reply=reply)
    current=store.get(key)
    if current.get('reply') and current['reply_state']=='pending':
        store.update(key,reply_state='sending')
        try:clients.say_thread(event['channel'],event['thread_ts'],current['reply'])
        except Exception:store.update(key,reply_state='review',error='lead_command_ack_unconfirmed')
        else:store.update(key,reply_state='sent')
