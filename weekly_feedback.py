"""Mention-only weekly feedback capture. No drafting, publication or lead routing.

A single outbox worker owns this queue. Notion appends have a durable write fence:
reconcile the exact marker after a timeout; never blindly append a second copy.
"""
import datetime as dt
import hashlib
import json
import os
import re
import urllib.parse
import uuid

import outbox as o

WEEKLY_SOURCE = '3c363142-e718-4f03-a6c9-cfa24783fbc0'
AUTHORS = {o.COLBY: 'Colby Henderson', 'U0C6DH3UUV9': 'Tanya Chandy'}


def enabled():
    return os.environ.get('WEEKLY_FEEDBACK_ENABLED') == 'true'


def accept(body):
    if not enabled():
        return None
    e = body.get('event', {})
    if (not body.get('event_id') or e.get('type') != 'app_mention' or e.get('subtype')
            or e.get('bot_id') or e.get('user') not in AUTHORS
            or e.get('channel') != o.WEEKLY_CHANNEL
            or '<@' + o.BOT + '>' not in e.get('text', '')):
        return None
    ts, parent = e.get('ts', ''), e.get('thread_ts', '')
    if not all(re.fullmatch(r'\d+\.\d+', t) for t in (ts, parent)) or ts == parent:
        return None
    text = e.get('text', '')
    if not text.replace('<@' + o.BOT + '>', '').strip():
        return None
    key = 'weekly-feedback:' + e['channel'] + ':' + ts
    return dict(kind='weekly_feedback', key=key, event_id=key, channel=e['channel'],
                message_ts=parent, ts=ts, user=e['user'], text=text,
                has_files=bool(e.get('files')), progress={})


def monday(r):
    match = re.fullmatch(r'weekly-update-summary:(\d{4}-\d{2}-\d{2})', r.get('title', ''))
    if not match or dt.date.fromisoformat(match[1]).weekday() != 0:
        raise ValueError('invalid_weekly_title')
    return match[1]


def validate_draft(page, week):
    source = page.get('parent', {}).get('data_source_id', '')
    if (source.replace('-', '') != WEEKLY_SOURCE.replace('-', '')
            or page.get('archived') or page.get('in_trash')
            or o.prop(page, 'Week') != week):
        raise ValueError('weekly_draft_mismatch')
    return str(uuid.UUID(page['id']))


def resolve_draft(remote, r):
    week = monday(r)
    if r.get('weekly_draft_id'):
        return validate_draft(remote.get(r['weekly_draft_id']), week)
    result = remote.notion('POST', 'data_sources/' + WEEKLY_SOURCE + '/query', {
        'filter': {'property': 'Week', 'date': {'equals': week}}, 'page_size': 100})
    pages = [p for p in result.get('results', []) if not p.get('archived') and not p.get('in_trash')]
    if result.get('has_more') or len(pages) != 1:
        raise ValueError('weekly_draft_missing_or_ambiguous')
    return validate_draft(pages[0], week)


def block_text(block):
    return ''.join(x.get('plain_text', x.get('text', {}).get('content', ''))
                   for x in block.get(block.get('type'), {}).get('rich_text', []))


def has_capture(remote, page_id, content):
    cursor = None
    for _ in range(50):
        params = {'page_size': 100}
        if cursor:
            params['start_cursor'] = cursor
        result = remote.notion('GET', 'blocks/' + page_id + '/children?' + urllib.parse.urlencode(params))
        for block in result.get('results', []):
            if not block.get('archived') and block_text(block) == content:
                return True
        if not result.get('has_more'):
            return False
        cursor = result.get('next_cursor')
        if not cursor:
            raise ValueError('incomplete_feedback_history')
    raise ValueError('feedback_history_too_large')


def append_capture(box, event, destination, page_id, content):
    progress = event['progress']
    if progress.get(destination) == 'synced':
        return
    if has_capture(box.remote, page_id, content):
        progress[destination] = 'synced'
        box.save_event(event)
        return
    if progress.get(destination) == 'writing':
        raise RuntimeError('uncertain_' + destination + '_write_requires_reconciliation')
    progress[destination] = 'writing'
    box.save_event(event)
    # One block contains both the receipt marker and the complete feedback. No
    # separate marker that could succeed without the corresponding text.
    box.remote.notion('PATCH', 'blocks/' + page_id + '/children', {'children': [{
        'object': 'block', 'type': 'paragraph', 'paragraph': o.rich(content)}]})
    progress[destination] = 'synced'
    box.save_event(event)


