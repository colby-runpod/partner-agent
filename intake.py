"""Slack-to-Notion intake service. Web and worker processes share one SQLite file."""
import datetime as dt
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request


class RemoteError(Exception):
    pass


def api(url, token, payload=None, version=None):
    headers = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    if version:
        headers['Notion-Version'] = version
    request = urllib.request.Request(url, headers=headers,
        data=json.dumps(payload).encode() if payload is not None else None)
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RemoteError('Remote HTTP ' + str(exc.code)) from None
    except (OSError, ValueError):
        raise RemoteError('Remote response unavailable') from None
    if result.get('ok') is False:
        raise RemoteError('Slack rejected the request')
    return result


def load_config():
    cfg = json.loads(Path(os.environ.get('INTAKE_CONFIG', 'intake-routing.json')).read_text())
    for key in ('SLACK_SIGNING_SECRET', 'SLACK_TEAM_ID', 'SLACK_APP_ID'):
        if not os.environ.get(key):
            raise ValueError('Missing environment setting: ' + key)
    cfg['team_id'] = os.environ['SLACK_TEAM_ID']
    cfg['app_id'] = os.environ['SLACK_APP_ID']
    cfg['secret'] = os.environ['SLACK_SIGNING_SECRET']
    cfg['enabled'] = os.environ.get('INTAKE_ENABLED') == 'true'
    cfg['slack']['form_bot_id'] = os.environ.get('FORM_BOT_ID')
    cfg['slack']['form_app_id'] = os.environ.get('FORM_APP_ID')
    return cfg


def verify(secret, timestamp, signature, body, now=None):
    try:
        if abs((time.time() if now is None else now) - int(timestamp)) > 300:
            return False
    except (ValueError, TypeError):
        return False
    expected = 'v0=' + hmac.new(secret.encode(), b'v0:' + timestamp.encode() + b':' + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or '')


def flatten(value):
    """Extract displayed Slack text without fetching arbitrary URLs or attachments."""
    if isinstance(value, list):
        return '\n'.join(filter(None, (flatten(x) for x in value)))
    if not isinstance(value, dict):
        return ''
    parts = []
    text = value.get('text')
    if isinstance(text, str):
        parts.append(text)
    elif isinstance(text, dict):
        parts.append(flatten(text))
    for key in ('pretext', 'fallback'):
        if isinstance(value.get(key), str):
            parts.append(value[key])
    for key in ('blocks', 'elements', 'fields', 'attachments'):
        if key in value:
            parts.append(flatten(value[key]))
    return '\n'.join(dict.fromkeys(p for p in parts if p))


def accept_event(body, cfg):
    if body.get('team_id') != cfg['team_id'] or body.get('api_app_id') != cfg['app_id']:
        return None
    event = body.get('event', {})
    if event.get('type') != 'message' or event.get('subtype') not in (None, 'bot_message', 'file_share'):
        return None
    channel = event.get('channel')
    form = channel == cfg['slack']['form_notifications_channel_id']
    if form:
        bot = cfg['slack'].get('form_bot_id')
        app = cfg['slack'].get('form_app_id')
        if not bot or not app or event.get('bot_id') != bot or event.get('app_id') != app or app == cfg['app_id']:
            return None
    elif channel == cfg['slack']['manual_capture_channel_id']:
        if event.get('bot_id') or event.get('app_id') or not event.get('user'):
            return None
    else:
        return None
    if not re.fullmatch(r'\d+\.\d+', event.get('ts', '')) or not body.get('event_id'):
        return None
    thread = event.get('thread_ts', event['ts'])
    if not re.fullmatch(r'\d+\.\d+', thread):
        return None
    return {'event_id': body['event_id'], 'key': ':'.join((body['team_id'], channel, event['ts'])),
            'channel': channel, 'ts': event['ts'], 'thread_ts': thread,
            'form': form, 'text': flatten(event), 'has_files': bool(event.get('files'))}


