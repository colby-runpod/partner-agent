import copy
import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch

import review
from intake import Application, Store

DIGEST = ('*PARTNERSHIP MQL DIGEST*\n*Source form:* Runpod Partner Program: Runpod Partner Request\n'
          '*Contact:* Demo Person\n*Email:* <mailto:demo@example.com|demo@example.com>\n*Company:* Example Inc\n'
          '*Latest partnership response:*\n- Ask: reseller partnership for EU customers.')
CFG = dict(team_id='T1', app_id='A1', secret='secret', enabled=True, channels={'C1'}, users={'U1'},
           chat_enabled=True, review_enabled=True, review_channels={'CSUB'}, review_bots={'BHUB'},
           review_mode='shadow', shadow_channel='CSHADOW', web_search=True, summary_channel='CSUM',
           summary_enabled=True, summary_hour=17, key_expires='')
BODY = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='Ev9', event=dict(
    type='message', channel='CSUB', bot_id='BHUB', ts='50.1', text=DIGEST))
MENTION = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='Ev1', event=dict(
    type='app_mention', channel='C1', user='U1', ts='12.2', thread_ts='12.1', text='<@UBOT> summarize'))
GOOD = {'company': 'Example Inc', 'contact': 'Demo Person', 'partnership_type': 'Reseller/VAR',
        'route': 'Partnerships', 'score': '4', 'decision': 'pursue',
        'checks': {'identity': {'verdict': 'verified', 'note': 'LinkedIn matches'},
                   'company': {'verdict': 'verified', 'note': 'registry and press'},
                   'fit': {'verdict': 'verified', 'note': 'reseller'},
                   'potential': {'verdict': 'unverified', 'note': 'no volume stated'}},
        'summary': 'Example Inc wants to resell Runpod in the EU.', 'why': 'Real company, clear motion.',
        'next_step': 'Invite to apply once you approve', 'risks': ['No volume stated'],
        'sources': [{'title': 'Registry', 'url': 'https://example.com/registry'}]}


class Fake:
    def __init__(self, result='card'):
        self.result, self.posted, self.calls = result, [], 0

    def review(self, event):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def post(self, event, text):
        self.posted.append((event['ts'], text))


def body(**changes):
    b = copy.deepcopy(BODY)
    b['event'].update(changes)
    return b


