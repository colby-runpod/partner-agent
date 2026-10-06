import copy
import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import build_brief, normalize, notion_records, text_value

TODAY = dt.date(2026, 10, 6)
FIXTURE = Path(__file__).with_name("demo.json")


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.records = normalize(json.loads(FIXTURE.read_text())["records"])

    def test_initial_snapshot_is_not_claimed_as_progress(self):
        result = build_brief("Pilot", self.records, None, TODAY)
        self.assertIn("No prior baseline", result)
        self.assertNotIn("Changes since", result)

    def test_unchanged_and_changed_status(self):
        self.assertIn("No changes", build_brief("Pilot", self.records, self.records, TODAY))
        changed = copy.deepcopy(self.records)
        changed["demo-objective"]["status"] = "Done"
        self.assertIn("status [In Progress] → [Done]", build_brief("Pilot", changed, self.records, TODAY))

    def test_removed_record_is_not_completion(self):
        changed = {"demo-task": self.records["demo-task"]}
        self.assertIn("not assumed completed", build_brief("Pilot", changed, self.records, TODAY))

    def test_completed_records_do_not_trigger_overdue_alerts(self):
        result = build_brief("Pilot", self.records, None, TODAY)
        self.assertNotIn("Overdue:", result)
        self.records["demo-task"]["status"] = "In Progress"
        self.assertIn("Overdue: 2026-10-05", build_brief("Pilot", self.records, None, TODAY))

    def test_source_cannot_create_slack_mentions(self):
        self.records["demo-objective"]["title"] = "<!channel> <@U123>"
        result = build_brief("Pilot", self.records, None, TODAY)
        self.assertNotIn("<!channel>", result)
        self.assertNotIn("<@U123>", result)

    def test_empty_and_duplicate_sources_rejected(self):
        for records in ([], [{"id": "a", "title": "A"}, {"id": "a", "title": "B"}]):
            with self.assertRaises(ValueError):
                normalize(records)

    def test_notion_supported_properties(self):
        self.assertEqual(text_value({"type": "status", "status": {"name": "Done"}}), "Done")
        self.assertEqual(text_value({"type": "date", "date": None}), "")
        with self.assertRaises(ValueError):
            text_value({"type": "relation", "relation": []})

    def test_notion_missing_token_stops_before_network(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(ValueError, "NOTION_TOKEN"):
                notion_records({"pages": []})

    def test_preview_does_not_advance_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            cmd = [sys.executable, str(Path(__file__).with_name("agent.py")), "--fixture", str(FIXTURE),
                   "--state", tmp + "/state.db", "--output", tmp + "/brief.txt", "--date", "2026-10-06"]
            first = subprocess.check_output(cmd, text=True)
            second = subprocess.check_output(cmd, text=True)
            self.assertIn("No prior baseline", first)
            self.assertIn("No prior baseline", second)
            subprocess.check_output(cmd + ["--accept-baseline"], text=True)
            self.assertIn("No changes", subprocess.check_output(cmd, text=True))


if __name__ == "__main__":
    unittest.main()
