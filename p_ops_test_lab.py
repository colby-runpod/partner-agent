"""Read-only evaluation workspace for the p-OPS operator console.

The test lab may read a Notion task and call the configured triage model. It
never writes to Notion, Slack, Linear, Gmail, or an execution queue. Results
and operator feedback are stored only in the local p-OPS SQLite database.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
import uuid

from p_ops_eval import load_cases, summary
from p_ops_notion import FIELDS, _property
from p_ops_shadow import _candidate, _shortlist
from p_ops_triage import guard_decision


FEEDBACK = {"approved", "too_aggressive", "too_cautious", "missing_context", "wrong_disposition"}


def notion_page_id(value):
    """Extract a canonical Notion page UUID from a page URL or UUID."""
    matches = re.findall(r"(?i)(?<![0-9a-f])[0-9a-f]{32}(?![0-9a-f])", value or "")
    if matches:
        return str(uuid.UUID(matches[-1]))
    try:
        return str(uuid.UUID((value or "").strip()))
    except (ValueError, AttributeError):
        raise ValueError("Enter a valid Notion task URL or page ID.") from None


def bundle_from_text(title, context, current_date=None):
    title, context = (title or "").strip(), (context or "").strip()
    if not title or not context:
        raise ValueError("A task title and context are required.")
    if len(title) > 240 or len(context) > 20000:
        raise ValueError("The test task is too large.")
    digest = hashlib.sha256((title + "\n" + context).encode()).hexdigest()[:20]
    return {
        "source": {"id": "test:" + digest, "type": "manual_test", "url": "",
                   "text": (title + "\n" + context)[:22000]},
        "existing_tasks": [], "meetings": [], "objectives": [], "workstreams": [], "partners": [],
        "explicit_deadlines": sorted(set(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", context))),
        "risk_classes": [], "explicit_cancellation": False,
        "current_date": (current_date or dt.date.today()).isoformat(),
        "evidence_limitations": [
            "This test uses only the context entered by the operator.",
            "No cross-system evidence was retrieved.",
            "The result is read-only and cannot execute or approve an action.",
        ],
    }


def bundle_from_notion_url(value, notion_client, current_date=None):
    page_id = notion_page_id(value)
    page = notion_client.page(page_id)
    task = _candidate(page)
    title = task.get("title") or "Untitled Working Task"
    source_text = "\n".join(filter(None, (
        title,
        "Status: " + (task.get("status") or "Unknown"),
        "Context: " + (task.get("context") or "Not provided"),
        "Next step: " + (task.get("next_step") or "Not provided"),
        "Due date: " + str(_property(page, FIELDS["due_date"]) or "Not provided"),
        "Owner: " + str(_property(page, FIELDS["owner"]) or "Not provided"),
        "Work mode: " + str(_property(page, FIELDS["work_mode"]) or "Not provided"),
    )))
    active = [candidate for candidate in notion_client.active_pages() if candidate.get("id") != page.get("id")]
    related = _shortlist(active, source_text)
    objective_ids = task.get("objective_ids") or []
    workstream_ids = task.get("workstream_ids") or []
    partner_ids = task.get("partner_ids") or []
    due_date = str(_property(page, FIELDS["due_date"]) or "")[:10]
    return {
        "source": {"id": page.get("id", page_id), "type": "notion_working_task",
                   "url": page.get("url") or value, "text": source_text[:22000]},
        "existing_tasks": related,
        "meetings": [],
        "objectives": [{"id": item} for item in objective_ids],
        "workstreams": [{"id": item} for item in workstream_ids],
        "partners": [{"id": item} for item in partner_ids],
        "explicit_deadlines": [due_date] if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", due_date) else [],
        "risk_classes": [], "explicit_cancellation": False,
        "current_date": (current_date or dt.date.today()).isoformat(),
        "evidence_limitations": [
            "The test includes the selected Working Task and a lexical shortlist of active tasks.",
            "Meeting, Slack, Gmail, transcript, and other workspace evidence was not retrieved.",
            "The result is read-only and cannot execute or approve an action.",
        ],
    }


class TestLabLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_test_lab (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
                input_json TEXT NOT NULL, result_json TEXT, state TEXT NOT NULL,
                created REAL NOT NULL, error TEXT, feedback TEXT, feedback_note TEXT,
                feedback_at REAL)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def record(self, kind, label, input_data, result=None, error=None):
        run_id = uuid.uuid4().hex[:24]
        state = "completed" if result is not None else "error"
        with self.connect() as db:
            db.execute("""INSERT INTO p_ops_test_lab
                (id,kind,label,input_json,result_json,state,created,error)
                VALUES (?,?,?,?,?,?,?,?)""",
                (run_id, kind, label[:240], json.dumps(input_data, sort_keys=True),
                 json.dumps(result, sort_keys=True) if result is not None else None,
                 state, time.time(), error))
        return run_id

    def feedback(self, run_id, value, note=""):
        if value not in FEEDBACK:
            raise ValueError("Unsupported test feedback.")
        if len(note or "") > 2000:
            raise ValueError("Feedback note is too long.")
        with self.connect() as db:
            changed = db.execute("""UPDATE p_ops_test_lab
                SET feedback=?, feedback_note=?, feedback_at=? WHERE id=?""",
                (value, (note or "").strip(), time.time(), run_id)).rowcount
        if not changed:
            raise KeyError(run_id)

    def rows(self, limit=25):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM p_ops_test_lab ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [dict(row) for row in rows]


def validate_corpus(path):
    cases = load_cases(path)
    return summary(cases)


def evaluate_bundle(bundle, triage_client):
    guarded = guard_decision(triage_client.analyze(bundle), bundle, pilot=True)
    return guarded.to_dict()
