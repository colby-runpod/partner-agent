"""Read-only shadow triage for captured p-OPS work.

The shadow runner may read active Notion tasks and call the configured model,
but it never mutates Notion, Linear, Slack, Gmail, or an approval queue. Its
only write is an append-only local audit row.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time

from p_ops_notion import FIELDS, _property
from p_ops_triage import guard_decision


STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "but", "can", "for", "from", "have",
    "into", "need", "that", "the", "their", "this", "through", "with", "would", "your",
}
RISK_TERMS = {
    "credit": "credit", "credits": "credit", "discount": "pricing", "price": "pricing",
    "pricing": "pricing", "contract": "legal", "agreement": "legal", "legal": "legal",
    "permission": "access", "access": "access", "hire": "personnel", "hiring": "personnel",
    "public": "public", "roadmap": "roadmap", "commitment": "strategic_commitment",
}


def _tokens(value):
    return {word for word in re.findall(r"[a-z0-9]{3,}", (value or "").lower()) if word not in STOP_WORDS}


def _relation(page, field):
    return [str(value) for value in (_property(page, field) or []) if value]


def _candidate(page):
    return {
        "id": str(page.get("id", "")),
        "url": page.get("url", ""),
        "title": _property(page, FIELDS["title"]),
        "status": _property(page, FIELDS["status"]),
        "context": _property(page, "Context")[:2500],
        "next_step": _property(page, FIELDS["next_step"])[:1500],
        "objective_ids": _relation(page, FIELDS["objective"]),
        "workstream_ids": _relation(page, FIELDS["workstreams"]),
        "partner_ids": _relation(page, FIELDS["partner"]),
    }


def _shortlist(pages, source_text, limit=12):
    source_tokens = _tokens(source_text)
    scored = []
    for page in pages:
        row = _candidate(page)
        title_tokens = _tokens(row["title"])
        context_tokens = _tokens(row["context"] + " " + row["next_step"])
        score = 4 * len(source_tokens & title_tokens) + len(source_tokens & context_tokens)
        if score:
            scored.append((score, row["title"].lower(), row))
    return [row for _, _, row in sorted(scored, key=lambda value: (-value[0], value[1]))[:limit]]


def bundle_from_inbox(row, pages, current_date=None):
    proposal = json.loads(row["proposal_json"])
    source_text = (proposal.get("title", "") + "\n" + proposal.get("context", "")).strip()
    tasks = _shortlist(pages, source_text)
    objective_ids = sorted({value for task in tasks for value in task["objective_ids"]})
    workstream_ids = sorted({value for task in tasks for value in task["workstream_ids"]})
    partner_ids = sorted({value for task in tasks for value in task["partner_ids"]})
    words = _tokens(source_text)
    risks = sorted({risk for term, risk in RISK_TERMS.items() if term in words})
    deadlines = sorted(set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", source_text)))
    return {
        "source": {"id": "capture:" + row["capture_id"], "type": "slack_capture",
                   "url": proposal.get("source_url", ""), "text": source_text[:12000]},
        "existing_tasks": tasks,
        "meetings": [],
        "objectives": [{"id": value} for value in objective_ids],
        "workstreams": [{"id": value} for value in workstream_ids],
        "partners": [{"id": value} for value in partner_ids],
        "explicit_deadlines": deadlines,
        "risk_classes": risks,
        "explicit_cancellation": False,
        "current_date": (current_date or dt.date.today()).isoformat(),
        "evidence_limitations": [
            "Candidate relationships are limited to a lexical shortlist of active Working Tasks.",
            "Meeting, Slack-thread, Gmail, transcript, and cross-database evidence has not been retrieved.",
            "The result is shadow-only and cannot execute or approve an action.",
        ],
    }


def bundle_hash(bundle):
    return hashlib.sha256(json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ShadowLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_triage_shadow (
                id TEXT PRIMARY KEY, source_id TEXT NOT NULL, bundle_hash TEXT NOT NULL,
                bundle_json TEXT NOT NULL, decision_json TEXT, state TEXT NOT NULL,
                created REAL NOT NULL, error TEXT, UNIQUE(source_id, bundle_hash))""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def exists(self, source_id, digest):
        with self.connect() as db:
            row = db.execute("SELECT 1 FROM p_ops_triage_shadow WHERE source_id=? AND bundle_hash=?",
                             (source_id, digest)).fetchone()
        return bool(row)

    def record(self, bundle, guarded):
        digest = bundle_hash(bundle)
        source_id = bundle["source"]["id"]
        item_id = hashlib.sha256((source_id + digest).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_triage_shadow
                (id,source_id,bundle_hash,bundle_json,decision_json,state,created)
                VALUES (?,?,?,?,?,'shadow',?)""",
                (item_id, source_id, digest, json.dumps(bundle, sort_keys=True),
                 json.dumps(guarded.to_dict(), sort_keys=True), time.time()))
        return item_id

    def record_error(self, bundle, error):
        digest = bundle_hash(bundle)
        source_id = bundle["source"]["id"]
        item_id = hashlib.sha256((source_id + digest).encode()).hexdigest()[:24]
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_triage_shadow
                (id,source_id,bundle_hash,bundle_json,state,created,error)
                VALUES (?,?,?,?,'error',?,?)""",
                (item_id, source_id, digest, json.dumps(bundle, sort_keys=True), time.time(),
                 "triage_failed:" + type(error).__name__))
        return item_id

    def rows(self, states=None):
        with self.connect() as db:
            if states:
                marks = ",".join("?" for _ in states)
                rows = db.execute("SELECT * FROM p_ops_triage_shadow WHERE state IN (%s) ORDER BY created" % marks,
                                  tuple(states)).fetchall()
            else:
                rows = db.execute("SELECT * FROM p_ops_triage_shadow ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def get(self, item_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_triage_shadow WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise KeyError(item_id)
        return dict(row)


def run_shadow(inbox, ledger, notion_client, triage_client, current_date=None):
    """Analyze each unseen review proposal and persist a non-executable result."""
    proposals = inbox.rows(("needs_review",))
    if not proposals:
        return []
    pages = notion_client.active_pages()
    results = []
    for row in proposals:
        bundle = bundle_from_inbox(row, pages, current_date=current_date)
        digest = bundle_hash(bundle)
        if ledger.exists(bundle["source"]["id"], digest):
            continue
        try:
            guarded = guard_decision(triage_client.analyze(bundle), bundle, pilot=True)
            item_id = ledger.record(bundle, guarded)
            results.append((item_id, "shadow"))
        except Exception as exc:
            item_id = ledger.record_error(bundle, exc)
            results.append((item_id, "error"))
    return results
