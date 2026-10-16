import json
import os
import tempfile
import unittest
from unittest.mock import patch

from p_ops_inbox import InboxLedger, create_approved_task, proposal_from_capture


CAPTURE = {"event_id": "Ev1", "state": "captured", "source_url": "https://slack.example/source",
           "source_text": "<@U1> Follow up with Redis\nConfirm the integration owner."}


class FakeNotion:
    def __init__(self, existing=None, fail=False):
        self.existing = existing or []
        self.fail = fail
        self.created = 0
    def find_by_context(self, source_url):
        return self.existing
    def create_inbox(self, fields):
        self.created += 1
        if self.fail: raise TimeoutError()
        return {"id": "N1", "url": "https://notion.so/N1"}


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = InboxLedger(self.tmp.name + "/p_ops.sqlite3")
        self.action = self.ledger.propose(CAPTURE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_capture_becomes_conservative_inbox_proposal(self):
        fields = proposal_from_capture(CAPTURE)
        self.assertEqual("Follow up with Redis", fields["title"])
        self.assertEqual("Inbox", fields["status"])
        self.assertIn(CAPTURE["source_url"], fields["context"])
        self.assertEqual("needs_review", self.ledger.get(self.action)["state"])
        self.assertEqual(self.action, self.ledger.propose(CAPTURE))

    def test_approved_proposal_searches_then_creates(self):
        self.ledger.approve(self.action)
        client = FakeNotion()
        with patch.dict(os.environ, {"P_OPS_NOTION_CREATE": "true"}):
            self.assertEqual("created", create_approved_task(client, self.ledger, self.action))
        self.assertEqual(1, client.created)
        self.assertEqual("created", self.ledger.get(self.action)["state"])

    def test_duplicate_prevents_create(self):
        self.ledger.approve(self.action)
        client = FakeNotion([{"id": "OLD", "url": "https://notion.so/OLD"}])
        with patch.dict(os.environ, {"P_OPS_NOTION_CREATE": "true"}):
            self.assertEqual("duplicate", create_approved_task(client, self.ledger, self.action))
        self.assertEqual(0, client.created)

    def test_uncertain_create_reconciles_or_stops(self):
        self.ledger.approve(self.action)
        client = FakeNotion(fail=True)
        with patch.dict(os.environ, {"P_OPS_NOTION_CREATE": "true"}):
            self.assertEqual("review", create_approved_task(client, self.ledger, self.action))
            with self.assertRaises(ValueError):
                create_approved_task(client, self.ledger, self.action)
        self.assertEqual(1, client.created)

    def test_creation_is_disabled_by_default(self):
        self.ledger.approve(self.action)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                create_approved_task(FakeNotion(), self.ledger, self.action)


if __name__ == "__main__":
    unittest.main()
