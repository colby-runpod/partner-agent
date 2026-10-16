"""Slack reaction capture for p-OPS.

Only an allowlisted user's :handshake::skin-tone-2: reaction on an allowlisted
message is accepted. Captured Slack text is source evidence, never an
instruction. This module queues evidence only; it does not create a task or
post a Slack acknowledgement.
"""
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import urllib.parse

from intake import api, verify


REACTION = "handshake::skin-tone-2"


def _ids(value):
    return {item.strip() for item in (value or "").split(",") if item.strip()}


def load_capture_config(env=None):
    env = env or os.environ
    required = ("SLACK_SIGNING_SECRET", "SLACK_TEAM_ID", "SLACK_APP_ID")
    missing = [key for key in required if not env.get(key)]
    if missing:
        raise ValueError("Missing environment setting: " + ", ".join(missing))
    return {
        "enabled": env.get("P_OPS_CAPTURE_ENABLED") == "true",
        "secret": env["SLACK_SIGNING_SECRET"], "team_id": env["SLACK_TEAM_ID"],
        "app_id": env["SLACK_APP_ID"],
        "channel_ids": _ids(env.get("P_OPS_CAPTURE_CHANNEL_IDS")),
        "user_ids": _ids(env.get("P_OPS_CAPTURE_USER_IDS")),
    }


def accept_reaction(body, cfg):
    if not cfg.get("enabled"):
        return None
    if body.get("team_id") != cfg["team_id"] or body.get("api_app_id") != cfg["app_id"]:
        return None
    event = body.get("event") or {}
    item = event.get("item") or {}
    if event.get("type") != "reaction_added" or event.get("reaction") != REACTION:
        return None
    if event.get("user") not in cfg["user_ids"] or item.get("channel") not in cfg["channel_ids"]:
        return None
    if item.get("type") != "message" or not re.fullmatch(r"\d+\.\d+", item.get("ts", "")):
        return None
    if not body.get("event_id"):
        return None
    return {"event_id": body["event_id"], "team_id": body["team_id"],
            "channel": item["channel"], "ts": item["ts"], "reactor": event["user"],
            "reaction": REACTION, "event_time": event.get("event_ts", "")}


class CaptureStore:
    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS p_ops_captures (
                event_id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL, payload TEXT NOT NULL,
                state TEXT NOT NULL, created REAL NOT NULL, captured_at REAL,
                source_url TEXT, source_text TEXT, source_user TEXT, thread_ts TEXT,
                error TEXT)""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        return db

    def enqueue(self, event):
        source_key = ":".join((event["team_id"], event["channel"], event["ts"], event["reactor"], REACTION))
        with self.connect() as db:
            return db.execute("""INSERT OR IGNORE INTO p_ops_captures
                (event_id,source_key,payload,state,created) VALUES (?,?,?,'pending',?)""",
                (event["event_id"], source_key, json.dumps(event), time.time())).rowcount == 1

    def get(self, event_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM p_ops_captures WHERE event_id=?", (event_id,)).fetchone()
        return dict(row) if row else None

    def rows(self, states=None):
        with self.connect() as db:
            if states:
                marks = ",".join("?" for _ in states)
                rows = db.execute("SELECT * FROM p_ops_captures WHERE state IN (%s) ORDER BY created" % marks,
                                  tuple(states)).fetchall()
            else:
                rows = db.execute("SELECT * FROM p_ops_captures ORDER BY created").fetchall()
        return [dict(row) for row in rows]

    def update(self, event_id, **fields):
        allowed = {"state", "captured_at", "source_url", "source_text", "source_user", "thread_ts", "error"}
        if not fields.keys() <= allowed:
            raise ValueError("Unsupported capture field.")
        with self.connect() as db:
            db.execute("UPDATE p_ops_captures SET " + ",".join(k + "=?" for k in fields) + " WHERE event_id=?",
                       list(fields.values()) + [event_id])


class SlackCaptureClient:
    def __init__(self, token=None):
        self.token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self.token:
            raise ValueError("Set SLACK_BOT_TOKEN before resolving captures.")

    def message(self, channel, ts):
        params = urllib.parse.urlencode({"channel": channel, "timestamp": ts, "full": "true"})
        result = api("https://slack.com/api/reactions.get?" + params, self.token)
        message = result.get("message")
        if not isinstance(message, dict) or message.get("ts") != ts:
            raise ValueError("Slack did not return the reacted message.")
        return message

    def permalink(self, channel, ts):
        params = urllib.parse.urlencode({"channel": channel, "message_ts": ts})
        return api("https://slack.com/api/chat.getPermalink?" + params, self.token)["permalink"]


def resolve_capture(store, event_id, client):
    row = store.get(event_id)
    if not row or row["state"] != "pending":
        raise ValueError("Capture is not pending.")
    event = json.loads(row["payload"])
    store.update(event_id, state="resolving")
    try:
        message = client.message(event["channel"], event["ts"])
        url = client.permalink(event["channel"], event["ts"])
    except Exception:
        store.update(event_id, state="review", error="source_resolution_failed_no_retry")
        return "review"
    text = message.get("text") or ""
    if not text.strip():
        store.update(event_id, state="review", error="captured_message_has_no_text")
        return "review"
    store.update(event_id, state="captured", captured_at=time.time(), source_url=url,
                 source_text=text, source_user=message.get("user") or message.get("bot_id") or "",
                 thread_ts=message.get("thread_ts") or message["ts"], error=None)
    return "captured"


def receive(raw_body, timestamp, signature, cfg, store):
    """Validate and enqueue one Slack event body without doing remote work."""
    if not verify(cfg["secret"], timestamp, signature, raw_body):
        return False
    try:
        body = json.loads(raw_body)
    except (ValueError, TypeError):
        return False
    event = accept_reaction(body, cfg)
    return bool(event and store.enqueue(event))
