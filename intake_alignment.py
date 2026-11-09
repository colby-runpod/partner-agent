"""Shared intake/outbox contract. Enabled explicitly after the cutover preflight.

No prospect sends, CRM writes or qualification overrides. The outbox worker is
sole Slack handoff sender; the review worker only produces Notion queue rows.
"""
import datetime as dt
import hashlib
import html
import os
import re
import urllib.parse

GUIDE_VERSION = '2026-11-09.1'
ACTIVITY = 'b17354bc-69b1-44fe-bb92-7fda8fab1a5d'
INTAKE = 'ffecfb43-625c-42c6-a6af-1f8951641bba'
OUTBOX = 'b9f3d371-6c3b-47f5-880a-47342ae69456'
COLBY = 'U0BEHPEJA6L'
# Validated snapshot of Operating Model section 5. Changes are release-reviewed.
ROUTES = {
    'Supply Team': ('C0CAL8C08RK', ('U0ACSTWBYHG',)),
    'Events/Sponsorship': ('C0CAKA6PW8K', ('U0AB1TSBK5X', 'U0APRAUGJ6P')),
    'Marketing': ('C0CAKA6PW8K', ('U0AB1TSBK5X', 'U0APRAUGJ6P')),
    'Startup Program': ('C0CCQ9311D2', ('U0B9ZRF3JUX',)),
    'VC/Incubator': ('C0CCQ9311D2', ('U0CE06U7K52',)),
    'Research/University': ('C0CCBS9NU2V', ('U0CE06U7K52',)),
    'Affiliate Program': ('C0CD3M1QG4U', ()),
}
SPECIALIST_CHANNELS = {v[0] for v in ROUTES.values()}


def enabled():
    return os.environ.get('INTAKE_ALIGNMENT_ENABLED') == 'true'


def rich(s):
    s = str(s)
    return {'rich_text': [{'type': 'text', 'text': {'content': s[i:i+1800]}} for i in range(0, len(s), 1800)]}


def title(s):
    return {'title': rich(s)['rich_text']}


def value(page, name):
    p = page.get('properties', {}).get(name, {})
    kind = p.get('type') or next(iter(p), '')
    if kind in ('rich_text', 'title'):
        return ''.join(x.get('plain_text') or x.get('text', {}).get('content', '') for x in p.get(kind, []))
    if kind in ('select', 'status'): return (p.get(kind) or {}).get('name', '')
    if kind == 'date': return (p.get(kind) or {}).get('start', '')
    if kind == 'relation': return [x['id'] for x in p.get(kind, [])]
    return p.get(kind) or ''


def query(req, source, predicate=None):
    cursor = None
    for _ in range(100):
        body = {'page_size': 100}
        if predicate: body['filter'] = predicate
        if cursor: body['start_cursor'] = cursor
        result = req('POST', 'data_sources/'+source+'/query', body)
        yield from result.get('results', [])
        if not result.get('has_more'): return
        cursor = result.get('next_cursor')
        if not cursor: raise ValueError('Incomplete Notion pagination')
    raise ValueError('Notion query exceeded bound; no partial result is safe')


def audit(req, key, action, detail, intake=None, outbox=None, channel='', permalink='', actor='Team Partner Agent', before='', after='', when=None):
    """Stable event identity, not intake/action/permalink which loses repeated decisions."""
    ref = 'tpa-event:'+hashlib.sha256(key.encode()).hexdigest()
    hits = list(query(req, ACTIVITY, {'property':'Source Ref','rich_text':{'equals':ref}}))
    if hits: return hits[0]['id']
    stamp = when or dt.datetime.now(dt.timezone.utc).isoformat()
    props = {'Event':title(action+': '+detail[:120]), 'Source Ref':rich(ref),
             'Action':{'select':{'name':action}}, 'Actor':{'select':{'name':actor}},
             'When':{'date':{'start':stamp}}, 'Detail':rich(detail), 'Guide Version':rich(GUIDE_VERSION),
             'Run':rich('tpa '+stamp[:10]), 'Channel':rich(channel),
             'Status Before':rich(before), 'Status After':rich(after)}
    if intake: props['Intake'] = {'relation':[{'id':intake}]}
    if outbox: props['Outbox Row'] = {'relation':[{'id':outbox}]}
    if permalink: props['Permalink'] = {'url':permalink}
    return req('POST','pages',{'parent':{'type':'data_source_id','data_source_id':ACTIVITY},'properties':props})['id']


