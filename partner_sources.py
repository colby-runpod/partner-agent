"""Explicit Colby-approved Slack intake and durable specialist handoffs."""
import intake_alignment as alignment
import hubspot_links
import routing_policy
import hashlib
import html
import json
import re
import time
from intake import flatten

COLBY = 'U0BEHPEJA6L'
FORWARD_CHANNEL = 'C0C7HG8KYFL'
REACTION = 'eyes'
VC_ROUTE = 'VC/Incubator'
VC_CHANNEL = 'C0CCQ9311D2'
CAMERON = 'U0B9ZRF3JUX'
RESEARCH_CHANNEL = 'C0CCBS9NU2V'
AFFILIATE_CHANNEL = 'C0CD3M1QG4U'


def accept_forward(body, cfg):
    if not cfg.get('forward_enabled') or not cfg.get('review_enabled'):
        return None
    if body.get('team_id') != cfg['team_id'] or body.get('api_app_id') != cfg['app_id'] or not body.get('event_id'):
        return None
    e = body.get('event', {})
    # Mention and reaction must be Colby's. Re-fetch the original message before processing.
    if e.get('type') == 'app_mention' and e.get('user') == COLBY and not e.get('bot_id'):
        channel, ts = e.get('channel'), e.get('ts')
    elif e.get('type') == 'reaction_added' and e.get('user') == COLBY and e.get('reaction') == REACTION:
        item = e.get('item', {})
        if item.get('type') != 'message': return None
        channel, ts = item.get('channel'), item.get('ts')
    elif e.get('type') == 'message' and e.get('subtype') == 'message_changed':
        msg = e.get('message', {})
        if msg.get('user') != COLBY or (msg.get('edited') or {}).get('user') != COLBY: return None
        channel, ts = e.get('channel'), msg.get('ts')
    else:
        return None
    if channel != FORWARD_CHANNEL or not re.fullmatch(r'\d+\.\d+', ts or ''): return None
    return dict(kind='forward_check', key='forward-check:'+body['event_id'], event_id=body['event_id'],
                channel=channel, ts=ts, thread_ts=ts)


def process_forward(store, job, clients, cfg):
    event = json.loads(job['payload'])
    msg = clients.forward_message(event)
    # Attachment contents cannot authorize intake; the actual message must mention our bot.
    approved = (msg.get('user') == COLBY and '<@'+cfg['bot_user']+'>' in msg.get('text', '')
                and any(r.get('name') == REACTION and COLBY in r.get('users', []) for r in msg.get('reactions', [])))
    if approved:
        key = ':'.join((cfg['team_id'], event['channel'], event['ts']))
        store.enqueue(dict(event, kind='review_forward', key=key, event_id='forward:'+key,
                           user=COLBY, thread_ts=msg.get('thread_ts') or event['ts'], text=flatten(msg)[:20000], hubspot_url=hubspot_links.extract(msg), source_type='slack_forward'))
    store.update(job['key'], state='done')


def safe(value, limit=1800):
    # Only configured owners may be mentioned. Model/source text is never Slack markup.
    return html.escape(str(value or 'Not stated')[:limit], quote=False)


def score_badge(review):
    score = str(review.get('score', ''))
    badges = dict(zip('12345', ('1️⃣', '2️⃣', '3️⃣', '4️⃣', '5️⃣')))
    return badges[score]+' '+score+'/5' if score in badges else ('DQ' if score == 'DQ' else 'Not scored')


def assessment(review):
    if (review.get('signals') or {}).get('blocker'):
        return '🔴 Hold - resolve blocker'
    return {'pursue': '🟢 Pursue', 'triage': '🟡 Needs triage',
            'redirect': '🔵 Point to self-serve', 'do_not_pursue': '🔴 Do not pursue'}.get(
                review.get('decision'), '⚪ Not assessed')


