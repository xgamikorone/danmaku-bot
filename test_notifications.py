import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from bot import NotificationConfig, ServerChanNotifier


class ServerChanNotifierTests(unittest.IsolatedAsyncioTestCase):
    def make_notifier(self, sendkey="SCTtest"):
        with patch.dict(os.environ, {"SERVERCHAN_SENDKEY": sendkey}):
            return ServerChanNotifier(
                NotificationConfig(True, 1800, True, 5),
                Path(tempfile.gettempdir()) / "danmaku-bot-test-serverchan-state.json",
            )

    def test_endpoints(self):
        self.assertEqual(
            self.make_notifier().endpoint(),
            "https://sctapi.ftqq.com/SCTtest.send",
        )
        self.assertEqual(
            self.make_notifier("sctp123tSecret").endpoint(),
            "https://123.push.ft07.com/send/sctp123tSecret.send",
        )

    async def test_deduplication_and_recovery(self):
        notifier = self.make_notifier()
        notifier.push = AsyncMock(return_value=True)
        await notifier.report_api_error(None, 100, 200, -101, "expired")
        await notifier.report_api_error(None, 100, 200, -101, "expired")
        self.assertEqual(notifier.push.await_count, 1)
        await notifier.report_recovery(None, 100)
        self.assertEqual(notifier.push.await_count, 2)

    async def test_daily_limit_blocks_push(self):
        notifier = self.make_notifier()
        notifier.quota_count = notifier.daily_limit
        self.assertFalse(await notifier.push(None, "title", "description"))

    def test_sendkey_is_required_when_enabled(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "SERVERCHAN_SENDKEY"):
                ServerChanNotifier(NotificationConfig(enabled=True))


if __name__ == "__main__":
    unittest.main()
