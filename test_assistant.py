import copy
import json
import tempfile
import unittest
from unittest.mock import patch

import assistant as agent
import review
import tasks
from intake import Store

ACFG = dict(team_id='T1', app_id='A1', secret='secret', enabled=True, channels={'C1'}, users={'U1'},
            feedback_users={'U1'}, chat_enabled=True, review_enabled=True, review_channels={'CSUB'},
            review_bots={'BHUB'}, review_mode='thread', shadow_channel='CSHADOW', summary_channel='CSUM',
            tasks_enabled=True, tasks_source='TASKS', partners_source='PARTNERS', task_owner='OWNER1',
            agent_enabled=True, agent_channels={'C1'}, agent_web_search=True,
            notion_sources={n: d for n, (d, _) in agent.SOURCES.items()})
FORWARD = {'is_share': True, 'author_name': 'Eric Sun', 'text': 'Adding the link to Comfy template on your hub',
           'from_url': 'https://runpod.slack.com/archives/C09C9U6UC2G/p1792023886430509'}
ASK = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='EvA', event=dict(
    type='app_mention', channel='C1', user='U1', ts='80.1', text='<@UBOT> have we talked to Comfy before? what is open?',
    attachments=[FORWARD]))


def ask(**changes):
    b = copy.deepcopy(ASK)
    b['event'].update(changes)
    return b


def tool_use(name, args, id_='t1'):
    return {'stop_reason': 'tool_use', 'content': [{'type': 'text', 'text': 'Checking.'},
                                                   {'type': 'tool_use', 'id': id_, 'name': name, 'input': args}]}