class FilterTests(unittest.TestCase):
    def test_accepts_top_level_hubspot_digest(self):
        e = review.accept_review(BODY, CFG)
        self.assertEqual((e['kind'], e['thread_ts'], e['key']), ('review', '50.1', 'T1:CSUB:50.1'))
        self.assertEqual(review.accept_review(body(subtype='bot_message'), CFG)['kind'], 'review')
        self.assertIsNotNone(review.accept_review(body(thread_ts='50.1'), CFG))

    def test_rejects_everything_else(self):
        for change in [dict(bot_id='BOTHER'), dict(channel='COTHER'), dict(thread_ts='49.0'),
                       dict(text='hello team'), dict(subtype='message_changed'), dict(type='app_mention'),
                       dict(ts='bad')]:
            self.assertIsNone(review.accept_review(body(**change), CFG), change)
        b = copy.deepcopy(BODY); b['team_id'] = 'TOTHER'
        self.assertIsNone(review.accept_review(b, CFG))
        self.assertIsNone(review.accept_review(BODY, dict(CFG, review_enabled=False)))

    def test_combined_filter_keeps_chat(self):
        self.assertEqual(review.accept(MENTION, CFG)['thread_ts'], '12.1')
        self.assertIsNone(review.accept(MENTION, dict(CFG, chat_enabled=False)))
        self.assertEqual(review.accept(BODY, CFG)['kind'], 'review')

    def test_signed_request_queues_once(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q.db'); app = Application(CFG, store, review.accept)
            raw = json.dumps(BODY).encode(); ts = str(int(time.time()))
            sig = 'v0=' + hmac.new(b'secret', b'v0:' + ts.encode() + b':' + raw, hashlib.sha256).hexdigest()
            for _ in range(2):
                codes = []
                app(dict(PATH_INFO='/slack/events', REQUEST_METHOD='POST', CONTENT_LENGTH=str(len(raw)),
                         HTTP_X_SLACK_REQUEST_TIMESTAMP=ts, HTTP_X_SLACK_SIGNATURE=sig,
                         **{'wsgi.input': io.BytesIO(raw)}), lambda code, headers: codes.append(code))
                self.assertEqual(codes, ['200 OK'])
            job = store.next_job()
            self.assertEqual(json.loads(job['payload'])['kind'], 'review')
            store.update(job['key'], state='done')
            self.assertIsNone(store.next_job())


class CardTests(unittest.TestCase):
    def test_render_good_review(self):
        card = review.render(review.parse('Here you go: ' + json.dumps(GOOD) + ' done'))
        for part in [':large_green_circle: Pursue  ·  Score 4  ·  Partnerships', '*Partner review · Example Inc*',
                     'Pursue', 'Identity: verified - LinkedIn matches', 'Invite to apply',
                     '<https://example.com/registry|Registry>', 'Advisory only']:
            self.assertIn(part, card)
        self.assertNotIn('\u2014', card)

    def test_untrusted_values_are_neutralised(self):
        bad = copy.deepcopy(GOOD)
        bad.update(route='Somewhere', partnership_type='Made up', summary='<!channel> ping <@U99> & <b>',
                   sources=[{'title': 'x', 'url': 'http://insecure.example'},
                            {'title': 'y', 'url': 'https://ok.example/a|b'}])
        r = review.parse(json.dumps(bad))
        self.assertEqual((r['route'], r['partnership_type']), ('Review Needed', 'Misrouted / Other'))
        self.assertEqual(r['sources'], [])
        card = review.render(r)
        self.assertNotIn('<!channel>', card); self.assertNotIn('<@U99>', card)
        self.assertIn('&amp; &lt;b&gt;', card)

    def test_missing_score_or_json_fails(self):
        with self.assertRaises(ValueError):
            review.parse('no json here')
        with self.assertRaises(ValueError):
            review.parse(json.dumps({k: v for k, v in GOOD.items() if k != 'checks'}))
        # A score or decision from the model is ignored; the rules decide.
        r = review.parse(json.dumps(dict(GOOD, score='1', decision='do_not_pursue')))
        self.assertEqual((r['score'], r['decision']), ('4', 'pursue'))

    def test_newlines_inside_strings_are_tolerated(self):
        raw = json.dumps(GOOD).replace('Real company, clear motion.', 'Real company,\nclear motion.')
        self.assertIn('Real company, clear motion.', review.render(review.parse(raw)))


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db')
        self.event = review.accept_review(BODY, CFG); self.store.enqueue(self.event)

    def test_posts_once(self):
        client = Fake()
        review.process(self.store, self.store.next_job(), client)
        review.process(self.store, self.store.get(self.event['key']), client)
        self.assertEqual(client.posted, [('50.1', 'card')]); self.assertEqual(client.calls, 1)
        self.assertIsNone(self.store.next_job())

    def test_model_failure_posts_safe_notice(self):
        client = Fake(ValueError('secret detail'))
        review.process(self.store, self.store.next_job(), client)
        self.assertEqual(client.posted[0][1], review.FAILED)
        self.assertNotIn('secret detail', client.posted[0][1])

    def test_ambiguous_send_not_repeated(self):
        client = Fake()
        def fail(e, t):
            client.posted.append(t); raise TimeoutError()
        client.post = fail
        review.process(self.store, self.store.next_job(), client)
        review.process(self.store, self.store.get(self.event['key']), client)
        self.assertEqual(len(client.posted), 1)
        self.assertEqual(self.store.get(self.event['key'])['reply_state'], 'review')
        self.assertIsNone(self.store.next_job())


class ClientTests(unittest.TestCase):
    def respond(self, urlopen, *datas):
        urlopen.return_value.__enter__.side_effect = [io.BytesIO(json.dumps(d).encode()) for d in datas]

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'claude-test'})
    @patch('review.urllib.request.urlopen')
    def test_web_search_with_pause_turn(self, urlopen):
        paused = {'stop_reason': 'pause_turn', 'content': [{'type': 'server_tool_use', 'id': 's1', 'name': 'web_search', 'input': {}}]}
        done = {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(GOOD)[:40]},
                                                       {'type': 'text', 'text': json.dumps(GOOD)[40:]}]}
        self.respond(urlopen, paused, done)
        card = review.ReviewClients(CFG).review(review.accept_review(BODY, CFG))
        self.assertIn('Score 4', card)
        first, second = [json.loads(c.args[0].data) for c in urlopen.call_args_list]
        self.assertEqual(first['tools'][0]['type'], 'web_search_20250305')
        self.assertEqual(first['system'], [{'type': 'text', 'text': review.INSTRUCTIONS,
                                            'cache_control': {'type': 'ephemeral'}}])
        self.assertEqual(second['messages'][-1], {'role': 'assistant', 'content': paused['content']})

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'claude-test'})
    @patch('review.urllib.request.urlopen')
    def test_no_tools_when_search_disabled_and_incomplete_fails(self, urlopen):
        self.respond(urlopen, {'stop_reason': 'max_tokens', 'content': [{'type': 'text', 'text': '{'}]})
        with self.assertRaises(ValueError):
            review.ReviewClients(dict(CFG, web_search=False)).review(review.accept_review(BODY, CFG))
        self.assertNotIn('tools', json.loads(urlopen.call_args.args[0].data))

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'm'})
    @patch('review.urllib.request.urlopen', side_effect=TimeoutError('private'))
    def test_timeout_sanitised(self, urlopen):
        with self.assertRaisesRegex(ValueError, '^Claude response unavailable$'):
            review.ReviewClients(CFG).review(review.accept_review(BODY, CFG))

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN': 'xoxb-test'})
    @patch('review.api')
    def test_thread_and_shadow_delivery(self, api):
        event = review.accept_review(BODY, CFG)
        review.ReviewClients(dict(CFG, review_mode='thread')).post(event, 'card')
        sent = api.call_args.args[2]
        self.assertEqual((sent['channel'], sent['thread_ts'], sent['text']), ('CSUB', '50.1', 'card'))
        api.reset_mock()
        api.side_effect = [{'permalink': 'https://slack.example/p50'}, {'ok': True}]
        review.ReviewClients(CFG).post(event, 'card')
        sent = api.call_args.args[2]
        self.assertEqual(sent['channel'], 'CSHADOW'); self.assertNotIn('thread_ts', sent)
        self.assertTrue(sent['text'].startswith('Shadow review of <https://slack.example/p50|this submission>'))


