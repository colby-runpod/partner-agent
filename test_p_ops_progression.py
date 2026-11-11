import json
import tempfile
import unittest
from unittest.mock import patch

from p_ops_approval import ApprovalLedger
from p_ops_progression import (ProgressionLedger, SlackDraftReviewClient, eligibility, load_routes,
                               prepare_task_drafts, publish_ready_reviews, task_bundle)


ROUTES = json.dumps([{
    "key": "leanscale", "channel_id": "C0CE711BW06",
    "channel_label": "#external-leanscale-fours-prm",
    "keywords": ["leanscale", "fours"], "recipients": ["U0B17K8H88M", "U03L35YRNSU"],
}])


def rich(value):
    return {"type": "rich_text", "rich_text": [{"plain_text": value}]}


def select(value):
    return {"type": "select", "select": {"name": value}}


def number(value):
    return {"type": "number", "number": value}


def task(**overrides):
    properties = {
        "Item": {"type": "title", "title": [{"plain_text": "Check in with LeanScale"}]},
        "Status": select("Next"), "Request Draft": {"type": "checkbox", "checkbox": True},
        "Draft Status": select("Requested"), "Approval Class": select("Communication"),
        "Work Mode": select("Prepare"), "Blocked": {"type": "checkbox", "checkbox": False},
        "Decision Required": rich(""), "Agent Confidence": number(0.92), "Risk Score": number(12),
        "Priority Score": number(75), "Alignment Confidence": select("High"),
        "Context": rich("LeanScale is defining the Fours to HubSpot data flow."),
        "Next Step": rich("Ask Ian and Eduardo for status and confirm the next checkpoint."),
        "Next Step Date": {"type": "date", "date": {"start": "2026-11-11"}},
        "Due Date": {"type": "date", "date": None},
        "Definition of Done": rich("Current status and next checkpoint are confirmed."),
        "Evidence Links": rich("https://notion.example/meeting"),
        "Source Link": {"type": "url", "url": "https://calendar.google.com/event"},
        "Automation Notes": rich("Captured from a calendar reminder."),
    }
    properties.update(overrides)
    return {"id": "11111111-1111-1111-1111-111111111111", "url": "https://notion.example/task",
            "properties": properties}


class FakeDraft:
    def draft(self, bundle):
        return {"summary": "Follow up on the current implementation status.",
                "context_points": ["PRM setup is complete", "HubSpot data flow is still being defined"],
                "message": "quick check-in on where things stand with the HubSpot data flow and what you need from us next.",
                "evidence_used": [bundle["task"]["url"]], "assumptions": [], "missing_context": [],
                "confidence": 0.91}


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.progression = ProgressionLedger(self.tmp.name + "/p_ops.sqlite3")
        self.messages = ApprovalLedger(self.tmp.name + "/p_ops.sqlite3")
        self.routes = load_routes(ROUTES)

    def test_only_explicit_low_risk_communication_is_eligible(self):
        decision = eligibility(task(), self.routes)
        self.assertTrue(decision["eligible"])
        self.assertEqual("leanscale", decision["route"].key)
        blocked = eligibility(task(**{"Blocked": {"type": "checkbox", "checkbox": True}}), self.routes)
        self.assertFalse(blocked["eligible"])
        self.assertIn("task_blocked", blocked["reasons"])
        missing_request = eligibility(task(**{"Request Draft": {"type": "checkbox", "checkbox": False}}), self.routes)
        self.assertIn("draft_not_requested", missing_request["reasons"])

    def test_route_must_be_unique_and_exactly_allowlisted(self):
        routes = load_routes(json.dumps([
            {"key": "aa", "channel_id": "C12345678", "channel_label": "#a",
             "keywords": ["leanscale"], "recipients": []},
            {"key": "bb", "channel_id": "C87654321", "channel_label": "#b",
             "keywords": ["fours"], "recipients": []},
        ]))
        decision = eligibility(task(), routes)
        self.assertFalse(decision["eligible"])
        self.assertIn("destination_not_uniquely_allowlisted", decision["reasons"])

    def test_preparation_creates_exact_unsent_message_once(self):
        result = prepare_task_drafts([task()], self.routes, self.progression, self.messages, FakeDraft())
        self.assertEqual(1, len(result))
        message = self.messages.rows()[0]
        self.assertEqual("needs_review", message["state"])
        self.assertEqual("C0CE711BW06", message["destination"])
        self.assertTrue(message["content"].startswith("<@U0B17K8H88M> <@U03L35YRNSU>"))
        self.assertEqual([], prepare_task_drafts([task()], self.routes, self.progression, self.messages, FakeDraft()))

    def test_missing_context_is_held_without_creating_message(self):
        class Uncertain(FakeDraft):
            def draft(self, bundle):
                value = super().draft(bundle); value["missing_context"] = ["Current owner"]
                return value
        result = prepare_task_drafts([task()], self.routes, self.progression, self.messages, Uncertain())
        self.assertEqual("error", result[0][1])
        self.assertEqual([], self.messages.rows())

    def test_review_notification_links_task_destination_and_console(self):
        prepare_task_drafts([task()], self.routes, self.progression, self.messages, FakeDraft())
        row, message = self.progression.rows()[0], self.messages.rows()[0]
        with patch("p_ops_progression.api", return_value={"ts": "123.4"}) as mocked:
            client = SlackDraftReviewClient("C0C7HG8KYFL", "T06F03V8NUQ", "https://agents.example", "U1", "xoxb")
            self.assertEqual([(row["id"], "notified")], publish_ready_reviews(self.progression, self.messages, client))
        payload = mocked.call_args.args[2]
        self.assertEqual("C0C7HG8KYFL", payload["channel"])
        actions = payload["blocks"][-1]["elements"]
        self.assertEqual(["https://agents.example/review#messages",
                          "https://app.slack.com/client/T06F03V8NUQ/C0CE711BW06",
                          "https://notion.example/task"], [item["url"] for item in actions])
        self.assertEqual("notified", self.progression.rows()[0]["state"])

    def test_bundle_contains_no_external_source_fetch(self):
        bundle = task_bundle(task(), self.routes[0])
        self.assertTrue(bundle["constraints"]["preparation_only"])
        self.assertFalse(bundle["constraints"]["external_send_authorized"])


if __name__ == "__main__":
    unittest.main()