def final(text):
    return {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': text}]}


class Clients:
    def __init__(self, script):
        self.script, self.payloads, self.said = list(script), [], []

    def _call(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return self.script.pop(0)

    def permalink(self, channel, ts):
        return 'https://runpod.slack.com/archives/%s/p%s' % (channel, ts.replace('.', ''))

    def say_blocks(self, channel, thread_ts, text, blocks=None):
        self.said.append((channel, thread_ts, text, blocks))


class Chat:
    def thread(self, event):
        return [{'user': 'U2', 'text': 'first'}, {'user': 'U1', 'text': 'second'}]


class Writer:
    def __init__(self, existing=None):
        self.existing, self.created = existing, []

    def find(self, source_id):
        return self.existing

    def create(self, task, source_id, link, today):
        self.created.append((task, source_id, link))
        return 'https://www.notion.so/task1', 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', True


PARTNER_PAGE = {'url': 'https://www.notion.so/comfy', 'properties': {
    'Partner Name': {'type': 'title', 'title': [{'plain_text': 'Comfy'}]},
    'Status': {'type': 'select', 'select': {'name': 'Active'}},
    'Website': {'type': 'url', 'url': 'https://comfy.org'},
    'Tier': {'type': 'select', 'select': None}}}


def fake_notion(calls):
    def fake(url, token, payload=None, version=None):
        calls.append((url.split('/v1/')[1], payload))
        if url.endswith('/data_sources/90430d4f-99ca-4925-9df3-72abc1b609b7'):
            return {'properties': {'Partner Name': {'type': 'title'}, 'Website': {'type': 'url'},
                                   'Status': {'type': 'select'}}}
        if '/query' in url:
            return {'results': [PARTNER_PAGE]}
        if '/blocks/' in url:
            return {'results': [{'type': 'paragraph', 'paragraph': {'rich_text': [{'plain_text': 'Kickoff notes'}]}}]}
        return PARTNER_PAGE
    return fake


class AcceptTests(unittest.TestCase):
    def test_accepts_and_routes(self):
        e = agent.accept_agent(ASK, ACFG)
        self.assertEqual((e['kind'], e['thread_ts'], e['forward']['author']), ('agent', '80.1', 'Eric Sun'))
        self.assertEqual(review.accept(ASK, ACFG)['kind'], 'agent')

    def test_tasks_and_reviews_keep_their_fast_paths(self):
        self.assertEqual(review.accept(ask(text='<@UBOT> flag this as a task'), ACFG)['kind'], 'task')
        manual = ask(channel='CSUB', thread_ts='50.1', text='<@UBOT> review', attachments=[])
        self.assertEqual(review.accept(manual, ACFG)['kind'], 'review_manual')

    def test_off_or_wrong_place_falls_back_to_chat(self):
        self.assertNotIn('kind', review.accept(ASK, dict(ACFG, agent_enabled=False)))
        for change in [dict(user='U9'), dict(channel='C9'), dict(bot_id='B1')]:
            self.assertIsNone(agent.accept_agent(ask(**change), ACFG), change)

    @patch.dict('os.environ', {'AGENT_ENABLED': 'true'})
    def test_config_defaults_to_chat_channels(self):
        cfg = agent.config({'channels': {'C1', 'C2'}})
        self.assertEqual((cfg['agent_enabled'], cfg['agent_channels']), (True, {'C1', 'C2'}))
        self.assertEqual(cfg['notion_sources']['tasks'], 'e77648f6-502d-450f-8b79-4dd2ced52bab')


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db')

    def run_agent(self, script, writer=None, body=ASK, env=None):
        self.store.enqueue(agent.accept_agent(body, ACFG))
        c = Clients(script)
        with patch.dict('os.environ', env or {'NOTION_TOKEN': 'secret_x', 'ANTHROPIC_MODEL': 'm'}):
            agent.process_agent(self.store, self.store.next_job(), c, Chat(), ACFG, writer)
        return c

    @patch('assistant.api')
    def test_search_then_answer(self, api):
        calls = []; api.side_effect = fake_notion(calls)
        c = self.run_agent([tool_use('notion_search', {'database': 'partners', 'query': 'Comfy'}),
                            final('Yes, Comfy is an active partner. <https://www.notion.so/comfy|Comfy>')])
        path, payload = calls[-1]
        self.assertEqual(path, 'data_sources/90430d4f-99ca-4925-9df3-72abc1b609b7/query')
        self.assertIn({'property': 'Partner Name', 'title': {'contains': 'Comfy'}}, payload['filter']['or'])
        self.assertIn({'property': 'Website', 'url': {'contains': 'Comfy'}}, payload['filter']['or'])
        result = json.loads(c.payloads[1]['messages'][-1]['content'][0]['content'])
        self.assertEqual(result['rows'][0]['fields'], {'Partner Name': 'Comfy', 'Status': 'Active', 'Website': 'https://comfy.org'})
        channel, thread, text, blocks = c.said[0]
        self.assertEqual((channel, thread, blocks), ('C1', '80.1', None))
        self.assertTrue(text.startswith('<@U1> Yes, Comfy is an active partner.'))
        self.assertIn('_Used: Notion search_', text)
        first = json.loads(c.payloads[0]['messages'][0]['content'])
        self.assertEqual(first['forwarded_message']['author'], 'Eric Sun')
        self.assertIn('web_search', [t['name'] for t in c.payloads[0]['tools']])

    @patch('assistant.api')
    def test_notion_get_reads_fields_and_body(self, api):
        calls = []; api.side_effect = fake_notion(calls)
        c = self.run_agent([tool_use('notion_get', {'page': 'https://www.notion.so/Comfy-3f9ff732fc348165bc29db22b0a35473'}),
                            final('Done.')])
        self.assertEqual(calls[0][0], 'pages/3f9ff732-fc34-8165-bc29-db22b0a35473')
        result = json.loads(c.payloads[1]['messages'][-1]['content'][0]['content'])
        self.assertEqual(result['body'], 'Kickoff notes')

    def test_create_task_from_forward_with_drop_button(self):
        w = Writer()
        c = self.run_agent([tool_use('create_task', {'item': 'Follow up with Comfy on the Hub link',
                                                     'next_step': 'Confirm the link with Eric', 'partner': 'Comfy'}),
                            tool_use('create_task', {'item': 'Second', 'next_step': 'x'}, 't2'),
                            final('Added a task. <https://www.notion.so/task1|Follow up with Comfy>')], writer=w)
        self.assertEqual(len(w.created), 1)
        self.assertEqual(w.created[0][1], 'slack:C09C9U6UC2G:1792023886.430509')
        second = json.loads(c.payloads[2]['messages'][-1]['content'][0]['content'])
        self.assertIn('already created', second['error'])
        blocks = c.said[0][3]
        self.assertEqual([b['action_id'] for b in blocks[-1]['elements']], ['task_open', 'task_drop'])
        self.assertEqual(blocks[-1]['elements'][1]['value'], 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee')
        self.assertIn('_Used: task created_', c.said[0][2])

    def test_existing_task_is_linked_not_duplicated(self):
        w = Writer(existing=('https://www.notion.so/old', 'old', 'Old task'))
        c = self.run_agent([tool_use('create_task', {'item': 'X', 'next_step': 'Y'}), final('Already tracked.')], writer=w)
        self.assertEqual(w.created, [])
        self.assertTrue(json.loads(c.payloads[1]['messages'][-1]['content'][0]['content'])['already_tracked'])

    def test_task_tool_off_says_so(self):
        c = self.run_agent([tool_use('create_task', {'item': 'X', 'next_step': 'Y'}), final('Could not.')], writer=None)
        self.assertIn('Task capture is off', c.payloads[1]['messages'][-1]['content'][0]['content'])

    def test_read_thread_outside_thread(self):
        c = self.run_agent([tool_use('read_thread', {}), final('ok')])
        self.assertIn('Not in a thread', c.payloads[1]['messages'][-1]['content'][0]['content'])
        c = self.run_agent([tool_use('read_thread', {}), final('ok')], body=dict(ask(ts='81.1', thread_ts='60.1'), event_id='EvB'))
        self.assertIn('second', c.payloads[1]['messages'][-1]['content'][0]['content'])
        self.assertEqual(c.said[0][1], '60.1')

    def test_bad_tool_input_and_unknown_db(self):
        with patch('assistant.api', side_effect=fake_notion([])):
            c = self.run_agent([tool_use('notion_search', {'database': 'hr', 'query': 'x'}),
                                tool_use('notion_search', {'nope': 1}, 't2'), final('ok')])
        self.assertIn('Unknown database', c.payloads[1]['messages'][-1]['content'][0]['content'])
        self.assertIn('Bad arguments', c.payloads[2]['messages'][-1]['content'][0]['content'])

    def test_notion_failure_is_reported_to_model_not_user(self):
        with patch('assistant.api', side_effect=ValueError('403')):
            c = self.run_agent([tool_use('notion_search', {'database': 'partners', 'query': 'x'}), final('ok')])
        self.assertIn('may not be shared', c.payloads[1]['messages'][-1]['content'][0]['content'])

    def test_turn_limit_forces_an_answer(self):
        script = [tool_use('read_thread', {}, 't%d' % i) for i in range(agent.MAX_TURNS)] + [final('Partial answer.')]
        c = self.run_agent(script)
        self.assertEqual(c.payloads[-1]['tool_choice'], {'type': 'none'})
        self.assertIn('Partial answer.', c.said[0][2])

    def test_pause_turn_continues(self):
        c = self.run_agent([{'stop_reason': 'pause_turn', 'content': [{'type': 'server_tool_use', 'id': 's1'}]},
                            final('Found it.')])
        self.assertEqual(c.payloads[1]['messages'][-1]['role'], 'assistant')
        self.assertIn('Found it.', c.said[0][2])

    def test_failure_message_and_no_retry(self):
        c = self.run_agent([{'stop_reason': 'end_turn', 'content': []}])
        self.assertIn("I couldn't finish that. Nothing was changed.", c.said[0][2])
        self.assertIsNone(self.store.next_job())

    def test_model_text_cannot_ping(self):
        c = self.run_agent([final('Ask <!channel> and <@U77> -- or <#C9|general> \u2014 now')])
        self.assertNotIn('<!channel>', c.said[0][2]); self.assertNotIn('<@U77>', c.said[0][2])
        self.assertNotIn('\u2014', c.said[0][2])


class PlainTests(unittest.TestCase):
    def test_property_types(self):
        P = lambda t, v: agent.plain({'type': t, t: v})
        self.assertEqual(P('multi_select', [{'name': 'A'}, {'name': 'B'}]), 'A, B')
        self.assertEqual(P('date', {'start': '2026-10-15'}), '2026-10-15')
        self.assertEqual(P('people', [{'name': 'Colby'}]), 'Colby')
        self.assertEqual(P('checkbox', True), 'yes'); self.assertEqual(P('checkbox', False), '')
        self.assertEqual(P('relation', [{'id': 'a'}, {'id': 'b'}]), '2 linked')
        self.assertEqual(P('formula', {'type': 'number', 'number': 3}), '3')
        self.assertEqual(P('status', {'name': 'In Progress'}), 'In Progress')
        self.assertEqual(P('rollup', {'x': 1}), '')



class ContextTests(unittest.TestCase):
    FWD = {'is_share': True, 'author_name': 'Rei', 'text': 'Still listing the product.',
           'from_url': 'https://runpod-team.slack.com/archives/C09UGSU62R4/p1792167034178459?thread_ts=1792160000.000100&cid=C09UGSU62R4',
           'files': [{'url_private': 'https://files.slack.com/a.png', 'mimetype': 'image/png', 'size': 1000, 'name': 'a.png'},
                     {'url_private': 'https://evil.example/b.png', 'mimetype': 'image/png', 'size': 10},
                     {'url_private': 'https://files.slack.com/c.pdf', 'mimetype': 'application/pdf', 'size': 10}]}

    def test_forward_parses_thread_and_files(self):
        f = tasks.forwarded({'attachments': [self.FWD]})
        self.assertEqual((f['channel'], f['ts'], f['thread_ts']), ('C09UGSU62R4', '1792167034.178459', '1792160000.000100'))
        self.assertEqual([x['url'] for x in f['files']], ['https://files.slack.com/a.png'])
        plain = dict(self.FWD, from_url='https://runpod.slack.com/archives/C1/p1792167034178459')
        self.assertEqual(tasks.forwarded({'attachments': [plain]})['thread_ts'], '1792167034.178459')

    def test_only_read_methods(self):
        with self.assertRaises(ValueError):
            agent.Context(ACFG)._read('chat.postMessage', {})

    @patch.dict('os.environ', {'SLACK_USER_TOKEN': 'xoxp-user', 'SLACK_BOT_TOKEN': 'xoxb-bot'})
    def test_thread_prefers_user_token_and_names_people(self):
        calls = []
        def fake(url, token, payload=None, version=None):
            calls.append((url.split('/api/')[1].split('?')[0], token))
            if 'users.info' in url:
                return {'user': {'team_id': 'TEXT', 'is_stranger': True, 'profile': {'real_name': 'Rei Mendes', 'title': 'Partnerships, Suger'}}}
            if token == 'xoxp-user':
                return {'messages': [{'user': 'U9', 'ts': '1.1', 'text': 'Started listing',
                                      'files': [{'url_private': 'https://files.slack.com/s.png', 'mimetype': 'image/png', 'size': 5}]},
                                     {'bot_id': 'B1', 'username': 'HubSpot', 'ts': '1.2', 'text': 'digest'}]}
        with patch('assistant.api', side_effect=fake):
            ctx = agent.Context(ACFG)
            messages, files = ctx.thread('C9', '1.1')
            ctx.person('U9')
        self.assertEqual(calls[0], ('conversations.replies', 'xoxp-user'))
        self.assertEqual(messages[0]['from'], {'name': 'Rei Mendes', 'title': 'Partnerships, Suger', 'external': True})
        self.assertEqual(messages[1]['from'], {'bot': 'HubSpot'})
        self.assertEqual(files[0]['url'], 'https://files.slack.com/s.png')
        self.assertEqual(sum(1 for c in calls if c[0] == 'users.info'), 1)  # cached

    @patch.dict('os.environ', {'SLACK_USER_TOKEN': 'xoxp-user', 'SLACK_BOT_TOKEN': 'xoxb-bot'})
    def test_falls_back_to_bot_token(self):
        def fake(url, token, payload=None, version=None):
            if token == 'xoxp-user':
                raise ValueError('not_in_channel')
            return {'messages': [{'user': '', 'ts': '1.1', 'text': 'hi'}]}
        with patch('assistant.api', side_effect=fake):
            messages, _ = agent.Context(ACFG).thread('C9', '1.1')
        self.assertEqual(messages[0]['text'], 'hi')

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN': 'xoxb-bot'}, clear=True)
    def test_image_download(self):
        class Resp:
            def __init__(self, kind): self.headers = {'Content-Type': kind}
            def read(self, n): return b'PNGDATA'
            def __enter__(self): return self
            def __exit__(self, *a): return False
        ctx = agent.Context(ACFG)
        with patch('assistant.urllib.request.urlopen', return_value=Resp('image/png')):
            block = ctx.image({'url': 'https://files.slack.com/a.png', 'mimetype': 'image/png'})
        self.assertEqual(block['source']['data'], 'UE5HREFUQQ==')
        with patch('assistant.urllib.request.urlopen', return_value=Resp('text/html')):
            self.assertIsNone(ctx.image({'url': 'https://files.slack.com/a.png', 'mimetype': 'image/png'}))
        self.assertIsNone(ctx.image({'url': 'https://evil.example/a.png', 'mimetype': 'image/png'}))

    def test_gather_paths(self):
        class Ctx:
            def __init__(self, fail=False): self.fail, self.asked = fail, []
            def thread(self, channel, ts, latest=None):
                self.asked.append((channel, ts, latest))
                if self.fail: raise ValueError('no access')
                return [{'from': {'name': 'Rei'}, 'text': 'full thread'}], [{'url': 'https://files.slack.com/t.png', 'mimetype': 'image/png'}]
            def image(self, ref): return {'type': 'image', 'source': {'url': ref['url']}}
        fwd = tasks.forwarded({'attachments': [self.FWD]})
        event = dict(channel='C1', ts='80.1', thread_ts='80.1', forward=fwd,
                     files=[{'url': 'https://files.slack.com/m.png', 'mimetype': 'image/png'}] * 3)
        c = Ctx(); found, blocks = agent.gather(event, c)
        self.assertEqual(c.asked, [('C09UGSU62R4', '1792160000.000100', None)])
        self.assertEqual(found['source'][0]['text'], 'full thread')
        self.assertEqual(len(blocks), 3)  # deduped: m.png, a.png, t.png
        c = Ctx(fail=True); found, _ = agent.gather(event, c)
        self.assertIsNone(found['source']); self.assertIn("Couldn't open the original thread", found['note'])
        c = Ctx(); found, _ = agent.gather(dict(channel='C1', ts='81.1', thread_ts='60.1', forward=None, files=[]), c)
        self.assertEqual(c.asked, [('C1', '60.1', '81.1')])

    def test_images_and_thread_reach_the_model(self):
        c = Clients([final('Suger began listing. *Recommended next step:* update the GCP task.')])
        event = agent.accept_agent(ASK, ACFG)
        tools = agent.Tools(ACFG, event, c, Chat(), None)
        found = {'source': [{'from': {'name': 'Rei'}, 'text': 'x'}], 'note': 'n', 'images': 1}
        agent.run_agent(event, c, tools, ACFG, found, [{'type': 'image', 'source': {}}])
        content = c.payloads[0]['messages'][0]['content']
        self.assertEqual([b['type'] for b in content], ['text', 'image'])
        self.assertEqual(json.loads(content[0]['text'])['source_thread'][0]['from']['name'], 'Rei')
        self.assertIn('Recommended next step', agent.AGENT_INSTRUCTIONS)

    def test_process_agent_gathers_context(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); store.enqueue(agent.accept_agent(ASK, ACFG)); c = Clients([final('ok')])
            with patch('assistant.gather', return_value=({'source': None, 'note': 'only forward', 'images': 0}, [])) as g:
                agent.process_agent(store, store.next_job(), c, Chat(), ACFG, None)
            g.assert_called_once()
            self.assertEqual(json.loads(c.payloads[0]['messages'][0]['content'])['context_note'], 'only forward')

    def test_task_capture_uses_original_thread(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q')
            body = dict(ASK, event=dict(ASK['event'], text='<@UBOT> flag this as a task', attachments=[self.FWD]))
            store.enqueue(tasks.accept_task(body, ACFG)); c = Clients([final(json.dumps(
                {'item': 'Follow up with Suger on GCP listing', 'next_step': 'Ask Rei for the next milestone'}))])
            w = Writer()
            with patch('assistant.Context.thread', return_value=([{'from': {'name': 'Rei'}, 'text': 'thread detail'}], [])):
                tasks.process_task(store, store.next_job(), c, ACFG, w)
            sent = json.loads(c.payloads[0]['messages'][0]['content'])['source_message']
            self.assertIn('thread detail', sent)
            self.assertEqual(w.created[0][1], 'slack:C09UGSU62R4:1792167034.178459')



class WeeklyFlagTests(unittest.TestCase):
    TASK = 'https://app.notion.com/p/GCP-3-Start-GCP-Producer-Portal-listing-setup-397ff732fc34816fb9edc28efbb44273'

    class FlagWriter(Writer):
        def __init__(self, not_task=False):
            super().__init__(); self.flags, self.not_task = [], not_task
        def flag_weekly(self, page_id, note, today):
            if self.not_task: raise tasks.NotATask()
            self.flags.append((page_id, note)); return 'https://www.notion.so/gcp3', 'GCP #3 Start listing'

    def run_agent(self, script, writer):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q')
            store.enqueue(agent.accept_agent(dict(ASK, event=dict(ASK['event'], text=
                "<@UBOT> flag this for next week's partnership update"), event_id='EvW'), ACFG))
            c = Clients(script)
            with patch.dict('os.environ', {'NOTION_TOKEN': 'x', 'ANTHROPIC_MODEL': 'm'}):
                agent.process_agent(store, store.next_job(), c, Chat(), ACFG, writer)
            return c

    def test_flags_existing_task_with_unflag_button(self):
        w = self.FlagWriter()
        c = self.run_agent([tool_use('flag_for_weekly', {'page': self.TASK, 'note': 'Suger submitted the listing for review'}),
                            tool_use('flag_for_weekly', {'page': self.TASK, 'note': 'again'}, 't2'),
                            final('Flagged GCP #3 for the weekly update.')], w)
        self.assertEqual(w.flags, [('397ff732-fc34-816f-b9ed-c28efbb44273', 'Suger submitted the listing for review')])
        self.assertEqual(w.created, [])
        second = json.loads(c.payloads[2]['messages'][-1]['content'][0]['content'])
        self.assertTrue(second['already_flagged_this_run'])
        channel, thread, text, blocks = c.said[0]
        self.assertIn('_Used: weekly flag_', text)
        self.assertEqual([b['action_id'] for b in blocks[-1]['elements']], ['task_open', 'task_unflag'])
        self.assertEqual(blocks[-1]['elements'][1]['value'], '397ff732-fc34-816f-b9ed-c28efbb44273')

    def test_non_task_rows_are_refused(self):
        c = self.run_agent([tool_use('flag_for_weekly', {'page': self.TASK, 'note': 'x'}), final('ok')],
                           self.FlagWriter(not_task=True))
        self.assertIn('Only Working Tasks rows', c.payloads[1]['messages'][-1]['content'][0]['content'])
        self.assertIsNone(c.said[0][3])

    def test_intent_rules_in_prompt(self):
        for phrase in ('flag_for_weekly on the EXISTING Working Task', 'Never create a task for this',
                       'end with one short question instead of guessing', '"track this / remind me'):
            self.assertIn(phrase, agent.AGENT_INSTRUCTIONS)

    def test_writer_flag_payload(self):
        w = tasks.TaskWriter(dict(ACFG, tasks_source='e77648f6-502d-450f-8b79-4dd2ced52bab')); calls = []
        page = {'url': 'https://www.notion.so/gcp3', 'parent': {'type': 'data_source_id',
                'data_source_id': 'e77648f6-502d-450f-8b79-4dd2ced52bab'}, 'properties': {
                'Item': {'title': [{'plain_text': 'GCP #3'}]}, 'Context': {'rich_text': [{'plain_text': 'Older context'}]}}}
        w._req = lambda m, p, payload=None: calls.append((m, p, payload)) or (page if m == 'GET' else {})
        self.assertEqual(w.flag_weekly('pid', 'Reviews submitted', '2026-10-16'), ('https://www.notion.so/gcp3', 'GCP #3'))
        props = calls[1][2]['properties']
        self.assertEqual(props['Weekly'], {'checkbox': True})
        self.assertEqual(props['Context']['rich_text'][0]['text']['content'], '10/16 weekly: Reviews submitted\nOlder context')
        page['parent'] = {'data_source_id': 'other'}
        with self.assertRaises(tasks.NotATask):
            w.flag_weekly('pid', 'x', '2026-10-16')
        w.unflag('pid')
        self.assertEqual(calls[-1][2], {'properties': {'Weekly': {'checkbox': False}}})

    def test_unflag_button(self):
        class W:
            def __init__(self): self.unflagged = []
            def unflag(self, pid): self.unflagged.append(pid)
        payload = dict(type='block_actions', team={'id': 'T1'}, api_app_id='A1', user={'id': 'U1'}, channel={'id': 'C1'},
                       message={'ts': '71.1', 'text': 'Flagged GCP #3'}, response_url='https://hooks.slack.com/a/b',
                       actions=[{'action_id': 'task_unflag', 'value': 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', 'action_ts': '72.1'}])
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); store.enqueue(tasks.accept_task_action(payload, ACFG))
            w = W(); updates = []
            c = type('C', (), {'update_blocks': lambda self, *a: updates.append(a), 'ephemeral': lambda self, *a: None})()
            tasks.process_task_action(store, store.next_job(), c, w)
        self.assertEqual(w.unflagged, ['aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'])
        self.assertIn('Weekly flag removed by <@U1>', updates[0][2])
        self.assertEqual([b['action_id'] for b in updates[0][3][-1]['elements']], ['task_open'])


if __name__ == '__main__':
    unittest.main()
