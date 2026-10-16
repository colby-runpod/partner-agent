"""Slack-to-Working-Tasks capture for Team Partner Agent.

Mention the bot with "flag this as a task" (or "add a task", "track this", ...) on a forwarded message,
inside a thread, or with the task written out. The bot drafts the task with Claude, creates one row in
Working Tasks (Status Inbox, owner Colby, Review Required), and replies in the thread with Open and Drop.

Guardrails:
- Only FEEDBACK_USER_IDS can trigger it or click Drop.
- One row per source message: External Source ID is slack:<channel>:<ts>, checked in Notion before creating,
  so the dedupe survives restarts.
- Writes only these Working Tasks fields. Never owners other than the configured one, never approvals,
  decisions or due dates the message does not state.
"""
import datetime as dt
import html
import json
import os
import re
import urllib.parse
import urllib.request

from intake import api, flatten

NOTION_VERSION = '2025-09-03'
TASKS_SOURCE = 'e77648f6-502d-450f-8b79-4dd2ced52bab'     # Working Tasks data source
PARTNERS_SOURCE = '90430d4f-99ca-4925-9df3-72abc1b609b7'  # Partners data source (title: Partner Name)
OWNER_NOTION_ID = '38dd872b-594c-8152-9d06-0002630c4d98'  # Colby
TASK_TYPES = ('Task', 'Reply Needed', 'Action', 'Review', 'Meeting Follow-up')
FUNCTIONS = ('Partnerships', 'Product', 'Partner SA', 'Engineering', 'Finance', 'RevOps', 'Legal', 'Security or IT',
             'Sales', 'Customer Success', 'Support', 'Marketing or Comms', 'Supply or Capacity', 'People or Talent',
             'Operations', 'Executive', 'External party')
TASK_ACTIONS = {'task_drop': 'Drop'}
MENTIONS = re.compile(r'<[@#!][^>]*>')
ASK = re.compile(r'\b(?:flag|add|make|create|log|capture|file|turn|save|put|open)\b[^.?!\n]{0,60}?\b(?:task|to-?do)s?\b'
                 r'|^\s*(?:please\s+)?(?:task|to-?do)\b|^\s*(?:please\s+)?track\s+(?:this|it)\b', re.I)
SLACK_LINK = re.compile(r'https://[\w-]+(?:\.enterprise)?\.slack\.com/archives/(C\w+|G\w+)/p(\d{10})(\d{6})\S*')
PAGE_ID = re.compile(r'[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}')

TASK_INSTRUCTIONS = '''You turn a Slack message into one task for Colby Henderson, who runs Partnerships at
Runpod (write "Runpod", never "RunPod"). Return ONLY a JSON object with these keys:

- item: the task title, an imperative verb phrase under 100 characters naming the counterparty, e.g.
  "Follow up with Comfy (Eric Sun) on the deployment flow and Hub template link".
- type: one of Task, Reply Needed, Action, Review, Meeting Follow-up. Reply Needed when someone is waiting
  on Colby's answer.
- context: 2-4 short bullet lines starting with "- ": what was said, by whom, and why it matters. Mark claims
  as the sender's claims, not facts.
- next_step: the single immediate next action, one sentence.
- definition_of_done: one sentence describing what finished looks like.
- whos_waiting: the person or company blocked on Colby, or "" if nobody.
- owning_function: one of Partnerships, Product, Partner SA, Engineering, Finance, RevOps, Legal,
  Security or IT, Sales, Customer Success, Support, Marketing or Comms, Supply or Capacity, People or Talent,
  Operations, Executive, External party. Default Partnerships.
- partner: the partner company's short name if the task is about one (e.g. "Comfy"), else "".
- due_date: YYYY-MM-DD only if the message states a date or deadline explicitly, else "". Never guess.
- confidence: 0 to 1, how sure you are this is a real, well-defined task.

Use Colby's request text for intent ("flag this as a task" adds nothing; "task: send Eric the Hub link by
Friday" is the task itself). Keep it plain and specific. No em dashes. Do not invent owners, dates or
commitments that the message does not contain.'''


def _strip(text):
    return re.sub(r'\s+', ' ', MENTIONS.sub(' ', html.unescape(str(text or '')))).strip()