def source_ref(event):
    return 'tpa:%s:%s' % (event['channel'], event.get('thread_ts') or event['ts'])


def capture(notion, event, data=None):
    """Exact submission identity. Never merge separate requests solely by email."""
    refresh_guide(notion._req)
    if _GUIDE_STATE.get('blocked'): raise ValueError(_GUIDE_STATE['reason'])
    ref = source_ref(event)
    hits = list(query(notion._req, INTAKE, {'or':[{'property':'Intake Ref','rich_text':{'equals':ref}},
            {'property':'Intake Ref','rich_text':{'equals':ref.replace('tpa:','slack:',1)}}]}))
    if len(hits)>1: raise ValueError('Multiple records for this submission; reconcile first')
    if hits: page=hits[0]
    else:
        props={'Contact':title((data or {}).get('contact') or 'Partner submission'),
               'Intake Ref':rich(ref), 'Status':{'select':{'name':'New'}},
               'Source':{'select':{'name':'Slack Form'}},
               'Request Summary':rich(event.get('text','')[:3000])}
        page=notion._req('POST','pages',{'parent':{'type':'data_source_id','data_source_id':INTAKE},'properties':props})
    audit(notion._req, ref+':logged','Logged','Captured source '+ref,intake=page['id'])
    return page


def set_route(notion, page, route, key, explicit=False):
    if route not in ROUTES and route not in ('Partnerships','Sales','Product/Eng','Review Needed'):
        raise ValueError('Unknown route')
    old=value(page,'Forward To')
    # A pipeline/human route already set wins over a repeated advisory review.
    if old and old != route and not explicit: return old
    if old != route:
        audit(notion._req,key+':route:'+route,'Status change','Forward To: '+str(old)+' -> '+route,
              intake=page['id'],before=old,after=route)
        notion._req('PATCH','pages/'+page['id'],{'properties':{'Forward To':{'select':{'name':route}}}})
    return route


def queue_handoff(notion, event):
    """Producer only. Outbox owns delivery and cross-producer duplicate reconciliation."""
    page=capture(notion,event['completion_source'])
    route=event['completion_route']
    route=set_route(notion,page,route,event['key'],bool(event.get('requested_by')))
    if ROUTES.get(route,('',))[0] != event['channel']:
        raise ValueError('Saved route differs from requested handoff')
    matches=[p for p in query(notion._req,OUTBOX,{'property':'Related Intake','relation':{'contains':page['id']}})
             if value(p,'Channel ID')==event['channel'] and not value(p,'Thread TS')]
    # Failed/declined/cancelled rows are not permission to repeat a delivery.
    if matches:
        live=sorted(matches,key=lambda p:(value(p,'Status') not in ('Posted','Confirmed','Declined'),p.get('created_time','')))
        return live[0]
    kind={'Supply Team':'supply_lead','Events/Sponsorship':'event_request','Marketing':'event_request'}.get(route,'other')
    name='tpa:'+page['id']+':'+kind+':'+event['completion_source']['ts']
    props={'Title':title(name),'Kind':{'select':{'name':kind}},'Status':{'select':{'name':'Queued'}},
           'Channel ID':rich(event['channel']),'Body':rich(event['text']), 'Mention IDs':rich(','.join(ROUTES[route][1])),
           'Related Intake':{'relation':[{'id':page['id']}]}}
    result=notion._req('POST','pages',{'parent':{'type':'data_source_id','data_source_id':OUTBOX},'properties':props})
    audit(notion._req,result['id']+':queued','Handoff queued','Queued '+route,intake=page['id'],outbox=result['id'],channel=event['channel'])
    return result


