"""Standalone p-OPS receiver, review dashboard, and single background worker.

This runtime is intentionally separate from the existing Slack chat service.
Every external side effect retains its module-level approval and environment
gate. The dashboard only changes persisted approval state.
"""
import base64
import datetime as dt
import hashlib
import hmac
import html
import json
import os
from pathlib import Path
import sqlite3
import time
import urllib.parse
from zoneinfo import ZoneInfo

from intake import Application, api
from p_ops_approval import ApprovalLedger, send_message
from p_ops_capture import CaptureStore, SlackCaptureClient, accept_reaction, load_capture_config, resolve_capture
from p_ops_inbox import InboxLedger, create_approved_task
from p_ops_linear import LinearClient, LinearLedger, create_approved_issue
from p_ops_notion import Ledger as NotionLedger, NotionClient, apply_ready, preview
from p_ops_shadow import ShadowLedger, run_shadow
from p_ops_triage import ResponsesTriageClient


REVIEW_STATES = ("needs_review", "approved", "send_authorized", "review", "conflict")


def config(env=None):
    env = env or os.environ
    runtime_enabled = env.get("P_OPS_ENABLED") == "true"
    cfg = load_capture_config(env) if runtime_enabled else {
        "enabled": False, "secret": "", "team_id": "", "app_id": "",
        "channel_ids": set(), "user_ids": set(),
    }
    cfg.update({
        "runtime_enabled": runtime_enabled,
        "db": env.get("P_OPS_DB", "/data/p_ops.sqlite3"),
        "review_user": env.get("P_OPS_REVIEW_USER", ""),
        "review_password": env.get("P_OPS_REVIEW_PASSWORD", ""),
        "csrf_secret": env.get("P_OPS_CSRF_SECRET", ""),
        "daily_review": env.get("P_OPS_DAILY_REVIEW_ENABLED") == "true",
        "triage_shadow": env.get("P_OPS_TRIAGE_SHADOW_ENABLED") == "true",
    })
    if cfg["runtime_enabled"] and not all((cfg["review_user"], cfg["review_password"], cfg["csrf_secret"])):
        raise ValueError("Set P_OPS_REVIEW_USER, P_OPS_REVIEW_PASSWORD, and P_OPS_CSRF_SECRET.")
    return cfg


class RuntimeState:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS p_ops_runtime_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get(self, key):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT value FROM p_ops_runtime_state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key, value):
        with sqlite3.connect(self.path) as db:
            db.execute("INSERT INTO p_ops_runtime_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (key, value))


class SlackSender:
    def __init__(self, token=None):
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.token:
            raise ValueError("Set SLACK_BOT_TOKEN before sending Slack messages.")

    def __call__(self, row):
        if row["kind"] != "slack":
            raise ValueError("This sender only supports Slack drafts.")
        result = api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": row["destination"], "text": row["content"], "mrkdwn": False,
            "parse": "none", "unfurl_links": False, "unfurl_media": False})
        if not result.get("ts"):
            raise ValueError("Slack did not confirm message delivery.")
        return result["ts"]


def _basic_authorized(env, cfg):
    value = env.get("HTTP_AUTHORIZATION", "")
    if not value.startswith("Basic "):
        return False
    try:
        user, password = base64.b64decode(value[6:], validate=True).decode().split(":", 1)
    except (ValueError, UnicodeDecodeError):
        return False
    return hmac.compare_digest(user, cfg["review_user"]) and hmac.compare_digest(password, cfg["review_password"])


def _csrf(cfg, kind, action, item_id):
    value = ":".join((kind, action, item_id)).encode()
    return hmac.new(cfg["csrf_secret"].encode(), value, hashlib.sha256).hexdigest()


