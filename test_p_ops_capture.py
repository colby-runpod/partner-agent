import hmac
import json
import hashlib
import tempfile
import time
import unittest
from unittest.mock import patch

from p_ops_capture import (CaptureStore, REACTION, SlackCaptureClient, accept_capture, accept_mention, acknowledge_capture,
                           accept_reaction, receive, resolve_capture)


CFG = {"enabled": True, "team_id": "T1", "app_id": "A1", "secret": "secret",
       "channel_ids": {"C1"}, "user_ids": {"U1"}}


def body(**event_changes):
    event = {"type": "reaction_added", "reaction": REACTION, "user": "U1",
             "item": {"type": "message", "channel": "C1", "ts": "123.456"}}
    event.update(event_changes)
    return {"team_id": "T1", "api_app_id": "A1", "event_id": "Ev1", "event": event}


class FakeSlack:
    def __init__(self): self.acks = []
    def message(self, channel, ts):
        return {"ts": ts, "thread_ts": "123.000", "user": "U2", "text": "Follow up with this partner"}
    def permalink(self, channel, ts):
        return "https://example.slack.com/archives/C1/p123456"
    def thread(self, channel, ts, latest=""):
        return [
            {"ts": ts, "user": "U2", "text": "Follow up with this partner"},
            {"ts": "123.456", "user": "U1", "text": "<@UBOT> what should we do next?"},
        ]
    def acknowledge(self, channel, thread_ts, requester_user_id):
        self.acks.append((channel, thread_ts, requester_user_id))
        return "124.000"


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = CaptureStore(self.tmp.name + "/capture.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_exact_allowlisted_reaction_is_accepted(self):
        self.assertIsNotNone(accept_reaction(body(), CFG))
        self.assertIsNone(accept_reaction(body(reaction="handshake"), CFG))
        self.assertIsNone(accept_reaction(body(user="U9"), CFG))
        denied = dict(CFG, enabled=False)
        self.assertIsNone(accept_reaction(body(), denied))

    def test_allowlisted_mention_captures_the_containing_thread(self):
        mention = {"team_id": "T1", "api_app_id": "A1", "event_id": "EvM",
                   "event": {"type": "app_mention", "channel": "C1", "user": "U1",
                             "ts": "123.456", "thread_ts": "123.000",
                             "text": "<@UBOT> evaluate this"}}
        event = accept_mention(mention, CFG)
        self.assertEqual("mention", event["trigger"])
        self.assertEqual("123.000", event["ts"])
        self.assertIsNotNone(accept_capture(mention, CFG))
        mention["event"]["user"] = "U9"
        self.assertIsNone(accept_mention(mention, CFG))

    def test_capture_is_idempotent_and_stores_source_evidence(self):
        event = accept_reaction(body(), CFG)
        self.assertTrue(self.store.enqueue(event))
        self.assertFalse(self.store.enqueue(event))
        self.assertEqual("captured", resolve_capture(self.store, "Ev1", FakeSlack()))
        row = self.store.get("Ev1")
        self.assertIn("Follow up with this partner", row["source_text"])
        self.assertIn("what should we do next?", row["source_text"])
        self.assertEqual("123.000", row["thread_ts"])

    def test_resolution_failure_does_not_retry(self):
        class Broken:
            def message(self, channel, ts):
                raise TimeoutError()
        self.store.enqueue(accept_reaction(body(), CFG))
        self.assertEqual("review", resolve_capture(self.store, "Ev1", Broken()))
        with self.assertRaises(ValueError):
            resolve_capture(self.store, "Ev1", FakeSlack())

    def test_acknowledgement_is_posted_once_in_the_source_thread(self):
        client = FakeSlack()
        self.store.enqueue(accept_reaction(body(), CFG))
        resolve_capture(self.store, "Ev1", client)
        self.assertEqual("acknowledged", acknowledge_capture(self.store, "Ev1", client))
        self.assertEqual("skipped", acknowledge_capture(self.store, "Ev1", client))
        self.assertEqual([("C1", "123.000", "U1")], client.acks)

    def test_acknowledgement_tags_requester_and_replies_in_source_thread(self):
        with patch("p_ops_capture.api", return_value={"ts": "124.000"}) as mocked:
            SlackCaptureClient("xoxb-test").acknowledge("C1", "123.000", "U1")
        payload = mocked.call_args.args[2]
        self.assertEqual("123.000", payload["thread_ts"])
        self.assertTrue(payload["text"].startswith("<@U1> Captured."))
        self.assertTrue(payload["mrkdwn"])

    def test_receiver_requires_valid_slack_signature(self):
        raw = json.dumps(body()).encode()
        timestamp = str(int(time.time()))
        signature = "v0=" + hmac.new(CFG["secret"].encode(),
            b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        self.assertTrue(receive(raw, timestamp, signature, CFG, self.store))
        self.assertFalse(receive(raw, timestamp, "v0=bad", CFG, self.store))


if __name__ == "__main__":
    unittest.main()
