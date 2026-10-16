import copy
import datetime as dt
import json
import tempfile
import unittest
from unittest.mock import patch

import review
import tasks
from intake import Store

TCFG = dict(team_id='T1', app_id='A1', secret='secret', enabled=True, channels={'C1'}, users={'U1'},
            feedback_users={'U1'}, chat_enabled=True, review_enabled=True, review_channels={'CSUB'},
            review_bots={'BHUB'}, review_mode='thread', shadow_channel='CSHADOW', summary_channel='CSUM',
            tasks_enabled=True, tasks_source='TASKS', partners_source='PARTNERS', task_owner='OWNER1')
FORWARD = {'is_share': True, 'author_name': 'Eric Sun', 'from_url':
           'https://runpod.slack.com/archives/C09C9U6UC2G/p1792023886430509?thread_ts=1791393982.618629',
           'text': 'Adding the link to Comfy template on your hub seems to be reasonable first step',
           'channel_id': 'C09C9U6UC2G', 'ts': '1792023886.430509'}
MENTION = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='EvT', event=dict(
    type='app_mention', channel='C1', user='U1', ts='70.1',
    text='<@UBOT> can you flag this as a task in the working tasks database?', attachments=[FORWARD]))
DRAFT = {'item': 'Follow up with Comfy (Eric Sun) on the Hub template link', 'type': 'Task',
         'context': '- Eric says the deploy flow is tested\n- He suggests linking the Comfy template on the Hub',
         'next_step': 'Confirm the Hub template link as the first step with Eric',
         'definition_of_done': 'Hub template links to the Comfy deploy page', 'whos_waiting': 'Eric Sun (Comfy)',
         'owning_function': 'Partnerships', 'partner': 'Comfy', 'due_date': '', 'confidence': 0.9}


def mention(**changes):
    b = copy.deepcopy(MENTION)
    b['event'].update(changes)
    return b


class FakeClients:
    def __init__(self, reply=None):
        self.said, self.updates, self.ephemerals, self.reply = [], [], [], reply or DRAFT
        self.calls = []

    def _call(self, payload):
        self.calls.append(payload)
        return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(self.reply)}]}

    def permalink(self, channel, ts):
        return 'https://runpod.slack.com/archives/%s/p%s' % (channel, ts.replace('.', ''))

    def say_blocks(self, channel, thread_ts, text, blocks=None):
        self.said.append((channel, thread_ts, text, blocks))

    def update_blocks(self, channel, ts, text, blocks):
        self.updates.append((channel, ts, text, blocks))

    def ephemeral(self, event, text):
        self.ephemerals.append(text)


class FakeWriter:
    def __init__(self, existing=None, fail=None):
        self.existing, self.fail, self.created, self.dropped, self.found = existing, fail, [], [], []

    def find(self, source_id):
        self.found.append(source_id)
        if self.fail == 'find':
            raise ValueError('403')
        return self.existing

    def create(self, task, source_id, link, today):
        if self.fail == 'create':
            raise ValueError('403')
        self.created.append((task, source_id, link, today))
        return 'https://www.notion.so/task1', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', True

    def drop(self, page_id, today):
        if self.fail == 'drop':
            raise ValueError('403')
        self.dropped.append((page_id, today))