class Store:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS intake_jobs (
                key TEXT PRIMARY KEY, event_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending', ref TEXT, result_url TEXT,
                reply TEXT, reply_state TEXT NOT NULL DEFAULT 'pending', error TEXT)''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=1)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, event):
        with self.connect() as db:
            return db.execute('INSERT OR IGNORE INTO intake_jobs(key,event_id,payload) VALUES (?,?,?)',
                              (event['key'], event['event_id'], json.dumps(event))).rowcount == 1

    def update(self, key, **fields):
        allowed = {'state', 'ref', 'result_url', 'reply', 'reply_state', 'error'}
        if not fields.keys() <= allowed:
            raise ValueError('Invalid state field')
        with self.connect() as db:
            db.execute('UPDATE intake_jobs SET ' + ','.join(k + '=?' for k in fields) + ' WHERE key=?',
                       list(fields.values()) + [key])

    def get(self, key):
        with self.connect() as db:
            return dict(db.execute('SELECT * FROM intake_jobs WHERE key=?', (key,)).fetchone())

    def next_job(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM intake_jobs WHERE state IN ('pending','processing','writing') OR (reply IS NOT NULL AND reply_state='pending') ORDER BY rowid LIMIT 1").fetchone()
            return dict(row) if row else None


def plain(value):
    value = re.sub(r'<mailto:([^>|]+)(?:\|[^>]+)?>', r'\1', value)
    value = re.sub(r'<(https?://[^>|]+)(?:\|[^>]+)?>', r'\1', value)
    return html.unescape(value).strip()


def extract(event):
    text = event['text'].strip()
    # Full details are required for initial intake; this worker does not interpret general commands.
    fields = {}
    for line in text.splitlines():
        match = re.match(r'^\*?(Contact|Company|Email|Website|Stated ask)\*?:\*?\s*(.*)', line.strip(), re.I)
        if match:
            fields[match[1].lower()] = plain(match[2])
    email = fields.get('email', '')
    if email and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email):
        email = ''
    if not event['form'] and not fields.get('contact') and not fields.get('company'):
        return None
    if event['form'] and not fields.get('contact') and not fields.get('company'):
        return None
    return {'contact': fields.get('contact') or fields.get('company'),
            'company': fields.get('company', ''), 'email': email,
            'summary': fields.get('stated ask') or 'Captured request; original source preserved below. Needs triage.',
            'raw': text}


def rich(text):
    return [{'type': 'text', 'text': {'content': text[i:i + 1800]}} for i in range(0, len(text), 1800)]