class ConfigTests(unittest.TestCase):
    ENV = {'REVIEW_ENABLED': 'true', 'SLACK_BOT_TOKEN': 'x', 'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'm'}

    @patch('chat.load_config', return_value={})
    def test_review_alone_enables_receiver_in_shadow_mode(self, _):
        with patch.dict('os.environ', self.ENV, clear=True):
            cfg = review.config()
        self.assertTrue(cfg['enabled']); self.assertFalse(cfg['chat_enabled'])
        self.assertEqual((cfg['review_mode'], cfg['shadow_channel']), ('shadow', 'C0C7R970C5T'))
        self.assertEqual(cfg['review_bots'], {'B083VARARJ7'})

    @patch('chat.load_config', return_value={})
    def test_bad_mode_and_missing_key_fail(self, _):
        with patch.dict('os.environ', dict(self.ENV, REVIEW_MODE='loud'), clear=True):
            with self.assertRaisesRegex(ValueError, 'REVIEW_MODE'):
                review.config()
        with patch.dict('os.environ', dict(self.ENV, ANTHROPIC_MODEL=''), clear=True):
            with self.assertRaisesRegex(ValueError, 'ANTHROPIC_MODEL'):
                review.config()

    @patch('chat.load_config', return_value={})
    def test_off_by_default(self, _):
        with patch.dict('os.environ', {}, clear=True):
            cfg = review.config()
        self.assertFalse(cfg['review_enabled']); self.assertFalse(cfg['enabled'])



MANUAL = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='Ev7', event=dict(
    type='app_mention', channel='CSUB', user='U1', ts='50.9', thread_ts='50.1', text='<@UBOT> review'))


def manual(**changes):
    b = copy.deepcopy(MANUAL)
    b['event'].update(changes)
    return b


