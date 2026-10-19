import hashlib
import hmac
import io
import json
import tempfile
import time
import unittest
from unittest.mock import patch
import urllib.parse

from p_ops_workflow import (ApprovalFlowLedger, SlackApprovalClient, accept_action, accept_context_reply,
                            process_flows, receive_interaction, step_blocks)


CFG = {"review_interactive": True, "team_id": "T1", "app_id": "A1", "secret": "secret",
       "review_channel": "C-REVIEW", "review_user_ids": {"U1"}}


def shadow_row():
    return {
        "id": "triage-1",
        "decision_json": json.dumps({"decision": {
            "title": "Prepare Suger onboarding", "summary": "Implementation prep is ready.",
            "disposition": "route_department", "next_step": "Approve the onboarding scope.",
            "owning_function": "Marketing Ops", "owner": "", "supporting_functions": ["Data"],
            "requires_linear": True, "requires_message": True,
            "proposed_actions": ["prepare_handoff", "prepare_linear_issue", "prepare_message"],
        }, "review_required": True, "policy_reasons": ["pilot_shadow_mode"]}),
        "bundle_json": json.dumps({
            "source": {"url": "https://slack.example/source", "requester_user_id": "U1"},
            "existing_tasks": [{"url": "https://notion.example/task"}],
        }),
    }


def click(flow_id, step_key="recommendation", action="p_ops_approve", message_ts="200.1", user="U1", action_ts="300.1"):
    return {"type": "block_actions", "team": {"id": "T1"}, "api_app_id": "A1",
            "user": {"id": user}, "channel": {"id": "C-REVIEW"}, "message": {"ts": message_ts},
            "actions": [{"action_id": action, "value": flow_id + ":" + step_key, "action_ts": action_ts}]}


