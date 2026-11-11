"""Guide 8a observability. Never gate delivery or execute ledger prose as commands."""
import datetime as dt
import json
import time
from zoneinfo import ZoneInfo

SOURCE = '161dfa06-2229-4758-8fb3-a837cfeca8ee'
SYSTEM = 'Team Partner Agent'
MT = ZoneInfo('America/Denver')
_store = None
_snapshot = {}


def bind(store):
    global _store
    _store = store


def run_name(when):
    if isinstance(when, str):
        when = dt.datetime.fromisoformat(when.replace('Z', '+00:00'))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return 'tpa ' + when.astimezone(MT).date().isoformat()


def fallback(req, operation):
    """One durable note per outage episode; audit errors must also fail open."""
    import intake_alignment as a
    _snapshot['fallback'] = 'Ledger not readable/writable by agent; running on guide only.'
    print(_snapshot['fallback'] + ' Operation: ' + operation, flush=True)
    try:
        state = json.loads(_store.get('automation_ledger_outage', '{}')) if _store else {}
        if state.get('active'):
            return
        episode = state.get('episode', 0) + 1
        a.audit(req, 'automation-ledger-outage:' + str(episode), 'Note', _snapshot['fallback'])
        if _store:
            _store.put('automation_ledger_outage', json.dumps(dict(active=True, episode=episode)))
    except Exception:
        print('Ledger fallback Activity Log note unavailable; retry on next refresh.', flush=True)


def recovered():
    if _store:
        state = json.loads(_store.get('automation_ledger_outage', '{}'))
        if state.get('active'):
            state['active'] = False
            _store.put('automation_ledger_outage', json.dumps(state))
    _snapshot.pop('fallback', None)


def read(req):
    """Newest by Entry ID, independently for the guide and each system."""
    import intake_alignment as a
    global _snapshot
    try:
        def latest(kind, system=None):
            filters = [{'property': 'Type', 'select': {'equals': kind}}]
            if system:
                filters.append({'property': 'System', 'select': {'equals': system}})
            result = req('POST', 'data_sources/' + SOURCE + '/query', {
                'page_size': 1, 'filter': {'and': filters},
                'sorts': [{'property': 'Entry ID', 'direction': 'descending'}]})
            return next(iter(result.get('results', [])), {})
        snap = {'guide': latest('Guide change'), 'own': latest('Run', SYSTEM),
                'pipeline': latest('Run', 'Pipeline')}
        snap['changed'] = bool(a.value(snap['guide'], 'Guide Version') and
            a.value(snap['guide'], 'Guide Version') != a.value(snap['own'], 'Guide Version'))
        snap['open_items'] = a.value(snap['pipeline'], 'Open Items')
        _snapshot = snap
        # Reads alone do not prove the ledger writable. Clear an outage only after a write.
        return snap
    except Exception:
        fallback(req, 'read')
        return dict(_snapshot)


def gap_report(req, now):
    """Report each missing weekday slot since the latest run, bounded to seven days.

    Agent gaps begin only after a persisted activation baseline, never retroactively.
    """
    import intake_alignment as a
    local = now.astimezone(MT)
    start = local - dt.timedelta(days=7)
    rows = list(a.query(req, SOURCE, {'and': [
        {'property': 'Type', 'select': {'equals': 'Run'}},
        {'property': 'Started', 'date': {'on_or_after': start.isoformat()}}]}))
    slots = set()
    for row in rows:
        stamp = a.value(row, 'Started')
        if not stamp:
            continue
        t = dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).astimezone(MT)
        system = a.value(row, 'System')
        hour = (14 if t.hour >= 14 else 8) if system == 'Pipeline' else 17
        slots.add((system, t.date(), hour))
    enabled = bool(a.value(_snapshot.get('guide', {}), 'Agent Updated'))
    baseline = _store.get('automation_ledger_enabled_at') if _store else None
    if enabled and not baseline and _store:
        baseline = local.isoformat()
        _store.put('automation_ledger_enabled_at', baseline)
    missing = []
    for offset in range(7):
        day = local.date() - dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for system, hours in (('Pipeline', (8, 14)), (SYSTEM, (17,))):
            latest = a.value(_snapshot.get('pipeline' if system == 'Pipeline' else 'own', {}), 'Started')
            # With no historical run baseline, report only today's due slots.
            floor = dt.datetime.fromisoformat(latest.replace('Z', '+00:00')).astimezone(MT) if latest else local.replace(hour=0, minute=0, second=0)
            if system == SYSTEM:
                if not enabled or not baseline:
                    continue
                floor = max(floor, dt.datetime.fromisoformat(baseline))
            for hour in hours:
                slot = dt.datetime.combine(day, dt.time(hour), MT)
                # A run still in progress is not missing; allow one hour to record it.
                if floor < slot < local - dt.timedelta(hours=1) and (system, day, hour) not in slots:
                    missing.append('%s %s %02d:00 MT' % (system, day, hour))
    return 'Missing scheduled Runs: ' + '; '.join(missing) if missing else ''