def is_task_request(text):
    words = _strip(text)
    return bool(words) and len(words.split()) <= 40 and bool(ASK.search(words))


def forwarded(event):
    """The first shared or forwarded message attached to the mention, if any."""
    for att in event.get('attachments') or []:
        if not isinstance(att, dict) or not (att.get('is_share') or att.get('is_msg_unfurl') or att.get('from_url')):
            continue
        text = flatten(att) or ''
        link = str(att.get('from_url') or '')
        match = SLACK_LINK.match(link)
        if match:
            source_id = 'slack:%s:%s.%s' % match.groups()
        elif re.fullmatch(r'[CG]\w+', str(att.get('channel_id', ''))) and re.fullmatch(r'\d+\.\d+', str(att.get('ts', ''))):
            source_id = 'slack:%s:%s' % (att['channel_id'], att['ts'])
        else:
            source_id = ''
        channel, ts = (match.group(1), '%s.%s' % match.group(2, 3)) if match else (
            str(att.get('channel_id', '')), str(att.get('ts', '')))
        thread = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get('thread_ts', [''])[0] if match else ''
        thread = thread or str(att.get('thread_ts') or '')
        ok = re.fullmatch(r'[CGD]\w+', channel) and re.fullmatch(r'\d+\.\d+', ts)
        return {'text': text[:8000], 'author': _strip(att.get('author_name') or att.get('author_subname'))[:80],
                'link': link if match else '', 'source_id': source_id,
                'channel': channel if ok else '', 'ts': ts if ok else '',
                'thread_ts': thread if ok and re.fullmatch(r'\d+\.\d+', thread) else (ts if ok else ''),
                'files': file_refs(att.get('files'))}
    return None


def file_refs(files):
    """Image attachments worth showing to Claude: Slack-hosted, small, at most 4."""
    out = []
    for f in files or []:
        if not isinstance(f, dict):
            continue
        url, kind = str(f.get('url_private') or ''), str(f.get('mimetype') or '')
        if kind in ('image/png', 'image/jpeg', 'image/gif', 'image/webp') and re.match(r'https://files\.slack\.com/', url) \
                and int(f.get('size') or 0) <= 4_500_000:
            out.append({'url': url, 'mimetype': kind, 'name': _strip(f.get('name'))[:80]})
    return out[:4]


def accept_task(body, cfg):
    e = body.get('event', {})
    if not cfg.get('tasks_enabled'):
        return None
    if body.get('team_id') != cfg['team_id'] or body.get('api_app_id') != cfg['app_id']:
        return None
    if e.get('type') != 'app_mention' or e.get('subtype') or e.get('bot_id'):
        return None
    if e.get('user') not in (cfg.get('feedback_users') or cfg.get('users', set())):
        return None
    channels = cfg.get('channels', set()) | cfg.get('review_channels', set()) | {cfg.get('shadow_channel'),
                                                                               cfg.get('summary_channel')}
    if e.get('channel') not in channels:
        return None
    ts, thread = e.get('ts', ''), e.get('thread_ts') or e.get('ts', '')
    if not body.get('event_id') or not re.fullmatch(r'\d+\.\d+', ts) or not re.fullmatch(r'\d+\.\d+', thread):
        return None
    if not is_task_request(e.get('text', '')):
        return None
    return dict(kind='task', event_id=body['event_id'], key='task:%s:%s:%s' % (body['team_id'], e['channel'], ts),
                channel=e['channel'], ts=ts, thread_ts=thread, user=e['user'], request=_strip(e.get('text'))[:600],
                forward=forwarded(e))


