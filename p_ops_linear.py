"""Pilot-safe Linear issue drafting and controlled creation for p-OPS.

Issue creation requires exact-draft approval and P_OPS_LINEAR_WRITE=true.
Priority, due date, assignment, project, and roadmap commitments are outside this
adapter's write vocabulary.
"""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.error
import urllib.request
import uuid


ENDPOINT = "https://api.linear.app/graphql"


class LinearError(Exception):
    pass


def issue_description(source_url, context, business_impact, acceptance_criteria):
    sections = [
        "## Source", source_url,
        "## Context", context.strip(),
        "## Business impact", business_impact.strip(),
        "## Acceptance criteria", acceptance_criteria.strip(),
    ]
    return "\n\n".join(sections)


def draft_hash(team_id, title, description, source_url):
    raw = {"team_id": team_id, "title": title, "description": description, "source_url": source_url}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class LinearClient:
    def __init__(self, token=None, auth_mode=None, opener=None):
        self.token = token or os.environ.get("LINEAR_ACCESS_TOKEN", "")
        self.auth_mode = auth_mode or os.environ.get("LINEAR_AUTH_MODE", "personal")
        self.opener = opener or urllib.request.urlopen
        if not self.token:
            raise ValueError("Set LINEAR_ACCESS_TOKEN before reading Linear.")
        if self.auth_mode not in {"personal", "oauth"}:
            raise ValueError("LINEAR_AUTH_MODE must be personal or oauth.")

    def graphql(self, query, variables=None):
        auth = ("Bearer " if self.auth_mode == "oauth" else "") + self.token
        request = urllib.request.Request(ENDPOINT, method="POST",
            data=json.dumps({"query": query, "variables": variables or {}}).encode(),
            headers={"Authorization": auth, "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=30) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise LinearError("Linear HTTP %d" % exc.code) from None
        except (OSError, ValueError, TimeoutError):
            raise LinearError("Linear response unavailable") from None
        if result.get("errors"):
            raise LinearError("Linear GraphQL returned errors")
        return result.get("data") or {}

    def teams(self):
        data = self.graphql("query Teams { teams { nodes { id key name } } }")
        return data.get("teams", {}).get("nodes", [])

    def find_by_source(self, source_url):
        query = """query SourceIssue($source: String!) {
          issues(first: 10, filter: { description: { contains: $source } }) {
            nodes { id identifier title url description }
          }
        }"""
        data = self.graphql(query, {"source": source_url})
        return data.get("issues", {}).get("nodes", [])

    def create_issue(self, team_id, title, description):
        mutation = """mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) { success issue { id identifier title url } }
        }"""
        data = self.graphql(mutation, {"input": {"teamId": team_id, "title": title,
                                                  "description": description}})
        result = data.get("issueCreate") or {}
        issue = result.get("issue") or {}
        if not result.get("success") or not issue.get("id") or not issue.get("url"):
            raise LinearError("Linear did not confirm issue creation")
        return issue


class LinearLedger:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_linear_actions (
                id TEXT PRIMARY KEY, source_url TEXT UNIQUE NOT NULL, team_id TEXT NOT NULL,
                title TEXT NOT NULL, description TEXT NOT NULL, draft_hash TEXT NOT NULL,
                approved_hash TEXT, state TEXT NOT NULL, created REAL NOT NULL, approved REAL,
                attempted REAL, completed REAL, issue_id TEXT, issue_url TEXT, error TEXT)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def draft(self, source_url, team_id, title, context, business_impact, acceptance_criteria):
        if not all(x.strip() for x in (source_url, team_id, title, context, business_impact, acceptance_criteria)):
            raise ValueError("A Linear draft requires source, team, title, context, impact, and acceptance criteria.")
        description = issue_description(source_url, context, business_impact, acceptance_criteria)
        digest = draft_hash(team_id, title, description, source_url)
        action_id = uuid.uuid4().hex[:24]
        try:
            with self.connect() as db:
                db.execute("""INSERT INTO p_ops_linear_actions
                    (id,source_url,team_id,title,description,draft_hash,state,created)
                    VALUES (?,?,?,?,?,?,'needs_review',?)""",
                    (action_id, source_url, team_id, title, description, digest, time.time()))
        except sqlite3.IntegrityError:
            raise ValueError("A Linear action already exists for this source.") from None
        return action_id

    def get(self, action_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_linear_actions WHERE id=?", (action_id,)).fetchone()
        if not row:
            raise KeyError(action_id)
        return dict(row)

    def approve(self, action_id):
        row = self.get(action_id)
        if row["state"] != "needs_review":
            raise ValueError("Linear draft is not waiting for review.")
        with self.connect() as db:
            db.execute("UPDATE p_ops_linear_actions SET state='approved',approved_hash=draft_hash,approved=? WHERE id=?",
                       (time.time(), action_id))

    def state(self, action_id, state, **fields):
        allowed = {"attempted", "completed", "issue_id", "issue_url", "error"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported Linear field.")
        values = {"state": state, **fields}
        with self.connect() as db:
            db.execute("UPDATE p_ops_linear_actions SET " + ",".join(k + "=?" for k in values) + " WHERE id=?",
                       list(values.values()) + [action_id])


def create_approved_issue(client, ledger, action_id):
    if os.environ.get("P_OPS_LINEAR_WRITE") != "true":
        raise ValueError("Set P_OPS_LINEAR_WRITE=true to create Linear issues.")
    row = ledger.get(action_id)
    current_hash = draft_hash(row["team_id"], row["title"], row["description"], row["source_url"])
    if row["state"] != "approved" or row["approved_hash"] != current_hash:
        raise ValueError("Approve the exact current Linear draft before creation.")
    duplicates = client.find_by_source(row["source_url"])
    if duplicates:
        issue = duplicates[0]
        ledger.state(action_id, "duplicate", completed=time.time(), issue_id=issue.get("id"),
                     issue_url=issue.get("url"), error="existing_issue_found")
        return "duplicate"
    ledger.state(action_id, "creating", attempted=time.time())
    try:
        issue = client.create_issue(row["team_id"], row["title"], row["description"])
    except Exception:
        ledger.state(action_id, "review", error="creation_outcome_unconfirmed_no_retry")
        return "review"
    ledger.state(action_id, "created", completed=time.time(), issue_id=issue["id"],
                 issue_url=issue["url"], error=None)
    return "created"