class ManualTriggerTests(unittest.TestCase):
    def test_accepts_review_mention_in_thread(self):
        e = review.accept_manual(MANUAL, CFG)
        self.assertEqual((e['kind'], e['thread_ts'], e['user']), ('review_manual', '50.1', 'U1'))
        self.assertIsNotNone(review.accept_manual(manual(text='<@UBOT>  Review this please'), CFG))
        self.assertIsNotNone(review.accept_manual(manual(channel='C1'), CFG))  # chat channels too

    def test_rejects_other_mentions(self):
        for change in [dict(thread_ts=None), dict(thread_ts='50.9'), dict(user='UOTHER'), dict(text='<@UBOT> summarize'),
                       dict(text='<@UBOT> what should we review?'), dict(channel='COTHER'), dict(bot_id='B1'),
                       dict(type='message')]:
            b = manual(**change)
            if change.get('thread_ts', 1) is None:
                b['event'].pop('thread_ts')
            self.assertIsNone(review.accept_manual(b, CFG), change)
        self.assertIsNone(review.accept_manual(MANUAL, dict(CFG, review_enabled=False)))

    def test_manual_wins_over_chat(self):
        self.assertEqual(review.accept(manual(channel='C1'), CFG)['kind'], 'review_manual')
        self.assertNotIn('kind', review.accept(MENTION, CFG))

    def test_process_fetches_parent_then_reviews(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); e = review.accept_manual(MANUAL, CFG); store.enqueue(e)
            client = Fake(); seen = []
            client.parent = lambda ev: 'DIGEST TEXT'
            client.review = lambda ev: seen.append(ev['text']) or 'card'
            review.process(store, store.next_job(), client)
            self.assertEqual(seen, ['DIGEST TEXT']); self.assertEqual(client.posted, [('50.9', 'card')])

    def test_process_not_a_digest(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); store.enqueue(review.accept_manual(MANUAL, CFG)); client = Fake()
            client.parent = lambda ev: (_ for _ in ()).throw(review.NotDigest())
            review.process(store, store.next_job(), client)
            self.assertEqual(client.posted[0][1], review.NOT_DIGEST); self.assertEqual(client.calls, 0)

    def test_process_unreadable_thread(self):
        with tempfile.TemporaryDirectory() as d:
            store = Store(d + '/q'); store.enqueue(review.accept_manual(MANUAL, CFG)); client = Fake()
            client.parent = lambda ev: (_ for _ in ()).throw(TimeoutError('slack'))
            review.process(store, store.next_job(), client)
            self.assertEqual(client.posted[0][1], review.THREAD_UNREADABLE); self.assertEqual(client.calls, 0)

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN': 'xoxb-test'})
    @patch('review.api')
    def test_parent_must_be_hubspot_digest(self, api):
        e = review.accept_manual(MANUAL, CFG)
        api.return_value = {'messages': [{'ts': '50.1', 'bot_id': 'BHUB', 'text': DIGEST}]}
        self.assertIn('PARTNERSHIP MQL DIGEST', review.ReviewClients(CFG).parent(e))
        self.assertIn('ts=50.1', api.call_args.args[0])
        api.return_value = {'messages': [{'ts': '50.1', 'user': 'U1', 'text': DIGEST}]}
        with self.assertRaises(review.NotDigest):
            review.ReviewClients(CFG).parent(e)
        api.return_value = {'messages': [{'ts': '49.0', 'bot_id': 'BHUB', 'text': DIGEST}]}
        with self.assertRaises(ValueError):
            review.ReviewClients(CFG).parent(e)

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN': 'xoxb-test'})
    @patch('review.api')
    def test_manual_reply_goes_in_thread_and_tags_requester(self, api):
        review.ReviewClients(CFG).post(review.accept_manual(MANUAL, CFG), 'card')  # CFG is shadow mode
        sent = api.call_args.args[2]
        self.assertEqual((sent['channel'], sent['thread_ts'], sent['text']), ('CSUB', '50.1', '<@U1> card'))


ROW = {'url': 'https://www.notion.so/row1', 'properties': {
    'Contact': {'type': 'title', 'title': [{'plain_text': 'Demo Person'}]},
    'Company': {'type': 'rich_text', 'rich_text': [{'plain_text': 'Example Inc'}]},
    'Status': {'type': 'select', 'select': {'name': 'DQ'}},
    'Qualification Score': {'type': 'select', 'select': {'name': '2'}},
    'Forward To': {'type': 'select', 'select': {'name': 'Partnerships'}},
    'Submitted': {'type': 'date', 'date': {'start': '2026-10-05'}}}}


