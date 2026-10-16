import copy
import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
import urllib.parse
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
        for text in ['<@UBOT> can you review', '<@UBOT> can you review?', '<@UBOT> could you please re-review this',
                     '<@UBOT> please review', '<@UBOT> re-review', '<@UBOT> hey can you review this one again?',
                     '<@UBOT> would you run the review again']:
            self.assertIsNotNone(review.accept_manual(manual(text=text), CFG), text)

    def test_rejects_other_mentions(self):
        for change in [dict(thread_ts=None), dict(thread_ts='50.9'), dict(user='UOTHER'), dict(text='<@UBOT> summarize'),
                       dict(text='<@UBOT> what should we review?'), dict(text='<@UBOT> can you review the Q3 deck'), dict(text='<@UBOT> do not review'), dict(channel='COTHER'), dict(bot_id='B1'),
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
        # Pipeline 3b: a 3 is kept (probe) with one genuine signal, never pursued outright.
        self.assertEqual(review.decide(three, sig(named_customer=True), 'Partnerships')[1], 'triage')
        self.assertEqual(review.decide(three, sig(quantified_volume=True), 'Partnerships')[1], 'triage')
        self.assertEqual(review.decide(three, sig(specific_motion=True), 'Partnerships')[1], 'triage')
        weak_company = checks(i='partial', c='partial')
        self.assertEqual(review.decide(weak_company, sig(specific_motion=True), 'Partnerships'), ('3', 'do_not_pursue'))
        self.assertEqual(review.decide(three, sig(notable_counterparty=True), 'Partnerships')[1], 'triage')
        self.assertEqual(review.decide(three, sig(), 'Partnerships')[1], 'do_not_pursue')

    def test_overrides(self):
        self.assertEqual(review.decide(checks(), sig(blocker='sanctions check'), 'Partnerships'), ('4', 'triage'))
        self.assertEqual(review.decide(checks(), sig(already_in_play=True), 'Partnerships'), ('4', 'triage'))
        self.assertEqual(review.decide(checks(i='unverified'), sig(ask_unusable=True), 'Partnerships'), ('2', 'triage'))
        self.assertEqual(review.decide(checks(f='partial'), sig(), 'Startup Program'), ('1', 'redirect'))
        self.assertEqual(review.decide(checks(), sig(), 'Sales'), ('1', 'redirect'))
        self.assertEqual(review.decide(checks(), sig(wrong_door=True), 'Influencer/Creator'), ('1', 'redirect'))
        self.assertEqual(review.decide(checks(i='unverified'), sig(), 'Influencer/Creator'), ('1', 'redirect'))

    def test_escalations_are_never_dqd_on_judgment(self):
        self.assertEqual(review.decide(checks(c='unverified'), sig(warm_intro=True), 'Partnerships'), ('2', 'triage'))
        self.assertEqual(review.decide(checks(), sig(wrong_door=True, notable_counterparty=True), 'Review Needed'),
                         ('1', 'triage'))
        self.assertEqual(review.decide(checks(), sig(spam=True, warm_intro=True), 'Partnerships'), ('DQ', 'do_not_pursue'))
        self.assertEqual(review.decide(checks(), sig(spam=True, ask_unusable=True), 'Partnerships'), ('DQ', 'do_not_pursue'))

    def test_sales_redirect_has_no_pointer_and_routes_to_sales(self):
        r = review.parse(json.dumps(dict(GOOD, route='Sales', first_name='Demo', company_short='Example')))
        self.assertEqual((r['score'], r['decision']), ('1', 'redirect'))
        self.assertEqual(review.drafts(r, 'd@x.com', SUBMITTED), [])
        self.assertIn('Route to Sales with a MOPS ticket', review.render(r))

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
        self.assertIn('* Email: ops@site.com\n', text); self.assertNotIn('`', text); self.assertIn('* Region: US, Texas', text)
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
        self.assertIn(':memo: *Invitation', card); self.assertIn('```\nHi Demo,', card)

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



FCFG = dict(CFG, bot_user='UBOT')
REACT = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='EvR', event=dict(
    type='reaction_added', user='U1', reaction='+1', item_user='UBOT', item=dict(type='message', channel='CSUB', ts='50.5')))
FIX = dict(type='event_callback', team_id='T1', api_app_id='A1', event_id='EvF', event=dict(
    type='app_mention', channel='CSUB', user='U1', ts='50.9', thread_ts='50.1', text='<@UBOT> should be triage'))


