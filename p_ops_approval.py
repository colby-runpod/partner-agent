"""Exact-content review and send authorization for p-OPS communications.

Drafting, approval, and sending are separate operations. Any change to the
destination, subject, or content invalidates prior approval. Sending is off by
default and uncertain outcomes are never retried automatically.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


SENDABLE = {"slack", "email"}


def content_hash(kind, destination, subject, content):
    payload = {"kind": kind, "destination": destination, "subject": subject or "", "content": content}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ApprovalLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_messages (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, destination TEXT NOT NULL,
                subject TEXT NOT NULL, content TEXT NOT NULL, content_hash TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                approved_hash TEXT, approved_at REAL, send_authorized_at REAL,
                sending_at REAL, sent_at REAL, external_id TEXT, error TEXT)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def create(self, kind, destination, content, subject=""):
        if kind not in SENDABLE:
            raise ValueError("Unsupported message kind: " + kind)
        if not destination.strip() or not content.strip():
            raise ValueError("Destination and content are required.")
        message_id = uuid.uuid4().hex[:24]
        digest = content_hash(kind, destination, subject, content)
        now = time.time()
        with self.connect() as db:
            db.execute("""INSERT INTO p_ops_messages
                (id,kind,destination,subject,content,content_hash,state,created,updated)
                VALUES (?,?,?,?,?,?, 'needs_review',?,?)""",
                (message_id, kind, destination, subject, content, digest, now, now))
        return message_id

    def get(self, message_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_messages WHERE id=?", (message_id,)).fetchone()
        if not row:
            raise KeyError(message_id)
        return dict(row)

    def revise(self, message_id, *, destination=None, subject=None, content=None):
        row = self.get(message_id)
        destination = row["destination"] if destination is None else destination
        subject = row["subject"] if subject is None else subject
        content = row["content"] if content is None else content
        if not destination.strip() or not content.strip():
            raise ValueError("Destination and content are required.")
        digest = content_hash(row["kind"], destination, subject, content)
        with self.connect() as db:
            db.execute("""UPDATE p_ops_messages SET destination=?,subject=?,content=?,content_hash=?,
                state='needs_review',updated=?,approved_hash=NULL,approved_at=NULL,
                send_authorized_at=NULL,sending_at=NULL,error=NULL WHERE id=? AND state!='sent'""",
                (destination, subject, content, digest, time.time(), message_id))
            if db.total_changes != 1:
                raise ValueError("Sent messages cannot be revised.")

    def approve(self, message_id):
        row = self.get(message_id)
        if row["state"] != "needs_review":
            raise ValueError("Message is not waiting for review.")
        with self.connect() as db:
            db.execute("""UPDATE p_ops_messages SET state='approved',approved_hash=content_hash,
                approved_at=?,updated=? WHERE id=? AND state='needs_review'""",
                (time.time(), time.time(), message_id))

    def authorize_send(self, message_id):
        row = self.get(message_id)
        if row["state"] != "approved" or row["approved_hash"] != row["content_hash"]:
            raise ValueError("Approve the exact current message before authorizing send.")
        with self.connect() as db:
            db.execute("UPDATE p_ops_messages SET state='send_authorized',send_authorized_at=?,updated=? WHERE id=?",
                       (time.time(), time.time(), message_id))

    def _state(self, message_id, state, **fields):
        allowed = {"sending_at", "sent_at", "external_id", "error"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported message field.")
        values = {"state": state, "updated": time.time(), **fields}
        with self.connect() as db:
            db.execute("UPDATE p_ops_messages SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                       list(values.values()) + [message_id])


def send_message(ledger, message_id, sender):
    """Send exactly once after review, authorization, and an environment gate."""
    if os.environ.get("P_OPS_SEND_MESSAGES") != "true":
        raise ValueError("Set P_OPS_SEND_MESSAGES=true to enable outbound messages.")
    row = ledger.get(message_id)
    if row["state"] != "send_authorized":
        raise ValueError("Message is not authorized to send.")
    if row["approved_hash"] != content_hash(row["kind"], row["destination"], row["subject"], row["content"]):
        raise ValueError("Approved content no longer matches the message.")
    ledger._state(message_id, "sending", sending_at=time.time(), error=None)
    try:
        external_id = sender(row)
    except Exception:
        ledger._state(message_id, "review", error="send_outcome_unconfirmed_no_retry")
        return "review"
    if not external_id:
        ledger._state(message_id, "review", error="send_returned_no_external_id_no_retry")
        return "review"
    ledger._state(message_id, "sent", sent_at=time.time(), external_id=str(external_id), error=None)
    return "sent"
