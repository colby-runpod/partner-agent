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


def review_card(shadow_row, console_url=""):
    guarded = json.loads(shadow_row["decision_json"])
    decision = guarded["decision"]
    reasons = guarded.get("policy_reasons") or []
    source = json.loads(shadow_row["bundle_json"]).get("source") or {}
    review_required = bool(guarded.get("review_required"))
    lines = [
        ("p-OPS review: " if review_required else "p-OPS complete: ") + _clean(decision.get("title"), 180),
        "Disposition: " + _clean(decision.get("disposition"), 80).replace("_", " "),
        "Priority: %s · Severity: %s · Confidence: %s" % (
            _clean(decision.get("priority") or "not set", 40),
            _clean(decision.get("severity") or "not set", 40),
            _clean(decision.get("confidence") or "not set", 40)),
        "Why: " + _clean(decision.get("summary"), 500),
        "Next: " + _clean(decision.get("next_step"), 500),
        "Completed: Evidence was reviewed and the disposition was evaluated. No gated action was taken.",
    ]
    if reasons:
        lines.insert(5, "Review reason: " + _clean(", ".join(reasons), 500))
    lines.append("Your input: use the buttons below." if review_required else "No response needed unless you want to correct this disposition.")
    if source.get("url"):
        lines.append("Source: " + _clean(source["url"], 1000))
    bundle = json.loads(shadow_row["bundle_json"])
    task_urls = [row.get("url") for row in bundle.get("existing_tasks") or [] if row.get("url")]
    if task_urls:
        lines.append("Task: " + _clean(task_urls[0], 1000))
    if console_url:
        lines.append("p-OPS review: " + _clean(console_url.rstrip("/") + "/review#approvals", 1000))
    return "\n".join(lines)


class ReviewNotificationLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_review_notifications (
                triage_id TEXT PRIMARY KEY, channel TEXT NOT NULL, thread_ts TEXT,
                content TEXT NOT NULL,
                content_hash TEXT NOT NULL, state TEXT NOT NULL, created REAL NOT NULL,
                updated REAL NOT NULL, sent_at REAL, external_id TEXT, error TEXT)""")
            columns = {row[1] for row in db.execute("PRAGMA table_info(p_ops_review_notifications)")}
            if "thread_ts" not in columns:
                db.execute("ALTER TABLE p_ops_review_notifications ADD COLUMN thread_ts TEXT")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def queue(self, shadow_row, channel, console_url="", thread_ts=""):
        if not channel.strip():
            raise ValueError("The review notification channel is required.")
        content = review_card(shadow_row, console_url)
        digest = hashlib.sha256(content.encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_review_notifications
                (triage_id,channel,thread_ts,content,content_hash,state,created,updated)
                VALUES (?,?,?,?,?,'queued',?,?)""",
                (shadow_row["id"], channel, thread_ts or None, content, digest, now, now))

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