def notion_payload(data, event, ref, cfg):
    if len(data['raw']) > 60000:
        raise ValueError('Request too large for this pilot')
    props = {'Contact': {'title': rich(data['contact'][:1000])},
             'Company': {'rich_text': rich(data['company'][:1800])},
             'Request Summary': {'rich_text': rich(data['summary'][:1800])},
             'Intake Ref': {'rich_text': rich(ref)}}
    if data['email']:
        props['Email'] = {'email': data['email']}
    for key, value in cfg['notion']['defaults'].items():
        props[key] = {'select': {'name': value}}
    if event['form']:
        props['Source'] = {'select': {'name': 'Slack Form'}}
    # Do not assign Submitted: Slack notification time may differ from form submission time.
    captured = dt.datetime.fromtimestamp(float(event['ts']), dt.timezone.utc).isoformat()
    body = 'Slack source: ' + ref + '\nSlack message time (not verified form submission time): ' + captured + '\n\nOriginal request:\n' + data['raw']
    children = [{'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(body[i:i + 1800])}}
                for i in range(0, len(body), 1800)]
    return {'parent': {'type': 'data_source_id', 'data_source_id': cfg['notion']['data_source_id']},
            'properties': props, 'children': children}


class Clients:
    def __init__(self, cfg):
        self.cfg = cfg
        self.slack_token = os.environ['SLACK_BOT_TOKEN']
        self.notion_token = os.environ['NOTION_TOKEN']

    def slack(self, method, payload):
        return api('https://slack.com/api/' + method, self.slack_token, payload)

    def permalink(self, event):
        params = urllib.parse.urlencode({'channel': event['channel'], 'message_ts': event['ts']})
        return api('https://slack.com/api/chat.getPermalink?' + params, self.slack_token)['permalink']

    def find(self, ref):
        result = api('https://api.notion.com/v1/data_sources/' + self.cfg['notion']['data_source_id'] + '/query',
            self.notion_token, {'filter': {'property': 'Intake Ref', 'rich_text': {'equals': ref}}, 'page_size': 2}, '2025-09-03')
        rows = result.get('results', [])
        if len(rows) > 1 or result.get('has_more'):
            raise RemoteError('Multiple intake records match; manual review required')
        return rows[0]['url'] if rows else None

    def create(self, payload):
        return api('https://api.notion.com/v1/pages', self.notion_token, payload, '2025-09-03')['url']

    def reply(self, event, text):
        self.slack('chat.postMessage', {'channel': event['channel'], 'thread_ts': event['thread_ts'],
            'text': text, 'mrkdwn': False, 'parse': 'none', 'unfurl_links': False, 'unfurl_media': False})


def process(store, job, clients, cfg):
    key, event = job['key'], json.loads(job['payload'])
    if job['state'] in ('pending', 'processing', 'writing'):
        uncertain = job['state'] == 'writing'
        try:
            if not uncertain:
                store.update(key, state='processing')
            data = extract(event)
            if data is None:
                store.update(key, state='needs_info', reply='Please include the request details with Contact: and/or Company: fields. I cannot resolve “send this” or attachments alone yet. No intake entry was created.')
            else:
                ref = job['ref'] or clients.permalink(event)
                store.update(key, ref=ref)
                url = clients.find(ref)
                if not url and uncertain:
                    store.update(key, state='review', reply='The previous Notion write has an uncertain outcome. I could not confirm its record, so I have not retried creation. An operator needs to reconcile this request.', error='unconfirmed_write')
                else:
                    if not url:
                        payload = notion_payload(data, event, ref, cfg)
                        store.update(key, state='writing')
                        url = clients.create(payload)
                    store.update(key, state='done', result_url=url, reply='Saved in Partner Intake & Qualification as a new intake, or found the existing entry for this message: ' + url)
        except Exception:
            # Do not log message text, tokens, or raw remote errors. Writing state is retained for reconciliation.
            state = store.get(key)['state']
            if state == 'writing' and not uncertain:
                return
            store.update(key, state='review' if uncertain else 'failed', error='processing_failed', reply='I could not finish intake. The request is saved for operator review; no successful Notion write is confirmed. Do not resubmit until the existing request has been checked.')
    job = store.get(key)
    if job['reply'] and job['reply_state'] == 'pending':
        store.update(key, reply_state='sending')
        try:
            clients.reply(event, job['reply'])
        except Exception:
            store.update(key, reply_state='review', error='reply_unconfirmed')
        else:
            store.update(key, reply_state='sent')


class Application:
    def __init__(self, cfg, store):
        self.cfg, self.store = cfg, store

    def __call__(self, env, start):
        def respond(code, value):
            data = json.dumps(value).encode()
            start(code, [('Content-Type', 'application/json'), ('Content-Length', str(len(data)))])
            return [data]
        if env.get('PATH_INFO') == '/healthz' and env.get('REQUEST_METHOD') == 'GET':
            return respond('200 OK', {'status': 'listening', 'intake_enabled': self.cfg['enabled']})
        if env.get('PATH_INFO') != '/slack/events' or env.get('REQUEST_METHOD') != 'POST':
            return respond('404 Not Found', {})
        try:
            size = int(env.get('CONTENT_LENGTH') or 0)
            if size <= 0 or size > 262144:
                return respond('413 Payload Too Large', {})
            raw = env['wsgi.input'].read(size)
            if not verify(self.cfg['secret'], env.get('HTTP_X_SLACK_REQUEST_TIMESTAMP'), env.get('HTTP_X_SLACK_SIGNATURE'), raw):
                return respond('401 Unauthorized', {})
            body = json.loads(raw)
            if not isinstance(body, dict):
                return respond('400 Bad Request', {})
            if body.get('type') == 'url_verification':
                return respond('200 OK', {'challenge': body.get('challenge', '')})
            if not self.cfg['enabled']:
                return respond('503 Service Unavailable', {'error': 'intake_disabled'})
            if body.get('type') == 'event_callback':
                event = accept_event(body, self.cfg)
                if event:
                    self.store.enqueue(event)
            return respond('200 OK', {'ok': True})
        except (ValueError, TypeError, KeyError, AttributeError):
            return respond('400 Bad Request', {})
        except sqlite3.Error:
            return respond('503 Service Unavailable', {'error': 'queue_unavailable'})


def create_app():
    return Application(load_config(), Store(os.environ.get('INTAKE_DB', 'state/intake.sqlite3')))


def worker():
    import fcntl
    cfg = load_config()
    if not cfg['enabled']:
        raise SystemExit('Set INTAKE_ENABLED=true after deployment setup to run the worker.')
    store = Store(os.environ.get('INTAKE_DB', 'state/intake.sqlite3'))
    # One worker per persistent database; OS releases this lock on crashes.
    with open(store.path + '.worker.lock', 'w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        clients = Clients(cfg)
        while True:
            job = store.next_job()
            if job:
                process(store, job, clients, cfg)
            else:
                time.sleep(0.5)


if __name__ == '__main__':
    worker()
