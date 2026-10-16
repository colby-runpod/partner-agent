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
from p_ops_notifications import ReviewNotificationLedger
from p_ops_shadow import ShadowLedger, run_shadow
from p_ops_triage import ClaudeTriageClient


REVIEW_STATES = ("needs_review", "approved", "send_authorized", "review", "conflict")


DASHBOARD_CSS = r"""
:root {
  color-scheme: light;
  --bg: #f5f6fa;
  --surface: #ffffff;
  --surface-soft: #fafaff;
  --border: #e2e4eb;
  --border-strong: #d1d4de;
  --text: #17171c;
  --muted: #686a75;
  --subtle: #8b8d98;
  --purple: #5d29f0;
  --purple-dark: #4817d3;
  --purple-soft: #f0edff;
  --green: #087a55;
  --green-soft: #e9f8f1;
  --amber: #966516;
  --amber-soft: #fff6df;
  --red: #b33142;
  --red-soft: #fff0f2;
  --blue: #2463a9;
  --blue-soft: #edf5ff;
  --shadow: 0 1px 2px rgba(20, 20, 32, .04), 0 8px 24px rgba(33, 25, 57, .045);
  font-family: Diatype, Inter, ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  font-size: 15px;
  line-height: 1.45;
}
* { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body { margin: 0; color: var(--text); background: var(--bg); }
button, input, textarea, select { font: inherit; }
a { color: inherit; }
.shell { width: 100%; min-height: 100vh; overflow-x: hidden; display: grid; grid-template-columns: 244px minmax(0, 1fr); }
.sidebar {
  min-width: 0; position: sticky; top: 0; height: 100vh; padding: 24px 16px 18px;
  background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column;
}
.brand { display: flex; align-items: center; height: 34px; padding: 0 8px; margin-bottom: 34px; }
.brand img { width: 132px; height: auto; display: block; }
.nav-label { margin: 0 10px 8px; color: var(--subtle); font-size: 11px; font-weight: 650; letter-spacing: .08em; text-transform: uppercase; }
.nav { display: grid; gap: 4px; }
.nav a { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 10px 11px; border-radius: 8px; text-decoration: none; color: #34343b; font-size: 14px; }
.nav a:hover { background: #f1f1f5; }
.nav a.active { background: #ececf1; font-weight: 650; }
.nav-count { min-width: 24px; padding: 1px 7px; border-radius: 999px; background: #e2e2e8; color: #52545e; font-size: 11px; text-align: center; }
.side-footer { margin-top: auto; padding: 16px 10px 2px; border-top: 1px solid var(--border); }
.side-footer strong { display: block; font-size: 13px; }
.side-footer span { display: block; margin-top: 4px; color: var(--muted); font-size: 12px; }
.status-dot { display: inline-block; width: 7px; height: 7px; margin-right: 6px; border-radius: 50%; background: #21a273; box-shadow: 0 0 0 3px #e3f6ef; }
.main { min-width: 0; }
.topbar { min-height: 66px; padding: 0 38px; border-bottom: 1px solid var(--border); background: rgba(255,255,255,.94); display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.topbar-title { font-size: 14px; font-weight: 650; }
.topbar-meta { display: flex; align-items: center; gap: 9px; }
.environment { padding: 6px 10px; border: 1px solid var(--border); border-radius: 7px; color: var(--muted); background: var(--surface); font-size: 12px; }
.avatar { display: grid; place-items: center; width: 32px; height: 32px; border-radius: 50%; background: var(--purple); color: white; font-size: 11px; font-weight: 750; }
.content { width: min(1240px, 100%); margin: 0 auto; padding: 42px 38px 72px; }
.hero { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; margin-bottom: 27px; }
.eyebrow { margin: 0 0 8px; color: var(--purple); font-size: 11px; font-weight: 750; letter-spacing: .09em; text-transform: uppercase; }
h1 { margin: 0; font-size: clamp(30px, 4vw, 42px); line-height: 1.12; letter-spacing: -.035em; font-weight: 650; }
.lede { max-width: 720px; margin: 12px 0 0; color: var(--muted); font-size: 15px; }
.mode-pill { flex: 0 0 auto; display: inline-flex; align-items: center; gap: 7px; padding: 8px 11px; border: 1px solid #dcd5ff; border-radius: 999px; color: var(--purple-dark); background: var(--purple-soft); font-size: 12px; font-weight: 650; }
.mode-pill::before { content: ""; width: 7px; height: 7px; border-radius: 50%; background: var(--purple); }
.safety { display: flex; align-items: center; justify-content: space-between; gap: 24px; padding: 14px 16px; margin-bottom: 24px; border: 1px solid #e5defe; border-radius: 10px; background: #f8f6ff; }
.safety-copy { min-width: 0; }
.safety-copy strong { display: block; font-size: 13px; }
.safety-copy span { color: var(--muted); font-size: 12px; }
.gates { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 7px; }
.gate { padding: 5px 8px; border: 1px solid #ded9ee; border-radius: 6px; background: white; color: #5b5767; font-size: 11px; white-space: nowrap; }
.metrics { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 32px; }
.metric { min-width: 0; padding: 18px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); box-shadow: 0 1px 1px rgba(20,20,32,.02); }
.metric-value { display: block; margin-bottom: 3px; font-size: 28px; line-height: 1; letter-spacing: -.04em; font-weight: 650; }
.metric-label { color: var(--muted); font-size: 12px; }
.queue-section { scroll-margin-top: 22px; margin-top: 34px; }
.section-heading { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 12px; }
.section-heading h2 { margin: 0; font-size: 20px; letter-spacing: -.02em; font-weight: 650; }
.section-heading p { margin: 3px 0 0; color: var(--muted); font-size: 12px; }
.section-count { color: var(--subtle); font-size: 12px; }
.queue-list { display: grid; gap: 12px; }
.review-card { overflow: hidden; border: 1px solid var(--border); border-radius: 11px; background: var(--surface); box-shadow: var(--shadow); }
.card-main { padding: 19px 20px 18px; }
.card-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
.card-title { min-width: 0; }
.card-title h3 { margin: 0; font-size: 15px; line-height: 1.35; font-weight: 650; overflow-wrap: anywhere; }
.card-kind { margin: 0 0 5px; color: var(--subtle); font-size: 10px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
.state { flex: 0 0 auto; padding: 4px 8px; border-radius: 999px; background: #f0f1f4; color: #5d5f68; font-size: 10px; font-weight: 700; letter-spacing: .02em; text-transform: uppercase; }
.state-needs-review, .state-review, .state-conflict { background: var(--amber-soft); color: var(--amber); }
.state-approved, .state-send-authorized, .state-captured { background: var(--green-soft); color: var(--green); }
.state-shadow, .state-resolving { background: var(--blue-soft); color: var(--blue); }
.state-error, .state-failed { background: var(--red-soft); color: var(--red); }
.card-body { margin: 14px 0 0; color: #484a54; white-space: pre-wrap; overflow-wrap: anywhere; font: inherit; font-size: 13px; line-height: 1.55; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 17px; padding-top: 15px; border-top: 1px solid var(--border); }
.actions form { display: inline; margin: 0; }
button { min-height: 34px; padding: 7px 12px; border: 1px solid var(--purple); border-radius: 7px; background: var(--purple); color: white; cursor: pointer; font-size: 12px; font-weight: 650; }
button:hover { background: var(--purple-dark); border-color: var(--purple-dark); }
button.secondary { border-color: var(--border-strong); background: white; color: #3f4149; }
button.secondary:hover { border-color: #b9bbc5; background: #f6f6f8; }
.empty { display: grid; place-items: center; min-height: 98px; padding: 22px; border: 1px dashed var(--border-strong); border-radius: 10px; background: rgba(255,255,255,.45); color: var(--muted); text-align: center; font-size: 13px; }
.empty strong { display: block; margin-bottom: 3px; color: #3c3e47; font-weight: 600; }
.page-footer { margin-top: 48px; padding-top: 20px; border-top: 1px solid var(--border); color: var(--subtle); font-size: 11px; }
@media (max-width: 940px) {
  .shell { grid-template-columns: minmax(0, 1fr); }
  .sidebar { position: static; width: 100%; height: auto; padding: 16px 20px; display: block; }
  .brand { margin-bottom: 14px; }
  .nav-label, .side-footer { display: none; }
  .nav { display: flex; overflow-x: auto; padding-bottom: 2px; }
  .nav a { flex: 0 0 auto; }
  .topbar { padding: 0 22px; }
  .content { padding: 30px 22px 56px; }
  .metrics { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
@media (max-width: 600px) {
  .topbar-meta .environment { display: none; }
  .hero, .safety { align-items: flex-start; flex-direction: column; }
  .gates { justify-content: flex-start; }
  .metrics { grid-template-columns: 1fr 1fr; }
  .card-head { flex-direction: column; gap: 9px; }
}
"""


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
        "review_posting": env.get("P_OPS_REVIEW_POSTING_ENABLED") == "true",
        "review_channel": env.get("P_OPS_REVIEW_CHANNEL_ID", ""),
    })
    if cfg["runtime_enabled"] and not all((cfg["review_user"], cfg["review_password"], cfg["csrf_secret"])):
        raise ValueError("Set P_OPS_REVIEW_USER, P_OPS_REVIEW_PASSWORD, and P_OPS_CSRF_SECRET.")
    if cfg["review_posting"] and not cfg["review_channel"]:
        raise ValueError("Set P_OPS_REVIEW_CHANNEL_ID before enabling review posting.")
    if cfg["review_posting"] and not env.get("SLACK_BOT_TOKEN"):
        raise ValueError("Set SLACK_BOT_TOKEN before enabling review posting.")
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