class FakeSlack:
    def __init__(self): self.steps, self.prompts, self.outcomes = [], [], []
    def post_step(self, flow, step):
        self.steps.append((flow["id"], step["step_key"])); return "200.%d" % len(self.steps)
    def post_context_prompt(self, flow): self.prompts.append(flow["id"])
    def post_outcome(self, flow, outcome): self.outcomes.append((flow["id"], outcome))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger = ApprovalFlowLedger(self.tmp.name + "/p_ops.sqlite3")
        self.flow_id = self.ledger.create(shadow_row(), "C-REVIEW", "100.1", "https://p-ops.example")

    def test_flow_keeps_links_and_builds_sequential_questions(self):
        flow = self.ledger.flow(self.flow_id)
        self.assertEqual("U1", flow["requester_user_id"])
        links = json.loads(flow["links_json"])
        self.assertEqual(["Source", "Open task", "Open p-OPS"], [row["label"] for row in links])
        with self.ledger.connect() as db:
            steps = db.execute("SELECT step_key FROM p_ops_approval_steps WHERE flow_id=? ORDER BY position",
                               (self.flow_id,)).fetchall()
        self.assertEqual(["recommendation", "handoff", "linear", "message_direction"],
                         [row["step_key"] for row in steps])
        blocks = step_blocks(flow, self.ledger.current_step(self.flow_id))
        self.assertIn("<@U1>", blocks[0]["text"]["text"])
        self.assertEqual(["p_ops_approve", "p_ops_decline", "p_ops_context", "p_ops_hold"],
                         [row["action_id"] for row in blocks[-1]["elements"]])

    def test_question_tags_requester_and_stays_in_origin_thread(self):
        flow = self.ledger.flow(self.flow_id)
        step = self.ledger.current_step(self.flow_id)
        with patch("p_ops_workflow.api", return_value={"ts": "200.1"}) as mocked:
            SlackApprovalClient("C-REVIEW", "xoxb-test").post_step(flow, step)
        payload = mocked.call_args.args[2]
        self.assertEqual("100.1", payload["thread_ts"])
        self.assertTrue(payload["text"].startswith("<@U1> "))
        self.assertIn("<@U1>", payload["blocks"][0]["text"]["text"])

    def test_buttons_advance_and_context_reply_is_recorded(self):
        client = FakeSlack()
        self.assertIn((self.flow_id, "question_posted"), process_flows(self.ledger, client))
        event = accept_action(click(self.flow_id), CFG, self.ledger)
        self.assertTrue(self.ledger.enqueue(event))
        self.assertIn((self.flow_id, "next"), process_flows(self.ledger, client))
        self.assertEqual("handoff", self.ledger.current_step(self.flow_id)["step_key"])
        handoff_ts = self.ledger.current_step(self.flow_id)["message_ts"]
        context = accept_action(click(self.flow_id, "handoff", "p_ops_context", handoff_ts, action_ts="300.2"), CFG, self.ledger)
        self.ledger.enqueue(context); process_flows(self.ledger, client)
        self.assertEqual("awaiting_context", self.ledger.flow(self.flow_id)["state"])
        body = {"type": "event_callback", "team_id": "T1", "api_app_id": "A1", "event_id": "Ev1",
                "event": {"type": "message", "user": "U1", "channel": "C-REVIEW", "thread_ts": "100.1",
                          "ts": "400.1", "text": "Include Ryan Wong as the data owner."}}
        reply = accept_context_reply(body, CFG, self.ledger)
        self.ledger.enqueue(reply); process_flows(self.ledger, client)
        with self.ledger.connect() as db:
            row = db.execute("SELECT state,answer FROM p_ops_approval_steps WHERE flow_id=? AND step_key='handoff'",
                             (self.flow_id,)).fetchone()
        self.assertEqual(("revised", "Include Ryan Wong as the data owner."), (row["state"], row["answer"]))
        self.assertEqual("linear", self.ledger.current_step(self.flow_id)["step_key"])

    def test_action_rejects_wrong_user_channel_or_stale_message(self):
        process_flows(self.ledger, FakeSlack())
        self.assertIsNone(accept_action(click(self.flow_id, user="U9"), CFG, self.ledger))
        wrong = click(self.flow_id); wrong["channel"] = {"id": "C-OTHER"}
        self.assertIsNone(accept_action(wrong, CFG, self.ledger))
        self.assertIsNone(accept_action(click(self.flow_id, message_ts="999.1"), CFG, self.ledger))

    def test_quick_yes_reply_advances_the_current_question(self):
        client = FakeSlack()
        process_flows(self.ledger, client)
        body = {"type": "event_callback", "team_id": "T1", "api_app_id": "A1", "event_id": "EvYes",
                "event": {"type": "message", "user": "U1", "channel": "C-REVIEW", "thread_ts": "100.1",
                          "ts": "401.1", "text": "yes"}}
        reply = accept_context_reply(body, CFG, self.ledger)
        self.assertEqual("approved", reply["answer"])
        self.ledger.enqueue(reply)
        self.assertIn((self.flow_id, "next"), process_flows(self.ledger, client))
        self.assertEqual("handoff", self.ledger.current_step(self.flow_id)["step_key"])

    def test_second_button_is_ignored_after_context_was_requested(self):
        client = FakeSlack()
        process_flows(self.ledger, client)
        context = accept_action(click(self.flow_id, action="p_ops_context", action_ts="300.3"), CFG, self.ledger)
        approve = accept_action(click(self.flow_id, action_ts="300.4"), CFG, self.ledger)
        self.ledger.enqueue(context)
        self.ledger.enqueue(approve)
        results = process_flows(self.ledger, client)
        self.assertIn((self.flow_id, "awaiting_context"), results)
        self.assertIn((self.flow_id, "ignored"), results)
        self.assertEqual("awaiting_context", self.ledger.flow(self.flow_id)["state"])

    def test_signed_interaction_endpoint_queues_once(self):
        process_flows(self.ledger, FakeSlack())
        raw = urllib.parse.urlencode({"payload": json.dumps(click(self.flow_id))}).encode()
        ts = str(int(time.time()))
        sig = "v0=" + hmac.new(b"secret", b"v0:" + ts.encode() + b":" + raw, hashlib.sha256).hexdigest()
        def call(signature):
            codes = []
            env = {"CONTENT_LENGTH": str(len(raw)), "HTTP_X_SLACK_REQUEST_TIMESTAMP": ts,
                   "HTTP_X_SLACK_SIGNATURE": signature, "wsgi.input": io.BytesIO(raw)}
            receive_interaction(env, lambda code, headers: codes.append(code), CFG, self.ledger)
            return codes[0]
        self.assertEqual("401 Unauthorized", call("bad"))
        self.assertEqual("200 OK", call(sig))
        self.assertEqual(1, len(self.ledger.pending_events()))


if __name__ == "__main__":
    unittest.main()
