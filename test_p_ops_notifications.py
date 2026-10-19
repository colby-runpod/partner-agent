import json
import tempfile
import unittest

from p_ops_notifications import ReviewNotificationLedger


def shadow_row(item_id="triage-1"):
    return {
        "id": item_id,
        "decision_json": json.dumps({
            "decision": {
                "title": "Confirm Redis POC owner",
                "summary": "The POC has a concrete follow-up.",
                "disposition": "create_child_action",
                "next_step": "Confirm the owner and checkpoint.",
            },
            "review_required": True,
            "policy_reasons": ["pilot_shadow_mode"],
        }),
        "bundle_json": json.dumps({"source": {"url": "https://slack.example/source"}}),
    }


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = ReviewNotificationLedger(self.tmp.name + "/p_ops.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_queue_is_deduplicated_and_restricted_to_expected_channel(self):
        row = shadow_row()
        row["bundle_json"] = json.dumps({"source": {"url": "https://slack.example/source"},
                                         "existing_tasks": [{"url": "https://notion.example/task"}]})
        self.ledger.queue(row, "C-APPROVED", "https://p-ops.example", "123.456")
        self.ledger.queue(row, "C-APPROVED", "https://p-ops.example", "123.456")
        self.assertEqual(1, len(self.ledger.rows()))
        content = self.ledger.get("triage-1")["content"]
        self.assertEqual("123.456", self.ledger.get("triage-1")["thread_ts"])
        self.assertIn("https://notion.example/task", content)
        self.assertIn("https://p-ops.example/review#approvals", content)
        calls = []
        result = self.ledger.publish("triage-1", "C-DIFFERENT", lambda row: calls.append(row))
        self.assertEqual("review", result)
        self.assertEqual([], calls)
        self.assertEqual("configured_channel_changed", self.ledger.rows()[0]["error"])

    def test_uncertain_delivery_is_not_retried(self):
        self.ledger.queue(shadow_row(), "C-APPROVED")
        def fail(_): raise TimeoutError()
        self.assertEqual("review", self.ledger.publish("triage-1", "C-APPROVED", fail))
        with self.assertRaises(ValueError):
            self.ledger.publish("triage-1", "C-APPROVED", lambda row: "later")


if __name__ == "__main__":
    unittest.main()