class FakeFb(FakeSay):
    def __init__(self, *a):
        super().__init__(*a); self.thread_said = []
    def say_thread(self, channel, thread_ts, text): self.thread_said.append((channel, thread_ts, text))
    def post(self, event, text):
        super().post(event, text); return '50.5'


class FeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db'); self.ledger = review.Ledger(self.store.path)
        e = review.accept_review(BODY, FCFG); self.store.enqueue(e)
        self.client = FakeFb(); self.client.last = review.parse(json.dumps(GOOD))  # pursue, score 4
        review.process(self.store, self.store.next_job(), self.client, self.ledger, FCFG)

    def run_event(self, body):
        ev = review.accept(body, FCFG); self.assertIsNotNone(ev); self.store.enqueue(ev)
        review.process_feedback(self.store, self.store.next_job(), self.client, self.ledger)
        return ev

    def test_card_ts_recorded(self):
        self.assertEqual(self.ledger.find(card_ts='50.5')['decision'], 'pursue')

    def test_filters(self):
        self.assertEqual(review.accept_feedback(REACT, FCFG)['vote'], 'up')
        for change in [dict(item_user='USOMEONE'), dict(reaction='tada'), dict(user='UOTHER'),
                       dict(item=dict(type='message', channel='COTHER', ts='50.5'))]:
            b = copy.deepcopy(REACT); b['event'].update(change)
            self.assertIsNone(review.accept_feedback(b, FCFG), change)
        b = copy.deepcopy(REACT); b['event']['reaction'] = '-1::skin-tone-2'
        self.assertEqual(review.accept_feedback(b, FCFG)['vote'], 'down')
        self.assertIsNone(review.accept_feedback(REACT, dict(FCFG, review_enabled=False)))
        team = dict(FCFG, feedback_users={'U1', 'UTANYA'})
        b = copy.deepcopy(REACT); b['event']['user'] = 'UTANYA'
        self.assertIsNotNone(review.accept_feedback(b, team)); self.assertIsNone(review.accept_feedback(b, FCFG))

    def test_correction_parsing_and_precedence(self):
        for text, value in [('<@UBOT> should be triage', 'triage'), ('<@UBOT> actually do not pursue', 'do_not_pursue'),
                            ('<@UBOT> correction: self-serve', 'redirect'), ('<@UBOT> should be dnp', 'do_not_pursue')]:
            b = copy.deepcopy(FIX); b['event']['text'] = text
            self.assertEqual(review.accept(b, FCFG)['value'], value, text)
        b = copy.deepcopy(FIX); b['event']['text'] = '<@UBOT> what do you think?'
        self.assertIsNone(review.accept_feedback(b, FCFG))
        b = copy.deepcopy(FIX); b['event']['text'] = '<@UBOT> review'
        self.assertEqual(review.accept(b, FCFG)['kind'], 'review_manual')

    def test_votes_one_per_person_latest_wins_and_removal(self):
        self.run_event(REACT)
        b = copy.deepcopy(REACT); b['event_id'] = 'EvR2'; b['event']['reaction'] = 'thumbsdown'
        self.run_event(b)
        votes = self.ledger.feedback_since(0)
        self.assertEqual([f['vote'] for f in votes], ['down'])
        b = copy.deepcopy(b); b['event_id'] = 'EvR3'; b['event']['type'] = 'reaction_removed'
        self.run_event(b)
        self.assertEqual(self.ledger.feedback_since(0), [])

    def test_correction_logged_and_acked(self):
        self.run_event(FIX)
        f = self.ledger.feedback_since(0)[0]
        self.assertEqual((f['vote'], f['value'], f['decision']), ('correct', 'triage', 'pursue'))
        self.assertEqual(self.client.thread_said[0][:2], ('CSUB', '50.1'))
        self.assertIn('logged as triage (I said pursue)', self.client.thread_said[0][2])

    def test_unknown_card_ignored(self):
        b = copy.deepcopy(REACT); b['event']['item']['ts'] = '99.9'
        self.run_event(b)
        self.assertEqual(self.ledger.feedback_since(0), [])

    def test_weekly_report(self):
        self.run_event(FIX)
        e = review.accept_review(BODY, FCFG)
        self.ledger.record('k2', dict(e, thread_ts='60.1'), review.parse(json.dumps(GOOD)), 'ok')
        self.ledger.feedback('f2', 'k2', 'U1', 'correct', 'triage')
        rows, fb = self.ledger.since(0), self.ledger.feedback_since(0)
        text = review.build_weekly(rows, fb, self.client)
        self.assertIn('2 reviewed this week  ·  :+1: 0  ·  :-1: 0  ·  corrected 2', text)
        self.assertIn('Agreement on the 2 you weighed in on: 0%', text)
        self.assertIn('I said pursue, you said triage', text)
        self.assertIn('Most common miss: pursue when you wanted triage (2 times)', text)
        self.assertIn('No feedback yet', review.build_weekly(rows, [], self.client))

    def test_weekly_posts_fridays_once(self):
        client = FakeFb(); ledger = review.Ledger(self.tmp.name + '/w.db')
        friday = review.dt.datetime(2026, 10, 16, 23, 30)
        review.run_summary(FCFG, ledger, client, friday)
        self.assertEqual(len(client.said), 2); self.assertIn('*Weekly review check*', client.said[1][1])
        ledger.put('summary_date', '')  # force another daily pass the same week
        review.run_summary(FCFG, ledger, client, friday)
        self.assertEqual(sum('Weekly review check' in t for _, t in client.said), 1)

    def test_old_ledger_gets_card_ts_column(self):
        import sqlite3
        path = self.tmp.name + '/old.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE reviews (key TEXT PRIMARY KEY, channel TEXT, ts TEXT, created REAL, company TEXT, '
                       'route TEXT, score TEXT, decision TEXT, headline TEXT, outcome TEXT, delivered TEXT)')
        review.Ledger(path).delivered('x', 'sent', '1.2')
        with sqlite3.connect(path) as db:
            self.assertIn('card_ts', [r[1] for r in db.execute('PRAGMA table_info(reviews)')])