class TriggerTests(unittest.TestCase):
    def test_phrasings(self):
        for text in ['<@UBOT> can you flag this as a task in the working tasks database?', '<@UBOT> add a task',
                     '<@UBOT> please make this a task', '<@UBOT> track this', '<@UBOT> task: send Eric the Hub link',
                     '<@UBOT> create a to-do for this', '<@UBOT> log this as a task please']:
            self.assertTrue(tasks.is_task_request(text), text)
        for text in ['<@UBOT> review', '<@UBOT> summarize this thread', '<@UBOT> what tasks are open?',
                     '<@UBOT> should be triage', '<@UBOT> ' + 'word ' * 50 + 'add a task']:
            self.assertFalse(tasks.is_task_request(text), text)

    def test_accepts_forward_with_source_id(self):
        e = tasks.accept_task(MENTION, TCFG)
        self.assertEqual((e['kind'], e['thread_ts'], e['user']), ('task', '70.1', 'U1'))
        self.assertEqual(e['forward']['source_id'], 'slack:C09C9U6UC2G:1792023886.430509')
        self.assertEqual(e['forward']['author'], 'Eric Sun')
        self.assertTrue(e['forward']['link'].startswith('https://runpod.slack.com/archives/C09C9U6UC2G/'))

    def test_rejects(self):
        for change in [dict(user='UOTHER'), dict(channel='COTHER'), dict(bot_id='B1'), dict(type='message'),
                       dict(text='<@UBOT> summarize')]:
            self.assertIsNone(tasks.accept_task(mention(**change), TCFG), change)
        self.assertIsNone(tasks.accept_task(MENTION, dict(TCFG, tasks_enabled=False)))

    def test_router_sends_tasks_before_chat_and_keeps_review(self):
        self.assertEqual(review.accept(MENTION, TCFG)['kind'], 'task')
        self.assertNotIn('kind', review.accept(mention(text='<@UBOT> summarize'), TCFG))
        manual = mention(channel='CSUB', thread_ts='50.1', text='<@UBOT> review', attachments=[])
        self.assertEqual(review.accept(manual, TCFG)['kind'], 'review_manual')

    def test_forward_without_slack_link_uses_channel_and_ts(self):
        att = dict(FORWARD, from_url='')
        self.assertEqual(tasks.forwarded({'attachments': [att]})['source_id'], 'slack:C09C9U6UC2G:1792023886.430509')
        self.assertIsNone(tasks.forwarded({'attachments': [{'text': 'a link preview'}]}))


