"""Reviewable Working Tasks Inbox proposals created from verified captures."""
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


def _clean_title(text):
    first = next((line.strip() for line in text.splitlines() if line.strip()), "Captured Slack task")
    first = re.sub(r"<@\w+>", "", first)
    first = re.sub(r"<([^>|]+)(?:\|[^>]+)?>", r"\1", first)
    first = re.sub(r"\s+", " ", html.unescape(first)).strip(" -")
    return (first or "Captured Slack task")[:160]


def proposal_from_capture(capture):
    if capture.get("state") != "captured" or not capture.get("source_url") or not capture.get("source_text"):
        raise ValueError("Only resolved captures can become task proposals.")
    source_url = capture["source_url"]
    source_text = capture["source_text"].strip()
    event = json.loads(capture.get("payload") or "{}")
    fields = {
        "title": _clean_title(source_text),
        "status": "Inbox",
        "context": "Captured from Slack for p-OPS triage. Source: %s\n\nOriginal message:\n%s" %
                   (source_url, source_text[:12000]),
        "next_step": "Review the complete source, find related work, choose the correct disposition, and complete safe next actions.",
        "review_reason": "Captured from Slack and awaiting full multi-lens triage.",
        "source_url": source_url,
        "source_channel": event.get("channel", ""),
        "source_thread_ts": capture.get("thread_ts", ""),
    }
    return fields


def proposal_hash(fields):
    return hashlib.sha256(json.dumps(fields, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class InboxLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_inbox_actions (
                id TEXT PRIMARY KEY, capture_id TEXT UNIQUE NOT NULL, proposal_json TEXT NOT NULL,
                proposal_hash TEXT NOT NULL, approved_hash TEXT, state TEXT NOT NULL,
                created REAL NOT NULL, approved REAL, attempted REAL, completed REAL,
                task_id TEXT, task_url TEXT, error TEXT)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def propose(self, capture):
        fields = proposal_from_capture(capture)
        action_id = uuid.uuid4().hex[:24]
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_inbox_actions
                (id,capture_id,proposal_json,proposal_hash,state,created)
                VALUES (?,?,?,?, 'needs_review',?)""",
                (action_id, capture["event_id"], json.dumps(fields, sort_keys=True), proposal_hash(fields), time.time()))
            row = db.execute("SELECT id FROM p_ops_inbox_actions WHERE capture_id=?",
                             (capture["event_id"],)).fetchone()
        return row["id"]

    def get(self, action_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_inbox_actions WHERE id=?", (action_id,)).fetchone()
        if not row:
            raise KeyError(action_id)
        return dict(row)

    def rows(self, states=None):
        with self.connect() as db:
            if states:
                marks = ",".join("?" for _ in states)
                rows = db.execute("SELECT * FROM p_ops_inbox_actions WHERE state IN (%s) ORDER BY created" % marks,
                                  tuple(states)).fetchall()
            else:
                rows = db.execute("SELECT * FROM p_ops_inbox_actions ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def approve(self, action_id):
        row = self.get(action_id)
        if row["state"] != "needs_review":
            raise ValueError("Task proposal is not waiting for review.")
        with self.connect() as db:
            changed = db.execute("""UPDATE p_ops_inbox_actions SET state='approved',approved_hash=proposal_hash,
                approved=? WHERE id=? AND state='needs_review' AND proposal_hash=?""",
                (time.time(), action_id, row["proposal_hash"])).rowcount
        if not changed:
            raise ValueError("Task proposal changed before approval.")

    def reject(self, action_id):
        with self.connect() as db:
            changed = db.execute("""UPDATE p_ops_inbox_actions SET state='rejected',error='rejected_by_operator'
                WHERE id=? AND state IN ('needs_review','approved')""", (action_id,)).rowcount
        if not changed:
            raise ValueError("Task proposal cannot be rejected from its current state.")

    def claim(self, action_id, approved_hash):
        with self.connect() as db:
            changed = db.execute("""UPDATE p_ops_inbox_actions SET state='checking',attempted=?,error=NULL
                WHERE id=? AND state='approved' AND approved_hash=? AND proposal_hash=?""",
                (time.time(), action_id, approved_hash, approved_hash)).rowcount
        return changed == 1

    def state(self, action_id, state, **fields):
        allowed = {"attempted", "completed", "task_id", "task_url", "error"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported task proposal field.")
        values = {"state": state, **fields}
        with self.connect() as db:
            db.execute("UPDATE p_ops_inbox_actions SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                       list(values.values()) + [action_id])


def create_approved_task(client, ledger, action_id):
    if os.environ.get("P_OPS_NOTION_CREATE") != "true":
        raise ValueError("Set P_OPS_NOTION_CREATE=true to create Working Tasks Inbox items.")
    row = ledger.get(action_id)
    fields = json.loads(row["proposal_json"])
    if row["state"] != "approved" or row["approved_hash"] != proposal_hash(fields):
        raise ValueError("Approve the exact current task proposal before creation.")
    if not ledger.claim(action_id, row["approved_hash"]):
        raise ValueError("Task proposal approval changed before creation.")
    try:
        existing = client.find_by_context(fields["source_url"])
    except Exception:
        ledger.state(action_id, "review", error="duplicate_check_failed_no_create")
        return "review"
    if existing:
        page = existing[0]
        ledger.state(action_id, "duplicate", completed=time.time(), task_id=page.get("id"),
                     task_url=page.get("url"), error="existing_task_found")
        return "duplicate"
    ledger.state(action_id, "creating")
    try:
        page = client.create_inbox(fields)
    except Exception:
        try:
            existing = client.find_by_context(fields["source_url"])
        except Exception:
            existing = []
        if existing:
            page = existing[0]
            ledger.state(action_id, "created", completed=time.time(), task_id=page.get("id"),
                         task_url=page.get("url"), error=None)
            return "reconciled"
        ledger.state(action_id, "review", error="creation_outcome_unconfirmed_no_retry")
        return "review"
    ledger.state(action_id, "created", completed=time.time(), task_id=page["id"],
                 task_url=page["url"], error=None)
    return "created"