def intake_for(remote, r):
    if _GUIDE_STATE.get('blocked'): raise ValueError(_GUIDE_STATE['reason'])
    if len(r['related']) != 1: raise ValueError('Specialist handoff requires exactly one intake record')
    p=remote.get(r['related'][0])
    if p.get('parent',{}).get('data_source_id') != INTAKE: raise ValueError('Unapproved intake source')
    route=value(p,'Forward To')
    if route=='Influencer/Creator': route='Affiliate Program'
    if ROUTES.get(route,('',))[0] != r['channel']: raise ValueError('Intake route and destination disagree')
    if value(p,'Status')=='Closed': raise ValueError('Intake is closed; reconcile before routing')
    notes=value(p,'Next Step Notes')+' '+value(p,'Draft / Resource')
    if re.search(r'Parked by Colby|\[routing hold\]',notes,re.I): raise ValueError('Colby parked or held this lead')
    owners=set(ROUTES[route][1])
    mentioned=set(re.findall(r'<@([UW][A-Z0-9]+)>',r['body'])) | set(filter(None,(s.strip() for s in r['mentions'].split(','))))
    if not owners <= mentioned or mentioned - owners - {COLBY}:
        raise ValueError('Handoff mentions disagree with current route owners; update queued content')
    return p


def route_channel(page, kind):
    route=value(page,'Forward To')
    if route=='Influencer/Creator': route='Affiliate Program'
    if route in ROUTES: return ROUTES[route][0]
    if route=='Partnerships':
        if kind=='invitation_queue': return 'C0C7HG8KYFL'
        if kind in ('priority_alert','other'): return 'C0C7R970C5T'
    return None


def backfill_mentions(remote, page):
    r = {'id':page['id'],'mentions':value(page,'Mention IDs'),'channel':value(page,'Channel ID'),
         'related':value(page,'Related Intake') or [],'kind':value(page,'Kind')}
    if r['mentions'].strip() or r['channel'] not in SPECIALIST_CHANNELS or len(r['related'])!=1: return page
    intake=remote.get(r['related'][0])
    if route_channel(intake,r['kind'])!=r['channel']: return page
    route=value(intake,'Forward To')
    if route=='Influencer/Creator': route='Affiliate Program'
    owners=ROUTES[route][1]
    if owners:
        remote.patch(r['id'],{'Mention IDs':rich(','.join(owners))})
        return remote.get(r['id'])
    return page


def delayed_check(remote,r):
    if len(r['related'])!=1: return 'Delayed handoff requires exactly one linked intake row'
    intake=remote.get(r['related'][0])
    if intake.get('parent',{}).get('data_source_id')!=INTAKE: return 'Linked intake is outside the approved database'
    if value(intake,'Status')!='Flagged': return 'Delayed handoff held: intake Status is '+str(value(intake,'Status'))+', not Flagged'
    if route_channel(intake,r['kind'])!=r['channel']: return 'Delayed handoff held: Forward To does not match destination channel'
    return ''


def source_links(text):
    # Exact original Slack source URL, never a company/contact match alone.
    links={'https://slack.com/archives/'+c+'/p'+ts for c,ts in re.findall(r'https://(?:[\w-]+\.)*slack\.com/archives/([A-Z0-9]+)/p(\d+)',text)}
    for url in re.findall(r'https://mail\.google\.com/[^|>\s]+',text):
        fragment=urllib.parse.urlsplit(url).fragment
        if fragment and '/' in fragment: links.add('https://mail.google.com/#'+fragment.rsplit('/',1)[-1])
    return links


