"""Exactly-once internal review notifications for the approved p-OPS channel.

This path is intentionally narrower than general outbound communications. It
can publish only a generated review card to the single configured internal
channel. Delivery uncertainty is recorded for reconciliation and never retried.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import time


def _clean(value, limit):
    value = " ".join(str(value or "").split())
    return value[:limit]


def _label(value, fallback="not set"):
    return _clean(value or fallback, 80).replace("_", " ")


def _link(url, label):
    return "<%s|%s>" % (_clean(url, 1000), label)


REVIEW_REASON_LABELS = {
    "assumptions_or_missing_evidence": "Some assumptions or missing evidence need confirmation.",
    "confidence_below_0.80": "Confidence is below the autonomous-action threshold.",
    "handoff_or_reassignment_review": "The proposed owner or department handoff needs approval.",
    "linear_creation_requires_review_during_pilot": "Creating a Linear issue still requires approval during the pilot.",
    "message_requires_review": "Any outbound message must be reviewed before it is sent.",
    "pilot_shadow_mode": "p-OPS is still in review-first pilot mode.",
    "policy_violation": "The proposed action is outside the agent’s current permissions.",
    "sensitive_data": "The source may contain sensitive data that needs careful handling.",
}


def _review_reason(value):
    return REVIEW_REASON_LABELS.get(value, value.replace("_", " ").capitalize() + ".")


def review_card(shadow_row, console_url="", requester_user_id=""):
    guarded = json.loads(shadow_row["decision_json"])
    decision = guarded["decision"]
    reasons = guarded.get("policy_reasons") or []
    source = json.loads(shadow_row["bundle_json"]).get("source") or {}
    review_required = bool(guarded.get("review_required"))
    mention = "<@%s> " % requester_user_id if requester_user_id and requester_user_id.isalnum() else ""
    icon = ":mag:" if review_required else ":white_check_mark:"
    heading = "p-OPS review" if review_required else "p-OPS complete"
    lines = [
        mention + "%s *%s: %s*" % (icon, heading, _clean(decision.get("title"), 180)),
        "*Disposition:* %s\n*Priority:* %s  |  *Severity:* %s  |  *Confidence:* %s" % (
            _label(decision.get("disposition")), _label(decision.get("priority")),
            _label(decision.get("severity")), _label(decision.get("confidence"))),
        "*Why*\n• " + _clean(decision.get("summary"), 500),
        "*Recommended next step*\n• " + _clean(decision.get("next_step"), 500),
        "*Already completed*\n• Reviewed the evidence and evaluated the disposition. No gated action was taken.",
    ]
    if reasons:
        readable = [_review_reason(reason) for reason in reasons]
        lines.insert(4, "*Why I need you*\n" + "\n".join("• " + _clean(reason, 180) for reason in readable))
    lines.append(":point_right: *Your input:* Use the buttons below." if review_required else
                 "_No response needed unless you want to correct this disposition._")
    links = []
    if source.get("url"):
        links.append(_link(source["url"], "Source"))
    bundle = json.loads(shadow_row["bundle_json"])
    task_urls = [row.get("url") for row in bundle.get("existing_tasks") or [] if row.get("url")]
    if task_urls:
        links.append(_link(task_urls[0], "Working Task"))
    if console_url:
        links.append(_link(console_url.rstrip("/") + "/review#approvals", "Open p-OPS"))
    if links:
        lines.append("*Open:* " + "  ·  ".join(links))
    return "\n".join(lines)


class ReviewNotificationLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_review_notifications (
                triage_id TEXT PRIMARY KEY, channel TEXT NOT NULL, thread_ts TEXT, requester_user_id TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, sent_at REAL, external_id TEXT, error TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(p_ops_review_notifications)")}
            if "thread_ts" not in columns:
                db.execute("ALTER TABLE p_ops_review_notifications ADD COLUMN thread_ts TEXT")
            if "requester_user_id" not in columns:
                db.execute("ALTER TABLE p_ops_review_notifications ADD COLUMN requester_user_id TEXT")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def queue(self, shadow_row, channel, console_url="", thread_ts="", requester_user_id=""):
        if not channel.strip():
            raise ValueError("The review notification channel is required.")
        content = review_card(shadow_row, console_url, requester_user_id)
        digest = hashlib.sha256(content.encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_review_notifications
                (triage_id,channel,thread_ts,requester_user_id,content,content_hash,state,created,updated)
                VALUES (?,?,?,?,?,?,'queued',?,?)""",
                (shadow_row["id"], channel, thread_ts or None, requester_user_id or None,
                 content, digest, now, now))

    def rows(self, states=None):
        with self.connect() as db:
            if states:
                marks = ",".join("?" for _ in states)
                rows = db.execute("SELECT * FROM p_ops_review_notifications WHERE state IN (%s) ORDER BY created" % marks,
                                  tuple(states)).fetchall()
            else:
                rows = db.execute("SELECT * FROM p_ops_review_notifications ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def get(self, triage_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_review_notifications WHERE triage_id=?", (triage_id,)).fetchone()
        if not row:
            raise KeyError(triage_id)
        return dict(row)

    def publish(self, triage_id, expected_channel, sender):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_review_notifications WHERE triage_id=?", (triage_id,)).fetchone()
            if not row or row["state"] != "queued":
                raise ValueError("Review notification is not queued.")
            if row["channel"] != expected_channel:
                db.execute("UPDATE p_ops_review_notifications SET state='review',updated=?,error=? WHERE triage_id=?",
                           (time.time(), "configured_channel_changed", triage_id))
                return "review"
            claimed = db.execute("""UPDATE p_ops_review_notifications SET state='sending',updated=?,error=NULL
                WHERE triage_id=? AND state='queued' AND content_hash=?""",
                (time.time(), triage_id, row["content_hash"])).rowcount
        if not claimed:
            raise ValueError("Review notification changed before delivery.")
        try:
            external_id = sender(dict(row))
        except Exception:
            with self.connect() as db:
                db.execute("UPDATE p_ops_review_notifications SET state='review',updated=?,error=? WHERE triage_id=?",
                           (time.time(), "send_outcome_unconfirmed_no_retry", triage_id))
            return "review"
        with self.connect() as db:
            db.execute("""UPDATE p_ops_review_notifications SET state='sent',updated=?,sent_at=?,external_id=?,error=NULL
                WHERE triage_id=?""", (time.time(), time.time(), str(external_id), triage_id))
        return "sent"
