import unittest

from src import wechat, wechat_draft


class WeChatBoundaryTests(unittest.TestCase):
    def test_legacy_wechat_module_reexports_draft_publisher(self):
        self.assertIs(wechat.publish_daily_article, wechat_draft.publish_daily_article)


if __name__ == "__main__":
    unittest.main()