class HistoryTests(unittest.TestCase):
    def test_digest_fields(self):
        self.assertEqual(review.digest_fields(DIGEST), ('demo@example.com', 'Example Inc'))
        self.assertEqual(review.digest_fields('*Email:* Bob@Gmail.com\n*Company:* N/A'), ('bob@gmail.com', ''))

    @patch.dict('os.environ', {'NOTION_TOKEN': 'secret_x'})
    @patch('review.api')
    def test_lookup_builds_filters_and_parses(self, api):
        api.side_effect = [{'results': [ROW]}, {'results': [{'url': 'https://www.notion.so/app1'}]}]
        h = review.NotionHistory(dict(CFG, notion_intake='INTAKE', notion_apps='APPS')).lookup(DIGEST)
        first = api.call_args_list[0]
        self.assertIn('/data_sources/INTAKE/query', first.args[0])
        self.assertEqual(first.args[3], '2025-09-03')
        terms = first.args[2]['filter']['or']
        self.assertIn({'property': 'Email', 'email': {'ends_with': '@example.com'}}, terms)
        self.assertIn({'property': 'Company', 'rich_text': {'contains': 'Example Inc'}}, terms)
        self.assertEqual(h['intake'][0]['status'], 'DQ'); self.assertEqual(h['applications'][0]['url'], 'https://www.notion.so/app1')

    @patch.dict('os.environ', {'NOTION_TOKEN': 'secret_x'})
    @patch('review.api')
    def test_free_mail_skips_domain_and_apps_failure_tolerated(self, api):
        api.side_effect = [{'results': []}, ValueError('applications query failed')]
        h = review.NotionHistory(dict(CFG, notion_intake='I', notion_apps='A')).lookup('*Email:* bob@gmail.com\n*Company:* N/A')
        self.assertEqual(api.call_args_list[0].args[2]['filter'], {'or': [{'property': 'Email', 'email': {'equals': 'bob@gmail.com'}}]})
        self.assertEqual(h, {'intake': [], 'applications': []})

    def test_render_history(self):
        self.assertEqual(review.render_history(None), [])
        self.assertIn('lookup failed', ' '.join(review.render_history('unavailable')))
        lines = review.render_history({'intake': [{'contact': 'Demo', 'company': 'Ex', 'status': 'DQ', 'score': '2',
                                                   'route': 'Partnerships', 'submitted': '2026-10-05', 'url': 'https://www.notion.so/r'}],
                                       'applications': [{'url': 'https://www.notion.so/a'}]})
        self.assertIn('• <https://www.notion.so/r|Demo, Ex> - DQ, score 2, Partnerships, 2026-10-05', lines)
        self.assertIn('• <https://www.notion.so/a|Partner application on file>', lines)
        self.assertIn('No earlier rows', ' '.join(review.render_history({'intake': [], 'applications': []})))

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'm'})
    @patch('review.urllib.request.urlopen')
    def test_history_reaches_model_and_card(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(
            {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(GOOD)}]}).encode())
        class H:
            def lookup(self, text): return {'intake': [], 'applications': []}
        card = review.ReviewClients(CFG, history=H()).review(review.accept_review(BODY, CFG))
        sent = json.loads(json.loads(urlopen.call_args.args[0].data)['messages'][0]['content'])
        self.assertEqual(sent['notion_history'], {'intake': [], 'applications': []})
        self.assertIn('No earlier rows', card)

    @patch.dict('os.environ', {'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'm'})
    @patch('review.urllib.request.urlopen')
    def test_history_failure_is_reported_not_fatal(self, urlopen):
        urlopen.return_value.__enter__.return_value = io.BytesIO(json.dumps(
            {'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': json.dumps(GOOD)}]}).encode())
        class H:
            def lookup(self, text): raise RuntimeError('notion down')
        card = review.ReviewClients(CFG, history=H()).review(review.accept_review(BODY, CFG))
        self.assertIn('lookup failed', card)
        sent = json.loads(json.loads(urlopen.call_args.args[0].data)['messages'][0]['content'])
        self.assertEqual(sent['notion_history'], 'unavailable')


class DiagnosticsTests(unittest.TestCase):
    @patch('review.config', return_value=dict(CFG, review_enabled=True, review_mode='shadow', web_search=True,
                                               notion_lookup=False, chat_enabled=False, summary_enabled=True,
                                               summary_hour=17))
    def test_healthz_reports_review_state(self, _):
        with tempfile.TemporaryDirectory() as d, patch.dict('os.environ', {'CHAT_DB': d + '/q.db'}):
            app = review.create_app(); out = []
            body = app({'PATH_INFO': '/healthz', 'REQUEST_METHOD': 'GET'}, lambda code, headers: out.append(code))
        data = json.loads(b''.join(body))
        self.assertEqual(out, ['200 OK'])
        self.assertEqual((data['review_enabled'], data['review_mode'], data['chat_enabled'], data['notion_lookup']),
                         (True, 'shadow', False, False))

    def test_ignored_digest_from_unknown_bot_is_logged_without_content(self):
        with patch('builtins.print') as p:
            self.assertIsNone(review.accept_review(body(bot_id='BNEW1'), CFG))
        msg = p.call_args.args[0]
        self.assertIn('bot_id=BNEW1', msg); self.assertNotIn('Demo Person', msg)



