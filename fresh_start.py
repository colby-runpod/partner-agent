"""Fail-closed forward-only startup and Slack admission boundary."""
import datetime as dt
from decimal import Decimal, InvalidOperation
import os


def cutoff():
    value=os.environ.get('POSTGRES_FRESH_START_AT')
    if value is None: return None
    try:
        parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if 'T' not in value or parsed.tzinfo is None: raise ValueError()
    except (ValueError,TypeError):
        raise ValueError('Fresh start requires an ISO timestamp with timezone') from None
    return parsed.astimezone(dt.timezone.utc)


def after(value,boundary):
    try:
        stamp=Decimal(str(value))
        return stamp.is_finite() and stamp > Decimal(str(boundary.timestamp()))
    except (InvalidOperation,ValueError,TypeError): return False


def allows_event(body):
    boundary=cutoff()
    if boundary is None: return True
    event=body.get('event') or {}
    # Event delivery time alone cannot make an old message eligible.
    primary=event.get('ts') or event.get('event_ts')
    if not after(primary,boundary): return False
    values=[event[k] for k in ('ts','event_ts','thread_ts') if k in event]
    item=event.get('item') or {}
    if 'ts' in item: values.append(item['ts'])
    nested=event.get('message') or {}
    values.extend(nested[k] for k in ('ts','thread_ts') if k in nested)
    return all(after(value,boundary) for value in values)


def allows_action(payload):
    boundary=cutoff()
    if boundary is None: return True
    message=payload.get('message') or {}
    actions=payload.get('actions') or []
    return (after(message.get('ts'),boundary) and bool(actions)
            and all(after(a.get('action_ts'),boundary) for a in actions)
            and ('thread_ts' not in message or after(message['thread_ts'],boundary)))


def pin_boundary(store):
    boundary=cutoff()
    with store.connect() as db:
        # Serialize startup across the receiver and both workers.
        db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',(store.namespace+':fresh-start-config',))
        marker=db.execute("SELECT v FROM partner_agent.review_meta WHERE namespace=%s AND k='fresh_start_boundary'",(store.namespace,)).fetchone()
        if marker:
            if boundary is None or marker['v']!=boundary.isoformat():
                raise ValueError('Persisted fresh-start boundary cannot be removed or changed')
            return
        if boundary is None: return
        for table in ('jobs','reviews','review_meta','feedback','outbox_delivery','outbox_events','worker_ownership'):
            if db.execute('SELECT 1 FROM partner_agent.'+table+' WHERE namespace=%s LIMIT 1',(store.namespace,)).fetchone():
                raise ValueError('Fresh-start namespace already has state; reconcile before activation')
        db.execute("INSERT INTO partner_agent.review_meta(namespace,k,v) VALUES (%s,'fresh_start_boundary',%s)",(store.namespace,boundary.isoformat()))