def finish(req, store, when, started, status, result, receipts=()):
    """One append-only Run per recap. Never retry an ambiguous create blindly.

    Persist the complete payload before creating; query by Run to adopt a receipt.
    A failed write cannot undo a confirmed Slack send or advance its send intent.
    """
    import intake_alignment as a
    bind(store)
    run = run_name(when)
    key = 'automation-ledger-run:' + run
    try:
        pending = json.loads(store.get('automation_ledger_pending', '{}'))
        pending.setdefault(run, dict(when=when.isoformat(), started=started, status=status,
                                     result=result, receipts=list(receipts)))
        store.put('automation_ledger_pending', json.dumps(pending))
        def completed(id):
            pending.pop(run, None)
            store.put('automation_ledger_pending', json.dumps(pending))
            recovered()
            return id
        saved = json.loads(store.get(key, '{}'))
        if saved.get('id'):
            return completed(saved['id'])
        hits = list(a.query(req, SOURCE, {'and': [
            {'property': 'Type', 'select': {'equals': 'Run'}},
            {'property': 'System', 'select': {'equals': SYSTEM}},
            {'property': 'Run', 'rich_text': {'equals': run}}]}))
        if len(hits) > 1:
            raise ValueError('Multiple ledger runs require reconciliation')
        if hits:
            store.put(key, json.dumps({'id': hits[0]['id']}))
            return completed(hits[0]['id'])
        if saved.get('attempted'):
            raise ValueError('Uncertain ledger create requires reconciliation')
        events = list(a.query(req, a.ACTIVITY, {'and': [
            {'property': 'Run', 'rich_text': {'equals': run}},
            {'property': 'Actor', 'select': {'equals': SYSTEM}}]}))
        count = lambda action: sum(a.value(e, 'Action') == action for e in events)
        drifts = sum('DRIFT' in a.value(e, 'Detail') for e in events)
        sources = {}
        for id in {id for e in events for id in a.value(e, 'Intake') or []}:
            intake = req('GET', 'pages/' + id)
            source = a.value(intake, 'Source') or 'Unspecified'
            sources[source] = sources.get(source, 0) + 1
        deliveries = list(a.query(req, a.OUTBOX, {'or': [
            {'property': 'Status', 'select': {'equals': 'Queued'}},
            {'property': 'Status', 'select': {'equals': 'Failed'}}]}))
        issues = [o for o in deliveries if a.value(o, 'Status') == 'Failed' or a.value(o, 'Error')]
        additions = []
        guide = _snapshot.get('guide', {})
        if guide and not a.value(guide, 'Agent Updated'):
            additions.append('not yet updated to guide v' + a.value(guide, 'Guide Version'))
        gap = gap_report(req, when)
        if gap:
            additions.append(gap)
        if _snapshot.get('fallback'):
            additions.append(_snapshot['fallback'])
        open_items = _snapshot.get('open_items', '')
        props = {
            'Entry': a.title('Team Partner Agent recap ' + run[4:]),
            'Type': {'select': {'name': 'Run'}}, 'System': {'select': {'name': SYSTEM}},
            'Run': a.rich(run), 'Guide Version': a.rich(a.GUIDE_VERSION),
            'Started': {'date': {'start': started}},
            'Finished': {'date': {'start': dt.datetime.now(dt.timezone.utc).isoformat()}},
            'Run Status': {'select': {'name': status}},
            'Guide Changed Since Last Run': {'checkbox': bool(_snapshot.get('changed'))},
            'Sources Checked': a.rich('Distinct intake rows touched by source: ' + json.dumps(sources, sort_keys=True) +
                '; shared Partner Biz Dev and Outbox; Activity Log for ' + run +
                '; logged captures: %d; handoff posts: %d; recap Slack parts: %d' % (count('Logged'), count('Handoff posted'), len(receipts))),
            'New Rows': {'number': count('Logged')},
            'Enriched or Adopted': {'number': count('Adopted')},
            'Handoffs Queued': {'number': count('Handoff queued')},
            'Drift Entries': {'number': drifts},
            'Delivery Health': a.rich('Recap: ' + status + '; confirmed parts: ' + str(len(receipts)) +
                '; queued/failed outbox rows: %d; rows with delivery issues: %d' % (len(deliveries), len(issues))),
            'Open Items': a.rich(open_items),
            'Result': a.rich(result + ('\n' + '\n'.join(additions) if additions else '')),
            'Activity Log Entries': {'relation': [{'id': e['id']} for e in events[:100]]},
        }
        if len(events) > 100:
            props['Result'] = a.rich(a.value({'properties': {'r': props['Result']}}, 'r') +
                '\nFirst 100 Activity Log relations attached; remaining entries share Run ' + run)
        store.put(key, json.dumps({'attempted': True, 'properties': props}))
        page = req('POST', 'pages', {'parent': {'type': 'data_source_id', 'data_source_id': SOURCE}, 'properties': props})
        store.put(key, json.dumps({'id': page['id']}))
        return completed(page['id'])
    except Exception:
        fallback(req, 'write Run ' + run)
        return None


def retry(req, store):
    """Retry bookkeeping independently of Slack delivery, at most every five minutes."""
    try:
        bind(store)
        if time.time() - float(store.get('automation_ledger_retry_at', '0')) < 300:
            return
        store.put('automation_ledger_retry_at', str(time.time()))
        for context in json.loads(store.get('automation_ledger_pending', '{}')).values():
            finish(req, store, dt.datetime.fromisoformat(context['when']), context['started'],
                   context['status'], context['result'], context['receipts'])
    except Exception:
        fallback(req, 'retry bookkeeping')