class SlackReviewSender:
    def __init__(self, expected_channel, token=None):
        self.expected_channel = expected_channel
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.expected_channel or not self.token:
            raise ValueError("Review channel and Slack bot token are required.")

    def __call__(self, row):
        if row["channel"] != self.expected_channel:
            raise ValueError("Review notification destination is not authorized.")
        result = api("https://slack.com/api/chat.postMessage", self.token, {
            "channel": self.expected_channel, "text": row["content"], "mrkdwn": False,
            "parse": "none", "unfurl_links": False, "unfurl_media": False})
        if not result.get("ts"):
            raise ValueError("Slack did not confirm review notification delivery.")
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
    css_class = "secondary" if action == "reject" else "primary"
    return ("<form method='post' action='/review/action'><input type='hidden' name='kind' value='%s'>"
            "<input type='hidden' name='action' value='%s'><input type='hidden' name='id' value='%s'>"
            "<input type='hidden' name='csrf' value='%s'><button class='%s'>%s</button></form>" %
            tuple(html.escape(x, quote=True) for x in (kind, action, item_id, token, css_class, label)))


def _card(title, state, body, buttons="", kind="Work item"):
    state_class = "".join(character if character.isalnum() else "-" for character in state.lower()).strip("-")
    actions = "<div class='actions'>%s</div>" % buttons if buttons else ""
    return ("<article class='review-card'><div class='card-main'><div class='card-head'>"
            "<div class='card-title'><p class='card-kind'>%s</p><h3>%s</h3></div>"
            "<span class='state state-%s'>%s</span></div><div class='card-body'>%s</div>%s"
            "</div></article>" % (
                html.escape(kind), html.escape(title), html.escape(state_class, quote=True),
                html.escape(state.replace("_", " ")), html.escape(body), actions))


