"""Guarded weekday Working Task briefing and feedback capture.

The briefing consumes a structured snapshot produced by the daily integrity
review.  It never infers that prose-only run notes are safe to publish.  Every
Working Task referenced by the snapshot is fetched again immediately before
delivery.
"""
import datetime as dt
import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request


NOTION_VERSION = '2025-09-03'
WI = re.compile(r'\bWI-\d+\b', re.I)
DECISION = re.compile(r'^decision\s+([^:]+):\s*(.+)$', re.I | re.S)
SOURCE_URL = re.compile(r'https://(?:app\.notion\.com|[^\s<>]+\.slack\.com|slack\.com)/[^\s<>]+', re.I)
COMMANDS = (
    re.compile(r'^focus\s+(WI-\d+)\s+today\s*$', re.I),
    re.compile(r'^deprioritize\s+(WI-\d+)\s*$', re.I),
    re.compile(r'^snooze\s+(WI-\d+)\s+until\s+(\d{4}-\d{2}-\d{2})\s*$', re.I),
    re.compile(r'^correction\s+(WI-\d+):\s*(.+)$', re.I | re.S),
)
ALLOWED_COVERAGE = {'Complete', 'Partial'}


def _safe(value, limit=320):
    value = re.sub(r'\s+', ' ', str(value or '')).strip()
    return html.escape(value[:limit], quote=False)


def _text(prop):
    parts = (prop or {}).get('rich_text') or (prop or {}).get('title') or []
    return ''.join(x.get('plain_text') or (x.get('text') or {}).get('content', '') for x in parts).strip()


def _select(prop):
    return ((prop or {}).get('select') or {}).get('name', '')


def _date(prop):
    return ((prop or {}).get('date') or {}).get('start', '')


def _item_id(prop):
    value = (prop or {}).get('unique_id') or {}
    if value.get('number') is not None:
        return '%s-%s' % (value.get('prefix') or 'WI', value['number'])
    return _text(prop)


def _parse_time(value):
    if not isinstance(value, str) or not value:
        raise ValueError('snapshot timestamp missing')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValueError('snapshot timestamp is invalid') from None
    if parsed.tzinfo is None:
        raise ValueError('snapshot timestamp must include a timezone')
    return parsed.astimezone(dt.timezone.utc)


def config(cfg):
    cfg['working_task_brief_enabled'] = os.environ.get('WORKING_TASK_BRIEF_ENABLED') == 'true'
    cfg['working_task_brief_channel'] = os.environ.get('WORKING_TASK_BRIEF_CHANNEL_ID', 'C0C7HG8KYFL')
    cfg['working_task_brief_hour'] = int(os.environ.get('WORKING_TASK_BRIEF_HOUR', '8'))
    cfg['working_task_brief_minute'] = int(os.environ.get('WORKING_TASK_BRIEF_MINUTE', '15'))
    cfg['working_task_brief_max_age_hours'] = float(os.environ.get('WORKING_TASK_BRIEF_MAX_AGE_HOURS', '3'))
    cfg['working_task_run_log'] = os.environ.get('WORKING_TASK_RUN_LOG_ID', '8d12b4ca-c723-4372-827c-d607070179a2')
    cfg['working_tasks_source'] = os.environ.get('WORKING_TASKS_DATA_SOURCE_ID', 'e77648f6-502d-450f-8b79-4dd2ced52bab')
    if not (0 <= cfg['working_task_brief_hour'] <= 23 and 0 <= cfg['working_task_brief_minute'] <= 59):
        raise ValueError('Working Task briefing time is invalid')
    if cfg['working_task_brief_max_age_hours'] <= 0:
        raise ValueError('WORKING_TASK_BRIEF_MAX_AGE_HOURS must be positive')
    if cfg['working_task_brief_enabled'] and not os.environ.get('NOTION_TOKEN'):
        raise ValueError('Working Task briefing requires NOTION_TOKEN')
    return cfg