def suggested_review(review):
    # One actionable sentence; never turn a risk or missing fact into an approval.
    if (review.get('signals') or {}).get('blocker'):
        return 'Resolve the flagged blocker before proceeding.'
    route = review.get('route')
    if route == 'Supply Team':
        return 'Confirm available GPUs, site readiness and capacity before discussing fit.'
    if route == 'Events/Sponsorship':
        return 'Confirm the sponsorship ask, audience and decision deadline.'
    if route == 'Marketing':
        return 'Review the proposed campaign, audience, deliverables and requested contribution.'
    if route == 'Affiliate Program':
        return 'Review the affiliate request and choose the appropriate onboarding or program guidance.'
    if route == 'Research/University':
        return 'Review the institution, research or teaching use case, requested compute and timing.'
    if route == 'Startup Program':
        return 'Check startup program eligibility and direct the applicant to the right application path.'
    if route == VC_ROUTE:
        return 'Confirm portfolio fit, the requested support and a concrete next step with the fund or program.'
    return review.get('next_step') or 'Confirm the missing details before deciding.'


def handoff_key(review, event, route):
    if alignment.enabled():
        identity = alignment.source_ref(event) if event.get('channel') and event.get('ts') else event['key']
        destination = alignment.ROUTES.get(route, (route,))[0]
        return 'handoff:'+hashlib.sha256((destination+'|'+identity).encode()).hexdigest()
    identity = str(review.get('email') or review.get('company') or '').strip().casefold()
    if identity in ('', 'not stated', 'unknown', 'n/a'):
        identity = event['key']
    return 'handoff:'+hashlib.sha256((route+'|'+identity).encode()).hexdigest()


def handoff(review, event, cfg, requested_route=None, requested_by=None):
    if not cfg.get('routing_enabled'): return None
    if requested_by and requested_by != COLBY: return None
    route = requested_route or review.get('route')
    if route == 'Influencer/Creator': route = 'Affiliate Program'
    startup_request = route == 'Startup Program'
    signals = review.get('signals') or {}
    explicit = requested_by == COLBY and bool(requested_route)
    if signals.get('spam') or event.get('synthetic_test'): return None
    if alignment.enabled():
        screen=review.get('screening') or {}
        evidence=str(screen.get('risk_evidence') or '')
        if review.get('parked_by') or signals.get('legal_block_citation'): return None
        if screen.get('risk')=='policy_review' and re.search(r'https://',evidence) and re.search(r'sanction|export.control',evidence,re.I): return None
    # Classification alone is not an actionable submission.
    absent = {'', 'not stated', 'not provided', 'unknown', 'n/a', 'none', 'not captured'}
    details = [review.get(k) for k in ('company', 'contact', 'email', 'requested_action',
                                      'offered_value', 'summary', 'dean_motion', 'event_motion')]
    if not any(str(v or '').strip().casefold() not in absent for v in details):
        return None
    if route == 'Supply Team':
        channel, greeting = 'C0CAL8C08RK', 'Hey <@U0ACSTWBYHG>-passing along a supply-side infra lead.'
    elif route in ('Events/Sponsorship', 'Marketing'):
        channel, greeting = 'C0CAKA6PW8K', 'Hey <@U0AB1TSBK5X> and <@U0APRAUGJ6P>-passing along an event/sponsorship request.'
    elif route in (VC_ROUTE, 'Startup Program'):
        channel = VC_CHANNEL
        owner = CAMERON if startup_request else 'U0CE06U7K52'
        greeting = 'Hey <@'+owner+'>-passing along a '+('startup program' if startup_request else 'VC/incubator')+' request.'
    elif route == 'Affiliate Program':
        channel, greeting = AFFILIATE_CHANNEL, 'Affiliate inquiry logged for the incoming owner’s review.'
    elif route == 'Research/University':
        channel, greeting = RESEARCH_CHANNEL, 'Hey <@U0CE06U7K52>-passing along a research/education request.'
    else: return None
    if alignment.enabled():
        channel, owners=alignment.ROUTES[route]
        prefix=' and '.join('<@'+u+'>' for u in owners)
        greeting=('Hey '+prefix+'-' if prefix else '')+'Passing along a '+route+' request.'
    # Shared receipt identity is the submission and destination when alignment is enabled.
    key = handoff_key(review, event, route)
    source = event.get('source_url')
    if not source:
        source = 'https://slack.com/archives/%s/p%s' % (event['channel'], event['ts'].replace('.', ''))
    if not re.fullmatch(r'https://(?:slack\.com/archives/[A-Z0-9]+/p[0-9]+|mail\.google\.com/mail/u/[^\s<>]+)', source):
        raise ValueError('Invalid handoff source')
    motion = (review.get('dean_motion') if route == 'Supply Team' else
              review.get('event_motion') if route == 'Events/Sponsorship' else review.get('summary'))
    text = greeting+'\n\n'+ '\n'.join('• *%s:* %s' % (label,safe(value)) for label,value in (
        ('Company',review.get('company')),('Contact',review.get('contact')),('Email',review.get('email')),
        ('Region',review.get('region')), ('Lead score',score_badge(review)),
        ('Assessment',assessment(review))))
    for label, value in routing_policy.detail_lines(review.get('routing_context')):
        text += '\n• *'+label+':* '+safe(value)
    text += '\n\n*What they want*'
    text += '\n• *Partnership requested:* '+safe(review.get('requested_partnership') or review.get('partnership_type') or 'Not specified in the saved review', 400)
    text += '\n• *Ask from Runpod:* '+safe(review.get('requested_action') or 'Not separately captured; see the original request', 600)
    text += '\n• *What they bring:* '+safe(review.get('offered_value') or 'Not separately captured; see the original request', 600)
    text += '\n\n*Assessment notes*\n'+safe(motion or review.get('summary'), 1200)
    text += '\n\n*Suggested review:* '+safe(suggested_review(dict(review, route='Startup Program' if startup_request else route)))
    if not explicit:
        text += '\n_Automatically routed for specialist review. The score is unchanged; this is not approval._'
    if explicit:
        text += '\n\n*Requested handoff:* Colby requested specialist validation despite the original assessment. This is not approval or qualification.'
    warnings = [str(signals['blocker'])] if signals.get('blocker') else []
    if signals.get('already_in_play'):
        warnings.append('An existing relationship or owner may already be involved; coordinate before outreach.')
    if warnings:
        text += '\n\n*Warnings for the receiving team:* '+safe('; '.join(warnings))
    text += '\n\n*Links*\n• <%s|Original request>' % source
    # Do not fabricate a CRM record ID or use an unverified model-generated CRM URL.
    crm = hubspot_links.source_url(event)
    text += ('\n• <%s|HubSpot contact>' % crm if crm else '\n• HubSpot: Not available in the original submission')
    text += '\n\nReact :white_check_mark: or :eyes: to pick up; :x: to pass.'
    text += '\n\ncc <@%s>' % COLBY
    return dict(completion_source=event,completion_route=route,requested_by=requested_by,kind='specialist_handoff',key=key,event_id=key,channel=channel,text=text,ts=str(time.time()))


