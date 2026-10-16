import unittest

from p_ops_notion import RemoteError
import datetime as dt
import tempfile

from p_ops_test_lab import TestLabLedger, active_task_queue, bundle_from_notion_url, notion_page_id


def page(item_id, title, status, due="", next_date=""):
    def text(value, kind="rich_text"):
        return {"type": kind, kind: [{"plain_text": value}]} if kind in ("title", "rich_text") else {
            "type": kind, kind: {"name": value}}
    properties = {
        "Item": text(title, "title"), "Status": text(status, "status"),
        "Next Step": text("Move this forward"), "Accountable Owner": {"type": "people", "people": []},
        "Work Mode": text("Execute", "select"),
    }
    if due:
        properties["Due Date"] = {"type": "date", "date": {"start": due}}
    if next_date:
        properties["Next Step Date"] = {"type": "date", "date": {"start": next_date}}
    return {"id": item_id, "url": "https://notion.so/" + item_id, "properties": properties}


class InaccessibleNotionClient:
    def page(self, _):
        raise RemoteError("Notion HTTP 404")


class TestLabUrlTests(unittest.TestCase):
    def test_test_run_keeps_context_while_moving_through_stages(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = TestLabLedger(directory + "/test.sqlite3")
            bundle = {"source": {"id": "task-1", "text": "Original task context"}}
            run_id = ledger.start("notion", "Task one", bundle)
            self.assertEqual("evaluating", ledger.rows()[0]["state"])
            ledger.finish(run_id, {"decision": {"disposition": "merge_task"}})
            row = ledger.rows()[0]
            self.assertEqual("completed", row["state"])
            self.assertIn("Original task context", row["input_json"])

    def test_active_task_queue_prioritizes_unreviewed_overdue_work(self):
        pages = [
            page("reviewed", "Reviewed task", "In Progress", due="2026-10-10"),
            page("future", "Future task", "Next", due="2026-10-20"),
            page("overdue", "Overdue task", "Inbox", next_date="2026-10-12"),
        ]
        queue = active_task_queue(pages, {"reviewed"}, dt.date(2026, 10, 16))
        self.assertEqual(["overdue", "future", "reviewed"], [item["id"] for item in queue])
        self.assertTrue(queue[0]["overdue"])
        self.assertTrue(queue[-1]["evaluated"])

    def test_notion_url_uses_page_id_from_path_not_view_query(self):
        value = ("https://app.notion.com/p/runpod/Record-informal-demo-video-"
                 "3faff732fc3481f6ba47fcc306fabc9d"
                 "?v=cb116cb51aa54f1589a127097f632da5&source=copy_link")
        self.assertEqual("3faff732-fc34-81f6-ba47-fcc306fabc9d", notion_page_id(value))

    def test_plain_page_id_is_supported(self):
        self.assertEqual("3faff732-fc34-81f6-ba47-fcc306fabc9d",
                         notion_page_id("3faff732fc3481f6ba47fcc306fabc9d"))

    def test_invalid_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid Notion task URL"):
            notion_page_id("https://app.notion.com/p/runpod/not-a-page")

    def test_inaccessible_page_explains_how_to_fix_the_connection(self):
        with self.assertRaisesRegex(ValueError, "Connect the Working Tasks database"):
            bundle_from_notion_url(
                "https://app.notion.com/p/runpod/SoftServe-f4ba99ddef5d42d99d9ec92638415199",
                InaccessibleNotionClient(),
            )


if __name__ == "__main__":
    unittest.main()