ICFG = dict(FCFG, interactive=True, notion_write=True, notion_intake='INTAKE')
PROBE_GOOD = dict(GOOD, first_name='Demo', company_short='Example', probe_ask='which workload comes first',
                  checks=dict(GOOD['checks'], identity={'verdict': 'partial'}), signals={'specific_motion': True})


class FakeUI(FakeFb):
    def __init__(self, *a):
        super().__init__(*a); self.updates = []; self.ephemerals = []; self.posted_buttons = []
    def post(self, event, text, buttons=None):
        self.posted_buttons.append(buttons); self.last_posted_text = text
        return super().post(event, text)
    def update_card(self, channel, ts, text, buttons, notes): self.updates.append((channel, ts, buttons, list(notes)))
    def ephemeral(self, event, text): self.ephemerals.append(text)


class FakeNotion:
    def __init__(self, existed=False, fail=False):
        self.logged, self.drafts, self.existed, self.fail = [], [], existed, fail
    def log(self, data, row):
        if self.fail: raise ValueError('Notion HTTP 403')
        self.logged.append((data['company'], row['ts'])); return 'https://www.notion.so/row1', 'pid1', self.existed
    def request_draft(self, page_id, data, body): self.drafts.append((page_id, data['email'], body))


def click(action, key='T1:CSUB:50.1', user='U1', ts='70.1'):
    return dict(type='block_actions', team={'id': 'T1'}, api_app_id='A1', user={'id': user},
                channel={'id': 'CSUB'}, message={'ts': '50.5'}, response_url='https://hooks.slack.com/actions/T1/1/abc',
                actions=[{'action_id': action, 'value': key, 'action_ts': ts}])


class ButtonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db'); self.ledger = review.Ledger(self.store.path)
        self.client = FakeUI()
        r = review.parse(json.dumps(PROBE_GOOD)); r['email'], r['submitted'] = 'demo@example.com', '2026-10-13'
        r['drafts'] = review.drafts(r, 'demo@example.com', SUBMITTED); self.client.last = r
        self.store.enqueue(review.accept_review(BODY, ICFG))
        review.process(self.store, self.store.next_job(), self.client, self.ledger, ICFG)

    def act(self, action, notion=None, **kw):
        ev = review.accept_action(click(action, **kw), ICFG); self.assertIsNotNone(ev); self.store.enqueue(ev)
        review.process_action(self.store, self.store.next_job(), self.client, self.ledger, ICFG, notion)

    def test_card_posted_with_buttons_and_recorded(self):
        self.assertEqual(self.client.posted_buttons[0],
                         [('log_notion', 'T1:CSUB:50.1'), ('draft_gmail', 'T1:CSUB:50.1'), ('park', 'T1:CSUB:50.1')])
        row = self.ledger.review('T1:CSUB:50.1')
        self.assertEqual(row['card_text'], 'card')
        self.assertEqual(json.loads(row['data'])['email'], 'demo@example.com')

    def test_no_buttons_when_not_interactive_or_notion_off(self):
        self.assertEqual(review.action_buttons(dict(ICFG, interactive=False), {'key_for_buttons': 'k'}), [])
        self.assertEqual(review.action_buttons(dict(ICFG, notion_write=False), {'key_for_buttons': 'k', 'email': 'x@y.z',
                                               'drafts': [['Probe reply', 'hi']]}), [('park', 'k')])
        self.assertEqual(review.action_buttons(ICFG, {'key_for_buttons': 'k', 'drafts': [['Draft for Dean (you send)', 'x']],
                                                      'email': 'a@b.c'}), [('log_notion', 'k'), ('park', 'k')])

    def test_blocks(self):
        blocks = review.card_blocks('line\n' * 2000, [('park', 'k')], ['a note'])
        self.assertTrue(all(len(b['text']['text']) <= 2900 for b in blocks if b['type'] == 'section'))
        self.assertEqual(blocks[-2], {'type': 'context', 'elements': [{'type': 'mrkdwn', 'text': 'a note', 'verbatim': True}]})
        self.assertTrue(all(b['text']['verbatim'] for b in blocks if b['type'] == 'section'))
        self.assertEqual(blocks[-1]['elements'][0]['action_id'], 'park')
        self.assertEqual(review.card_blocks('x', [('log_notion', 'k')])[-1]['elements'][0]['style'], 'primary')

    def test_accept_action_filters(self):
        self.assertEqual(review.accept_action(click('park'), ICFG)['action'], 'park')
        self.assertIsNone(review.accept_action(click('park', user='UOTHER'), ICFG))
        self.assertIsNone(review.accept_action(click('delete_everything'), ICFG))
        self.assertIsNone(review.accept_action(click('park', key='not-a-key'), ICFG))
        self.assertIsNone(review.accept_action(click('park'), dict(ICFG, interactive=False)))
        bad = click('park'); bad['team'] = {'id': 'TOTHER'}
        self.assertIsNone(review.accept_action(bad, ICFG))
        odd = click('park'); odd['response_url'] = 'https://evil.example/x'
        self.assertEqual(review.accept_action(odd, ICFG)['response_url'], '')

    def test_signed_interaction_endpoint(self):
        raw = urllib.parse.urlencode({'payload': json.dumps(click('park'))}).encode(); ts = str(int(time.time()))
        sig = 'v0=' + hmac.new(b'secret', b'v0:' + ts.encode() + b':' + raw, hashlib.sha256).hexdigest()
        def call(signature):
            codes = []
            env = {'PATH_INFO': '/slack/interactions', 'REQUEST_METHOD': 'POST', 'CONTENT_LENGTH': str(len(raw)),
                   'HTTP_X_SLACK_REQUEST_TIMESTAMP': ts, 'HTTP_X_SLACK_SIGNATURE': signature, 'wsgi.input': io.BytesIO(raw)}
            review.interactions(env, lambda code, headers: codes.append(code), ICFG, self.store)
            return codes[0]
        self.assertEqual(call('bad'), '401 Unauthorized')
        self.assertEqual(call(sig), '200 OK')
        self.assertEqual(json.loads(self.store.next_job()['payload'])['kind'], 'action')

    def test_park(self):
        self.act('park')
        self.assertEqual(self.ledger.review('T1:CSUB:50.1')['parked_by'], 'U1')
        ch, ts, buttons, notes = self.client.updates[0]
        self.assertEqual((ch, ts, buttons), ('CSUB', '50.5', [])); self.assertIn('Parked by <@U1>', notes[0])
        self.act('park', ts='70.2')
        self.assertIn('already parked', self.client.ephemerals[0])

    def test_log_in_notion_once(self):
        notion = FakeNotion()
        self.act('log_notion', notion)
        self.assertEqual(notion.logged, [('Example Inc', '50.1')])
        self.assertEqual(self.ledger.review('T1:CSUB:50.1')['notion_url'], 'https://www.notion.so/row1')
        _, _, buttons, notes = self.client.updates[-1]
        self.assertEqual([b[0] for b in buttons], ['draft_gmail', 'park'])
        self.assertIn('Logged in Notion by <@U1>', notes[0])
        self.act('log_notion', notion, ts='70.3')
        self.assertEqual(len(notion.logged), 1); self.assertIn('Already logged', self.client.ephemerals[-1])

    def test_existing_row_is_linked_not_duplicated(self):
        self.act('log_notion', FakeNotion(existed=True))
        self.assertIn('Already in Notion, linked', self.client.updates[-1][3][0])

    def test_draft_in_gmail_logs_row_then_requests(self):
        notion = FakeNotion()
        self.act('draft_gmail', notion)
        self.assertEqual(len(notion.logged), 1)
        pid, email, body = notion.drafts[0]
        self.assertEqual((pid, email), ('pid1', 'demo@example.com'))
        self.assertTrue(body.startswith('Hi Demo-- thanks for submitting your partner request.'))
        _, _, buttons, notes = self.client.updates[-1]
        self.assertEqual([b[0] for b in buttons], ['park']); self.assertIn('Gmail draft requested', notes[-1])

    def test_notion_off_and_failures_change_nothing(self):
        self.act('log_notion', None)
        self.assertIn('turned off', self.client.ephemerals[-1])
        self.act('log_notion', FakeNotion(fail=True), ts='70.4')
        self.assertIn("didn't go through", self.client.ephemerals[-1])
        self.assertIsNone(self.ledger.review('T1:CSUB:50.1')['notion_url']); self.assertEqual(self.client.updates, [])

    def test_unknown_review(self):
        self.act('park', key='T1:CSUB:99.9')
        self.assertIn("can't find that review", self.client.ephemerals[-1])


