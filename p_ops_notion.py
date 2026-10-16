"""Notion adapter, preview queue, approval ledger, and safe write reconciler.

Preview is the default. A write requires both an approved/auto-ready ledger row
and P_OPS_NOTION_WRITE=true. Uncertain writes are read back once and never
blindly retried.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
import uuid

from p_ops import NOTION_DATA_SOURCE_ID, TaskEvidence, notion_patch, plan_task


NOTION_VERSION = "2025-09-03"
ACTIVE = ("Inbox", "Next", "In Progress", "Waiting on Other", "Snoozed", "Finalizing")
TERMINAL = {"applied", "skipped"}
FIELDS = {
    "title": "Item", "status": "Status", "owner": "Accountable Owner",
    "next_step": "Next Step", "next_step_date": "Next Step Date", "due_date": "Due Date",
    "definition_of_done": "Definition of Done", "completion_evidence": "Completion Evidence",
    "close_reason": "Close Reason", "closed_at": "Closed At",
    "last_activity": "Last Meaningful Activity", "last_review": "Last Automation Review",
    "waiting_since": "Waiting Since", "who_waits": "Who's Waiting",
    "dependency": "Dependency Context", "blocked": "Blocked", "risk": "Risk Notes",
    "decision": "Decision Required", "work_mode": "Work Mode", "owning": "Owning Function",
    "supporting": "Supporting Functions", "objective": "Objective", "workstreams": "Workstreams",
    "partner": "Associated Partner(s)", "review_required": "Review Required",
    "review_reason": "Review Reason", "children": "Child Actions", "linear": "Linear Issue",
}


class RemoteError(Exception):
    pass


def _text(prop):
    if not isinstance(prop, dict):
        return ""
    kind = prop.get("type")
    value = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(x.get("plain_text", x.get("text", {}).get("content", "")) for x in value or [])
    if kind in ("status", "select"):
        return (value or {}).get("name", "")
    if kind == "people":
        return ", ".join(x.get("name") or x.get("id", "") for x in value or [])
    if kind == "date":
        return (value or {}).get("start", "")
    if kind in ("url", "email", "phone_number"):
        return value or ""
    if kind == "checkbox":
        return bool(value)
    if kind == "number":
        return value
    if kind == "multi_select":
        return [x.get("name", "") for x in value or []]
    if kind == "relation":
        return [x.get("id", "") for x in value or []]
    return ""


def _property(page, name):
    return _text(page.get("properties", {}).get(name, {}))


def _date(value):
    if not value:
        return None
    return dt.date.fromisoformat(str(value)[:10])


def _page_url(page):
    return page.get("url") or "https://www.notion.so/" + page["id"].replace("-", "")


def normalize_page(page, today):
    """Convert one Working Tasks page into conservative planner evidence."""
    status = _property(page, FIELDS["status"])
    next_date = _date(_property(page, FIELDS["next_step_date"]))
    dependency = _property(page, FIELDS["dependency"])
    blocked = _property(page, FIELDS["blocked"])
    if status != "Waiting on Other" and not blocked:
        dependency = ""
    owning = _property(page, FIELDS["owning"])
    supporting = _property(page, FIELDS["supporting"]) or []
    engineering_functions = {"Product", "Partner SA", "Engineering", "Security or IT"}
    close_reason = _property(page, FIELDS["close_reason"])
    explicit_drop = close_reason if close_reason in {"No longer relevant", "Declined", "Superseded"} else ""
    future_trigger = ""
    if next_date and next_date >= today:
        future_trigger = "Next Step Date: " + next_date.isoformat()
    snooze_until = next_date if status == "Snoozed" and next_date and next_date > today else None
    return TaskEvidence(
        task_id=page["id"], title=_property(page, FIELDS["title"]), status=status, today=today,
        last_meaningful_activity=_date(_property(page, FIELDS["last_activity"])),
        completion_evidence=_property(page, FIELDS["completion_evidence"]),
        explicit_drop_evidence=explicit_drop, future_trigger=future_trigger,
        active_dependency=dependency, unresolved_commitment=_property(page, FIELDS["decision"]),
        material_risk=_property(page, FIELDS["risk"]), someone_waiting=_property(page, FIELDS["who_waits"]),
        snooze_until=snooze_until, owner=_property(page, FIELDS["owner"]),
        next_step=_property(page, FIELDS["next_step"]), next_step_date=next_date,
        due_date=_date(_property(page, FIELDS["due_date"])),
        definition_of_done=_property(page, FIELDS["definition_of_done"]),
        work_mode=_property(page, FIELDS["work_mode"]), objective=", ".join(_property(page, FIELDS["objective"]) or []),
        workstream=", ".join(_property(page, FIELDS["workstreams"]) or []),
        partner=", ".join(_property(page, FIELDS["partner"]) or []),
        child_actions_unverified=bool(_property(page, FIELDS["children"])),
        requires_engineering=owning in engineering_functions or bool(engineering_functions & set(supporting)),
        linear_issue_url=_property(page, FIELDS["linear"]), linear_team=owning if owning in engineering_functions else "",
    )


def snapshot(page):
    """Hash only planner and patch-relevant properties."""
    values = {name: _property(page, field) for name, field in FIELDS.items()}
    return hashlib.sha256(json.dumps(values, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _rich(value):
    return [{"type": "text", "text": {"content": value[i:i + 1800]}}
            for i in range(0, len(value), 1800)]


def encode_patch(patch):
    """Encode the bounded p-OPS patch vocabulary for Notion's page API."""
    encoded = {}
    for name, value in patch.items():
        if name in {"Status"}:
            encoded[name] = {"status": {"name": value}}
        elif name in {"Close Reason"}:
            encoded[name] = {"select": {"name": value}}
        elif name in {"Review Required"}:
            encoded[name] = {"checkbox": bool(value)}
        elif name in {"Closed At", "Waiting Since", "Last Automation Review"}:
            encoded[name] = {"date": {"start": value}}
        elif name in {"Review Reason", "Completion Evidence"}:
            encoded[name] = {"rich_text": _rich(str(value))}
        elif name == "Linear Issue":
            encoded[name] = {"url": value or None}
        else:
            raise ValueError("p-OPS is not allowed to write property: " + name)
    return encoded