def acknowledge(box, event, text, field='ack'):
    progress = event['progress']
    if progress.get(field):
        return  # Includes an uncertain send; no duplicate notification on retries.
    progress[field] = 'sending'
    box.save_event(event)
    result = box.remote.slack('chat.postMessage', {
        'channel': event['channel'], 'thread_ts': event['message_ts'], 'text': text,
        'parse': 'none', 'link_names': False, 'unfurl_links': False, 'unfurl_media': False,
        'client_msg_id': str(uuid.uuid5(uuid.NAMESPACE_URL, event['key'] + ':' + field))})
    if not re.fullmatch(r'\d+\.\d+', result.get('ts', '')):
        raise RuntimeError('ack_receipt_missing')
    progress[field] = result['ts']
    box.save_event(event)


def process(box, event):
    if not enabled():
        raise RuntimeError('weekly_feedback_paused')  # Preserve accepted work.
    if (event.get('user') not in AUTHORS or event.get('channel') != o.WEEKLY_CHANNEL
            or '<@' + o.BOT + '>' not in event.get('text', '')):
        raise ValueError('unauthorized_weekly_feedback')
    event.setdefault('progress', {})
    try:
        matches = [d for d in box.matches(event['message_ts'])
                   if json.loads(d['snapshot']).get('channel') == o.WEEKLY_CHANNEL]
        if len(matches) != 1:
            raise ValueError('weekly_parent_missing_or_ambiguous')
        delivery = matches[0]
        r = json.loads(delivery['snapshot'])
        if delivery['state'] not in ('posted', 'sync') or r.get('kind') != 'other':
            raise ValueError('weekly_parent_not_posted')
        current = box.remote.get(r['id'])
        if (current.get('archived') or current.get('in_trash') or o.prop(current, 'Status') == 'Cancelled'):
            raise ValueError('weekly_outbox_cancelled')
        if not event.get('draft_id'):
            event['draft_id'] = resolve_draft(box.remote, r)
            box.bind_weekly_draft(r['id'], event['draft_id'])
            event['week'] = monday(r)
            box.save_event(event)
        validate_draft(box.remote.get(event['draft_id']), event['week'])
        if len(event['text']) > 12000:
            raise ValueError('feedback_too_long_split_into_shorter_replies')
        if not event.get('content'):
            link = box.remote.slack('chat.getPermalink', {'channel': event['channel'], 'message_ts': event['ts']})['permalink']
            marker = hashlib.sha256(event['key'].encode()).hexdigest()[:24]
            timestamp = dt.datetime.fromtimestamp(float(event['ts']), dt.timezone.utc).isoformat()
            event['content'] = ('Slack feedback / staging capture\nReceipt: ' + marker +
                '\nWeek: ' + event['week'] + '\nAuthor: ' + AUTHORS[event['user']] +
                '\nCaptured message time (UTC): ' + timestamp + '\nSource: ' + link +
                '\nWeekly draft: https://www.notion.so/' + event['draft_id'].replace('-', '') +
                '\nEditorial status: Pending incorporation\nFeedback (verbatim):\n' + event['text'] +
                ('\nAttachments were not captured; open the Slack source.' if event.get('has_files') else ''))
            box.save_event(event)
        append_capture(box, event, 'outbox', r['id'], event['content'])
        append_capture(box, event, 'draft', event['draft_id'], event['content'])
        event['error'] = ''
        box.save_event(event)
        acknowledge(box, event, 'Captured your feedback in the outbox record and this week’s draft staging notes.\n'
                    '<https://app.notion.com/p/' + event['draft_id'].replace('-', '') +
                    '|View weekly draft in Notion>\n'
                    'It is pending incorporation into the draft; nothing was republished.')
    except Exception as exc:
        # Do not persist provider response bodies or credentials in diagnostics.
        event['error'] = str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__
        event['error'] = event['error'][:160]
        box.save_event(event)
        if not all(event['progress'].get(k) == 'synced' for k in ('outbox', 'draft')):
            acknowledge(box, event, 'Your weekly feedback is saved in the processing queue, but I could not confirm '
                        'completion in both Notion records. It remains pending; no draft was republished.', 'failure_ack')
        raise