class NotionWriterTests(unittest.TestCase):
    def data(self, **over):
        r = review.parse(json.dumps(dict(PROBE_GOOD, **over)))
        r['email'], r['submitted'] = 'demo@example.com', '2026-10-13'
        r['drafts'] = review.drafts(r, 'demo@example.com', SUBMITTED)
        return {k: (list(map(list, v)) if k == 'drafts' else v) for k, v in r.items()}

    def test_properties_triage_and_dq(self):
        w = review.NotionWriter(ICFG)
        p = w.properties(self.data(), 'tpa:CSUB:50.1')
        self.assertEqual((p['Status']['select']['name'], p['Intake Stage']['select']['name'],
                          p['Qualification Score']['select']['name'], p['Quality']['select']['name'],
                          p['Suggested Next Step']['select']['name'], p['Email']['email']),
                         ('New', 'Triaging', '3', 'Medium', 'Draft email reply', 'demo@example.com'))
        self.assertEqual(p['Partnership Type'], {'multi_select': [{'name': 'Reseller/VAR'}]})
        self.assertIn('**IDENTITY:** partial', p['Quality Notes']['rich_text'][0]['text']['content'])
        self.assertNotIn('Qualification Decision Date', p)
        dq = w.properties(self.data(signals={'spam': True}), 'tpa:x')
        self.assertEqual((dq['Status']['select']['name'], dq['Intake Stage']['select']['name'],
                          dq['Suggested Next Step']['select']['name']), ('DQ', 'Disqualified', 'No action'))
        self.assertIn('Qualification Decision Date', dq)
        self.assertTrue(dq['Quality Notes']['rich_text'][0]['text']['content'].startswith('DQ reason:'))

    @patch.dict('os.environ', {'NOTION_TOKEN': 'secret_x'})
    def test_log_finds_before_creating(self):
        w = review.NotionWriter(ICFG); calls = []
        def fake(method, path, payload=None):
            calls.append((method, path, payload))
            if path.endswith('/query'):
                return {'results': [{'url': 'https://www.notion.so/old', 'id': 'old1'}]} if len(calls) == 1 else {'results': []}
            return {'url': 'https://www.notion.so/new', 'id': 'new1'}
        w._req = fake
        self.assertEqual(w.log(self.data(), {'channel': 'CSUB', 'ts': '50.1'}), ('https://www.notion.so/old', 'old1', True))
        flt = calls[0][2]['filter']['or']
        self.assertEqual(flt[0], {'property': 'Intake Ref', 'rich_text': {'equals': 'tpa:CSUB:50.1'}})
        self.assertEqual(flt[1]['and'][1], {'property': 'Submitted', 'date': {'on_or_after': '2026-10-10'}})
        self.assertEqual(w.log(self.data(), {'channel': 'CSUB', 'ts': '50.1'}), ('https://www.notion.so/new', 'new1', False))
        self.assertEqual(calls[-1][:2], ('POST', 'pages'))
        self.assertEqual(calls[-1][2]['parent'], {'type': 'data_source_id', 'data_source_id': 'INTAKE'})

    def test_request_draft_uses_next_step_notes(self):
        w = review.NotionWriter(ICFG); calls = []
        w._req = lambda m, p, payload=None: calls.append((m, p, payload)) or {}
        w.request_draft('pid1', {'email': 'demo@example.com'}, 'Hi Demo-- hello')
        self.assertEqual(calls[0][:2], ('GET', 'pages/pid1'))
        m, path, payload = calls[1]
        self.assertEqual((m, path), ('PATCH', 'pages/pid1'))
        note = payload['properties']['Next Step Notes']['rich_text'][0]['text']['content']
        self.assertIn('Create a Gmail draft to demo@example.com', note); self.assertIn('Hi Demo-- hello', note)
        self.assertEqual(payload['properties']['Suggested Next Step'], {'select': {'name': 'Draft email reply'}})

    @patch('review.urllib.request.urlopen')
    def test_ephemeral_uses_response_url_only(self, urlopen):
        c = review.ReviewClients(ICFG)
        c.ephemeral({'response_url': ''}, 'x'); urlopen.assert_not_called()
        c.ephemeral({'response_url': 'https://hooks.slack.com/actions/T1/1/abc'}, 'hello')
        req = urlopen.call_args.args[0]
        self.assertEqual(json.loads(req.data), {'response_type': 'ephemeral', 'replace_original': False, 'text': 'hello'})

    @patch.dict('os.environ', {'SLACK_BOT_TOKEN': 'xoxb-test'})
    @patch('review.api', return_value={'ts': '9.9'})
    def test_post_with_buttons_sends_blocks(self, api):
        ts = review.ReviewClients(dict(ICFG, review_mode='thread')).post(review.accept_review(BODY, ICFG), 'card', [('park', 'k')])
        sent = api.call_args.args[2]
        self.assertEqual(ts, '9.9'); self.assertEqual(sent['blocks'][-1]['type'], 'actions'); self.assertEqual(sent['text'], 'card')