class PolishTests(unittest.TestCase):
    def test_citation_markup_removed(self):
        r = review.parse(json.dumps(dict(GOOD, why='<cite index="1-2">Built In describes it</cite> as real.')))
        self.assertEqual(r['why'], 'Built In describes it as real.')
        self.assertNotIn('cite', review.render(r))

    def test_overlong_fields_end_on_a_word(self):
        long_note = ' '.join(['capacity'] * 60)
        bad = copy.deepcopy(GOOD); bad['checks']['fit']['note'] = long_note; bad['next_step'] = long_note
        r = review.parse(json.dumps(bad))
        note = r['checks']['fit'][1]
        self.assertTrue(note.endswith('capacity...'), note[-20:]); self.assertLessEqual(len(note), 300)
        self.assertTrue(r['next_step'].endswith('capacity...'))

    def test_risks_capped_at_three(self):
        self.assertEqual(len(review.parse(json.dumps(dict(GOOD, risks=['a', 'b', 'c', 'd'])))['risks']), 3)

    def test_prompt_sets_brevity_and_voice(self):
        self.assertIn('ONE sentence under 160 characters', ' '.join(review.INSTRUCTIONS.split()))
        self.assertIn('Address the reader as "you"', review.INSTRUCTIONS)



def checks(i='verified', c='verified', f='verified', p='unverified'):
    return {n: (v, '') for n, v in zip(('identity', 'company', 'fit', 'potential'), (i, c, f, p))}


def sig(**on):
    base = {n: False for n in review.SIGNALS}; base['blocker'] = ''
    base.update(on); return base


class DecideTests(unittest.TestCase):
    def test_scores(self):
        self.assertEqual(review.decide(checks(p='verified'), sig(), 'Partnerships'), ('5', 'pursue'))
        self.assertEqual(review.decide(checks(), sig(), 'Partnerships'), ('4', 'pursue'))
        self.assertEqual(review.decide(checks(f='partial'), sig(), 'Partnerships'), ('3', 'do_not_pursue'))
        self.assertEqual(review.decide(checks(i='unverified'), sig(), 'Partnerships'), ('2', 'do_not_pursue'))
        self.assertEqual(review.decide(checks(), sig(spam=True), 'Partnerships'), ('DQ', 'do_not_pursue'))
        self.assertEqual(review.decide(checks(), sig(wrong_door=True), 'Partnerships'), ('1', 'do_not_pursue'))
        self.assertEqual(review.decide(checks(), sig(below_supply_bar=True), 'Supply Team'), ('2', 'do_not_pursue'))

    def test_score_three_paths(self):
        three = checks(i='partial')
        self.assertEqual(review.decide(three, sig(named_customer=True), 'Partnerships')[1], 'pursue')
        self.assertEqual(review.decide(three, sig(quantified_volume=True), 'Partnerships')[1], 'pursue')
        self.assertEqual(review.decide(three, sig(specific_motion=True), 'Partnerships')[1], 'triage')
        self.assertEqual(review.decide(three, sig(notable_counterparty=True), 'Partnerships')[1], 'triage')
        self.assertEqual(review.decide(three, sig(), 'Partnerships')[1], 'do_not_pursue')

    def test_overrides(self):
        self.assertEqual(review.decide(checks(), sig(blocker='sanctions check'), 'Partnerships'), ('4', 'triage'))
        self.assertEqual(review.decide(checks(), sig(already_in_play=True), 'Partnerships'), ('4', 'triage'))
        self.assertEqual(review.decide(checks(i='unverified'), sig(ask_unusable=True), 'Partnerships'), ('2', 'triage'))
        self.assertEqual(review.decide(checks(f='partial'), sig(), 'Startup Program'), ('3', 'redirect'))
        self.assertEqual(review.decide(checks(), sig(wrong_door=True), 'Influencer/Creator'), ('1', 'redirect'))
        self.assertEqual(review.decide(checks(i='unverified'), sig(), 'Influencer/Creator'), ('2', 'do_not_pursue'))

    def test_same_facts_same_call(self):
        raw = json.dumps(dict(GOOD, signals={'specific_motion': True}))
        self.assertEqual(len({(review.parse(raw)['score'], review.parse(raw)['decision']) for _ in range(5)}), 1)


SUBMITTED = review.dt.datetime(2026, 10, 13, 15, 0)