def process_handoff(store, job, clients):
    if alignment.enabled():
        if job['state'] != 'pending': return
        import review
        cfg=review.config()
        event=json.loads(job['payload'])
        try:
            page=alignment.queue_handoff(review.NotionWriter(cfg),event)
            status=alignment.value(page,'Status')
            ts=alignment.value(page,'Message TS');url=alignment.value(page,'Permalink')
            store.update(job['key'],state='done',ref=ts or page['id'],result_url=url if ts else '',
                         error='' if status in ('Queued','Posted','Confirmed') else 'handoff_'+status.lower()+'_needs_reconciliation')
        except Exception:
            store.update(job['key'],state='done',error='handoff_queue_reconciliation_required')
        return
    # Commit the send intent before the network call. Never retry an uncertain send.
    if job['state'] != 'pending':
        store.update(job['key'], state='done', error='handoff_send_unconfirmed')
        return
    event=json.loads(job['payload'])
    store.update(job['key'],state='writing')
    try:
        ts=clients.send_handoff(event)
        if not ts: raise ValueError('Missing Slack receipt')
    except Exception:
        store.update(job['key'],state='done',error='handoff_send_unconfirmed')
    else:
        store.update(job['key'],state='done',ref=ts,result_url='https://slack.com/archives/%s/p%s'%(event['channel'],ts.replace('.','')))

        import lead_completion
        lead_completion.enqueue_handoff(store,event,event.get('completion_source') or {},event.get('completion_route'))
