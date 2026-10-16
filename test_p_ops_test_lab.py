import unittest

from p_ops_notion import RemoteError
from p_ops_test_lab import bundle_from_notion_url, notion_page_id


class InaccessibleNotionClient:
    def page(self, _):
        raise RemoteError("Notion HTTP 404")


class TestLabUrlTests(unittest.TestCase):
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
