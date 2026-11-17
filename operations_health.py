"""Durable incident notifications. Never retry an ambiguous Slack send."""
import datetime as dt
import hashlib
import json


def notify_once(ledger, send, incident, message):
    if ledger is None:
        return
    key='incident:'+hashlib.sha256(incident.encode()).hexdigest()
    if ledger.get(key):
        return
    # Fence before network I/O: a timeout must not produce repeated alerts.
    ledger.put(key,json.dumps({'state':'attempted'}))
    send(message)
    ledger.put(key,json.dumps({'state':'sent'}))


def outbox_health(pages, now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    eligible=[]
    for p in pages:
        from outbox import prop
        title=prop(p,'Title')
        if not title.startswith(('pipeline:','tpa:','Invitation:')) or title.upper().startswith('TEST'):
            continue
        try: age=(now-dt.datetime.fromisoformat(p['created_time'].replace('Z','+00:00'))).total_seconds()/3600
        except (ValueError,KeyError):continue
        eligible.append((age,p))
    overdue=[x for x in eligible if x[0]>=24]
    return {'queued':len(eligible),'overdue':len(overdue),'oldest_hours':round(max([x[0] for x in eligible] or [0]))}
