import json
from pathlib import Path
import unittest

from p_ops_eval import bundle_from_case, load_cases, score_case, summary
from p_ops_triage import ResponsesTriageClient, TriageDecision, guard_decision


def decision(**changes):
    raw = {
        "disposition": "create_task", "title": "Follow up with Redis", "summary": "A tracked follow-up is needed.",
        "strategic_value": "medium", "objective_id": None, "workstream_id": None,
        "partner_ids": ["partner-redis"], "priority": "medium", "severity": "low", "work_mode": "Execute",
        "canonical_task_id": None, "parent_task_id": None, "meeting_id": None, "owner": "Colby",
        "owning_function": "Partnerships", "supporting_functions": [], "next_step": "Confirm the POC owner.",
        "next_step_date": None, "due_date": None, "definition_of_done": "The POC owner and next checkpoint are recorded.",
        "dependency": "", "waiting_on": "", "requires_linear": False, "requires_message": False,
        "preparation_required": True, "proposed_actions": ["create_notion_task"],
        "evidence_refs": ["src-1", "partner-redis"], "assumptions": [], "missing_evidence": [],
        "review_reasons": [], "confidence": 0.92, "rationale": "The source contains a specific action and outcome."
    }
    raw.update(changes)
    return raw


BUNDLE = {"source": {"id": "src-1", "text": "Follow up with Redis"}, "existing_tasks": [],
          "meetings": [], "objectives": [], "workstreams": [],
          "partners": [{"id": "partner-redis", "name": "Redis"}], "explicit_deadlines": [],
          "risk_classes": [], "explicit_cancellation": False}


class Response:
    def __init__(self, payload): self.payload = payload
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def read(self): return json.dumps(self.payload).encode()


class TriageTests(unittest.TestCase):
    def test_strict_schema_rejects_missing_or_extra_fields(self):
        raw = decision()
        raw.pop("rationale")
        with self.assertRaises(ValueError): TriageDecision.from_dict(raw)
        raw = decision(extra="bad")
        with self.assertRaises(ValueError): TriageDecision.from_dict(raw)

    def test_guard_rejects_invented_due_date_and_relationships(self):
        guarded = guard_decision(decision(due_date="2026-10-31", objective_id="made-up"), BUNDLE)
        self.assertIn("due_date_not_explicitly_supported", guarded.violations)
        self.assertIn("objective_not_in_evidence", guarded.violations)
        self.assertFalse(guarded.executable)

    def test_message_linear_and_sensitive_work_require_review(self):
        bundle = dict(BUNDLE, risk_classes=["credit"])
        guarded = guard_decision(decision(disposition="create_or_link_linear", requires_linear=True,
            requires_message=True, proposed_actions=["prepare_linear_issue", "prepare_message"]), bundle)
        self.assertIn("message_requires_review", guarded.policy_reasons)
        self.assertIn("linear_creation_requires_review_during_pilot", guarded.policy_reasons)
        self.assertTrue(any(reason.startswith("sensitive_action:") for reason in guarded.policy_reasons))

    def test_meeting_without_preparation_cannot_create_redundant_task(self):
        bundle = dict(BUNDLE, meetings=[{"id": "meet-1", "date": "2026-10-16"}])
        guarded = guard_decision(decision(meeting_id="meet-1", preparation_required=False), bundle)
        self.assertIn("redundant_task_for_scheduled_meeting", guarded.violations)

    def test_merge_requires_canonical_task_from_evidence(self):
        guarded = guard_decision(decision(disposition="merge_task", canonical_task_id="unknown"), BUNDLE)
        self.assertIn("canonical_task_not_in_evidence", guarded.violations)

    def test_responses_client_parses_schema_valid_output(self):
        payload = {"status": "completed", "output": [{"content": [
            {"type": "output_text", "text": json.dumps(decision())}]}]}
        client = ResponsesTriageClient("key", "model", opener=lambda request, timeout: Response(payload))
        self.assertEqual("create_task", client.analyze(BUNDLE).disposition)

    def test_eval_corpus_has_25_valid_cases_and_history(self):
        path = Path(__file__).with_name("evals") / "p_ops_triage_cases.jsonl"
        cases = load_cases(path)
        report = summary(cases)
        self.assertEqual(25, report["cases"])
        self.assertGreaterEqual(report["historical"], 15)
        self.assertFalse(report["live_model_run"])

    def test_eval_scores_expected_disposition_and_review(self):
        case = {"id": "c", "name": "case", "provenance": "policy_edge",
                "source": {"id": "src-1", "text": "Follow up"},
                "partners": [{"id": "partner-redis"}],
                "expected": {"dispositions": ["create_task"], "review_required": True}}
        guarded = guard_decision(decision(), bundle_from_case(case), pilot=True)
        self.assertTrue(score_case(case, guarded)["passed"])


if __name__ == "__main__":
    unittest.main()
