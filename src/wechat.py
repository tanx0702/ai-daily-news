"""Compatibility wrapper for WeChat draft publishing.

New code should import from `src.wechat_draft`. This module remains so older
scripts that import `src.wechat.publish_daily_article` keep working.
"""

from src.wechat_draft import publish_daily_article

__all__ = ["publish_daily_article"]
