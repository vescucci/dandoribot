import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import bot


class BotTests(unittest.TestCase):
    def test_naive_schedule_uses_configured_timezone(self):
        result = bot.parse_scheduled_at("2026-09-15 10:17", ZoneInfo("America/New_York"))
        self.assertEqual(result.utcoffset().total_seconds(), -4 * 3600)

    def test_aware_schedule_keeps_offset(self):
        result = bot.parse_scheduled_at("2026-12-15T10:17:00-05:00", ZoneInfo("UTC"))
        self.assertEqual(result.utcoffset().total_seconds(), -5 * 3600)

    def test_link_facet_uses_utf8_byte_offsets(self):
        text = "準備 first: https://example.com/test."
        facet = bot.clickable_link_facets(text)[0]
        expected_start = len("準備 first: ".encode("utf-8"))
        expected_end = expected_start + len("https://example.com/test".encode("utf-8"))
        self.assertEqual(facet.index.byte_start, expected_start)
        self.assertEqual(facet.index.byte_end, expected_end)


if __name__ == "__main__":
    unittest.main()