def local_time(now_utc):
    """America/Denver without a container tzdata dependency."""
    value = now_utc.astimezone(dt.timezone.utc).replace(tzinfo=None)
    year = value.year
    march = dt.datetime(year, 3, 1)
    dst_start = march + dt.timedelta(days=(6 - march.weekday()) % 7 + 7, hours=9)
    nov = dt.datetime(year, 11, 1)
    dst_end = nov + dt.timedelta(days=(6 - nov.weekday()) % 7, hours=8)
    return value + dt.timedelta(hours=-6 if dst_start <= value < dst_end else -7)


def due(cfg, ledger, now_utc):
    if not cfg.get('working_task_brief_enabled'):
        return False
    local = local_time(now_utc)
    threshold = local.replace(hour=cfg['working_task_brief_hour'], minute=cfg['working_task_brief_minute'],
                              second=0, microsecond=0)
    state = json.loads(ledger.get('working_task_brief_state', '{}'))
    return local.weekday() < 5 and local >= threshold and state.get('date') != local.strftime('%Y-%m-%d')


class Notion:
    def __init__(self, token=None):
        self.token = token or os.environ['NOTION_TOKEN']

    def request(self, method, path, payload=None):
        request = urllib.request.Request('https://api.notion.com/v1/' + path, method=method, headers={
            'Authorization': 'Bearer ' + self.token, 'Notion-Version': NOTION_VERSION,
            'Content-Type': 'application/json'}, data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError('Notion HTTP %d' % exc.code) from None
        except (OSError, ValueError):
            raise ValueError('Notion unavailable') from None

    def integrity_run(self, source, date):
        result = self.request('POST', 'data_sources/%s/query' % source, {'filter': {
            'property': 'Run Key', 'rich_text': {'equals': 'working-task-daily-integrity-review:' + date}},
            'sorts': [{'property': 'Finished At', 'direction': 'descending'}], 'page_size': 2})
        rows = result.get('results') or []
        if len(rows) != 1:
            raise ValueError('today integrity review missing or ambiguous')
        return rows[0]

    def page(self, page_id):
        return self.request('GET', 'pages/' + page_id)


def snapshot_from_run(page):
    props = page.get('properties') or {}
    status = _select(props.get('Status'))
    raw = _text(props.get('Briefing Snapshot'))
    if not raw:
        raise ValueError('structured briefing snapshot missing')
    try:
        snapshot = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError('structured briefing snapshot is invalid JSON') from None
    snapshot['_integrity_status'] = status
    snapshot['_finished_at'] = _date(props.get('Finished At'))
    return snapshot


def task_refs(snapshot):
    refs = []
    for section in ('today', 'progress', 'risks_decisions', 'cadence'):
        for item in snapshot.get(section) or []:
            if item.get('wi_id'):
                refs.append((item.get('wi_id'), item.get('task_url')))
    unique = []
    for ref in refs:
        if ref not in unique:
            unique.append(ref)
    return unique


def validate_snapshot(snapshot, notion, now_utc, cfg):
    reasons = []
    if snapshot.get('version') != 1:
        reasons.append('snapshot version is unsupported')
    try:
        as_of = _parse_time(snapshot.get('as_of'))
    except ValueError as exc:
        return reasons + [str(exc)]
    if as_of > now_utc.astimezone(dt.timezone.utc) + dt.timedelta(minutes=5):
        reasons.append('snapshot timestamp is in the future')
    if now_utc.astimezone(dt.timezone.utc) - as_of > dt.timedelta(hours=cfg['working_task_brief_max_age_hours']):
        reasons.append('snapshot is more than three hours old')
    if snapshot.get('_integrity_status') not in ('Complete', 'Partial'):
        reasons.append('today integrity review has not completed')
    if snapshot.get('safe_for_briefing') is not True:
        reasons.append('snapshot is not marked safe for briefing')
    if snapshot.get('task_updates_complete') is not True:
        reasons.append('relevant task updates have not finished')
    coverage = snapshot.get('coverage') or {}
    for name in ('slack', 'gmail', 'calendar_meetings'):
        if coverage.get(name) not in ALLOWED_COVERAGE:
            reasons.append(name.replace('_', '/') + ' coverage is not recorded')
    for wi_id, url in task_refs(snapshot):
        if not re.fullmatch(r'WI-\d+', str(wi_id or ''), re.I) or not isinstance(url, str) or 'notion.com/' not in url:
            reasons.append('referenced Working Task is missing a valid WI ID or Notion URL')
            continue
        page_id = url.split('?')[0].rstrip('/').rsplit('-', 1)[-1]
        if not re.fullmatch(r'[0-9a-fA-F]{32}', page_id):
            reasons.append('%s has an invalid Notion URL' % wi_id.upper())
            continue
        page = notion.page(page_id)
        props = page.get('properties') or {}
        actual = _item_id(props.get('Item ID')).upper()
        if actual != wi_id.upper():
            reasons.append('%s no longer matches its task page' % wi_id.upper())
        edited = page.get('last_edited_time')
        if edited and _parse_time(edited) > as_of + dt.timedelta(seconds=2):
            reasons.append('%s changed after the verified snapshot' % wi_id.upper())
    return list(dict.fromkeys(reasons))


def _link(item, label):
    url = item.get('task_url') or item.get('url')
    label = _safe(label, 80)
    return '<%s|%s>' % (url, label) if isinstance(url, str) and url.startswith('https://app.notion.com/') else label


def render(snapshot, local):
    coverage = snapshot['coverage']
    data_local = local_time(_parse_time(snapshot['as_of']))
    lines = ['*Working Task brief - %s*' % local.strftime('%B %-d, %Y'),
             '_Data as of %s | Integrity: %s | Slack: %s | Gmail: %s | Calendar/meetings: %s_' % (
                 data_local.strftime('%-I:%M %p Mountain'), snapshot['_integrity_status'],
                 coverage['slack'], coverage['gmail'], coverage['calendar_meetings'])]
    sections = (
        ('Today', 'today', lambda x: '• *%s* - %s | Owner: %s | Checkpoint: %s' % (
            _link(x, x['wi_id']), _safe(x['why_today']), _safe(x.get('owner') or 'Unassigned', 100),
            _safe(x['checkpoint'], 200))),
        ('Progress', 'progress', lambda x: '• *%s* - %s' % (_link(x, x['wi_id']), _safe(x['summary']))),
        ('Risks / decisions', 'risks_decisions', lambda x: '• *%s* - %s | Owner: %s | Deadline: %s' % (
            _link(x, x.get('wi_id') or x.get('decision_key') or 'Decision'), _safe(x['summary']),
            _safe(x.get('owner') or 'Unassigned', 100), _safe(x.get('deadline') or 'Not set', 80))),
        ('Cadence', 'cadence', lambda x: '• %s' % _safe(x['summary'])),
        ('Data quality', 'data_quality', lambda x: '• %s' % _safe(x if isinstance(x, str) else x['summary'])),
    )
    for heading, key, formatter in sections:
        rows = snapshot.get(key) or []
        if key == 'today':
            rows = rows[:3]
        else:
            rows = rows[:5]
        if rows:
            lines += ['', '*%s*' % heading] + [formatter(row) for row in rows]
    lines += ['', '*Feedback*', '_Reply in this thread with one command:_',
              '• `focus WI-123 today`', '• `deprioritize WI-123`',
              '• `snooze WI-123 until YYYY-MM-DD`',
              '• `correction WI-123: <fact and supporting source>`',
              '• `decision <Decision Key>: <explicit outcome>`',
              '• `add: <distinct outcome, owner if known, timing, and source link>`']
    return '\n'.join(lines)


def hold(snapshot, local, reasons):
    as_of = snapshot.get('as_of') or snapshot.get('_finished_at') or 'unavailable'
    tomorrow = local + dt.timedelta(days=1)
    while tomorrow.weekday() >= 5:
        tomorrow += dt.timedelta(days=1)
    return ('*Working Task brief - %s*\n'
            ':warning: Briefing held because %s.\n'
            '_Data timestamp: %s | Next verification: %s at 8:15 AM Mountain_'
            % (local.strftime('%B %-d, %Y'), '; '.join(reasons[:3]), as_of,
               tomorrow.strftime('%A, %B %-d')))


def run(cfg, ledger, clients, notion=None, now_utc=None):
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=dt.timezone.utc)
    if not due(cfg, ledger, now_utc):
        return False
    local = local_time(now_utc)
    date = local.strftime('%Y-%m-%d')
    state = {'date': date, 'channel': cfg['working_task_brief_channel'], 'status': 'checking'}
    ledger.put('working_task_brief_state', json.dumps(state))
    snapshot = {'as_of': 'unavailable', '_integrity_status': 'Missing'}
    try:
        clients.slack_preflight(cfg['working_task_brief_channel'])
        source = notion or Notion()
        snapshot = snapshot_from_run(source.integrity_run(cfg['working_task_run_log'], date))
        reasons = validate_snapshot(snapshot, source, now_utc, cfg)
        text = hold(snapshot, local, reasons) if reasons else render(snapshot, local)
        if not reasons and len(text) > 3500:
            reasons = ['briefing content exceeds the two-minute delivery limit']
            text = hold(snapshot, local, reasons)
        state['kind'] = 'hold' if reasons else 'brief'
        state['reasons'] = reasons
    except Exception as exc:
        state['kind'] = 'hold'
        state['reasons'] = [str(exc)]
        text = hold(snapshot, local, state['reasons'])
    state.update(status='sending', text=text)
    ledger.put('working_task_brief_state', json.dumps(state))
    try:
        receipt = clients.say(cfg['working_task_brief_channel'], text)
        if not receipt or not re.fullmatch(r'\d+\.\d+', receipt):
            raise ValueError('Slack did not return a post receipt')
    except Exception as exc:
        state.update(status='unconfirmed', error=type(exc).__name__)
        ledger.put('working_task_brief_state', json.dumps(state))
        return True
    state.update(status='sent', receipt=receipt)
    ledger.put('working_task_brief_state', json.dumps(state))
    return True