class ConsistencyTests(unittest.TestCase):
    """A re-review says whether it agrees with the earlier call, and the drafts stay copy-ready."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db'); self.ledger = review.Ledger(self.store.path)
        self.event = dict(channel='CSUB', ts='50.1', thread_ts='50.1')

    def earlier(self, score, decision, potential):
        r = review.parse(json.dumps(GOOD))
        r.update(score=score, decision=decision)
        r['checks'] = dict(r['checks'], potential=[potential, ''])
        self.ledger.record('T1:CSUB:50.1', self.event, r, 'ok', now=1791943621)

    def now(self, score, decision, potential):
        c = Fake('top line\n\n*Partner review*'); c.last = review.parse(json.dumps(GOOD))
        c.last.update(score=score, decision=decision)
        c.last['checks'] = dict(c.last['checks'], potential=[potential, ''])
        return review.with_prior('top line\n\n*Partner review*', self.ledger, c, self.event)

    def test_no_prior_leaves_card_alone(self):
        self.assertEqual(self.now('3', 'triage', 'partial'), 'top line\n\n*Partner review*')

    def test_changed_call_says_what_moved(self):
        self.earlier('2', 'do_not_pursue', 'unverified')
        text = self.now('3', 'triage', 'partial')
        first, second = text.split('\n')[:2]
        self.assertEqual(first, 'top line')
        self.assertIn('*Changed from the earlier review (Oct 13):* that one said Score 2, do not pursue.', second)
        self.assertIn('Potential went from unverified to partial.', second)

    def test_changed_with_same_checks_blames_signals(self):
        self.earlier('2', 'do_not_pursue', 'partial')
        self.assertIn('checks match', self.now('3', 'triage', 'partial'))

    def test_same_call(self):
        self.earlier('3', 'triage', 'partial')
        self.assertIn(':repeat: Same call as the earlier review (Oct 13).', self.now('3', 'triage', 'partial'))

    def test_process_adds_line_on_rereview(self):
        self.earlier('2', 'do_not_pursue', 'unverified')
        self.store.enqueue(dict(review.accept_review(BODY, CFG), key='again'))
        c = Fake('top\n\nbody'); c.last = review.parse(json.dumps(GOOD))
        review.process(self.store, self.store.next_job(), c, self.ledger, CFG)
        self.assertIn('Changed from the earlier review', c.posted[0][1])

    def test_draft_ending_in_backtick_stays_fenced(self):
        card = review.render(review.parse(json.dumps(GOOD)), None, [('Probe reply', 'see `x`')])
        self.assertIn("```\nsee 'x'\n```", card)


class DraftSafetyTests(unittest.TestCase):
    """Draft in Gmail on a row the pipeline already handled: keep its notes, never ask for a second draft."""
    def page(self, notes='', draft_id=''):
        rt = lambda t: [{'plain_text': t}] if t else []
        return {'properties': {'Next Step Notes': {'rich_text': rt(notes)}, 'Gmail Draft ID': {'rich_text': rt(draft_id)}}}

    def test_keeps_earlier_notes(self):
        w = review.NotionWriter(ICFG); calls = []
        def fake(m, p, payload=None):
            calls.append((m, p, payload)); return self.page('Routed to Dean on Oct 13') if m == 'GET' else {}
        w._req = fake
        w.request_draft('pid1', {'email': 'demo@example.com'}, 'Hi Demo')
        note = ''.join(t['text']['content'] for t in calls[1][2]['properties']['Next Step Notes']['rich_text'])
        self.assertTrue(note.startswith('Create a Gmail draft to demo@example.com'))
        self.assertTrue(note.endswith('Earlier notes on this row:\nRouted to Dean on Oct 13'))

    def test_refuses_second_draft(self):
        w = review.NotionWriter(ICFG); calls = []
        w._req = lambda m, p, payload=None: calls.append(m) or self.page(draft_id='r-123')
        with self.assertRaises(review.AlreadyDrafted):
            w.request_draft('pid1', {'email': 'demo@example.com'}, 'Hi Demo')
        self.assertEqual(calls, ['GET'])

    def test_button_reports_existing_draft(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        store = Store(tmp.name + '/q.db'); ledger = review.Ledger(store.path); client = FakeUI()
        r = review.parse(json.dumps(PROBE_GOOD)); r['email'], r['submitted'] = 'demo@example.com', '2026-10-13'
        r['drafts'] = review.drafts(r, 'demo@example.com', SUBMITTED); client.last = r
        store.enqueue(review.accept_review(BODY, ICFG)); review.process(store, store.next_job(), client, ledger, ICFG)
        class Drafted(FakeNotion):
            def request_draft(self, *a): raise review.AlreadyDrafted()
        ev = review.accept_action(click('draft_gmail'), ICFG); store.enqueue(ev)
        review.process_action(store, store.next_job(), client, ledger, ICFG, Drafted())
        self.assertIn('already has a Gmail draft', client.ephemerals[-1])
        self.assertEqual(client.updates[-1][2], [('park', 'T1:CSUB:50.1')])
        self.assertIn('Logged in Notion', client.updates[-1][3][0])



class PipelineAgreementTests(unittest.TestCase):
    """The card shows the scheduled pipeline's call once it has logged the submission."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name + '/q.db'); self.ledger = review.Ledger(self.store.path)
        self.client = FakeUI()
        r = review.parse(json.dumps(PROBE_GOOD)); r['email'], r['submitted'] = 'demo@example.com', '2026-10-13'
        r['drafts'] = review.drafts(r, 'demo@example.com', SUBMITTED); self.client.last = r
        self.cfg = dict(ICFG, review_mode='thread')
        self.store.enqueue(review.accept_review(BODY, self.cfg))
        review.process(self.store, self.store.next_job(), self.client, self.ledger, self.cfg)

    class Check:
        def __init__(self, found=None, fail=False): self.found, self.fail, self.asked = found, fail, []
        def find(self, email, submitted):
            self.asked.append((email, submitted))
            if self.fail: raise ValueError('Remote HTTP 404')
            return self.found

    def row(self, **over):
        r = {'status': 'Actioned', 'score': '3', 'next_step': 'Draft email reply', 'route': 'Partnerships',
             'url': 'https://www.notion.so/row1', 'id': 'row-1'}
        r.update(over); return r

    def test_agreement_note_and_buttons_point_at_pipeline_row(self):
        check = self.Check(self.row())
        review.reconcile(self.cfg, self.ledger, self.client, check, now=time.time())
        self.assertEqual(check.asked, [('demo@example.com', '2026-10-13')])
        channel, ts, buttons, notes = self.client.updates[-1]
        self.assertEqual((channel, ts), ('CSUB', '50.5'))
        self.assertTrue(notes[-1].startswith(':white_check_mark: Notion pipeline agrees: Needs triage · Score 3'))
        self.assertNotIn('log_notion', [b[0] for b in buttons])
        self.assertEqual(self.ledger.review('T1:CSUB:50.1')['notion_url'], 'https://www.notion.so/row1')
        self.assertTrue(json.loads(self.ledger.get('nv:T1:CSUB:50.1'))['agree'])
        review.reconcile(self.cfg, self.ledger, self.client, check, now=time.time() + 1000)
        self.assertEqual(len(check.asked), 1)  # Once per card.

    def test_disagreement_note(self):
        review.reconcile(self.cfg, self.ledger, self.client, self.Check(self.row(status='DQ', score='2', next_step='No action')),
                         now=time.time())
        note = self.client.updates[-1][3][-1]
        self.assertIn(':x: Notion pipeline differs: DQ · Score 2 · Partnerships · No action (this card: Needs triage · Score 3)', note)
        self.assertFalse(json.loads(self.ledger.get('nv:T1:CSUB:50.1'))['agree'])

    def test_waits_for_the_row_and_throttles(self):
        check = self.Check(None)
        t = time.time()
        review.reconcile(self.cfg, self.ledger, self.client, check, now=t)
        review.reconcile(self.cfg, self.ledger, self.client, check, now=t + 60)
        self.assertEqual(len(check.asked), 1)  # 15-minute throttle
        self.assertEqual(self.client.updates, [])
        self.assertIsNone(self.ledger.get('nv:T1:CSUB:50.1'))

    def test_failure_is_logged_not_fatal(self):
        review.reconcile(self.cfg, self.ledger, self.client, self.Check(fail=True), now=time.time())
        self.assertEqual(self.client.updates, [])

    def test_pipeline_decision_mapping(self):
        D = review.pipeline_decision
        self.assertEqual(D(self.row(status='DQ', score='3')), 'do_not_pursue')
        self.assertEqual(D(self.row(next_step='Point to resource', score='1')), 'redirect')
        self.assertEqual(D(self.row(next_step='Route to sales', score='1', status='Flagged')), 'redirect')
        self.assertEqual(D(self.row(score='4', next_step='Flag for review')), 'pursue')
        self.assertEqual(D(self.row(score='3', next_step='Route to supply')), 'triage')

    @patch.dict('os.environ', {'NOTION_TOKEN': 'secret_x'})
    def test_find_skips_unprocessed_bot_rows(self):
        def page(ref, notes, url):
            return {'url': url, 'id': url[-1], 'properties': {
                'Intake Ref': {'type': 'rich_text', 'rich_text': [{'plain_text': ref}]},
                'Quality Notes': {'type': 'rich_text', 'rich_text': [{'plain_text': notes}]},
                'Status': {'type': 'select', 'select': {'name': 'DQ'}},
                'Qualification Score': {'type': 'select', 'select': {'name': '2'}}}}
        with patch('review.api', return_value={'results': [page('tpa:C:1', 'bot notes', 'https://www.notion.so/a'),
                                                            page('slack:9.9', 'x', 'https://www.notion.so/b')]}) as api:
            found = review.PipelineCheck(dict(ICFG, notion_intake='INTAKE')).find('demo@example.com', '2026-10-13')
        self.assertEqual(found['url'], 'https://www.notion.so/b')
        flt = api.call_args.args[2]['filter']['and']
        self.assertEqual(flt[1], {'property': 'Submitted', 'date': {'on_or_after': '2026-10-10'}})
        with patch('review.api', return_value={'results': [page('tpa:C:1', 'Pipeline ref: slack:9.9', 'https://www.notion.so/a')]}):
            self.assertEqual(review.PipelineCheck(dict(ICFG, notion_intake='INTAKE')).find('d@e.f', '2026-10-13')['url'],
                             'https://www.notion.so/a')

    def test_lookup_failure_is_logged(self):
        class H:
            def lookup(self, text): raise ValueError('Remote HTTP 404')
        c = review.ReviewClients(ICFG, history=H())
        with patch('builtins.print') as p:
            self.assertEqual(c.lookup('x'), 'unavailable')
        self.assertIn('Remote HTTP 404', p.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