def prior_post(remote,r):
    """Bounded historical reconciliation. Fail closed when history is incomplete."""
    if r['thread']: return None
    links=source_links(r['body'])
    # First use all-status shared receipts, including pipeline/AWS duplicates.
    for p in remote.query({'property':'Related Intake','relation':{'contains':r['related'][0]}}):
        if p['id']==r['id'] or value(p,'Channel ID')!=r['channel'] or value(p,'Thread TS'): continue
        if value(p,'Message TS') and value(p,'Status') in ('Posted','Confirmed','Declined'):
            return value(p,'Message TS')
    if not links: return None
    cursor=None
    for _ in range(30):
        args={'channel':r['channel'],'limit':100}
        if cursor: args['cursor']=cursor
        result=remote.slack('conversations.history',args)
        for msg in result.get('messages',[]):
            if msg.get('user')!='U0C7372ET45': continue
            if links & source_links(msg.get('text','')): return msg['ts']
        cursor=result.get('response_metadata',{}).get('next_cursor')
        if not cursor: return None
    raise ValueError('History reconciliation incomplete; hold delivery')


def sync_receipt(remote,r,d,permalink):
    if not r['related']: return
    page=remote.get(r['related'][0]);old=value(page,'Status')
    decision=d.get('decision')
    action='Handoff declined' if decision=='Declined' else 'Pickup confirmed' if decision else 'Handoff posted'
    key=r['id']+':'+action
    audit(remote.notion,key,action,r['title'],intake=page['id'],outbox=r['id'],channel=r['channel'],
          permalink=permalink,actor='Other human' if decision else 'Team Partner Agent',before=old,
          after='Flagged' if decision=='Declined' else 'Actioned',when=d['at'])
    # Preserve final closure. A receipt does not reopen a closed record.
    if old != 'Closed':
        props={'Status':{'select':{'name':'Flagged' if decision=='Declined' else 'Actioned'}}}
        if decision!='Declined': props['Intake Stage']={'select':{'name':'Routed Internally'}}
        remote.patch(page['id'],props)
    ref=value(page,'Intake Ref')
    match=re.fullmatch(r'(?:tpa|slack):([A-Z0-9]+):(\d+\.\d+)',ref)
    # Pipeline-owned HubSpot DMs are not conversations this bot can access.
    # Their destination receipt and audit still complete; only source reactions skip.
    if match and not match[1].startswith('D'):
        emoji='clock3' if decision=='Declined' else 'white_check_mark'
        for method,name in [('reactions.add',emoji),('reactions.remove','white_check_mark' if decision=='Declined' else 'clock3')]:
            try: remote.slack(method,{'channel':match[1],'timestamp':match[2],'name':name})
            except Exception as exc:
                if str(exc) not in ('already_reacted','no_reaction'): raise


def pickup_allowed(r,user):
    owners=set().union(*(set(owners) for channel,owners in ROUTES.values() if channel==r['channel']))
    return user in owners | {COLBY}