def _empty(title, detail):
    return "<div class='empty'><div><strong>%s</strong>%s</div></div>" % (
        html.escape(title), html.escape(detail))


def _section(section_id, title, detail, cards):
    count = len(cards)
    content = "".join(cards) if cards else _empty("Queue is clear", "Nothing needs attention here.")
    return ("<section class='queue-section' id='%s'><div class='section-heading'><div><h2>%s</h2>"
            "<p>%s</p></div><span class='section-count'>%d %s</span></div><div class='queue-list'>%s</div>"
            "</section>" % (html.escape(section_id, quote=True), html.escape(title), html.escape(detail),
                             count, "item" if count == 1 else "items", content))


class Dashboard:
    def __init__(self, cfg, capture, inbox, messages, linear, notion, triage):
        self.cfg, self.capture, self.inbox = cfg, capture, inbox
        self.messages, self.linear, self.notion, self.triage = messages, linear, notion, triage

    def render(self):
        captures = self.capture.rows(("pending", "resolving", "captured", "review"))
        inbox_rows = self.inbox.rows(REVIEW_STATES)
        message_rows = self.messages.rows(REVIEW_STATES)
        linear_rows = self.linear.rows(REVIEW_STATES)
        notion_rows = self.notion.rows(REVIEW_STATES)
        triage_rows = self.triage.rows(("shadow", "error"))

        sections = []
        cards = [_card("Slack capture", row["state"], row.get("source_text") or row.get("error") or "Waiting to resolve",
                       kind="Slack capture")
                 for row in captures]
        sections.append(_section("captures", "Captures", "Slack reactions waiting to be resolved or classified.", cards))

        cards = []
        for row in inbox_rows:
            data = json.loads(row["proposal_json"])
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "inbox", "approve", row["id"], "Approve task") + _button(
                    self.cfg, "inbox", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                buttons = _button(self.cfg, "inbox", "reject", row["id"], "Cancel approval")
            cards.append(_card(data["title"], row["state"], data["context"] + "\n\nNext: " + data["next_step"],
                               buttons, "Task proposal"))
        sections.append(_section("task-proposals", "Task proposals",
                                 "New tasks proposed from captured work and supporting evidence.", cards))

        cards = []
        for row in message_rows:
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "message", "approve", row["id"], "Approve wording") + _button(
                    self.cfg, "message", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                if row["kind"] == "slack":
                    buttons = _button(self.cfg, "message", "authorize", row["id"], "Authorize send")
                buttons += _button(self.cfg, "message", "reject", row["id"], "Reject")
            cards.append(_card("%s to %s" % (row["kind"].title(), row["destination"]), row["state"],
                               ((row["subject"] + "\n\n") if row["subject"] else "") + row["content"], buttons,
                               "Message draft"))
        sections.append(_section("messages", "Messages", "Drafts that require review before anything is sent.", cards))

        cards = []
        for row in linear_rows:
            buttons = ""
            if row["state"] == "needs_review":
                buttons = _button(self.cfg, "linear", "approve", row["id"], "Approve issue") + _button(
                    self.cfg, "linear", "reject", row["id"], "Reject")
            elif row["state"] == "approved":
                buttons = _button(self.cfg, "linear", "reject", row["id"], "Cancel approval")
            cards.append(_card(row["title"], row["state"], row["description"], buttons, "Linear issue"))
        sections.append(_section("linear", "Linear", "Engineering work that may need a linked Linear issue.", cards))

        cards = []
        for row in notion_rows:
            plan = json.loads(row["plan_json"])
            buttons = (_button(self.cfg, "notion", "approve", row["id"], "Approve update") +
                       _button(self.cfg, "notion", "reject", row["id"], "Reject")) if row["state"] == "needs_review" else ""
            cards.append(_card(row["title"], row["state"], plan.get("reason", ""), buttons, "Notion task update"))
        sections.append(_section("task-updates", "Task updates",
                                 "Proposed changes to active Working Tasks in Notion.", cards))

        cards = []
        for row in triage_rows:
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
            cards.append(_card(title, row["state"], body, kind="Read-only evaluation"))
        sections.append(_section("shadow-triage", "Shadow triage",
                                 "Read-only decisions used to calibrate the agent before automation.", cards))

        groups = [
            ("captures", "Captures", len(captures)),
            ("task-proposals", "Task proposals", len(inbox_rows)),
            ("messages", "Messages", len(message_rows)),
            ("linear", "Linear", len(linear_rows)),
            ("task-updates", "Task updates", len(notion_rows)),
            ("shadow-triage", "Shadow triage", len(triage_rows)),
        ]
        all_rows = captures + inbox_rows + message_rows + linear_rows + notion_rows + triage_rows
        needs_review = sum(1 for row in all_rows if row["state"] in ("needs_review", "review", "conflict"))
        approved = sum(1 for row in all_rows if row["state"] in ("approved", "send_authorized"))
        nav = "".join("<a href='#%s'><span>%s</span><span class='nav-count'>%d</span></a>" %
                      (section_id, html.escape(label), count) for section_id, label, count in groups)
        metrics = "".join(
            "<div class='metric'><strong class='metric-value'>%d</strong><span class='metric-label'>%s</span></div>" % item
            for item in ((needs_review, "Needs review"), (approved, "Approved"),
                         (len(triage_rows), "Read-only evaluations"), (len(all_rows), "Open queue items")))
        gates = (
            ("Notion writes", os.environ.get("P_OPS_NOTION_WRITE") == "true"),
            ("Linear writes", os.environ.get("P_OPS_LINEAR_WRITE") == "true"),
            ("Message sending", os.environ.get("P_OPS_SEND_MESSAGES") == "true"),
            ("Slack review posts", bool(self.cfg.get("review_posting"))),
        )
        gate_html = "".join("<span class='gate'>%s: %s</span>" %
                            (html.escape(label), "on" if enabled else "off") for label, enabled in gates)
        return ("<div class='shell'><aside class='sidebar'><a class='brand' href='/review' aria-label='Runpod partner ops'>"
                "<img src='/assets/runpod-logo-purple.png' alt='Runpod'></a><p class='nav-label'>Queues</p>"
                "<nav class='nav'><a class='active' href='#overview'><span>Overview</span><span class='nav-count'>%d</span>"
                "</a>%s</nav><div class='side-footer'><strong><span class='status-dot'></span>Agent online</strong>"
                "<span>Partner ops · production</span></div></aside><main class='main'><header class='topbar'>"
                "<span class='topbar-title'>Partner ops</span><div class='topbar-meta'><span class='environment'>Production</span>"
                "<span class='avatar' aria-label='Colby'>CH</span></div></header><div class='content' id='overview'>"
                "<div class='hero'><div><p class='eyebrow'>Operations console</p><h1>Partner ops review</h1>"
                "<p class='lede'>Review agent recommendations, approve exact actions, and keep work moving without bypassing "
                "the write or send controls.</p></div><span class='mode-pill'>Guarded mode</span></div>"
                "<div class='safety'><div class='safety-copy'><strong>Automation safety controls</strong>"
                "<span>Approvals here only update the action queue. External changes still require their configured gate.</span>"
                "</div><div class='gates'>%s</div></div><div class='metrics'>%s</div>%s"
                "<footer class='page-footer'>Runpod Partner Ops · Internal operator console</footer></div></main></div>" %
                (len(all_rows), nav, gate_html, metrics, "".join(sections)))

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
                    ("Cache-Control", "no-store"), ("X-Frame-Options", "DENY"),
                    ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "same-origin")]
            start(code, base + list(headers)); return [raw]
        path, method = env.get("PATH_INFO"), env.get("REQUEST_METHOD")
        if path in ("/assets/runpod-logo-purple.png", "/assets/runpod-icon.png") and method == "GET":
            filename = path.rsplit("/", 1)[-1]
            asset = Path(__file__).with_name("assets") / filename
            if not asset.is_file():
                return respond("404 Not Found", "{}")
            return respond("200 OK", asset.read_bytes(), "image/png",
                           (("Cache-Control", "public, max-age=86400, immutable"),))
        if path == "/healthz" and method == "GET":
            return respond("200 OK", json.dumps({"status": "listening", "p_ops_enabled": self.cfg["runtime_enabled"],
                                                  "capture_enabled": self.cfg["enabled"],
                                                  "triage_shadow_enabled": self.cfg.get("triage_shadow", False),
                                                  "review_posting_enabled": self.cfg.get("review_posting", False),
                                                  "review_channel_configured": bool(self.cfg.get("review_channel"))}))
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
            body = ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
                    "<meta name='theme-color' content='#5d29f0'><title>Partner ops · Runpod</title>"
                    "<link rel='icon' type='image/png' href='/assets/runpod-icon.png'>"
                    "<style>%s</style></head><body>%s</body></html>" % (DASHBOARD_CSS, self.dashboard.render()))
            return respond("200 OK", body, "text/html; charset=utf-8",
                           (("Content-Security-Policy", "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
                                                       "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),))
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
        triage_client = clients.get("triage") or ClaudeTriageClient()
        shadow_rows = run_shadow(inbox, triage_ledger, notion_client, triage_client, current_date=today)
        results.extend(("triage_shadow", item_id, state) for item_id, state in shadow_rows)
        notifications = ReviewNotificationLedger(cfg["db"])
        for item_id, item_state in shadow_rows:
            if item_state != "shadow":
                continue
            row = triage_ledger.get(item_id)
            guarded = json.loads(row["decision_json"])
            if guarded.get("review_required") and cfg.get("review_channel"):
                notifications.queue(row, cfg.get("review_channel", ""))
        if cfg.get("review_posting"):
            sender = clients.get("review_sender") or SlackReviewSender(cfg["review_channel"])
            for row in notifications.rows(("queued",)):
                results.append(("review_notification", row["triage_id"],
                                notifications.publish(row["triage_id"], cfg["review_channel"], sender)))
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
