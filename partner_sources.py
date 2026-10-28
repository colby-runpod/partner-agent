"""Explicit Colby-approved Slack intake and durable specialist handoffs."""
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
                           user=COLBY, thread_ts=msg.get('thread_ts') or event['ts'], text=flatten(msg)[:20000], source_type='slack_forward'))
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
    if route == 'Research/University':
        return 'Review the institution, research or teaching use case, requested compute and timing.'
    if route == VC_ROUTE:
        return 'Confirm portfolio fit, the requested support and a concrete next step with the fund or program.'
    return review.get('next_step') or 'Confirm the missing details before deciding.'


def handoff_key(review, event, route):
    identity = str(review.get('email') or review.get('company') or '').strip().casefold()
    if identity in ('', 'not stated', 'unknown', 'n/a'):
        identity = event['key']
    return 'handoff:'+hashlib.sha256((route+'|'+identity).encode()).hexdigest()


def handoff(review, event, cfg, requested_route=None):
    if not cfg.get('routing_enabled'): return None
    tier = (review.get('screening') or {}).get('tier')
    route = requested_route or review.get('route')
    if not requested_route and route == 'Partnerships' and review.get('partnership_type') == 'VC/PE/Incubator':
        route = VC_ROUTE
    signals = review.get('signals') or {}
    if signals.get('blocker') or signals.get('already_in_play') or signals.get('spam'): return None
    if route == 'Supply Team' and (requested_route or tier in ('Act now', 'Qualify')):
        channel, greeting = 'C0CAL8C08RK', 'Hey <@U0ACSTWBYHG>-passing along a supply-side infra lead.'
    elif route == 'Events/Sponsorship' and (requested_route or tier in ('Act now', 'Qualify', 'Route')):
        channel, greeting = 'C0CAKA6PW8K', 'Hey <@U0AB1TSBK5X> and <@U0APRAUGJ6P>-passing along an event/sponsorship request.'
    elif route == VC_ROUTE and (requested_route or tier in ('Act now', 'Qualify', 'Route')):
        channel, greeting = VC_CHANNEL, 'Hey <@'+CAMERON+'>-passing along a VC/incubator request.'
    elif route == 'Research/University':
        channel, greeting = RESEARCH_CHANNEL, 'Research/education lead logged for Zoe’s review when she starts.'
    else: return None
    # One specialist handoff per contact/route, including repeated submissions across sources.
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
    text += '\n\n*Request & qualification*\n'+safe(motion or review.get('summary'))
    text += '\n\n*Suggested review:* '+safe(suggested_review(dict(review, route=route)))
    text += '\n\n*Links*\n• <%s|Original request>' % source
    # Do not fabricate a CRM record ID or use an unverified model-generated CRM URL.
    crm = re.search(r'https://app\.hubspot\.com/contacts/[0-9]+/(?:contact|record/0-1)/[0-9]+', event.get('text', ''))
    text += ('\n• <%s|HubSpot contact>' % crm.group(0) if crm else '\n• HubSpot: Not provided in the source')
    text += '\n\ncc <@%s>' % COLBY
    return dict(kind='specialist_handoff',key=key,event_id=key,channel=channel,text=text,ts=str(time.time()))


def process_handoff(store, job, clients):
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
