import datetime as dt
import io
import json
import os
import tempfile
import unittest
from unittest.mock import Mock, patch

import review
import working_task_brief as brief


CFG = {
    'working_task_brief_enabled': True,
    'working_task_brief_channel': 'C0C7HG8KYFL',
    'working_task_brief_hour': 8,
    'working_task_brief_minute': 15,
    'working_task_brief_max_age_hours': 3,
    'working_task_run_log': 'runs',
    'working_tasks_source': 'tasks',
    'feedback_users': {'U-COLBY'},
    'users': {'U-COLBY'},
}


def rich(value):
    return {'rich_text': [{'plain_text': value}]}


def snapshot(now='2026-11-05T14:30:00Z'):
    return {
        'version': 1, 'as_of': now, 'safe_for_briefing': True, 'task_updates_complete': True,
        'coverage': {'slack': 'Complete', 'gmail': 'Complete', 'calendar_meetings': 'Partial'},
        'today': [{'wi_id': 'WI-123', 'task_url': 'https://app.notion.com/p/runpod/Test-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                   'owner': 'Colby', 'checkpoint': 'Review today', 'why_today': 'Decision blocks implementation'}],
        'progress': [], 'risks_decisions': [], 'cadence': [], 'data_quality': [],
    }


def run_page(data=None, status='Complete'):
    return {'properties': {
        'Status': {'select': {'name': status}},
        'Finished At': {'date': {'start': '2026-11-05T14:31:00Z'}},
        'Briefing Snapshot': rich(json.dumps(data or snapshot())),
    }}


class FakeNotion:
    def __init__(self, page=None, edited='2026-11-05T14:30:00Z'):
        self.run = page or run_page()
        self.edited = edited

    def integrity_run(self, source, date):
        return self.run

    def page(self, page_id):
        return {'last_edited_time': self.edited,
                'properties': {'Item ID': {'unique_id': {'prefix': 'WI', 'number': 123}}}}


class Client:
    def __init__(self):
        self.said = []
        self.preflight = []

    def slack_preflight(self, channel):
        self.preflight.append(channel)

    def say(self, channel, text):
        self.said.append((channel, text))
        return '100.200'


class WorkingTaskBriefTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.ledger = review.Ledger(tmp.name + '/ledger.sqlite')
        self.client = Client()
        self.now = dt.datetime(2026, 11, 5, 15, 15, tzinfo=dt.timezone.utc)  # 8:15 Mountain

    def test_valid_snapshot_posts_one_brief_and_rereads_task(self):
        notion = FakeNotion()
        self.assertTrue(brief.run(CFG, self.ledger, self.client, notion=notion, now_utc=self.now))
        self.assertIn('*Working Task brief - November 5, 2026*', self.client.said[0][1])
        self.assertIn('<https://app.notion.com/p/runpod/Test-', self.client.said[0][1])
        state = json.loads(self.ledger.get('working_task_brief_state'))
        self.assertEqual((state['kind'], state['status'], state['receipt']), ('brief', 'sent', '100.200'))
        self.assertFalse(brief.run(CFG, self.ledger, self.client, notion=notion, now_utc=self.now))

    def test_missing_safe_flag_posts_hold_not_brief(self):
        data = snapshot(); data['safe_for_briefing'] = False
        brief.run(CFG, self.ledger, self.client, notion=FakeNotion(run_page(data)), now_utc=self.now)
        self.assertIn('Briefing held', self.client.said[0][1])
        self.assertIn('not marked safe', self.client.said[0][1])
        self.assertEqual(json.loads(self.ledger.get('working_task_brief_state'))['kind'], 'hold')

    def test_partial_integrity_can_post_only_when_explicitly_safe(self):
        brief.run(CFG, self.ledger, self.client, notion=FakeNotion(run_page(status='Partial')), now_utc=self.now)
        self.assertIn('Integrity: Partial', self.client.said[0][1])
        self.assertNotIn('Briefing held', self.client.said[0][1])

    def test_stale_snapshot_and_newer_task_are_held(self):
        data = snapshot('2026-11-05T11:00:00Z')
        brief.run(CFG, self.ledger, self.client,
                  notion=FakeNotion(run_page(data), edited='2026-11-05T14:35:00Z'), now_utc=self.now)
        self.assertIn('more than three hours old', self.client.said[0][1])
        self.assertIn('changed after the verified snapshot', self.client.said[0][1])

    def test_snapshot_contract_rejects_unknown_version_and_future_time(self):
        data = snapshot('2026-11-05T16:00:01Z'); data['version'] = 2
        brief.run(CFG, self.ledger, self.client, notion=FakeNotion(run_page(data)), now_utc=self.now)
        text = self.client.said[0][1]
        self.assertIn('snapshot version is unsupported', text)
        self.assertIn('snapshot timestamp is in the future', text)

    def test_preflight_failure_posts_hold_and_never_claims_normal_brief(self):
        self.client.slack_preflight = Mock(side_effect=ValueError('Slack channel read verification failed'))
        brief.run(CFG, self.ledger, self.client, notion=FakeNotion(), now_utc=self.now)
        self.assertIn('Briefing held', self.client.said[0][1])
        self.assertIn('Slack channel read verification failed', self.client.said[0][1])

    def test_unconfirmed_post_is_not_retried(self):
        self.client.say = Mock(side_effect=TimeoutError())
        brief.run(CFG, self.ledger, self.client, notion=FakeNotion(), now_utc=self.now)
        self.assertEqual(json.loads(self.ledger.get('working_task_brief_state'))['status'], 'unconfirmed')
        self.assertFalse(brief.run(CFG, self.ledger, self.client, notion=FakeNotion(), now_utc=self.now))
        self.client.say.assert_called_once()

    def test_feedback_parser_requires_identifier_or_source(self):
        self.assertEqual(brief.parse_feedback('focus WI-123 today')['type'], 'focus')
        self.assertEqual(brief.parse_feedback('snooze WI-123 until 2026-11-20')['value'], '2026-11-20')
        self.assertEqual(brief.parse_feedback('decision cloud-owner: use Tanya')['decision_key'], 'cloud-owner')
        self.assertIsNone(brief.parse_feedback('add: follow up with them tomorrow'))
        self.assertEqual(brief.parse_feedback('add: follow up https://app.notion.com/p/abc')['type'], 'add')

    def test_feedback_only_accepts_colby_in_current_brief_thread(self):
        self.ledger.put('working_task_brief_state', json.dumps({'kind': 'brief', 'receipt': '100.200'}))
        body = {'event_id': 'Ev1', 'event': {'type': 'message', 'channel': 'C0C7HG8KYFL',
                'thread_ts': '100.200', 'ts': '101.1', 'user': 'U-COLBY', 'text': 'deprioritize WI-123'}}
        event = brief.accept_feedback(body, CFG, self.ledger)
        self.assertEqual(event['command']['wi_id'], 'WI-123')
        body['event']['thread_ts'] = 'wrong'
        self.assertIsNone(brief.accept_feedback(body, CFG, self.ledger))

    def test_slack_preflight_reads_scopes_from_auth_response_header(self):
        class Response(io.BytesIO):
            def __init__(self):
                super().__init__(json.dumps({'ok': True, 'user_id': 'U-BOT'}).encode())
                self.headers = {'X-OAuth-Scopes': 'app_mentions:read,groups:history,chat:write'}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        client = review.ReviewClients({})
        replies = [
            {'ok': True, 'channel': {'id': 'C0C7HG8KYFL'}},
            {'ok': True, 'messages': []},
        ]
        with patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'xoxb-test'}), \
             patch('review.urllib.request.urlopen', return_value=Response()), \
             patch('review.api', side_effect=replies) as slack_api:
            self.assertTrue(client.slack_preflight('C0C7HG8KYFL'))
        self.assertIn('conversations.info', slack_api.call_args_list[0].args[0])
        self.assertIn('conversations.history', slack_api.call_args_list[1].args[0])

    def test_slack_preflight_rejects_missing_post_scope(self):
        class Response(io.BytesIO):
            headers = {'X-OAuth-Scopes': 'groups:history'}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self.close()

        response = Response(json.dumps({'ok': True, 'user_id': 'U-BOT'}).encode())
        with patch.dict(os.environ, {'SLACK_BOT_TOKEN': 'xoxb-test'}), \
             patch('review.urllib.request.urlopen', return_value=response), \
             patch('review.api', side_effect=[{'channel': {'id': 'C0C7HG8KYFL'}}, {'messages': []}]):
            with self.assertRaisesRegex(ValueError, 'chat:write'):
                review.ReviewClients({}).slack_preflight('C0C7HG8KYFL')


if __name__ == '__main__':
    unittest.main()