def patch_matches(page, patch):
    for name, desired in patch.items():
        current = _property(page, name)
        if name == "Review Required":
            if bool(current) != bool(desired):
                return False
        elif name in {"Closed At", "Waiting Since", "Last Automation Review"}:
            if str(current or "")[:10] != str(desired or "")[:10]:
                return False
        elif str(current or "") != str(desired or ""):
            return False
    return True


class NotionClient:
    def __init__(self, token=None, data_source_id=NOTION_DATA_SOURCE_ID, opener=None):
        self.token = token or os.environ.get("NOTION_TOKEN", "")
        self.data_source_id = data_source_id
        self.opener = opener or urllib.request.urlopen
        if not self.token:
            raise ValueError("Set NOTION_TOKEN before reading Working Tasks.")

    def request(self, method, url, payload=None):
        request = urllib.request.Request(url, method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + self.token, "Notion-Version": NOTION_VERSION,
                     "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            raise RemoteError("Notion HTTP %d" % exc.code) from None
        except (OSError, ValueError, TimeoutError):
            raise RemoteError("Notion response unavailable") from None

    def active_pages(self):
        pages, cursor = [], None
        while True:
            body = {"page_size": 100, "filter": {"or": [
                {"property": "Status", "status": {"equals": status}} for status in ACTIVE]}}
            if cursor:
                body["start_cursor"] = cursor
            result = self.request("POST", "https://api.notion.com/v1/data_sources/%s/query" % self.data_source_id, body)
            pages.extend(result.get("results", []))
            if not result.get("has_more"):
                return pages
            cursor = result.get("next_cursor")
            if not cursor:
                raise RemoteError("Notion pagination cursor missing")

    def page(self, page_id):
        return self.request("GET", "https://api.notion.com/v1/pages/" + str(uuid.UUID(page_id)))

    def update(self, page_id, patch):
        return self.request("PATCH", "https://api.notion.com/v1/pages/" + str(uuid.UUID(page_id)),
                            {"properties": encode_patch(patch)})

    def find_by_context(self, source_url):
        result = self.request("POST", "https://api.notion.com/v1/data_sources/%s/query" % self.data_source_id,
                              {"page_size": 10, "filter": {"property": "Context",
                               "rich_text": {"contains": source_url}}})
        return result.get("results", [])

    def create_inbox(self, fields):
        """Create only the bounded Inbox shape used by approved Slack captures."""
        properties = {
            "Item": {"title": _rich(fields["title"])},
            "Status": {"status": {"name": "Inbox"}},
            "Context": {"rich_text": _rich(fields["context"])},
            "Next Step": {"rich_text": _rich(fields["next_step"])},
            "Review Required": {"checkbox": True},
            "Review Reason": {"rich_text": _rich(fields["review_reason"])},
        }
        page = self.request("POST", "https://api.notion.com/v1/pages",
                            {"parent": {"type": "data_source_id", "data_source_id": self.data_source_id},
                             "properties": properties})
        if not page.get("id") or not page.get("url"):
            raise RemoteError("Notion did not confirm task creation")
        return page


class Ledger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_runs (
                id TEXT PRIMARY KEY, started REAL NOT NULL, completed REAL, source_count INTEGER DEFAULT 0)""")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_actions (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task_id TEXT NOT NULL, task_url TEXT NOT NULL,
                title TEXT NOT NULL, source_hash TEXT NOT NULL, plan_json TEXT NOT NULL, patch_json TEXT NOT NULL,
                patch_hash TEXT NOT NULL, state TEXT NOT NULL, auto_allowed INTEGER NOT NULL,
                review_required INTEGER NOT NULL, created REAL NOT NULL, approved REAL, applied REAL,
                error TEXT, UNIQUE(task_id, source_hash, patch_hash))""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def start(self):
        run_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO p_ops_runs(id,started) VALUES (?,?)", (run_id, time.time()))
        return run_id

    def finish(self, run_id, count):
        with self.connect() as db:
            db.execute("UPDATE p_ops_runs SET completed=?, source_count=? WHERE id=?", (time.time(), count, run_id))

    def add(self, run_id, page, plan, patch):
        source_hash = snapshot(page)
        patch_json = json.dumps(patch, sort_keys=True, separators=(",", ":"))
        patch_hash = hashlib.sha256(patch_json.encode()).hexdigest()
        action_id = hashlib.sha256((page["id"] + source_hash + patch_hash).encode()).hexdigest()[:24]
        state = "needs_review" if plan.review_required else "auto_ready"
        with self.connect() as db:
            db.execute("""INSERT OR IGNORE INTO p_ops_actions
                (id,run_id,task_id,task_url,title,source_hash,plan_json,patch_json,patch_hash,state,
                 auto_allowed,review_required,created) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (action_id, run_id, page["id"], _page_url(page), plan.task_id and _property(page, "Item"),
                 source_hash, json.dumps(plan.to_dict(), sort_keys=True), patch_json, patch_hash, state,
                 int(plan.auto_update_allowed or not plan.review_required), int(plan.review_required), time.time()))
        return action_id

    def approve(self, action_id):
        with self.connect() as db:
            changed = db.execute("UPDATE p_ops_actions SET state='approved', approved=? "
                                 "WHERE id=? AND state='needs_review'", (time.time(), action_id)).rowcount
        if not changed:
            raise ValueError("Action is not waiting for approval: " + action_id)

    def reject(self, action_id):
        with self.connect() as db:
            changed = db.execute("UPDATE p_ops_actions SET state='rejected', error='rejected_by_operator' "
                                 "WHERE id=? AND state='needs_review'", (action_id,)).rowcount
        if not changed:
            raise ValueError("Action is not waiting for approval: " + action_id)

    def rows(self, states=None):
        with self.connect() as db:
            if states:
                marks = ",".join("?" for _ in states)
                rows = db.execute("SELECT * FROM p_ops_actions WHERE state IN (%s) ORDER BY created" % marks,
                                  tuple(states)).fetchall()
            else:
                rows = db.execute("SELECT * FROM p_ops_actions ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def state(self, action_id, state, error=None):
        with self.connect() as db:
            db.execute("UPDATE p_ops_actions SET state=?, error=?, applied=CASE WHEN ?='applied' THEN ? ELSE applied END "
                       "WHERE id=?", (state, error, state, time.time(), action_id))


def changed_patch(page, patch):
    """Remove values already present so daily runs avoid redundant writes."""
    changed = {}
    for name, value in patch.items():
        if not patch_matches(page, {name: value}):
            changed[name] = value
    return changed


def preview(client, ledger, today=None):
    today = today or dt.date.today()
    run_id = ledger.start()
    pages = client.active_pages()
    created = []
    for page in pages:
        task = normalize_page(page, today)
        plan = plan_task(task)
        patch = changed_patch(page, notion_patch(task, plan))
        if patch:
            created.append(ledger.add(run_id, page, plan, patch))
    ledger.finish(run_id, len(pages))
    return {"run_id": run_id, "tasks_read": len(pages), "actions_queued": len(created), "action_ids": created}


def apply_action(client, ledger, row):
    """Apply once, then verify. Never retry an uncertain write."""
    action_id, page_id = row["id"], row["task_id"]
    patch = json.loads(row["patch_json"])
    current = client.page(page_id)
    if patch_matches(current, patch):
        ledger.state(action_id, "applied")
        return "already_applied"
    if snapshot(current) != row["source_hash"]:
        ledger.state(action_id, "conflict", "source_changed_since_preview")
        return "conflict"
    ledger.state(action_id, "applying")
    try:
        client.update(page_id, patch)
    except Exception:
        try:
            after = client.page(page_id)
        except Exception:
            ledger.state(action_id, "review", "write_and_reconciliation_unconfirmed")
            return "review"
        if patch_matches(after, patch):
            ledger.state(action_id, "applied")
            return "reconciled"
        ledger.state(action_id, "review", "write_unconfirmed_no_retry")
        return "review"
    try:
        after = client.page(page_id)
    except Exception:
        ledger.state(action_id, "review", "verification_unavailable")
        return "review"
    if not patch_matches(after, patch):
        ledger.state(action_id, "review", "verification_mismatch")
        return "review"
    ledger.state(action_id, "applied")
    return "applied"


def apply_ready(client, ledger, include_auto=False):
    if os.environ.get("P_OPS_NOTION_WRITE") != "true":
        raise ValueError("Set P_OPS_NOTION_WRITE=true to apply queued Notion changes.")
    states = ["approved"] + (["auto_ready"] if include_auto else [])
    results = {}
    for row in ledger.rows(states):
        results[row["id"]] = apply_action(client, ledger, row)
    return results


def main():
    parser = argparse.ArgumentParser(description="p-OPS Notion preview and controlled apply queue")
    parser.add_argument("--ledger", type=Path, default=Path("state/p_ops.sqlite3"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preview")
    sub.add_parser("list")
    approve = sub.add_parser("approve")
    approve.add_argument("action_id")
    apply_cmd = sub.add_parser("apply")
    apply_cmd.add_argument("--include-auto", action="store_true")
    args = parser.parse_args()
    ledger = Ledger(args.ledger)
    if args.command == "list":
        print(json.dumps(ledger.rows(), indent=2))
    elif args.command == "approve":
        ledger.approve(args.action_id)
        print("Approved " + args.action_id + ". No Notion write was performed.")
    elif args.command == "preview":
        print(json.dumps(preview(NotionClient(), ledger), indent=2))
    else:
        print(json.dumps(apply_ready(NotionClient(), ledger, args.include_auto), indent=2))


if __name__ == "__main__":
    main()
