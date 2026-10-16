import base64
import datetime as dt
import io
import json
import tempfile
import unittest
import urllib.parse

from p_ops_runtime import Dashboard, RuntimeApp, _csrf, components, run_once
from p_ops_triage import TriageDecision


CFG = {"runtime_enabled": True, "enabled": True, "team_id": "T1", "app_id": "A1", "secret": "secret",
       "channel_ids": {"C1"}, "user_ids": {"U1"}, "review_user": "colby",
       "review_password": "password", "csrf_secret": "csrf", "daily_review": False,
       "review_posting": False, "review_channel": "C-REVIEW"}


def request(app, path, method="GET", body=b"", auth=True):
    status, headers = [], []
    env = {"PATH_INFO": path, "REQUEST_METHOD": method, "CONTENT_LENGTH": str(len(body)),
           "wsgi.input": io.BytesIO(body)}
    if auth:
        env["HTTP_AUTHORIZATION"] = "Basic " + base64.b64encode(b"colby:password").decode()
    def start(value, values): status.append(value); headers.extend(values)
    output = b"".join(app(env, start))
    return status[0], dict(headers), output


class FakeSlackApp:
    def __call__(self, env, start):
        start("200 OK", [("Content-Length", "2")]); return [b"{}"]


class FakeCaptureClient:
    def message(self, channel, ts):
        return {"ts": ts, "user": "U2", "text": "Prepare Redis follow-up"}
    def permalink(self, channel, ts):
        return "https://slack.example/source"


class FakeNotion:
    def __init__(self): self.reads = 0
    def active_pages(self):
        self.reads += 1
        return []


class FakeTriage:
    def __init__(self): self.calls = 0
    def analyze(self, bundle):
        self.calls += 1
        return TriageDecision.from_dict({
            "disposition": "create_task", "title": "Prepare Redis follow-up",
            "summary": "The capture contains a concrete follow-up.", "strategic_value": "medium",
            "objective_id": None, "workstream_id": None, "partner_ids": [], "priority": "medium",
            "severity": "low", "work_mode": "Prepare", "canonical_task_id": None,
            "parent_task_id": None, "meeting_id": None, "owner": "Colby",
            "owning_function": "Partnerships", "supporting_functions": [],
            "next_step": "Review the source and confirm the owner.", "next_step_date": None,
            "due_date": None, "definition_of_done": "The owner and next checkpoint are recorded.",
            "dependency": "", "waiting_on": "", "requires_linear": False,
            "requires_message": False, "preparation_required": True,
            "proposed_actions": ["create_notion_task"],
            "evidence_refs": [bundle["source"]["id"]], "assumptions": [],
            "missing_evidence": ["Cross-system evidence has not been retrieved."],
            "review_reasons": [], "confidence": 0.83,
            "rationale": "The action is specific, but the shadow bundle is intentionally incomplete.",
        })