def build_recap(remote, now_utc, start):
    """Shared business state; include today's changes and older outstanding work once."""
    from zoneinfo import ZoneInfo
    now_utc=now_utc.replace(tzinfo=dt.timezone.utc) if now_utc.tzinfo is None else now_utc
    refresh_guide(remote.notion)
    pages=sorted(query(remote.notion,INTAKE),
                 key=lambda p:p.get('last_edited_time') or p.get('created_time') or '', reverse=True)
    deliveries=list(query(remote.notion,OUTBOX))
    by_intake={}
    for p in deliveries:
        for id in value(p,'Related Intake') or []: by_intake.setdefault(id,[]).append(p)
    groups={k:[] for k in ('Your decisions','Drafts awaiting your send','Routed / pickup','Waiting / overdue','Parked / closed','Delivery failures')}
    sources={}; seen=set()
    for p in pages:
        if p['id'] in seen or p.get('archived') or p.get('in_trash'): continue
        seen.add(p['id'])
        status=value(p,'Status');notes=value(p,'Next Step Notes')+' '+value(p,'Draft / Resource')
        outs=by_intake.get(p['id'],[])
        failures=[o for o in outs if value(o,'Status')=='Failed' or (value(o,'Status')=='Queued' and value(o,'Error'))]
        routed=[o for o in outs if value(o,'Status') in ('Posted','Confirmed','Declined') and value(o,'Channel ID') in SPECIALIST_CHANNELS]
        draft=value(p,'Gmail Draft ID')
        stamp=p.get('last_edited_time') or p.get('created_time') or ''
        try: recent=dt.datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()>=start
        except ValueError: recent=False
        if failures: group='Delivery failures'
        elif re.search('Parked by Colby',notes,re.I): group='Parked / closed'
        elif status in ('In Review','Flagged') or value(p,'Invitation Decision')=='Call first': group='Your decisions'
        elif draft and status not in ('Follow Up','Closed'): group='Drafts awaiting your send'
        elif routed: group='Routed / pickup'
        elif status in ('New','Follow Up','Actioned'): group='Waiting / overdue'
        elif status in ('Closed','DQ') and recent: group='Parked / closed'
        else: continue
        if status=='Closed' and not recent: continue
        name=value(p,'Company') or value(p,'Contact') or 'Submission'
        safe=lambda s:html.escape(str(s),quote=False)
        url=p.get('url') or 'https://www.notion.so/'+p['id'].replace('-','')
        source=value(p,'Source') or 'Unspecified'
        sources[source]=sources.get(source,0)+1
        lines=['• *<'+url+'|'+safe(name)+'>* · '+safe(value(p,'Forward To') or 'Review Needed')+' · '+safe(source),
               '  *Request:* '+safe(value(p,'Request Summary')[:350] or 'Not recorded'),
               '  *Status:* '+safe(status)+' · '+('Updated in this reporting window' if recent else 'Earlier outstanding work')]
        if draft and status not in ('Follow Up','Closed'): lines.append('  *Next:* Colby reviews/sends the saved draft. Draft creation is not an email sent.')
        for o in routed:
            state=value(o,'Status');posted=value(o,'Posted At')
            age=''
            if posted:
                try:
                    date=dt.datetime.fromisoformat(posted.replace('Z','+00:00')).date()
                    days=sum((date+dt.timedelta(days=i)).weekday()<5 for i in range(1,(now_utc.date()-date).days+1))
                    age=' · '+str(days)+' business days'+(' · PICKUP OVERDUE' if days>=3 and state=='Posted' else '')
                except ValueError: pass
            receipt=value(o,'Permalink')
            lines.append('  *Handoff:* '+{'Posted':'Delivered; pickup pending','Confirmed':'Picked up','Declined':'Declined; new routing decision needed'}[state]+age+(' · <'+receipt+'|Open>' if receipt else ''))
        for o in failures: lines.append('  *Delivery issue:* '+safe(value(o,'Error')[:250] or 'Failed; reconcile before retrying'))
        next_step=value(p,'Next Step Notes') or value(p,'Suggested Next Step')
        if next_step: lines.append('  *Next action:* '+safe(next_step[:250]))
        groups[group].append('\n'.join(lines))
    lines=['*Partner leads · '+now_utc.astimezone(ZoneInfo('America/Denver')).strftime('%a %b %d')+'*',
           '_Coverage: shared Partner Biz Dev and Outbox, including pipeline/Gmail intake. Includes earlier outstanding work._',
           'Sources: '+', '.join(html.escape(k)+': '+str(v) for k,v in sorted(sources.items()))]
    lines.append(guide_summary(remote.notion, start))
    for name,entries in groups.items():
        if entries:
            # Historical outstanding work remains counted, not expanded into hundreds of posts.
            # Keep whole entries (and their links) intact; newest updates are shown first.
            selected=[]; used=0
            for entry in entries:
                if len(selected)>=3 or used+len(entry)>1200: break
                selected.append(entry);used+=len(entry)+2
            section='*'+name+' ('+str(len(entries))+')*'
            if selected: section+='\n\n'+'\n\n'.join(selected)
            omitted=len(entries)-len(selected)
            if omitted:
                section+='\n<https://app.notion.com/p/e5e29cefe2d94e9bb76357328d483861|View '+str(omitted)+' additional records in Partner Biz Dev>'
            lines.append(section)
    if not any(groups.values()): lines.append('No recent or outstanding lead records.')
    return '\n\n'.join(lines)

