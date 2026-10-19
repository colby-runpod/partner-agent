"""Persisted, sequential Slack approvals for p-OPS review notifications.

The workflow is intentionally narrow: it records Colby's answers and advances
one question at a time inside the configured review-channel thread. It does not
perform the underlying Notion, Linear, access, or message action.
"""
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import urllib.parse

from intake import api, verify


FLOW_ACTIONS = {"p_ops_approve": "approved", "p_ops_decline": "declined",
                "p_ops_context": "awaiting_context", "p_ops_hold": "held"}
FLOW_VALUE = re.compile(r"([a-f0-9]{24}):([a-z0-9_]{1,48})")


def _clean(value, limit=500):
    return " ".join(str(value or "").split())[:limit]


def _safe_url(value):
    value = str(value or "").strip()
    return value if re.fullmatch(r"https://[^\s<>]+", value) else ""


def _step_specs(shadow_row):
    guarded = json.loads(shadow_row["decision_json"])
    decision = guarded["decision"]
    actions = set(decision.get("proposed_actions") or [])
    specs = [{
        "key": "recommendation",
        "prompt": "Approve the recommended next step?",
        "context": "%s: %s" % (_clean(decision.get("disposition"), 80).replace("_", " "),
                                 _clean(decision.get("next_step"), 700)),
    }]
    if decision.get("disposition") == "route_department" or "prepare_handoff" in actions:
        specs.append({
            "key": "handoff",
            "prompt": "Approve this owner and handoff direction?",
            "context": "%s owns it%s." % (
                _clean(decision.get("owning_function") or decision.get("owner") or "Proposed owner", 140),
                " with support from " + _clean(", ".join(decision.get("supporting_functions") or []), 220)
                if decision.get("supporting_functions") else ""),
        })
    if decision.get("requires_linear") or decision.get("disposition") == "create_or_link_linear":
        specs.append({
            "key": "linear",
            "prompt": "Approve preparing the Linear handoff after a duplicate check?",
            "context": "This approves the implementation-ready issue direction. It does not set priority, assignment, or a delivery commitment.",
        })
    if decision.get("requires_message") or "prepare_message" in actions:
        specs.append({
            "key": "message_direction",
            "prompt": "Approve preparing the communication in this direction?",
            "context": "This approves preparation only. Exact wording and authorization to send remain separate decisions.",
        })
    return specs


def _links(shadow_row, console_url=""):
    bundle = json.loads(shadow_row["bundle_json"])
    links, seen = [], set()
    candidates = [("Source", (bundle.get("source") or {}).get("url"))]
    candidates.extend(("Open task", row.get("url")) for row in bundle.get("existing_tasks") or [])
    if console_url:
        candidates.append(("Open p-OPS", console_url.rstrip("/") + "/review#approvals"))
    for label, url in candidates:
        url = _safe_url(url)
        if url and url not in seen:
            links.append({"label": label, "url": url})
            seen.add(url)
    return links[:6]


class ApprovalFlowLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_approval_flows (
                id TEXT PRIMARY KEY, triage_id TEXT UNIQUE NOT NULL, channel TEXT NOT NULL,
                thread_ts TEXT NOT NULL, requester_user_id TEXT, title TEXT NOT NULL, links_json TEXT NOT NULL,
                state TEXT NOT NULL, current_position INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL, completed REAL, error TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(p_ops_approval_flows)")}
            if "requester_user_id" not in columns:
                db.execute("ALTER TABLE p_ops_approval_flows ADD COLUMN requester_user_id TEXT")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_approval_steps (
                flow_id TEXT NOT NULL, position INTEGER NOT NULL, step_key TEXT NOT NULL,
                prompt TEXT NOT NULL, context TEXT NOT NULL, state TEXT NOT NULL,
                answer TEXT, answered_by TEXT, answered_at REAL, message_ts TEXT,
                PRIMARY KEY(flow_id, position), UNIQUE(flow_id, step_key))""")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_approval_events (
                id TEXT PRIMARY KEY, flow_id TEXT NOT NULL, step_key TEXT NOT NULL,
                kind TEXT NOT NULL, answer TEXT NOT NULL, user_id TEXT NOT NULL,
                channel TEXT NOT NULL, message_ts TEXT NOT NULL, note TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, error TEXT)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def create(self, shadow_row, channel, thread_ts, console_url=""):
        flow_id = hashlib.sha256((shadow_row["id"] + ":" + channel).encode()).hexdigest()[:24]
        guarded = json.loads(shadow_row["decision_json"])
        title = _clean(guarded["decision"].get("title"), 240)
        specs, now = _step_specs(shadow_row), time.time()
        source = json.loads(shadow_row["bundle_json"]).get("source") or {}
        requester_user_id = _clean(source.get("requester_user_id"), 40)
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_approval_flows
                (id,triage_id,channel,thread_ts,requester_user_id,title,links_json,state,created,updated)
                VALUES (?,?,?,?,?,?,?,'pending_post',?,?)""",
                (flow_id, shadow_row["id"], channel, thread_ts, requester_user_id,
                 title, json.dumps(_links(shadow_row, console_url)), now, now))
            for position, spec in enumerate(specs):
                db.execute("""INSERT OR IGNORE INTO p_ops_approval_steps
                    (flow_id,position,step_key,prompt,context,state) VALUES (?,?,?,?,?,'pending')""",
                    (flow_id, position, spec["key"], spec["prompt"], spec["context"]))
        return flow_id

    def flow(self, flow_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_approval_flows WHERE id=?", (flow_id,)).fetchone()
        if not row:
            raise KeyError(flow_id)
        return dict(row)

    def by_thread(self, channel, thread_ts, states=("awaiting_context",)):
        marks = ",".join("?" for _ in states)
        with self.connect() as db:
            row = db.execute("""SELECT * FROM p_ops_approval_flows
                WHERE channel=? AND thread_ts=? AND state IN (%s)
                ORDER BY created DESC LIMIT 1""" % marks,
                (channel, thread_ts, *states)).fetchone()
        return dict(row) if row else None

    def current_step(self, flow_id):
        flow = self.flow(flow_id)
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_approval_steps WHERE flow_id=? AND position=?",
                             (flow_id, flow["current_position"])).fetchone()
        return dict(row) if row else None

    def rows(self, states=None):
        with self.connect() as db:
            if states:
                marks = ",".join("?" for _ in states)
                rows = db.execute("SELECT * FROM p_ops_approval_flows WHERE state IN (%s) ORDER BY created" % marks,
                                  tuple(states)).fetchall()
            else:
                rows = db.execute("SELECT * FROM p_ops_approval_flows ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def steps(self, flow_id):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM p_ops_approval_steps WHERE flow_id=? ORDER BY position",
                              (flow_id,)).fetchall()
        return [dict(row) for row in rows]

    def mark_posted(self, flow_id, message_ts):
        with self.connect() as db:
            flow = db.execute("SELECT current_position FROM p_ops_approval_flows WHERE id=? AND state='pending_post'",
                              (flow_id,)).fetchone()
            if not flow:
                raise ValueError("Flow is not waiting to post.")
            db.execute("UPDATE p_ops_approval_steps SET message_ts=? WHERE flow_id=? AND position=?",
                       (message_ts, flow_id, flow["current_position"]))
            db.execute("UPDATE p_ops_approval_flows SET state='awaiting_answer',updated=? WHERE id=?",
                       (time.time(), flow_id))

    def enqueue(self, event):
        with self.connect() as db:
            return db.execute("""INSERT OR IGNORE INTO p_ops_approval_events
                (id,flow_id,step_key,kind,answer,user_id,channel,message_ts,note,state,created)
                VALUES (?,?,?,?,?,?,?,?,?,'pending',?)""",
                (event["event_id"], event["flow_id"], event["step_key"], event["kind"],
                 event["answer"], event["user_id"], event["channel"], event["message_ts"],
                 event.get("note", "")[:2000], time.time())).rowcount == 1

    def pending_events(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM p_ops_approval_events WHERE state='pending' ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def apply_event(self, event_id):
        with self.connect() as db:
            event = db.execute("SELECT * FROM p_ops_approval_events WHERE id=? AND state='pending'", (event_id,)).fetchone()
            if not event:
                raise ValueError("Approval event is not pending.")
            flow = db.execute("SELECT * FROM p_ops_approval_flows WHERE id=?", (event["flow_id"],)).fetchone()
            step = db.execute("SELECT * FROM p_ops_approval_steps WHERE flow_id=? AND step_key=?",
                              (event["flow_id"], event["step_key"])).fetchone()
            if not flow or not step or step["position"] != flow["current_position"]:
                db.execute("UPDATE p_ops_approval_events SET state='ignored',error='stale_step' WHERE id=?", (event_id,))
                return "ignored"
            expected_state = "awaiting_context" if event["kind"] == "context" else "awaiting_answer"
            if flow["state"] != expected_state:
                db.execute("UPDATE p_ops_approval_events SET state='ignored',error='stale_flow_state' WHERE id=?", (event_id,))
                return "ignored"
            if event["answer"] == "awaiting_context":
                db.execute("UPDATE p_ops_approval_steps SET state='awaiting_context',answer=?,answered_by=?,answered_at=? WHERE flow_id=? AND step_key=?",
                           (event["answer"], event["user_id"], time.time(), event["flow_id"], event["step_key"]))
                db.execute("UPDATE p_ops_approval_flows SET state='awaiting_context',updated=? WHERE id=?",
                           (time.time(), event["flow_id"]))
                result = "awaiting_context"
            elif event["answer"] == "held":
                db.execute("UPDATE p_ops_approval_steps SET state='held',answer=?,answered_by=?,answered_at=? WHERE flow_id=? AND step_key=?",
                           (event["answer"], event["user_id"], time.time(), event["flow_id"], event["step_key"]))
                db.execute("UPDATE p_ops_approval_flows SET state='held',updated=? WHERE id=?",
                           (time.time(), event["flow_id"]))
                result = "held"
            elif event["answer"] == "declined":
                db.execute("UPDATE p_ops_approval_steps SET state='declined',answer=?,answered_by=?,answered_at=? WHERE flow_id=? AND step_key=?",
                           (event["answer"], event["user_id"], time.time(), event["flow_id"], event["step_key"]))
                db.execute("UPDATE p_ops_approval_flows SET state='declined',updated=?,completed=? WHERE id=?",
                           (time.time(), time.time(), event["flow_id"]))
                result = "declined"
            else:
                step_state = "revised" if event["kind"] == "context" else "approved"
                db.execute("UPDATE p_ops_approval_steps SET state=?,answer=?,answered_by=?,answered_at=? WHERE flow_id=? AND step_key=?",
                           (step_state, event["note"] or event["answer"], event["user_id"], time.time(),
                            event["flow_id"], event["step_key"]))
                next_position = flow["current_position"] + 1
                remaining = db.execute("SELECT 1 FROM p_ops_approval_steps WHERE flow_id=? AND position=?",
                                       (event["flow_id"], next_position)).fetchone()
                if remaining:
                    db.execute("UPDATE p_ops_approval_flows SET state='pending_post',current_position=?,updated=? WHERE id=?",
                               (next_position, time.time(), event["flow_id"]))
                    result = "next"
                else:
                    db.execute("UPDATE p_ops_approval_flows SET state='completed',current_position=?,updated=?,completed=? WHERE id=?",
                               (next_position, time.time(), time.time(), event["flow_id"]))
                    result = "completed"
            db.execute("UPDATE p_ops_approval_events SET state='applied' WHERE id=?", (event_id,))
        return result

    def fail_post(self, flow_id):
        with self.connect() as db:
            db.execute("UPDATE p_ops_approval_flows SET state='review',updated=?,error='slack_post_unconfirmed_no_retry' WHERE id=? AND state='pending_post'",
                       (time.time(), flow_id))


def accept_action(payload, cfg, ledger):
    if not cfg.get("review_interactive") or payload.get("type") != "block_actions":
        return None
    if (payload.get("team") or {}).get("id") != cfg.get("team_id") or payload.get("api_app_id") != cfg.get("app_id"):
        return None
    user = str((payload.get("user") or {}).get("id", ""))
    channel = str((payload.get("channel") or {}).get("id", ""))
    if user not in cfg.get("review_user_ids", set()) or channel != cfg.get("review_channel"):
        return None
    action = (payload.get("actions") or [{}])[0]
    match = FLOW_VALUE.fullmatch(str(action.get("value", "")))
    action_ts = str(action.get("action_ts", ""))
    message_ts = str((payload.get("message") or {}).get("ts", ""))
    answer = FLOW_ACTIONS.get(action.get("action_id"))
    if not match or not answer or not re.fullmatch(r"\d+\.\d+", action_ts) or not re.fullmatch(r"\d+\.\d+", message_ts):
        return None
    flow_id, step_key = match.groups()
    try:
        flow, step = ledger.flow(flow_id), ledger.current_step(flow_id)
    except KeyError:
        return None
    if flow["channel"] != channel or not step or step["step_key"] != step_key or step.get("message_ts") != message_ts:
        return None
    return {"event_id": "flow:%s:%s" % (action_ts, user), "flow_id": flow_id, "step_key": step_key,
            "kind": "button", "answer": answer, "user_id": user, "channel": channel,
            "message_ts": message_ts, "note": ""}


def accept_context_reply(body, cfg, ledger):
    if not cfg.get("review_interactive") or body.get("type") != "event_callback":
        return None
    if body.get("team_id") != cfg.get("team_id") or body.get("api_app_id") != cfg.get("app_id"):
        return None
    event = body.get("event") or {}
    user, channel = str(event.get("user", "")), str(event.get("channel", ""))
    thread_ts, ts = str(event.get("thread_ts", "")), str(event.get("ts", ""))
    if (event.get("type") != "message" or event.get("subtype") or event.get("bot_id") or event.get("app_id") or
            user not in cfg.get("review_user_ids", set()) or channel != cfg.get("review_channel") or
            not re.fullmatch(r"\d+\.\d+", thread_ts) or not re.fullmatch(r"\d+\.\d+", ts)):
        return None
    note = str(event.get("text", "")).strip()
    flow = ledger.by_thread(channel, thread_ts, ("awaiting_answer", "awaiting_context"))
    if not flow or not note or len(note) > 2000:
        return None
    step = ledger.current_step(flow["id"])
    if flow["state"] == "awaiting_answer":
        answer = {
            "yes": "approved", "y": "approved", "approve": "approved", "approved": "approved",
            "go": "approved", "proceed": "approved", "no": "declined", "n": "declined",
            "decline": "declined", "stop": "declined", "hold": "held", "pause": "held",
            "later": "held",
        }.get(note.lower().strip(" .!"))
        if not answer:
            return None
        return {"event_id": "reply:" + str(body.get("event_id") or ts), "flow_id": flow["id"],
                "step_key": step["step_key"], "kind": "button", "answer": answer, "user_id": user,
                "channel": channel, "message_ts": step.get("message_ts") or ts, "note": ""}
    return {"event_id": "context:" + str(body.get("event_id") or ts), "flow_id": flow["id"],
            "step_key": step["step_key"], "kind": "context", "answer": "approved", "user_id": user,
            "channel": channel, "message_ts": step.get("message_ts") or ts, "note": note}


def receive_interaction(env, start, cfg, ledger):
    def respond(code):
        start(code, [("Content-Type", "text/plain"), ("Content-Length", "0")])
        return [b""]
    try:
        size = int(env.get("CONTENT_LENGTH") or 0)
        if size <= 0 or size > 262144:
            return respond("413 Payload Too Large")
        raw = env["wsgi.input"].read(size)
        if not verify(cfg["secret"], env.get("HTTP_X_SLACK_REQUEST_TIMESTAMP"),
                      env.get("HTTP_X_SLACK_SIGNATURE"), raw):
            return respond("401 Unauthorized")
        payload = json.loads(urllib.parse.parse_qs(raw.decode())["payload"][0])
        event = accept_action(payload, cfg, ledger)
        if event:
            ledger.enqueue(event)
        return respond("200 OK")
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        return respond("400 Bad Request")
    except sqlite3.Error:
        return respond("503 Service Unavailable")


def step_blocks(flow, step):
    links = json.loads(flow.get("links_json") or "[]")
    link_text = " · ".join("<%s|%s>" % (item["url"], item["label"]) for item in links)
    number = int(step["position"]) + 1
    value = flow["id"] + ":" + step["step_key"]
    mention = "<@%s> " % flow.get("requester_user_id") if flow.get("requester_user_id") else ""
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": "*%d. %s*\n%s" %
            (number, mention + step["prompt"], step["context"]), "verbatim": True}},
        {"type": "context", "elements": [{"type": "mrkdwn",
            "text": "Click a button or reply `yes`, `no`, or `hold`.", "verbatim": True}]},
    ]
    if link_text:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": link_text, "verbatim": True}]})
    blocks.append({"type": "actions", "elements": [
        {"type": "button", "action_id": "p_ops_approve", "text": {"type": "plain_text", "text": "Approve"},
         "style": "primary", "value": value},
        {"type": "button", "action_id": "p_ops_decline", "text": {"type": "plain_text", "text": "Decline"},
         "style": "danger", "value": value},
        {"type": "button", "action_id": "p_ops_context", "text": {"type": "plain_text", "text": "Add context"},
         "value": value},
        {"type": "button", "action_id": "p_ops_hold", "text": {"type": "plain_text", "text": "Hold"},
         "value": value},
    ]})
    return blocks


