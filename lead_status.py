"""Lead status on submission/review cards; never on Q&A or drafting replies."""
import json
import os
import urllib.parse
import urllib.request
import question_progress

CHANNEL = 'C0BQKEQLSKH'
BOT = 'U0C7372ET45'
ICONS = ('clock3', 'white_check_mark', 'double_vertical_bar')


def message(channel, ts):
    request = urllib.request.Request('https://slack.com/api/reactions.get?'+urllib.parse.urlencode(
        dict(channel=channel, timestamp=ts)), headers={'Authorization':'Bearer '+os.environ['SLACK_BOT_TOKEN']})
    with urllib.request.urlopen(request, timeout=3) as response:
        result = json.load(response)
    if result.get('error') == 'message_not_found':
        return None
    if not result.get('ok'):
        raise ValueError('Reaction lookup failed')
    return result['message']


def sync_row(row, receipts):
    import review
    data = json.loads(row.get('data') or '{}')
    source = data.get('source_event') or {}
    if row['channel'] != CHANNEL or source.get('synthetic_test'):
        return True
    status = review.summary_action(row, receipts)[0]
    import intake_alignment as alignment
    if alignment.enabled():
        import outbox
        remote=outbox.Remote()
        matches=list(alignment.query(remote.notion,alignment.INTAKE,{'property':'Intake Ref','rich_text':{'equals':'tpa:'+row['channel']+':'+row['ts']}}))
        if len(matches)!=1: return False
        page=matches[0]
        notes=alignment.value(page,'Next Step Notes')+' '+alignment.value(page,'Draft / Resource')
        entries=list(alignment.query(remote.notion,alignment.OUTBOX,{'property':'Related Intake','relation':{'contains':page['id']}}))
        status='needs_action'
        if alignment.value(page,'Status')=='Closed': status='actioned'
        elif 'Parked by Colby' in notes: status='parked'
        elif alignment.value(page,'Status') not in ('Flagged','In Review') and any(alignment.value(p,'Status') in ('Posted','Confirmed') and alignment.value(p,'Message TS') and alignment.value(p,'Permalink') and alignment.value(p,'Channel ID') in alignment.SPECIALIST_CHANNELS for p in entries): status='actioned'
    icon = {'actioned': 'white_check_mark', 'parked': 'double_vertical_bar', 'needs_action': 'clock3'}[status]
    ok = True
    for ts in dict.fromkeys(filter(None, (source.get('thread_ts') or row['ts'], row.get('card_ts')))):
        try:
            msg = message(row['channel'], ts)
            if msg is None:
                # Older shadow-mode cards may live elsewhere; source is authoritative.
                if ts == (source.get('thread_ts') or row['ts']):
                    ok = False
                continue
            owned = {r['name'] for r in msg.get('reactions', []) if BOT in r.get('users', [])}
            event = dict(progress=True, channel=row['channel'], ts=ts)
            # Add first. Never remove a human reaction or infer completion from one.
            if icon not in owned and not question_progress.change(event, 'add', icon):
                ok = False
                continue
            for other in owned.intersection(ICONS)-{icon}:
                ok = question_progress.change(event, 'remove', other) and ok
        except Exception:
            ok = False
    return ok


def reconcile(store, ledger, limit=10):
    # One bounded batch each minute. Walk all saved source leads and retry failures next cycle.
    latest = {}
    for row in sorted(ledger.since(0), key=lambda r: (r['created'], r['key'])):
        if row['channel'] == CHANNEL:
            latest[row['ts']] = row
    rows = sorted(latest.values(), key=lambda r: r['ts'])
    if not rows:
        return
    cursor = ledger.get('lead_status_cursor', '')
    candidates = [r for r in rows if r['ts'] > cursor] or rows
    receipts = store.completion_receipts()
    failed = 0
    for row in candidates[:limit]:
        failed += not sync_row(row, receipts)
        cursor = row['ts']
    ledger.put('lead_status_cursor', cursor)
    if failed:
        print('Lead status reactions pending retry: %d' % failed, flush=True)