class ParseTests(unittest.TestCase):
    def test_clamps_and_falls_back(self):
        t = tasks.parse_task(json.dumps(dict(DRAFT, type='Whatever', owning_function='Nope', due_date='Friday',
                                             confidence=7, item='Reply to Eric \u2014 soon')))
        self.assertEqual((t['type'], t['owning_function'], t['due_date'], t['confidence']),
                         ('Task', 'Partnerships', '', 1.0))
        self.assertNotIn('\u2014', t['item'])
        self.assertEqual(tasks.parse_task(json.dumps(dict(DRAFT, due_date='2026-10-17')))['due_date'], '2026-10-17')
        with self.assertRaises(ValueError):
            tasks.parse_task(json.dumps(dict(DRAFT, item='')))

    def test_strips_mentions(self):
        t = tasks.parse_task(json.dumps(dict(DRAFT, next_step='Ask <@U123> and <!channel> about it')))
        self.assertEqual(t['next_step'], 'Ask and about it')


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db')

    def run_task(self, body=MENTION, writer=None, clients=None):
        self.store.enqueue(tasks.accept_task(body, TCFG))
        clients = clients or FakeClients()
        tasks.process_task(self.store, self.store.next_job(), clients, TCFG, writer, now=dt.date(2026, 10, 15))
        return clients

    def test_forward_creates_one_task_and_replies_with_buttons(self):
        w = FakeWriter(); c = self.run_task(writer=w)
        task, source_id, link, today = w.created[0]
        self.assertEqual((source_id, today), ('slack:C09C9U6UC2G:1792023886.430509', '2026-10-15'))
        self.assertIn('C09C9U6UC2G', link)
        source = json.loads(c.calls[0]['messages'][0]['content'])
        self.assertEqual((source['source_author'], source['today']), ('Eric Sun', '2026-10-15'))
        self.assertIn('Comfy template', source['source_message'])
        channel, thread, text, blocks = c.said[0]
        self.assertEqual((channel, thread), ('C1', '70.1'))
        self.assertIn('Added to Working Tasks: <https://www.notion.so/task1|Follow up with Comfy', text)
        self.assertEqual([b['action_id'] for b in blocks[-1]['elements']], ['task_open', 'task_drop'])

    def test_duplicate_links_existing(self):
        w = FakeWriter(existing=('https://www.notion.so/old', 'old-id', 'Old task')); c = self.run_task(writer=w)
        self.assertEqual(w.created, [])
        self.assertIn('already in Working Tasks: <https://www.notion.so/old|Old task>', c.said[0][2])
        self.assertEqual([b['action_id'] for b in c.said[0][3][-1]['elements']], ['task_open'])

    def test_thread_source(self):
        w = FakeWriter()
        with patch('tasks.thread_text', return_value='<U2>: please send the deck by Friday') as tt:
            c = self.run_task(mention(thread_ts='60.1', attachments=[], text='<@UBOT> add a task'), writer=w)
        tt.assert_called_once_with('C1', '60.1', '70.1')
        self.assertEqual(w.created[0][1], 'slack:C1:60.1')
        self.assertEqual(c.said[0][1], '60.1')

    def test_top_level_request_is_the_task(self):
        w = FakeWriter(); c = self.run_task(mention(attachments=[], text='<@UBOT> task: send Eric the Hub link'), writer=w)
        self.assertEqual(w.created[0][1], 'slack:C1:70.1')
        self.assertEqual(json.loads(c.calls[0]['messages'][0]['content'])['source_message'], 'task: send Eric the Hub link')

    def test_failures_add_nothing_and_say_why(self):
        for fail, words in [('find', "couldn't reach Working Tasks"), ('create', "couldn't save it")]:
            store = Store(self.tmp.name + '/%s.db' % fail)
            store.enqueue(tasks.accept_task(MENTION, TCFG)); c = FakeClients(); w = FakeWriter(fail=fail)
            tasks.process_task(store, store.next_job(), c, TCFG, w)
            self.assertIn(words, c.said[0][2]); self.assertEqual(w.created, [])
        c = self.run_task(writer=None)
        self.assertIn('NOTION_WRITE', c.said[0][2])

    def test_never_retried(self):
        self.run_task(writer=FakeWriter())
        self.assertIsNone(self.store.next_job())