class SlackApprovalClient:
    def __init__(self, expected_channel, token=None):
        import os
        self.channel = expected_channel
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.channel or not self.token:
            raise ValueError("Review channel and Slack token are required.")

    def post_step(self, flow, step):
        if flow["channel"] != self.channel:
            raise ValueError("Approval flow channel is not authorized.")
        mention = "<@%s> " % flow.get("requester_user_id") if flow.get("requester_user_id") else ""
        result = api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": self.channel, "thread_ts": flow["thread_ts"],
            "text": mention + step["prompt"] + " " + step["context"], "blocks": step_blocks(flow, step),
            "unfurl_links": False, "unfurl_media": False})
        if not result.get("ts"):
            raise ValueError("Slack did not confirm approval-step delivery.")
        return result["ts"]

    def post_context_prompt(self, flow):
        mention = "<@%s> " % flow.get("requester_user_id") if flow.get("requester_user_id") else ""
        api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": self.channel, "thread_ts": flow["thread_ts"],
            "text": mention + "Reply in this thread with one sentence describing what should change. I’ll record it and continue the review.",
            "mrkdwn": True, "parse": "none", "unfurl_links": False, "unfurl_media": False})

    def update_answered(self, flow, step, event, outcome):
        labels = {"next": "Approved", "completed": "Approved", "declined": "Declined",
                  "held": "Held", "awaiting_context": "Context requested"}
        label = labels.get(outcome, "Recorded")
        api("https://slack.com/api/chat.update", self.token, {
            "channel": self.channel, "ts": step["message_ts"],
            "text": "%s: %s" % (label, step["prompt"]),
            "blocks": [{"type": "section", "text": {"type": "mrkdwn",
                "text": "*%s* · <@%s>\n%s" % (label, event["user_id"], step["prompt"]), "verbatim": True}}]})

    def post_outcome(self, flow, outcome):
        labels = {"completed": "Review complete. All questions are recorded.",
                  "declined": "Review stopped. The current recommendation was declined.",
                  "held": "Review paused. No gated action will move forward.",
                  "next": "Decision recorded. Here’s the next question."}
        mention = "<@%s> " % flow.get("requester_user_id") if flow.get("requester_user_id") else ""
        api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": self.channel, "thread_ts": flow["thread_ts"],
            "text": mention + labels.get(outcome, "Decision recorded."),
            "mrkdwn": True, "parse": "none", "unfurl_links": False, "unfurl_media": False})


def process_flows(ledger, client):
    results = []
    for event in ledger.pending_events():
        flow = ledger.flow(event["flow_id"])
        step = ledger.current_step(event["flow_id"])
        result = ledger.apply_event(event["id"])
        results.append((event["flow_id"], result))
        if hasattr(client, "update_answered"):
            try:
                client.update_answered(flow, step, event, result)
            except Exception:
                pass
        if result == "awaiting_context":
            client.post_context_prompt(flow)
        elif result in {"next", "completed", "declined", "held"}:
            client.post_outcome(flow, result)
    for flow in ledger.rows(("pending_post",)):
        step = ledger.current_step(flow["id"])
        if not step:
            continue
        try:
            message_ts = client.post_step(flow, step)
        except Exception:
            ledger.fail_post(flow["id"])
            results.append((flow["id"], "review"))
        else:
            ledger.mark_posted(flow["id"], message_ts)
            results.append((flow["id"], "question_posted"))
    return results