class FakeReviewSender:
    def __init__(self): self.rows = []
    def __call__(self, row):
        self.rows.append(row)
        return "171.001"


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = dict(CFG, db=self.tmp.name + "/p_ops.sqlite3")
        self.stores = components(self.cfg)
        self.dashboard = Dashboard(self.cfg, *self.stores)
        self.app = RuntimeApp(self.cfg, self.dashboard, FakeSlackApp())

    def tearDown(self):
        self.tmp.cleanup()

    def test_dashboard_requires_auth_and_escapes_source(self):
        status, headers, _ = request(self.app, "/review", auth=False)
        self.assertEqual("401 Unauthorized", status)
        self.assertIn("WWW-Authenticate", headers)
        message_id = self.stores[2].create("slack", "C1", "<script>alert(1)</script>")
        status, headers, body = request(self.app, "/review")
        self.assertEqual("200 OK", status)
        self.assertNotIn(b"<script>alert(1)</script>", body)
        self.assertIn(b"&lt;script&gt;", body)
        self.assertEqual("no-store", headers["Cache-Control"])

    def test_dashboard_uses_runpod_operator_console_and_serves_brand_assets(self):
        status, headers, body = request(self.app, "/review")
        self.assertEqual("200 OK", status)
        self.assertIn(b"Runpod", body)
        self.assertIn(b"Partner ops dashboard", body)
        self.assertIn(b"Automation safety controls", body)
        self.assertIn(b"Task updates", body)
        self.assertIn(b"Search or jump to", body)
        self.assertIn(b"Notifications", body)
        self.assertIn("img-src 'self'", headers["Content-Security-Policy"])
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        status, headers, asset = request(self.app, "/assets/runpod-logo-purple.png", auth=False)
        self.assertEqual("200 OK", status)
        self.assertEqual("image/png", headers["Content-Type"])
        self.assertTrue(asset.startswith(b"\x89PNG"))
        status, headers, script = request(self.app, "/assets/p_ops_console.js", auth=False)
        self.assertEqual("200 OK", status)
        self.assertIn("application/javascript", headers["Content-Type"])
        self.assertIn(b"openPalette", script)

    def test_activity_and_settings_are_authenticated_read_only_views(self):
        self.assertEqual("401 Unauthorized", request(self.app, "/activity", auth=False)[0])
        status, _, activity = request(self.app, "/activity")
        self.assertEqual("200 OK", status)
        self.assertIn(b"<h1>Activity</h1>", activity)
        status, _, settings = request(self.app, "/settings")
        self.assertEqual("200 OK", status)
        self.assertIn(b"<h1>Settings</h1>", settings)
        self.assertIn(b"Change these in Railway", settings)

    def test_csrf_protected_review_action(self):
        message_id = self.stores[2].create("slack", "C1", "Ready to send")
        bad = urllib.parse.urlencode({"kind": "message", "action": "approve", "id": message_id,
                                      "csrf": "bad"}).encode()
        self.assertEqual("403 Forbidden", request(self.app, "/review/action", "POST", bad)[0])
        good = urllib.parse.urlencode({"kind": "message", "action": "approve", "id": message_id,
            "csrf": _csrf(self.cfg, "message", "approve", message_id)}).encode()
        self.assertEqual("303 See Other", request(self.app, "/review/action", "POST", good)[0])
        self.assertEqual("approved", self.stores[2].get(message_id)["state"])

    def test_worker_resolves_capture_and_creates_one_proposal(self):
        event = {"event_id": "Ev1", "team_id": "T1", "channel": "C1", "ts": "123.456",
                 "reactor": "U1", "reaction": "handshake::skin-tone-2", "event_time": ""}
        self.stores[0].enqueue(event)
        results = run_once(self.cfg, self.stores, {"slack_capture": FakeCaptureClient()})
        self.assertEqual("captured", results[0][2])
        self.assertEqual(1, len(self.stores[1].rows()))
        run_once(self.cfg, self.stores, {"slack_capture": FakeCaptureClient()})
        self.assertEqual(1, len(self.stores[1].rows()))

    def test_health_reports_disabled_gates_without_exposing_secrets(self):
        status, _, body = request(self.app, "/healthz", auth=False)
        self.assertEqual("200 OK", status)
        data = json.loads(body)
        self.assertTrue(data["p_ops_enabled"])
        self.assertFalse(data["triage_shadow_enabled"])
        self.assertNotIn("password", data)

    def test_daily_preview_runs_once_per_mountain_day(self):
        cfg = dict(self.cfg, daily_review=True)
        notion = FakeNotion()
        day = dt.date(2026, 10, 15)
        first = run_once(cfg, self.stores, {"notion": notion}, today=day)
        second = run_once(cfg, self.stores, {"notion": notion}, today=day)
        self.assertTrue(any(row[0] == "daily_preview" for row in first))
        self.assertFalse(any(row[0] == "daily_preview" for row in second))
        self.assertEqual(1, notion.reads)

    def test_shadow_triage_records_read_only_decision_once(self):
        cfg = dict(self.cfg, triage_shadow=True)
        event = {"event_id": "Ev1", "team_id": "T1", "channel": "C1", "ts": "123.456",
                 "reactor": "U1", "reaction": "handshake::skin-tone-2", "event_time": ""}
        self.stores[0].enqueue(event)
        notion, triage = FakeNotion(), FakeTriage()
        clients = {"slack_capture": FakeCaptureClient(), "notion": notion, "triage": triage}
        first = run_once(cfg, self.stores, clients, today=dt.date(2026, 10, 15))
        second = run_once(cfg, self.stores, clients, today=dt.date(2026, 10, 15))
        self.assertTrue(any(row[0] == "triage_shadow" and row[2] == "shadow" for row in first))
        self.assertFalse(any(row[0] == "triage_shadow" for row in second))
        self.assertEqual(1, triage.calls)
        saved = self.stores[5].rows()
        self.assertEqual("shadow", saved[0]["state"])
        guarded = json.loads(saved[0]["decision_json"])
        self.assertFalse(guarded["executable"])
        self.assertIn("pilot_shadow_mode", guarded["policy_reasons"])

    def test_shadow_triage_posts_one_scoped_review_notification(self):
        cfg = dict(self.cfg, triage_shadow=True, review_posting=True)
        event = {"event_id": "Ev2", "team_id": "T1", "channel": "C1", "ts": "456.789",
                 "reactor": "U1", "reaction": "handshake::skin-tone-2", "event_time": ""}
        self.stores[0].enqueue(event)
        sender = FakeReviewSender()
        clients = {"slack_capture": FakeCaptureClient(), "notion": FakeNotion(),
                   "triage": FakeTriage(), "review_sender": sender}
        first = run_once(cfg, self.stores, clients, today=dt.date(2026, 10, 15))
        second = run_once(cfg, self.stores, clients, today=dt.date(2026, 10, 15))
        self.assertTrue(any(row[0] == "review_notification" and row[2] == "sent" for row in first))
        self.assertFalse(any(row[0] == "review_notification" for row in second))
        self.assertEqual(1, len(sender.rows))
        self.assertEqual("C-REVIEW", sender.rows[0]["channel"])
        self.assertIn("Review needed:", sender.rows[0]["content"])


if __name__ == "__main__":
    unittest.main()
