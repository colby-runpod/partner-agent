import os
import tempfile
import unittest
from unittest.mock import patch

from p_ops_linear import LinearClient, LinearError, LinearLedger, create_approved_issue


class FakeLinear:
    def __init__(self, duplicates=None, fail=False):
        self.duplicates = duplicates or []
        self.fail = fail
        self.created = 0
    def find_by_source(self, source_url):
        return self.duplicates
    def create_issue(self, team_id, title, description):
        self.created += 1
        if self.fail:
            raise TimeoutError()
        return {"id": "I1", "url": "https://linear.app/runpod/issue/PAR-1/task"}


class LinearTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = LinearLedger(self.tmp.name + "/linear.sqlite3")
        self.action = self.ledger.draft("https://notion.so/task", "TEAM", "Implement handoff",
            "Partner handoff needs product work.", "Removes a launch blocker.", "Handoff is recorded and tested.")

    def tearDown(self):
        self.tmp.cleanup()

    def test_write_is_disabled_by_default(self):
        self.ledger.approve(self.action)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                create_approved_issue(FakeLinear(), self.ledger, self.action)

    def test_approved_draft_searches_then_creates(self):
        self.ledger.approve(self.action)
        client = FakeLinear()
        with patch.dict(os.environ, {"P_OPS_LINEAR_WRITE": "true"}):
            self.assertEqual("created", create_approved_issue(client, self.ledger, self.action))
        self.assertEqual(1, client.created)
        self.assertEqual("created", self.ledger.get(self.action)["state"])

    def test_duplicate_source_prevents_creation(self):
        self.ledger.approve(self.action)
        client = FakeLinear([{"id": "OLD", "url": "https://linear.app/runpod/issue/PAR-0/old"}])
        with patch.dict(os.environ, {"P_OPS_LINEAR_WRITE": "true"}):
            self.assertEqual("duplicate", create_approved_issue(client, self.ledger, self.action))
        self.assertEqual(0, client.created)

    def test_uncertain_creation_does_not_retry(self):
        self.ledger.approve(self.action)
        client = FakeLinear(fail=True)
        with patch.dict(os.environ, {"P_OPS_LINEAR_WRITE": "true"}):
            self.assertEqual("review", create_approved_issue(client, self.ledger, self.action))
            with self.assertRaises(ValueError):
                create_approved_issue(client, self.ledger, self.action)
        self.assertEqual(1, client.created)

    def test_graphql_errors_are_failures_even_with_http_success(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return None
            def read(self): return b'{"data": {}, "errors": [{"message": "bad"}]}'
        client = LinearClient("token", opener=lambda request, timeout: Response())
        with self.assertRaises(LinearError):
            client.teams()


if __name__ == "__main__":
    unittest.main()
