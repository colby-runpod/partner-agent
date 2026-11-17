"""Durable incident notifications. Never retry an ambiguous Slack send."""
import datetime as dt
import hashlib
import json
import time


_PULSES={}


def pulse(ledger, role, *, job=None, error=None, backlog=None, now=None):
    """Durable progress, independent of the HTTP listener. No source text/secrets."""
    if ledger is None: return
    now=time.time() if now is None else now
    key='operations:'+role
    identity=(job or {}).get('key')
    last=_PULSES.get((id(ledger),role),(0,None))
    if now-last[0]<30 and identity==last[1] and error is None and backlog is None:return
    previous=json.loads(ledger.get(key,'{}'))
    started=previous.get('job_started',now) if identity and identity==previous.get('job') else now
    failures=[x for x in previous.get('failures',[]) if now-x<900]
    if error:failures.append(now)
    state=dict(heartbeat=now,job=identity,job_started=started,error=previous.get('error','') if error is None else error,failures=failures)
    if backlog is not None: state['backlog']=backlog
    elif 'backlog' in previous: state['backlog']=previous['backlog']
    ledger.put(key,json.dumps(state))
    _PULSES[(id(ledger),role)]=(now,identity)


def readiness(ledger, roles=('queue','outbox'), now=None):
    now=time.time() if now is None else now
    reasons=[]
    for role in roles:
        state=json.loads(ledger.get('operations:'+role,'{}'))
        if not state or now-state.get('heartbeat',0)>600: reasons.append(role+'_heartbeat_stale')
        elif state.get('error'): reasons.append(role+'_failure')
        if len([t for t in state.get('failures',[]) if now-t<900])>=3:reasons.append(role+'_repeated_failures')
        if state.get('job') and now-state.get('job_started',now)>900: reasons.append(role+'_job_stalled')
        if state.get('backlog',{}).get('ready_overdue',0): reasons.append(role+'_ready_backlog')
    return {'ready':not reasons,'reasons':reasons}


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


def classify_backlog(pages, now=None):
    """Separate actionable delay from holds; never change a row's business status."""
    from outbox import row, prop
    now=now or dt.datetime.now(dt.timezone.utc)
    result=dict(ready=0,ready_overdue=0,blocked=0,scheduled=0,excluded=0)
    for p in pages:
        r=row(p)
        if r['status']!='Queued': continue
        n=p.get('properties',{}).get('Post ID',{}).get('unique_id',{}).get('number')
        if n in (12,13) or r['title'].upper().startswith('TEST') or not r['title'].startswith(('pipeline:','tpa:','Invitation:','weekly-update-summary:','partner-signal-enrichment:')):
            result['excluded']+=1;continue
        if r['before']:
            try:
                if dt.datetime.fromisoformat(r['before'].replace('Z','+00:00'))>now:
                    result['scheduled']+=1;continue
            except (ValueError,TypeError): result['blocked']+=1;continue
        if r['thread'].startswith('parent:') or prop(p,'Error'):
            result['blocked']+=1;continue
        result['ready']+=1
        try:
            age=now-dt.datetime.fromisoformat(p['created_time'].replace('Z','+00:00'))
            if age.total_seconds()>900:result['ready_overdue']+=1
        except (KeyError,ValueError,TypeError): result['blocked']+=1;result['ready']-=1
    return result