_GUIDE_CACHE = None
_GUIDE_CHECKED = 0
_GUIDE_STATE = {'degraded': True, 'reason': 'Runtime guide not yet read', 'reviewed': ''}


def refresh_guide(req):
    """Read the current guide at most every 15 minutes; never execute prose.

Known routes may change owners without code changes. New channels/rules require
review. Last validated owners survive a temporary Notion outage in this process.
"""
    import time
    global _GUIDE_CACHE, _GUIDE_CHECKED, GUIDE_VERSION
    if _GUIDE_CHECKED and time.monotonic()-_GUIDE_CHECKED < 900: return
    _GUIDE_CHECKED=time.monotonic()
    try:
        def blocks(id):
            cursor=''; found=[]
            for _ in range(30):
                path='blocks/'+id+'/children?page_size=100'+('&start_cursor='+urllib.parse.quote(cursor,safe='') if cursor else '')
                r=req('GET',path)
                found.extend(r.get('results',[]))
                if not r.get('has_more'): return found
                cursor=r.get('next_cursor')
                if not cursor: raise ValueError('Incomplete guide')
            raise ValueError('Guide exceeds bound')
        def plain(xs): return ''.join(x.get('plain_text') or x.get('text',{}).get('content','') for x in xs)
        root=blocks('40fff732-fc34-810e-ac53-f085a6d47576')
        def text_content(items, depth=0):
            if depth>12: raise ValueError('Guide nesting exceeds bound')
            result=[]
            for block in items:
                result.append(plain(block.get(block['type'],{}).get('rich_text',[])))
                if block.get('has_children') and block['type'] not in ('table','child_page','child_database'):
                    result.extend(text_content(blocks(block['id']),depth+1))
            return result
        text='\n'.join(text_content(root))
        version=re.search(r'Guide version:\s*(\d{4}-\d{2}-\d{2}\.\d+)',text)
        if not version: raise ValueError('Guide version missing')
        if version[1] != GUIDE_VERSION:
            if _GUIDE_CACHE is None or policy_fingerprint(root, blocks) != _GUIDE_STATE.get('policy'):
                raise ValueError('Guide policy changed or has no validated baseline; release review required')
        directory=blocks('40fff732-fc34-80f9-b488-cfafd7a7b257')
        def scenario_pages(items, depth=0):
            if depth>12: raise ValueError('Guide directory nesting exceeds bound')
            for block in items:
                if block['type']=='child_page': yield block
                elif block.get('has_children') and block['type'] not in ('child_database','table'):
                    yield from scenario_pages(blocks(block['id']),depth+1)
        scenarios=[b for b in scenario_pages(directory) if b['id'].replace('-','') not in ('40fff732fc34810eac53f085a6d47576','40fff732fc3481d6b394ca03b7149f6d')]
        unknown=[]
        for scenario in scenarios:
            text_content(blocks(scenario['id']))
            # Reviewed scenarios: weekly updates and the separate, disabled Working Task briefing.
            # Reading a scenario does not enable its feature or grant new action authority.
            if scenario['id'].replace('-','') not in (
                '40fff732fc348187b3cbf23755ad4ea5',
                '40fff732fc3481e2894cc6bbfa2f97dc',
            ): unknown.append(scenario['id'])
        if unknown:
            raise ValueError('New scenario pages require applicability/policy review: '+', '.join(unknown))
        active=False; route_table=None
        for b in root:
            if b['type']=='heading_2': active=plain(b['heading_2']['rich_text']).startswith('5.')
            if active and b['type']=='table': route_table=b['id'];break
        if not route_table: raise ValueError('Routing table missing')
        validated={}
        for b in blocks(route_table):
            cells=[plain(x) for x in b.get('table_row',{}).get('cells',[])]
            if len(cells)<4: continue
            name=cells[0].strip()
            if name=='Affiliate / Influencer-Creator': name='Affiliate Program'
            if name not in ROUTES: continue
            channel=re.search(r'\bC[A-Z0-9]{8,}\b',cells[2])
            if not channel or channel[0]!=ROUTES[name][0]: raise ValueError('Guide destination changed; release review required')
            owners=tuple(re.findall(r'\bU[A-Z0-9]{8,}\b',cells[3]))
            if not owners and name!='Affiliate Program': raise ValueError('Guide owner missing')
            validated[name]=(channel[0],owners)
        if set(validated)!=set(ROUTES): raise ValueError('Incomplete guide routes')
        policy=policy_fingerprint(root, blocks)
        if _GUIDE_CACHE is not None and policy != _GUIDE_STATE.get('policy'):
            raise ValueError('Guide policy changed; release review required')
        ROUTES.update(validated);_GUIDE_CACHE=validated
        GUIDE_VERSION=version[1]
        _GUIDE_STATE['policy']=policy
        _GUIDE_STATE.update(degraded=False, blocked=False, reason='')
        reviewed=re.search(r'Last reviewed:\s*(\d{4}-\d{2}-\d{2})',text)
        _GUIDE_STATE['reviewed'] = reviewed[1] if reviewed else ''
        if reviewed and (dt.date.today()-dt.date.fromisoformat(reviewed[1])).days>14:
            audit(req,'guide-overdue:'+dt.date.today().isoformat(),'Reconciliation','DRIFT: Guide review overdue; section 14')
    except Exception as exc:
        _GUIDE_STATE.update(degraded=True, reason=str(exc)[:200])
        if isinstance(exc, ValueError): _GUIDE_STATE['blocked']=True
        # Record the degraded state; a missing log permission must not disappear.
        audit(req,'guide-read:'+dt.date.today().isoformat()+':'+str(exc)[:100], 'Reconciliation',
              'DRIFT: Guide read/validation unavailable; using last reviewed '+GUIDE_VERSION+'. '+str(exc)[:200])