def accept_feedback(body, cfg, ledger=None):
    event = body.get('event') or {}
    if (not cfg.get('working_task_brief_enabled') or not body.get('event_id') or
            event.get('type') != 'message' or event.get('subtype')):
        return None
    if event.get('channel') != cfg.get('working_task_brief_channel') or not event.get('thread_ts'):
        return None
    if event.get('user') not in (cfg.get('feedback_users') or cfg.get('users', set())):
        return None
    if ledger is not None:
        state = json.loads(ledger.get('working_task_brief_state', '{}'))
        if state.get('kind') != 'brief' or state.get('receipt') != event.get('thread_ts'):
            return None
    text = re.sub(r'^\s*<@[A-Z0-9]+>\s*', '', event.get('text') or '').strip()
    parsed = parse_feedback(text)
    if not parsed:
        return None
    return {'kind': 'working_task_feedback', 'event_id': body.get('event_id'),
            'key': 'working-task-feedback:' + str(body.get('event_id')), 'channel': event['channel'],
            'thread_ts': event['thread_ts'], 'ts': event.get('ts'), 'user': event.get('user'),
            'command': parsed, 'raw_text': text}


def parse_feedback(text):
    for regex in COMMANDS:
        match = regex.fullmatch(text)
        if match:
            kind = regex.pattern.split('\\s+', 1)[0].lstrip('^').lower()
            values = list(match.groups())
            return {'type': kind, 'wi_id': values[0].upper(), 'value': values[1] if len(values) > 1 else ''}
    match = DECISION.fullmatch(text)
    if match and match.group(1).strip() and match.group(2).strip():
        return {'type': 'decision', 'decision_key': match.group(1).strip(), 'value': match.group(2).strip()}
    if text.lower().startswith('add:') and SOURCE_URL.search(text):
        return {'type': 'add', 'value': text[4:].strip(), 'source_url': SOURCE_URL.search(text).group(0)}
    return None


def capture(store, job, ledger):
    """Durably retain feedback for the separate reconciliation automation."""
    event = json.loads(job['payload'])
    state = json.loads(ledger.get('working_task_brief_state', '{}'))
    if state.get('kind') != 'brief' or state.get('receipt') != event.get('thread_ts'):
        store.update(job['key'], state='done', error='not_a_working_task_brief_thread')
        return
    store.update(job['key'], state='done', ref=json.dumps({
        'status': 'captured_for_reconciliation', 'command': event['command'],
        'thread_ts': event['thread_ts'], 'message_ts': event.get('ts'), 'actor': event.get('user')}))
