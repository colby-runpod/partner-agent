import copy
import hashlib
import hmac
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from intake import Application, Store, accept_event, extract, notion_payload, process, verify


def config():
    cfg = json.loads(Path('intake-routing.json').read_text())
    cfg.update(team_id='T1', app_id='A1', secret='test-secret', enabled=True)
    cfg['slack'].update(form_bot_id='BFORM', form_app_id='AFORM')
    return cfg


def event(form=False):
    cfg = config()
    return {'type': 'event_callback', 'team_id': 'T1', 'api_app_id': 'A1', 'event_id': 'E1', 'event': {
        'type': 'message', 'channel': cfg['slack']['form_notifications_channel_id' if form else 'manual_capture_channel_id'],
        'ts': '1791300000.123456', 'user': 'UHUMAN', 'text': '*Contact:* Demo Person\n*Company:* Example Inc\n*Email:* <mailto:demo@example.com|demo@example.com>\n*Stated ask:* Explore an integration.',
        **({'bot_id': 'BFORM', 'app_id': 'AFORM', 'subtype': 'bot_message'} if form else {})}}


class FakeClients:
    def __init__(self):
        self.rows = {}
        self.creates = 0
        self.replies = []
        self.ambiguous_create = False
        self.fail_reply = False

    def permalink(self, ev):
        return 'https://runpod.enterprise.slack.com/archives/' + ev['channel'] + '/p' + ev['ts'].replace('.', '')

    def find(self, ref):
        return self.rows.get(ref)

    def create(self, payload):
        self.creates += 1
        ref = payload['properties']['Intake Ref']['rich_text'][0]['text']['content']
        self.rows[ref] = 'https://www.notion.so/example'
        if self.ambiguous_create:
            raise TimeoutError()
        return self.rows[ref]

    def reply(self, ev, text):
        self.replies.append((ev['thread_ts'], text))
        if self.fail_reply:
            raise TimeoutError()


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = config()
        self.store = Store(Path(self.tmp.name) / 'intake.db')
        self.clients = FakeClients()

    def enqueue(self, body=None):
        ev = accept_event(body or event(), self.cfg)
        self.store.enqueue(ev)
        return ev

    def test_signature_and_replay(self):
        raw = b'{}'
        stamp = '1000'
        sig = 'v0=' + hmac.new(b'secret', b'v0:1000:{}', hashlib.sha256).hexdigest()
        self.assertTrue(verify('secret', stamp, sig, raw, now=1000))
        self.assertFalse(verify('secret', stamp, sig, b'bad', now=1000))
        self.assertFalse(verify('secret', stamp, sig, raw, now=1301))

    def test_filter_channels_bots_edits_and_workspace(self):
        self.assertIsNotNone(accept_event(event(True), self.cfg))
        for part, key, value in [('event', 'channel', 'COTHER'), ('event', 'bot_id', 'BSELF'), ('event', 'subtype', 'message_changed'), ('body', 'team_id', 'TOTHER')]:
            body = event()
            (body if part == 'body' else body['event'])[key] = value
            self.assertIsNone(accept_event(body, self.cfg))
        body = event(True)
        body['event']['app_id'] = 'UNTRUSTED'
        self.assertIsNone(accept_event(body, self.cfg))

    def test_unconfigured_form_sender_is_rejected(self):
        self.cfg['slack']['form_bot_id'] = None
        self.assertIsNone(accept_event(event(True), self.cfg))

    def test_event_and_message_deduplication(self):
        ev = self.enqueue()
        self.assertFalse(self.store.enqueue(ev))
        ev['event_id'] = 'E2'
        self.assertFalse(self.store.enqueue(ev))

    def test_extract_and_schema(self):
        ev = accept_event(event(True), self.cfg)
        data = extract(ev)
        self.assertEqual(data['email'], 'demo@example.com')
        payload = notion_payload(data, ev, 'https://slack.com/example', self.cfg)
        self.assertEqual(payload['properties']['Status']['select']['name'], 'New')
        self.assertEqual(payload['parent']['data_source_id'], self.cfg['notion']['data_source_id'])
        self.assertNotIn('Submitted', payload['properties'])
        self.assertNotIn('Google Form Completed', payload['properties'])

    def test_hubspot_digest_name_maps_to_contact(self):
        body = event(True)
        body['event']['text'] = ('HubSpot: Incoming Partnership Request\n\nName: Demo Person\n'
                                 'Email: <mailto:demo@example.com|demo@example.com>\nCompany: Example Inc\n'
                                 'Reason: \nHow can we help?: Explore an integration.')
        data = extract(accept_event(body, self.cfg))
        self.assertEqual(data['contact'], 'Demo Person')
        self.assertEqual(data['company'], 'Example Inc')
        self.assertEqual(data['email'], 'demo@example.com')

    def test_explicit_contact_wins_over_name(self):
        body = event()
        body['event']['text'] = '*Name:* Form Name\n*Contact:* Real Person\n*Company:* Example Inc'
        self.assertEqual(extract(accept_event(body, self.cfg))['contact'], 'Real Person')

    def test_block_message_extraction(self):
        body = event(True)
        body['event'].pop('text')
        body['event']['blocks'] = [{'type': 'section', 'text': {'type': 'mrkdwn', 'text': '*Contact:* Demo Person\n*Company:* Example Inc'}}]
        self.assertEqual(extract(accept_event(body, self.cfg))['company'], 'Example Inc')

    def test_end_to_end_create_and_thread_reply(self):
        ev = self.enqueue()
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.store.get(ev['key'])['state'], 'done')
        self.assertEqual(self.clients.creates, 1)
        self.assertEqual(self.clients.replies[0][0], ev['ts'])
        self.assertIsNone(self.store.next_job())

    def test_existing_notion_entry_reused(self):
        ev = self.enqueue()
        self.clients.rows[self.clients.permalink(ev)] = 'https://notion.so/existing'
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.clients.creates, 0)
        self.assertIn('existing', self.clients.replies[0][1])

    def test_ambiguous_create_reconciled_without_second_write(self):
        ev = self.enqueue()
        self.clients.ambiguous_create = True
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.store.get(ev['key'])['state'], 'writing')
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.clients.creates, 1)
        self.assertEqual(self.store.get(ev['key'])['state'], 'done')

    def test_restart_after_uncertain_write_never_recreates(self):
        ev = self.enqueue()
        self.store.update(ev['key'], state='writing', ref=self.clients.permalink(ev))
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.clients.creates, 0)
        self.assertEqual(self.store.get(ev['key'])['state'], 'review')

    def test_reply_failure_preserves_created_record(self):
        ev = self.enqueue()
        self.clients.fail_reply = True
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.store.get(ev['key'])['state'], 'done')
        self.assertEqual(self.store.get(ev['key'])['reply_state'], 'review')
        self.assertIsNone(self.store.next_job())

    def test_vague_request_gets_clarification(self):
        body = event()
        body['event']['text'] = 'can you send this to the inbound queue?'
        ev = self.enqueue(body)
        process(self.store, self.store.next_job(), self.clients, self.cfg)
        self.assertEqual(self.clients.creates, 0)
        self.assertEqual(self.store.get(ev['key'])['state'], 'needs_info')

    def test_signed_http_request_is_persisted_before_ack(self):
        app = Application(self.cfg, self.store)
        raw = json.dumps(event()).encode()
        stamp = str(int(time.time()))
        sig = 'v0=' + hmac.new(self.cfg['secret'].encode(), b'v0:' + stamp.encode() + b':' + raw, hashlib.sha256).hexdigest()
        env = {'PATH_INFO': '/slack/events', 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(raw)),
               'wsgi.input': io.BytesIO(raw), 'HTTP_X_SLACK_REQUEST_TIMESTAMP': stamp, 'HTTP_X_SLACK_SIGNATURE': sig}
        statuses = []
        app(env, lambda status, headers: statuses.append(status))
        self.assertEqual(statuses, ['200 OK'])
        self.assertIsNotNone(self.store.next_job())
        env['wsgi.input'] = io.BytesIO(raw)
        env['HTTP_X_SLACK_SIGNATURE'] = 'invalid'
        app(env, lambda status, headers: statuses.append(status))
        self.assertEqual(statuses[-1], '401 Unauthorized')


if __name__ == '__main__':
    unittest.main()