def request_pipeline(notion, page_id, key, request, source_url):
    page=notion._req('GET','pages/'+page_id)
    earlier=value(page,'Next Step Notes')
    marker='[request:'+key+':Pending]'
    if marker in earlier: return
    instruction=(marker+'\nRequested by Colby. '+request+'\nSource: '+source_url+
        '\nOwner: scheduled pipeline. Report Completed with a receipt, or Blocked/Failed with a reason. '
        'This is Colby’s explicit draft request. Channel-routed leads otherwise receive handoffs only; no automatic affiliate pointer emails. Never send email. For email drafts, require partners@runpod.io From and partners@ signature; '
        'if unavailable, record Blocked. Partner signup invitations use '
        'https://www.suger.io/partner-signup/8a0b9ce8-c25c-4ffb-b5f2-2157e4b1373d.\n')
    audit(notion._req,key+':request','Card decision',instruction,intake=page_id,actor='Colby',permalink=source_url)
    notion._req('PATCH','pages/'+page_id,{'properties':{'Next Step Notes':rich(earlier+'\n'+instruction)}})


def record_post(req, key, body, channel, ts, intake=None, kind='other'):
    """Record an already acknowledged review/recap; never send from this path."""
    name='tpa:receipt:'+key
    hits=list(query(req,OUTBOX,{'property':'Title','title':{'equals':name}}))
    if hits:
        if len(hits)!=1 or value(hits[0],'Message TS')!=ts: raise ValueError('Conflicting message receipt')
        return hits[0]['id']
    url='https://slack.com/archives/'+channel+'/p'+ts.replace('.','')
    props={'Title':title(name),'Kind':{'select':{'name':kind}},'Status':{'select':{'name':'Posted'}},
           'Channel ID':rich(channel),'Body':rich(body),'Message TS':rich(ts),'Permalink':{'url':url},
           'Posted At':{'date':{'start':dt.datetime.fromtimestamp(float(ts),dt.timezone.utc).isoformat()}}}
    if intake: props['Related Intake']={'relation':[{'id':intake}]}
    page=req('POST','pages',{'parent':{'type':'data_source_id','data_source_id':OUTBOX},'properties':props})
    audit(req,key+':posted','Note','Slack message posted',intake=intake,outbox=page['id'],channel=channel,permalink=url)
    return page['id']