def accept_task_action(payload, cfg):
    """Drop on a task reply. Open is a plain link button and needs no handling."""
    if not cfg.get('tasks_enabled') or payload.get('type') != 'block_actions':
        return None
    if (payload.get('team') or {}).get('id') != cfg['team_id'] or payload.get('api_app_id') != cfg['app_id']:
        return None
    user = (payload.get('user') or {}).get('id')
    if user not in (cfg.get('feedback_users') or cfg.get('users', set())):
        return None
    action = (payload.get('actions') or [{}])[0]
    channel, message = (payload.get('channel') or {}).get('id'), payload.get('message') or {}
    value, action_ts = str(action.get('value', '')), str(action.get('action_ts', ''))
    if action.get('action_id') not in TASK_ACTIONS or not PAGE_ID.fullmatch(value):
        return None
    if not channel or not re.fullmatch(r'\d+\.\d+', message.get('ts', '')) or not re.fullmatch(r'\d+\.\d+', action_ts):
        return None
    response_url = payload.get('response_url', '')
    key = 'tact:%s:%s' % (action_ts, user)
    return dict(kind='task_action', event_id=key, key=key, action=action['action_id'], page_id=value, user=user,
                channel=channel, message_ts=message['ts'], message_text=str(message.get('text', ''))[:3000],
                response_url=response_url if re.fullmatch(r'https://hooks\.slack\.com/[\w/.-]+', response_url) else '')


def parse_task(raw):
    start, end = raw.find('{'), raw.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('No task JSON')
    data = json.loads(raw[start:end + 1], strict=False)
    if not isinstance(data, dict):
        raise ValueError('Task JSON is not an object')
    clean = lambda key, limit: _strip(data.get(key))[:limit].replace('\u2014', '--')
    item = clean('item', 120)
    if not item:
        raise ValueError('Task has no title')
    context = '\n'.join(_strip(line)[:300] for line in str(data.get('context') or '').splitlines()
                        if _strip(line))[:1500].replace('\u2014', '--')
    due = clean('due_date', 10)
    try:
        confidence = max(0.0, min(1.0, float(data.get('confidence'))))
    except (TypeError, ValueError):
        confidence = 0.5
    return {'item': item, 'type': data.get('type') if data.get('type') in TASK_TYPES else 'Task',
            'context': context, 'next_step': clean('next_step', 400), 'definition_of_done': clean('definition_of_done', 400),
            'whos_waiting': clean('whos_waiting', 120),
            'owning_function': data.get('owning_function') if data.get('owning_function') in FUNCTIONS else 'Partnerships',
            'partner': clean('partner', 60), 'due_date': due if re.fullmatch(r'\d{4}-\d{2}-\d{2}', due) else '',
            'confidence': round(confidence, 2)}


def _rich(text):
    text = str(text or '')
    return [{'type': 'text', 'text': {'content': text[i:i + 1800]}} for i in range(0, len(text), 1800)][:50]