def _button(cfg, kind, action, item_id, label):
    token = _csrf(cfg, kind, action, item_id)
    return ("<form method='post' action='/review/action'><input type='hidden' name='kind' value='%s'>"
            "<input type='hidden' name='action' value='%s'><input type='hidden' name='id' value='%s'>"
            "<input type='hidden' name='csrf' value='%s'><button>%s</button></form>" %
            tuple(html.escape(x, quote=True) for x in (kind, action, item_id, token, label)))


def _card(title, state, body, buttons=""):
    return "<article><h3>%s</h3><p><strong>%s</strong></p><pre>%s</pre><div class='actions'>%s</div></article>" % (
        html.escape(title), html.escape(state), html.escape(body), buttons)


class Dashboard:
    def __init__(self, cfg, capture, inbox, messages, linear, notion, triage):
        self.cfg, self.capture, self.inbox = cfg, capture, inbox
        self.messages, self.linear, self.notion, self.triage = messages, linear, notion, triage

    def render(self):
        sections = []
        captures = self.capture.rows(("pending", "resolving", "captured", "review"))
        cards = [_card("Slack capture", row["state"], row.get("source_text") or row.get("error") or "Waiting to resolve")
                 for row in captures]
        sections.append("<section><h2>Captures</h2>%s</section>" % ("".join(cards) or "<p>Nothing pending.</p>"))

        cards = []
        for row in self.inbox.rows(REVIEW_STATES):
            data = json.loads(row["proposal_json"])
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "inbox", "approve", row["id"], "Approve task") + _button(
                    self.cfg, "inbox", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                buttons = _button(self.cfg, "inbox", "reject", row["id"], "Cancel approval")
            cards.append(_card(data["title"], row["state"], data["context"] + "\n\nNext: " + data["next_step"], buttons))
        sections.append("<section><h2>Task proposals</h2>%s</section>" % ("".join(cards) or "<p>Nothing pending.</p>"))

        cards = []
        for row in self.messages.rows(REVIEW_STATES):
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "message", "approve", row["id"], "Approve wording") + _button(
                    self.cfg, "message", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                if row["kind"] == "slack":
                    buttons = _button(self.cfg, "message", "authorize", row["id"], "Authorize send")
                buttons += _button(self.cfg, "message", "reject", row["id"], "Reject")
            cards.append(_card("%s to %s" % (row["kind"].title(), row["destination"]), row["state"],
                               ((row["subject"] + "\n\n") if row["subject"] else "") + row["content"], buttons))
        sections.append("<section><h2>Messages</h2>%s</section>" % ("".join(cards) or "<p>Nothing pending.</p>"))

        cards = []
        for row in self.linear.rows(REVIEW_STATES):
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "linear", "approve", row["id"], "Approve issue") + _button(
                    self.cfg, "linear", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                buttons = _button(self.cfg, "linear", "reject", row["id"], "Cancel approval")
            cards.append(_card(row["title"], row["state"], row["description"], buttons))
        sections.append("<section><h2>Linear</h2>%s</section>" % ("".join(cards) or "<p>Nothing pending.</p>"))

        cards = []
        for row in self.notion.rows(REVIEW_STATES):
            plan = json.loads(row["plan_json"])
            buttons = (_button(self.cfg, "notion", "approve", row["id"], "Approve update") +
                       _button(self.cfg, "notion", "reject", row["id"], "Reject")) if row["state"] == "needs_review" else ""
            cards.append(_card(row["title"], row["state"], plan.get("reason", ""), buttons))
        sections.append("<section><h2>Task updates</h2>%s</section>" % ("".join(cards) or "<p>Nothing pending.</p>"))

        cards = []
        for row in self.triage.rows(("shadow", "error")):
            if row["decision_json"]:
                guarded = json.loads(row["decision_json"])
                decision = guarded["decision"]
                body = (decision["summary"] + "\n\nDisposition: " + decision["disposition"] +
                        "\nReview required: " + str(guarded["review_required"]) +
                        "\nPolicy: " + ", ".join(guarded["policy_reasons"]) +
                        "\nViolations: " + (", ".join(guarded["violations"]) or "none"))
                title = decision["title"]
            else:
                bundle = json.loads(row["bundle_json"])
                title = bundle["source"].get("text", "Shadow triage")[:120]
                body = row["error"] or "Triage failed."
            cards.append(_card(title, row["state"] + " - read only", body))
        sections.append("<section><h2>Shadow triage</h2>%s</section>" %
                        ("".join(cards) or "<p>Nothing analyzed.</p>"))
        return "".join(sections)

    def action(self, kind, action, item_id):
        if kind == "inbox" and action == "approve": self.inbox.approve(item_id)
        elif kind == "inbox" and action == "reject": self.inbox.reject(item_id)
        elif kind == "message" and action == "approve": self.messages.approve(item_id)
        elif kind == "message" and action == "reject": self.messages.reject(item_id)
        elif kind == "message" and action == "authorize": self.messages.authorize_send(item_id)
        elif kind == "linear" and action == "approve": self.linear.approve(item_id)
        elif kind == "linear" and action == "reject": self.linear.reject(item_id)
        elif kind == "notion" and action == "approve": self.notion.approve(item_id)
        elif kind == "notion" and action == "reject": self.notion.reject(item_id)
        else: raise ValueError("Unsupported review action.")


class RuntimeApp:
    def __init__(self, cfg, dashboard, slack_app):
        self.cfg, self.dashboard, self.slack_app = cfg, dashboard, slack_app

    def __call__(self, env, start):
        def respond(code, data, content_type="application/json", headers=()):
            raw = data if isinstance(data, bytes) else data.encode()
            base = [("Content-Type", content_type), ("Content-Length", str(len(raw))),
                    ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY")]
            start(code, base + list(headers)); return [raw]
        path, method = env.get("PATH_INFO"), env.get("REQUEST_METHOD")
        if path == "/healthz" and method == "GET":
            return respond("200 OK", json.dumps({"status": "listening", "p_ops_enabled": self.cfg["runtime_enabled"],
                                                  "capture_enabled": self.cfg["enabled"],
                                                  "triage_shadow_enabled": self.cfg.get("triage_shadow", False)}))
        if not self.cfg["runtime_enabled"]:
            return respond("503 Service Unavailable", json.dumps({"error": "p_ops_disabled"}))
        if path == "/slack/events":
            return self.slack_app(env, start)
        if path not in {"/review", "/review/action"}:
            return respond("404 Not Found", "{}")
        if not _basic_authorized(env, self.cfg):
            return respond("401 Unauthorized", "Authentication required", "text/plain; charset=utf-8",
                           (("WWW-Authenticate", 'Basic realm="p-OPS review"'),))
        if path == "/review" and method == "GET":
            body = """<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>
            <title>p-OPS Review</title><style>body{font-family:system-ui;max-width:980px;margin:32px auto;padding:0 18px;background:#f6f7f9;color:#17202a}article{background:white;border:1px solid #dfe3e8;border-radius:10px;padding:16px;margin:12px 0}pre{white-space:pre-wrap;font:inherit}.actions{display:flex;gap:8px}.actions form{display:inline}button{padding:8px 12px}h1{margin-bottom:4px}</style><h1>p-OPS review</h1><p>Approve exact actions. Nothing here bypasses its write or send gate.</p>%s""" % self.dashboard.render()
            return respond("200 OK", body, "text/html; charset=utf-8",
                           (("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'"),))
        if path == "/review/action" and method == "POST":
            try:
                size = int(env.get("CONTENT_LENGTH") or 0)
                if size <= 0 or size > 16384: raise ValueError()
                form = {key: values[0] for key, values in urllib.parse.parse_qs(
                    env["wsgi.input"].read(size).decode(), keep_blank_values=True).items()}
                kind, action, item_id = form["kind"], form["action"], form["id"]
                if not hmac.compare_digest(form.get("csrf", ""), _csrf(self.cfg, kind, action, item_id)):
                    return respond("403 Forbidden", "Invalid review token", "text/plain; charset=utf-8")
                self.dashboard.action(kind, action, item_id)
            except (ValueError, KeyError, UnicodeDecodeError, sqlite3.Error):
                return respond("400 Bad Request", "Review action was not applied", "text/plain; charset=utf-8")
            return respond("303 See Other", b"", headers=(("Location", "/review"),))
        return respond("405 Method Not Allowed", "{}")


def components(cfg):
    path = cfg["db"]
    capture, inbox = CaptureStore(path), InboxLedger(path)
    messages, linear = ApprovalLedger(path), LinearLedger(path)
    notion, triage = NotionLedger(path), ShadowLedger(path)
    return capture, inbox, messages, linear, notion, triage


def create_app():
    cfg = config()
    capture, inbox, messages, linear, notion, triage = components(cfg)
    dashboard = Dashboard(cfg, capture, inbox, messages, linear, notion, triage)
    slack_app = Application(cfg, capture, accept_reaction)
    return RuntimeApp(cfg, dashboard, slack_app)


def run_once(cfg, stores, clients=None, today=None):
    capture, inbox, messages, linear, notion, triage_ledger = stores
    clients = clients or {}
    results = []
    for row in capture.rows(("pending",))[:10]:
        client = clients.get("slack_capture") or SlackCaptureClient()
        results.append(("capture", row["event_id"], resolve_capture(capture, row["event_id"], client)))
    for row in capture.rows(("captured",)):
        inbox.propose(row)
    if cfg.get("triage_shadow"):
        notion_client = clients.get("notion") or NotionClient()
        triage_client = clients.get("triage") or ResponsesTriageClient()
        shadow_rows = run_shadow(inbox, triage_ledger, notion_client, triage_client, current_date=today)
        results.extend(("triage_shadow", item_id, state) for item_id, state in shadow_rows)
    if os.environ.get("P_OPS_NOTION_CREATE") == "true":
        client = clients.get("notion") or NotionClient()
        for row in inbox.rows(("approved",)):
            results.append(("inbox", row["id"], create_approved_task(client, inbox, row["id"])))
    if os.environ.get("P_OPS_LINEAR_WRITE") == "true":
        client = clients.get("linear") or LinearClient()
        for row in linear.rows(("approved",)):
            results.append(("linear", row["id"], create_approved_issue(client, linear, row["id"])))
    if os.environ.get("P_OPS_SEND_MESSAGES") == "true":
        sender = clients.get("slack_sender") or SlackSender()
        for row in messages.rows(("send_authorized",)):
            if row["kind"] == "slack":
                results.append(("message", row["id"], send_message(messages, row["id"], sender)))
    state = RuntimeState(cfg["db"])
    day = (today or dt.datetime.now(ZoneInfo("America/Denver")).date()).isoformat()
    if cfg["daily_review"] and state.get("last_daily_preview") != day:
        client = clients.get("notion") or NotionClient()
        results.append(("daily_preview", day, preview(client, notion, dt.date.fromisoformat(day))))
        state.set("last_daily_preview", day)
    if os.environ.get("P_OPS_NOTION_WRITE") == "true" and notion.rows(("approved",)):
        client = clients.get("notion") or NotionClient()
        results.append(("notion", "approved", apply_ready(client, notion, include_auto=False)))
    return results


def worker():
    import fcntl
    cfg = config()
    if not cfg["runtime_enabled"]:
        raise SystemExit("Set P_OPS_ENABLED=true to run the p-OPS worker.")
    stores = components(cfg)
    with open(cfg["db"] + ".worker.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        while True:
            run_once(cfg, stores)
            time.sleep(max(1, int(os.environ.get("P_OPS_POLL_SECONDS", "5"))))


if __name__ == "__main__":
    worker()