def guide_summary(req, start):
    """Report durable drift from both systems in the reporting window."""
    stamp=dt.datetime.fromtimestamp(start,dt.timezone.utc).isoformat()
    rows=query(req,ACTIVITY,{'and':[
        {'property':'When','date':{'on_or_after':stamp}},
        {'property':'Action','select':{'equals':'Reconciliation'}}]})
    count=sum(value(p,'Detail').startswith('DRIFT:') for p in rows)
    reviewed=_GUIDE_STATE['reviewed']
    overdue=not reviewed or (dt.date.today()-dt.date.fromisoformat(reviewed)).days>14
    return ('*Guide:* '+GUIDE_VERSION+' · '+str(count)+' drift events in this window · '+
            ('review overdue / review date unavailable' if overdue else 'review current')+
            (' · using last reviewed snapshot; '+html.escape(_GUIDE_STATE['reason']) if _GUIDE_STATE['degraded'] else ' · runtime read current'))


def validate_owner_membership(remote, r):
    """Resolve people and membership immediately before delivery; fail closed."""
    route=value(remote.get(r['related'][0]),'Forward To')
    if route=='Influencer/Creator': route='Affiliate Program'
    owners=ROUTES.get(route,('',()))[1]
    if not owners: return
    members=set();cursor=''
    for _ in range(30):
        args={'channel':r['channel'],'limit':200}
        if cursor: args['cursor']=cursor
        result=remote.slack('conversations.members',args)
        members.update(result.get('members',[]))
        cursor=result.get('response_metadata',{}).get('next_cursor')
        if not cursor: break
    else: raise ValueError('Owner membership pagination incomplete')
    for owner in owners:
        user=remote.slack('users.info',{'user':owner}).get('user',{})
        if user.get('id')!=owner or user.get('deleted') or user.get('is_bot') or owner not in members:
            raise ValueError('Guide owner is inactive or not in the destination channel: '+owner)


def drift(req, r, reason):
    audit(req,r['id']+':drift:'+reason,'Reconciliation',
          'DRIFT: section 5 / 14 - '+reason+' - reconcile guide, owner access or queued row before delivery',
          intake=r['related'][0] if len(r['related'])==1 else None,outbox=r['id'],channel=r['channel'])


def policy_fingerprint(root, blocks):
    """Route-owner/version-only changes can refresh without accepting new prose rules.

Exclude routing destinations, review dates and change logs; include all executable
policy sections and status-table contents. A prose-policy change requires review.
"""
    selected=[]; section=0; routing_table=False
    for b in root:
        kind=b['type']; data=b.get(kind,{})
        text=''.join(x.get('plain_text') or x.get('text',{}).get('content','') for x in data.get('rich_text',[]))
        if kind=='heading_2':
            m=re.match(r'(\d+)\.',text);section=int(m[1]) if m else 0
            routing_table=False
        if not 1<=section<=9: continue
        if kind=='table' and section==5 and not routing_table:
            routing_table=True;continue
        if kind=='table':
            for row in blocks(b['id']):
                selected.append(row.get('table_row',{}).get('cells',[]))
        else: selected.append(text)
    # Plain strings only; ignore Notion-generated annotation/link metadata.
    def clean(item):
        if isinstance(item,str): return item
        if isinstance(item,list): return [clean(x) for x in item]
        return item.get('plain_text') or item.get('text',{}).get('content','')
    import json
    return hashlib.sha256(json.dumps(clean(selected),sort_keys=True).encode()).hexdigest()
