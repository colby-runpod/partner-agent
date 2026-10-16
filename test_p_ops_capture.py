import hmac
import json
import hashlib
import tempfile
import time
import unittest

from p_ops_capture import CaptureStore, REACTION, accept_reaction, receive, resolve_capture


CFG = {"enabled": True, "team_id": "T1", "app_id": "A1", "secret": "secret",
       "channel_ids": {"C1"}, "user_ids": {"U1"}}


def body(**event_changes):
    event = {"type": "reaction_added", "reaction": REACTION, "user": "U1",
             "item": {"type": "message", "channel": "C1", "ts": "123.456"}}
    event.update(event_changes)
    return {"team_id": "T1", "api_app_id": "A1", "event_id": "Ev1", "event": event}


class FakeSlack:
    def message(self, channel, ts):
        return {"ts": ts, "thread_ts": "123.000", "user": "U2", "text": "Follow up with this partner"}
    def permalink(self, channel, ts):
        return "https://example.slack.com/archives/C1/p123456"


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

    def test_capture_is_idempotent_and_stores_source_evidence(self):
        event = accept_reaction(body(), CFG)
        self.assertTrue(self.store.enqueue(event))
        self.assertFalse(self.store.enqueue(event))
        self.assertEqual("captured", resolve_capture(self.store, "Ev1", FakeSlack()))
        row = self.store.get("Ev1")
        self.assertEqual("Follow up with this partner", row["source_text"])
        self.assertEqual("123.000", row["thread_ts"])

    def test_resolution_failure_does_not_retry(self):
        class Broken:
            def message(self, channel, ts):
                raise TimeoutError()
        self.store.enqueue(accept_reaction(body(), CFG))
        self.assertEqual("review", resolve_capture(self.store, "Ev1", Broken()))
        with self.assertRaises(ValueError):
            resolve_capture(self.store, "Ev1", FakeSlack())

    def test_receiver_requires_valid_slack_signature(self):
        raw = json.dumps(body()).encode()
        timestamp = str(int(time.time()))
        signature = "v0=" + hmac.new(CFG["secret"].encode(),
            b"v0:" + timestamp.encode() + b":" + raw, hashlib.sha256).hexdigest()
        self.assertTrue(receive(raw, timestamp, signature, CFG, self.store))
        self.assertFalse(receive(raw, timestamp, "v0=bad", CFG, self.store))


if __name__ == "__main__":
    unittest.main()