class TaskWriter:
    def __init__(self, cfg):
        self.cfg = cfg

    def _req(self, method, path, payload=None):
        url = 'https://api.notion.com/v1/' + path
        if method == 'GET':
            return api(url, os.environ['NOTION_TOKEN'], version=NOTION_VERSION)
        if method == 'PATCH':
            request = urllib.request.Request(url, method='PATCH', data=json.dumps(payload).encode(), headers={
                'Authorization': 'Bearer ' + os.environ['NOTION_TOKEN'], 'Notion-Version': NOTION_VERSION,
                'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=25) as response:
                return json.load(response)
        return api(url, os.environ['NOTION_TOKEN'], payload, version=NOTION_VERSION)

    def find(self, source_id):
        rows = self._req('POST', 'data_sources/%s/query' % self.cfg['tasks_source'], {
            'filter': {'property': 'External Source ID', 'rich_text': {'equals': source_id}},
            'page_size': 1}).get('results', [])
        if not rows:
            return None
        title = ''.join(t.get('plain_text', '') for t in ((rows[0].get('properties') or {}).get('Item') or {}).get('title', []))
        return rows[0]['url'], rows[0]['id'], title or 'the existing task'

    def partner(self, name):
        """The one Partners row whose name contains this, or None when there are zero or several."""
        if not name:
            return None
        try:
            rows = self._req('POST', 'data_sources/%s/query' % self.cfg['partners_source'], {
                'filter': {'property': 'Partner Name', 'title': {'contains': name}}, 'page_size': 3}).get('results', [])
        except Exception:
            return None
        return rows[0]['id'] if len(rows) == 1 else None

    def properties(self, task, source_id, link, partner_id, today):
        props = {
            'Item': {'title': _rich(task['item'])},
            'Status': {'select': {'name': 'Inbox'}},
            'Type': {'select': {'name': task['type']}},
            'Source': {'select': {'name': 'Slack'}},
            'External Source ID': {'rich_text': _rich(source_id)},
            'Capture Disposition': {'select': {'name': 'Create task'}},
            'Review Required': {'checkbox': True},
            'Accountable Owner': {'people': [{'id': self.cfg['task_owner']}]},
            'Owning Function': {'select': {'name': task['owning_function']}},
            'Agent Confidence': {'number': task['confidence']},
            'Context': {'rich_text': _rich(task['context'])},
            'Next Step': {'rich_text': _rich(task['next_step'])},
            'Definition of Done': {'rich_text': _rich(task['definition_of_done'])},
            'Automation Notes': {'rich_text': _rich('Captured by Team Partner Agent from Slack on %s at Colby\'s request.'
                                                    % today)},
        }
        if link:
            props['Source Link'] = {'url': link}
        if task['whos_waiting']:
            props["Who's Waiting"] = {'rich_text': _rich(task['whos_waiting'])}
        if task['due_date']:
            props['Due Date'] = {'date': {'start': task['due_date']}}
        if partner_id:
            props['Associated Partner(s)'] = {'relation': [{'id': partner_id}]}
        return props

    def create(self, task, source_id, link, today):
        partner_id = self.partner(task['partner'])
        page = self._req('POST', 'pages', {'parent': {'type': 'data_source_id', 'data_source_id': self.cfg['tasks_source']},
                                           'properties': self.properties(task, source_id, link, partner_id, today)})
        return page['url'], page['id'], bool(partner_id)

    def drop(self, page_id, today):
        self._req('PATCH', 'pages/%s' % page_id, {'properties': {
            'Status': {'select': {'name': 'Dropped'}}, 'Close Reason': {'select': {'name': 'Declined'}},
            'Closed At': {'date': {'start': today}}}})


def config(cfg):
    """Adds task settings to the review config. Capture needs NOTION_WRITE and the Working Tasks share."""
    cfg['tasks_enabled'] = os.environ.get('TASKS_ENABLED') == 'true'
    cfg['tasks_source'] = os.environ.get('TASKS_NOTION_ID', TASKS_SOURCE)
    cfg['partners_source'] = os.environ.get('PARTNERS_NOTION_ID', PARTNERS_SOURCE)
    cfg['task_owner'] = os.environ.get('TASK_OWNER_NOTION_ID', OWNER_NOTION_ID)
    return cfg


def thread_text(channel, thread_ts, latest):
    params = urllib.parse.urlencode({'channel': channel, 'ts': thread_ts, 'latest': latest, 'inclusive': 'true',
                                     'limit': 30})
    data = api('https://slack.com/api/conversations.replies?' + params, os.environ['SLACK_BOT_TOKEN'])
    lines = []
    for m in data.get('messages', [])[:30]:
        who = m.get('user') or m.get('bot_id') or 'someone'
        lines.append('<%s>: %s' % (who, flatten(m)[:1500]))
    return '\n'.join(lines)[:8000]


def draft(call, request, source, author, today):
    payload = {'model': os.environ.get('ANTHROPIC_MODEL', ''), 'max_tokens': 1200,
               'system': [{'type': 'text', 'text': TASK_INSTRUCTIONS, 'cache_control': {'type': 'ephemeral'}}],
               'messages': [{'role': 'user', 'content': json.dumps({
                   'colby_request': request, 'source_author': author, 'source_message': source, 'today': today})}]}
    data = call(payload)
    if data.get('stop_reason') != 'end_turn':
        raise ValueError('Incomplete Claude response')
    return parse_task(''.join(c.get('text', '') for c in data.get('content', []) if c.get('type') == 'text'))


def reply_blocks(text, url, page_id, drop=True):
    elements = [{'type': 'button', 'action_id': 'task_open', 'text': {'type': 'plain_text', 'text': 'Open'}, 'url': url}]
    if drop:
        elements.append({'type': 'button', 'action_id': 'task_drop', 'value': page_id,
                         'text': {'type': 'plain_text', 'text': 'Drop'}})
    return [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': text[:2900], 'verbatim': True}},
            {'type': 'actions', 'elements': elements}]


def _slack_safe(text):
    return html.escape(str(text or ''), quote=False)


def summary_text(task, url, partner_linked):
    lines = [':white_check_mark: Added to Working Tasks: <%s|%s>' % (url, _slack_safe(task['item'])),
             '*Next step* %s' % _slack_safe(task['next_step'] or 'not set'),
             '*Type* %s  ·  *Status* Inbox  ·  *Owner* you  ·  *Due* %s' % (task['type'], task['due_date'] or 'none set')]
    if task['partner']:
        lines.append('*Partner* %s' % (_slack_safe(task['partner']) if partner_linked
                                       else '%s (no single Partners match, so not linked)' % _slack_safe(task['partner'])))
    return '\n'.join(lines)


def process_task(store, job, clients, cfg, writer, now=None):
    event = json.loads(job['payload'])
    store.update(job['key'], state='done')  # Never retried: a retry could create a second row.
    thread = event['thread_ts']
    say = lambda text, blocks=None: clients.say_blocks(event['channel'], thread, text, blocks)
    if writer is None:
        say("Task capture needs Notion writing (NOTION_WRITE) turned on, so I didn't add anything.")
        return
    today = (now or dt.date.today()).isoformat()
    forward = event.get('forward')
    try:
        if forward and forward.get('text'):
            source, author = forward['text'], forward.get('author', '')
            if forward.get('channel') and forward.get('thread_ts'):
                try:  # Full original thread when Colby's read token (or the bot) can see it.
                    import assistant
                    messages, _ = assistant.Context(cfg).thread(forward['channel'], forward['thread_ts'])
                    source = json.dumps({'forwarded_message': source, 'original_thread': messages},
                                        ensure_ascii=False)[:9000]
                except Exception:
                    pass
            source_id = forward.get('source_id') or 'slack:%s:%s' % (event['channel'], event['ts'])
            link = forward.get('link') or clients.permalink(event['channel'], event['ts'])
        elif thread != event['ts']:
            source, author = thread_text(event['channel'], thread, event['ts']), ''
            source_id, link = 'slack:%s:%s' % (event['channel'], thread), clients.permalink(event['channel'], thread)
        else:
            source, author = event['request'], ''
            source_id, link = 'slack:%s:%s' % (event['channel'], event['ts']), clients.permalink(event['channel'], event['ts'])
    except Exception:
        say("I couldn't read the message to turn into a task. Check that I'm in this channel, then tag me again.")
        return
    try:
        existing = writer.find(source_id)
    except Exception:
        print('Task lookup failed.', flush=True)
        say("I couldn't reach Working Tasks, so I didn't add anything. Check that the database is shared with the "
            'Notion integration, then tag me again.')
        return
    if existing:
        url, page_id, title = existing
        say('This is already in Working Tasks: <%s|%s>' % (url, _slack_safe(title)), reply_blocks(
            'This is already in Working Tasks: <%s|%s>' % (url, _slack_safe(title)), url, page_id, drop=False))
        return
    try:
        task = draft(clients._call, event['request'], source, author, today)
    except Exception:
        print('Task draft failed.', flush=True)
        say("I couldn't draft the task, so nothing was added. Try again in a minute.")
        return
    try:
        url, page_id, linked = writer.create(task, source_id, link, today)
    except Exception:
        print('Task create failed.', flush=True)
        say("I drafted the task but couldn't save it to Working Tasks. Check the database is shared with the "
            'Notion integration, then tag me again.')
        return
    text = summary_text(task, url, linked)
    say(text, reply_blocks(text, url, page_id))


def process_task_action(store, job, clients, writer, now=None):
    event = json.loads(job['payload'])
    store.update(job['key'], state='done')
    if writer is None:
        clients.ephemeral(event, 'Notion writing is turned off (NOTION_WRITE), so nothing was changed.')
        return
    today = (now or dt.date.today()).isoformat()
    try:
        writer.drop(event['page_id'], today)
    except Exception:
        print('Task drop failed.', flush=True)
        clients.ephemeral(event, "That didn't go through, and the task wasn't changed. Try again in a minute.")
        return
    url = 'https://www.notion.so/' + event['page_id'].replace('-', '')
    text = '%s\n:wastebasket: Dropped by <@%s>' % (event.get('message_text') or 'Task', event['user'])
    try:
        clients.update_blocks(event['channel'], event['message_ts'], text, reply_blocks(text, url, event['page_id'], drop=False))
    except Exception:
        print('Task reply update failed after drop.', flush=True)
