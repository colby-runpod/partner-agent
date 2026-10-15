import copy
import datetime as dt
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from p_ops_notion import Ledger, apply_action, apply_ready, changed_patch, encode_patch, normalize_page, preview, snapshot


TODAY = dt.date(2026, 10, 14)


def rich(value):
    return {"type": "rich_text", "rich_text": [{"plain_text": value, "text": {"content": value}}]}


def select(kind, value):
    return {"type": kind, kind: {"name": value} if value else None}


def date(value):
    return {"type": "date", "date": {"start": value} if value else None}


def page(**changes):
    properties = {
        "Item": {"type": "title", "title": [{"plain_text": "Example task", "text": {"content": "Example task"}}]},
        "Status": select("status", "In Progress"), "Accountable Owner": {"type": "people", "people": [{"id": "u1", "name": "Colby"}]},
        "Next Step": rich("Review evidence"), "Next Step Date": date("2026-10-14"), "Due Date": date("2026-10-20"),
        "Definition of Done": rich("Outcome confirmed"), "Completion Evidence": rich(""),
        "Close Reason": select("select", ""), "Closed At": date(None), "Last Meaningful Activity": date("2026-10-14"),
        "Last Automation Review": date(None), "Waiting Since": date(None), "Who's Waiting": rich(""),
        "Dependency Context": rich(""), "Blocked": {"type": "checkbox", "checkbox": False}, "Risk Notes": rich(""),
        "Decision Required": rich(""), "Work Mode": select("select", "Execute"), "Owning Function": select("select", "Partnerships"),
        "Supporting Functions": {"type": "multi_select", "multi_select": []}, "Objective": {"type": "relation", "relation": []},
        "Workstreams": {"type": "relation", "relation": []}, "Associated Partner(s)": {"type": "relation", "relation": []},
        "Child Actions": {"type": "relation", "relation": []},
        "Review Required": {"type": "checkbox", "checkbox": False}, "Review Reason": rich(""),
        "Linear Issue": {"type": "url", "url": None},
    }
    for name, value in changes.items():
        properties[name] = value
    return {"id": "11111111-1111-1111-1111-111111111111", "url": "https://notion.so/example", "properties": properties}


def apply_encoded(source, patch):
    result = copy.deepcopy(source)
    for name, encoded in encode_patch(patch).items():
        kind, value = next(iter(encoded.items()))
        result["properties"][name] = {"type": kind, kind: value}
    return result


class FakeClient:
    def __init__(self, source, fail=False, apply_before_fail=False):
        self.current = copy.deepcopy(source)
        self.fail = fail
        self.apply_before_fail = apply_before_fail
        self.updates = 0

    def active_pages(self):
        return [copy.deepcopy(self.current)]

    def page(self, _):
        return copy.deepcopy(self.current)

    def update(self, _, patch):
        self.updates += 1
        if not self.fail or self.apply_before_fail:
            self.current = apply_encoded(self.current, patch)
        if self.fail:
            raise TimeoutError()
        return copy.deepcopy(self.current)


class NotionAdapterTests(unittest.TestCase):
    def test_normalize_is_conservative(self):
        source = page(**{
            "Status": select("status", "Snoozed"), "Next Step Date": date("2026-11-01"),
            "Owning Function": select("select", "Product"), "Dependency Context": rich("Old dependency")})
        task = normalize_page(source, TODAY)
        self.assertEqual(task.snooze_until, dt.date(2026, 11, 1))
        self.assertEqual(task.future_trigger, "Next Step Date: 2026-11-01")
        self.assertEqual(task.active_dependency, "")
        self.assertTrue(task.requires_engineering)
        self.assertEqual(task.linear_team, "Product")

    def test_waiting_dependency_is_used(self):
        source = page(**{"Status": select("status", "Waiting on Other"), "Dependency Context": rich("Legal approval")})
        self.assertEqual(normalize_page(source, TODAY).active_dependency, "Legal approval")

    def test_child_actions_prevent_unverified_parent_close(self):
        source = page(**{"Completion Evidence": rich("Parent artifact delivered"),
                         "Child Actions": {"type": "relation", "relation": [{"id": "child-1"}]}})
        task = normalize_page(source, TODAY)
        self.assertTrue(task.child_actions_unverified)

    def test_patch_encoder_rejects_unknown_fields(self):
        encoded = encode_patch({"Status": "Done", "Review Required": True, "Linear Issue": "https://linear.app/x"})
        self.assertEqual(encoded["Status"]["status"]["name"], "Done")
        with self.assertRaises(ValueError):
            encode_patch({"Due Date": "2026-10-20"})

    def test_changed_patch_removes_noops(self):
        source = page(**{"Last Automation Review": date("2026-10-14")})
        self.assertEqual(changed_patch(source, {"Last Automation Review": "2026-10-14"}), {})

    def test_preview_deduplicates_same_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory + "/ledger.db")
            client = FakeClient(page())
            first = preview(client, ledger, TODAY)
            second = preview(client, ledger, TODAY)
            self.assertEqual(first["actions_queued"], 1)
            self.assertEqual(second["actions_queued"], 1)
            self.assertEqual(len(ledger.rows()), 1)

    def test_apply_success_and_idempotent_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory + "/ledger.db")
            client = FakeClient(page(**{"Completion Evidence": rich("Verified") }))
            preview(client, ledger, TODAY)
            row = ledger.rows()[0]
            self.assertEqual(apply_action(client, ledger, row), "applied")
            self.assertEqual(client.updates, 1)
            self.assertEqual(ledger.rows()[0]["state"], "applied")

    def test_source_change_blocks_write(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory + "/ledger.db")
            client = FakeClient(page())
            preview(client, ledger, TODAY)
            row = ledger.rows()[0]
            client.current["properties"]["Next Step"] = rich("Changed after preview")
            self.assertEqual(apply_action(client, ledger, row), "conflict")
            self.assertEqual(client.updates, 0)

    def test_uncertain_write_reconciles_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory + "/ledger.db")
            client = FakeClient(page(), fail=True, apply_before_fail=True)
            preview(client, ledger, TODAY)
            self.assertEqual(apply_action(client, ledger, ledger.rows()[0]), "reconciled")
            self.assertEqual(client.updates, 1)

    def test_uncertain_write_not_applied_goes_to_review(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory + "/ledger.db")
            client = FakeClient(page(), fail=True, apply_before_fail=False)
            preview(client, ledger, TODAY)
            self.assertEqual(apply_action(client, ledger, ledger.rows()[0]), "review")
            self.assertEqual(client.updates, 1)
            self.assertEqual(ledger.rows()[0]["error"], "write_unconfirmed_no_retry")

    def test_write_gate_is_closed_by_default(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Ledger(directory + "/ledger.db")
            with patch.dict(os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, "P_OPS_NOTION_WRITE"):
                    apply_ready(FakeClient(page()), ledger, include_auto=True)


if __name__ == "__main__":
    unittest.main()
