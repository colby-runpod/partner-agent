import os
import tempfile
import unittest
from unittest.mock import patch

from p_ops_approval import ApprovalLedger, send_message


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = ApprovalLedger(self.tmp.name + "/ledger.sqlite3")
        self.message = self.ledger.create("slack", "C123", "Quick update")

    def tearDown(self):
        self.tmp.cleanup()

    def test_approval_and_send_authorization_are_separate(self):
        self.ledger.approve(self.message)
        self.assertEqual("approved", self.ledger.get(self.message)["state"])
        with patch.dict(os.environ, {"P_OPS_SEND_MESSAGES": "true"}):
            with self.assertRaises(ValueError):
                send_message(self.ledger, self.message, lambda row: "123.4")
        self.ledger.authorize_send(self.message)
        with patch.dict(os.environ, {"P_OPS_SEND_MESSAGES": "true"}):
            self.assertEqual("sent", send_message(self.ledger, self.message, lambda row: "123.4"))

    def test_revision_invalidates_approval(self):
        self.ledger.approve(self.message)
        self.ledger.revise(self.message, content="Edited update")
        row = self.ledger.get(self.message)
        self.assertEqual("needs_review", row["state"])
        self.assertIsNone(row["approved_hash"])
        with self.assertRaises(ValueError):
            self.ledger.authorize_send(self.message)

    def test_destination_change_invalidates_approval(self):
        self.ledger.approve(self.message)
        self.ledger.revise(self.message, destination="C999")
        self.assertEqual("needs_review", self.ledger.get(self.message)["state"])

    def test_sending_is_disabled_by_default(self):
        self.ledger.approve(self.message)
        self.ledger.authorize_send(self.message)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                send_message(self.ledger, self.message, lambda row: "123.4")

    def test_uncertain_send_is_not_retried(self):
        self.ledger.approve(self.message)
        self.ledger.authorize_send(self.message)
        calls = []
        def uncertain(row):
            calls.append(row["id"])
            raise TimeoutError()
        with patch.dict(os.environ, {"P_OPS_SEND_MESSAGES": "true"}):
            self.assertEqual("review", send_message(self.ledger, self.message, uncertain))
            with self.assertRaises(ValueError):
                send_message(self.ledger, self.message, uncertain)
        self.assertEqual(1, len(calls))


if __name__ == "__main__":
    unittest.main()