class ActionTests(unittest.TestCase):
    def click(self, **over):
        p = dict(type='block_actions', team={'id': 'T1'}, api_app_id='A1', user={'id': 'U1'}, channel={'id': 'C1'},
                 message={'ts': '71.1', 'text': 'Added to Working Tasks'}, response_url='https://hooks.slack.com/a/b',
                 actions=[{'action_id': 'task_drop', 'value': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
                           'action_ts': '72.1'}])
        p.update(over)
        return p

    def test_accept_drop(self):
        e = tasks.accept_task_action(self.click(), TCFG)
        self.assertEqual((e['kind'], e['page_id'], e['message_ts']),
                         ('task_action', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', '71.1'))
        self.assertIsNone(tasks.accept_task_action(self.click(user={'id': 'U9'}), TCFG))
        self.assertIsNone(tasks.accept_task_action(self.click(actions=[{'action_id': 'task_open', 'value': '',
                                                                         'action_ts': '72.1'}]), TCFG))
        self.assertIsNone(tasks.accept_task_action(self.click(actions=[{'action_id': 'task_drop', 'value': 'x',
                                                                         'action_ts': '72.1'}]), TCFG))

    def test_drop_updates_row_and_reply(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); store.enqueue(tasks.accept_task_action(self.click(), TCFG))
            c, w = FakeClients(), FakeWriter()
            tasks.process_task_action(store, store.next_job(), c, w, now=dt.date(2026, 10, 15))
            self.assertEqual(w.dropped, [('aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', '2026-10-15')])
            channel, ts, text, blocks = c.updates[0]
            self.assertEqual((channel, ts), ('C1', '71.1')); self.assertIn('Dropped by <@U1>', text)
            self.assertEqual([b['action_id'] for b in blocks[-1]['elements']], ['task_open'])

    def test_drop_failure_is_private(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); store.enqueue(tasks.accept_task_action(self.click(), TCFG))
            c = FakeClients(); tasks.process_task_action(store, store.next_job(), c, FakeWriter(fail='drop'))
            self.assertIn("wasn't changed", c.ephemerals[0]); self.assertEqual(c.updates, [])


class WriterTests(unittest.TestCase):
    @patch.dict('os.environ', {'NOTION_TOKEN': 'secret_x'})
    def test_properties_and_requests(self):
        w = tasks.TaskWriter(TCFG); calls = []
        def fake(method, path, payload=None):
            calls.append((method, path, payload))
            if path == 'data_sources/PARTNERS/query':
                return {'results': [{'id': 'partner-comfy'}]}
            if path == 'data_sources/TASKS/query':
                return {'results': []}
            return {'url': 'https://www.notion.so/new', 'id': 'new1'}
        w._req = fake
        self.assertIsNone(w.find('slack:C1:70.1'))
        self.assertEqual(calls[0][2]['filter'], {'property': 'External Source ID', 'rich_text': {'equals': 'slack:C1:70.1'}})
        task = tasks.parse_task(json.dumps(dict(DRAFT, due_date='2026-10-17')))
        self.assertEqual(w.create(task, 'slack:C1:70.1', 'https://runpod.slack.com/archives/C1/p70', '2026-10-15'),
                         ('https://www.notion.so/new', 'new1', True))
        props = calls[-1][2]['properties']
        self.assertEqual(calls[-1][2]['parent'], {'type': 'data_source_id', 'data_source_id': 'TASKS'})
        self.assertEqual((props['Status'], props['Source'], props['Capture Disposition'], props['Review Required']),
                         ({'select': {'name': 'Inbox'}}, {'select': {'name': 'Slack'}},
                          {'select': {'name': 'Create task'}}, {'checkbox': True}))
        self.assertEqual(props['Accountable Owner'], {'people': [{'id': 'OWNER1'}]})
        self.assertEqual(props['Associated Partner(s)'], {'relation': [{'id': 'partner-comfy'}]})
        self.assertEqual(props['Due Date'], {'date': {'start': '2026-10-17'}})
        self.assertEqual(props['Agent Confidence'], {'number': 0.9})
        self.assertEqual(props['Source Link'], {'url': 'https://runpod.slack.com/archives/C1/p70'})
        for never in ('Approval Owner', 'Execution Owner', 'Decision Required', 'Close Reason', 'Approval Class'):
            self.assertNotIn(never, props)

    def test_partner_only_links_a_single_match(self):
        w = tasks.TaskWriter(TCFG)
        w._req = lambda m, p, payload=None: {'results': [{'id': 'a'}, {'id': 'b'}]}
        self.assertIsNone(w.partner('Co'))
        self.assertIsNone(w.partner(''))

    def test_drop_payload(self):
        w = tasks.TaskWriter(TCFG); calls = []
        w._req = lambda m, p, payload=None: calls.append((m, p, payload)) or {}
        w.drop('pid1', '2026-10-15')
        self.assertEqual(calls[0][:2], ('PATCH', 'pages/pid1'))
        self.assertEqual(calls[0][2]['properties'], {'Status': {'select': {'name': 'Dropped'}},
                                                     'Close Reason': {'select': {'name': 'Declined'}},
                                                     'Closed At': {'date': {'start': '2026-10-15'}}})

    @patch.dict('os.environ', {'TASKS_ENABLED': 'true'})
    def test_config_defaults(self):
        cfg = tasks.config({})
        self.assertEqual((cfg['tasks_enabled'], cfg['tasks_source'], cfg['task_owner']),
                         (True, tasks.TASKS_SOURCE, tasks.OWNER_NOTION_ID))


if __name__ == '__main__':
    unittest.main()