class DraftTests(unittest.TestCase):
    def make(self, **over):
        data = copy.deepcopy(GOOD); data.update(first_name='Demo', company_short='Example', **over)
        return review.parse(json.dumps(data))

    def test_probe_for_triage(self):
        r = self.make(checks=dict(GOOD['checks'], identity={'verdict': 'partial'}), signals={'specific_motion': True},
                      probe_ask='which customer workload would come first?')
        (label, text), = review.drafts(r, 'demo@example.com', SUBMITTED)
        self.assertEqual(label, 'Probe reply')
        self.assertEqual(text, 'Hi Demo-- thanks for submitting your partner request.\n\nI dug a little more into Example'
                               ' - would you mind sharing which customer workload would come first?\n\nColby')

    def test_invite_for_pursue_four(self):
        (label, text), = review.drafts(self.make(), 'demo@example.com', SUBMITTED)
        self.assertIn('partners@runpod.io', label)
        self.assertIn('invite Example to apply', text); self.assertTrue(text.startswith('Hi Demo,'))

    def test_pointer_for_self_serve(self):
        (label, text), = review.drafts(self.make(route='Startup Program', checks=dict(GOOD['checks'], fit={'verdict': 'partial'})),
                                       'd@x.com', SUBMITTED)
        self.assertEqual(label, 'Pointer reply'); self.assertIn('runpod.io/startup-program', text)
        (label, text), = review.drafts(self.make(route='Influencer/Creator'), 'd@x.com', SUBMITTED)
        self.assertIn('referral-and-affiliate-program', text); self.assertIn('rather than up front', text)

    def test_dean_draft_for_supply(self):
        r = self.make(route='Supply Team', region='US, Texas', dean_motion='Offers 200 RTX PRO 6000 GPUs (their claim).')
        (label, text), = review.drafts(r, 'ops@site.com', SUBMITTED)
        self.assertEqual(label, 'Draft for Dean (you send)')
        self.assertIn('Hey Dean -- passing along a supply-side infra lead.\nQuick details:\n\n* Company: Example', text)
        self.assertIn('* Email: `ops@site.com`', text); self.assertIn('* Region: US, Texas', text)
        self.assertEqual(review.drafts(r, '', SUBMITTED), [])

    def test_decline_waits_seven_days(self):
        r = self.make(signals={'wrong_door': True})
        (label, text), = review.drafts(r, 'd@x.com', SUBMITTED)
        self.assertEqual(label, 'Decline'); self.assertIn('Oct 20', text)
        unverified = self.make(checks=dict(GOOD['checks'], identity={'verdict': 'unverified'}))
        self.assertEqual(review.drafts(unverified, 'd@x.com', SUBMITTED), [])

    def test_card_shows_draft_in_code_block_and_no_action_for_dnp(self):
        r = self.make(signals={'wrong_door': True})
        card = review.render(r, None, review.drafts(r, 'd@x.com', SUBMITTED))
        self.assertIn('*Suggested next step:* No action.', card)
        r = self.make()
        card = review.render(r, None, review.drafts(r, 'd@x.com', SUBMITTED))
        self.assertIn(':memo: *Invitation', card); self.assertIn('```Hi Demo,', card)

    def test_headline_and_blocker_on_card(self):
        r = self.make(headline='Probe on owned sites', signals={'blocker': 'Check 1260H exposure'})
        card = review.render(r)
        self.assertTrue(card.startswith(':large_yellow_circle: Needs triage  ·  Score 4  ·  Partnerships  ·  Probe on owned sites'))
        self.assertIn(':octagonal_sign: *Clear first:* Check 1260H exposure', card)


class FakeSay(Fake):
    def __init__(self, *a):
        super().__init__(*a); self.said = []
    def say(self, channel, text): self.said.append((channel, text))
    def permalink(self, channel, ts): return 'https://slack.example/%s/%s' % (channel, ts)


class LedgerSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db'); self.ledger = review.Ledger(self.store.path)

    def test_process_records_and_alerts(self):
        e = review.accept_review(BODY, CFG); self.store.enqueue(e)
        client = FakeSay(ValueError('x'))
        review.process(self.store, self.store.next_job(), client, self.ledger, CFG)
        rows = self.ledger.since(0)
        self.assertEqual((rows[0]['outcome'], rows[0]['delivered']), ('failed', 'sent'))
        self.assertEqual(client.said[0][0], 'CSUM'); self.assertIn('A review failed', client.said[0][1])

    def test_successful_review_recorded_from_last(self):
        e = review.accept_review(BODY, CFG); self.store.enqueue(e)
        client = FakeSay(); client.last = review.parse(json.dumps(dict(GOOD, headline='Invite once approved')))
        review.process(self.store, self.store.next_job(), client, self.ledger, CFG)
        row = self.ledger.since(0)[0]
        self.assertEqual((row['company'], row['decision'], row['score'], row['headline']),
                         ('Example Inc', 'pursue', '4', 'Invite once approved'))
        self.assertEqual(client.said, [])

    def test_unconfirmed_post_alerts(self):
        e = review.accept_review(BODY, CFG); self.store.enqueue(e); client = FakeSay()
        def fail(ev, t): raise TimeoutError()
        client.post = fail
        review.process(self.store, self.store.next_job(), client, self.ledger, CFG)
        self.assertEqual(self.ledger.since(0)[0]['delivered'], 'unconfirmed')
        self.assertIn('may not have posted', client.said[0][1])

    def test_mountain_time_dst(self):
        self.assertEqual(review.mountain(review.dt.datetime(2026, 10, 13, 23, 0)).hour, 17)   # MDT
        self.assertEqual(review.mountain(review.dt.datetime(2026, 12, 1, 0, 0)).hour, 17)     # MST
        self.assertEqual(review.mountain(review.dt.datetime(2026, 11, 1, 7, 59)).hour, 1)     # before fall back
        self.assertEqual(review.mountain(review.dt.datetime(2026, 11, 1, 9, 0)).hour, 2)      # after fall back

    def test_summary_once_per_weekday_after_hour(self):
        client = FakeSay()
        before = review.dt.datetime(2026, 10, 13, 22, 59)  # 4:59pm MDT Tue
        after = review.dt.datetime(2026, 10, 13, 23, 1)
        saturday = review.dt.datetime(2026, 10, 17, 23, 30)
        self.assertFalse(review.run_summary(CFG, self.ledger, client, before))
        self.assertTrue(review.run_summary(CFG, self.ledger, client, after))
        self.assertFalse(review.run_summary(CFG, self.ledger, client, after))
        self.assertFalse(review.run_summary(CFG, self.ledger, client, saturday))
        self.assertEqual(len(client.said), 1); self.assertIn('No new submissions', client.said[0][1])
        self.assertFalse(review.run_summary(dict(CFG, summary_enabled=False), review.Ledger(self.tmp.name + '/x.db'),
                                            client, after))

    def test_summary_content(self):
        e = review.accept_review(BODY, CFG)
        self.ledger.record('k1', e, review.parse(json.dumps(dict(GOOD, headline='Invite once approved'))), 'ok')
        self.ledger.record('k2', dict(e, thread_ts='51.1'), None, 'failed')
        text = review.build_summary(dict(CFG, key_expires='2026-11-06'), self.ledger.since(0), FakeSay(),
                                    review.dt.datetime(2026, 10, 23, 23, 30))
        self.assertIn('*Partner submissions · Fri Oct 23*', text)
        self.assertIn('1 reviewed: :large_green_circle: Pursue 1', text)
        self.assertIn(':large_green_circle: <https://slack.example/CSUB/50.1|Example Inc>  ·  Partnerships  ·  Invite once approved', text)
        self.assertIn('*Needs attention*', text); self.assertIn('review failed', text)
        self.assertIn('expires in 14 days', text)
        self.assertNotIn('expires', review.build_summary(dict(CFG, key_expires='2026-12-31'), [], FakeSay(),
                                                         review.dt.datetime(2026, 10, 23, 23, 30)))

    def test_config_summary_and_key_settings(self):
        env = {'REVIEW_ENABLED': 'true', 'SLACK_BOT_TOKEN': 'x', 'ANTHROPIC_API_KEY': 'k', 'ANTHROPIC_MODEL': 'm'}
        with patch('chat.load_config', return_value={}):
            with patch.dict('os.environ', env, clear=True):
                cfg = review.config()
            self.assertEqual((cfg['summary_hour'], cfg['summary_channel'], cfg['summary_enabled']), (17, 'C0C7R970C5T', True))
            with patch.dict('os.environ', dict(env, ANTHROPIC_KEY_EXPIRES='Nov 6'), clear=True):
                with self.assertRaises(ValueError): review.config()
            with patch.dict('os.environ', dict(env, SUMMARY_HOUR='25'), clear=True):
                with self.assertRaises(ValueError): review.config()


if __name__ == '__main__':
    unittest.main()
