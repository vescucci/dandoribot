import unittest
from unittest import mock

import bot


class FakeResponse:
    def __init__(self, number):
        self.uri = f"at://did:plc:test/app.bsky.feed.post/{number}"
        self.cid = f"bafytest{number}"


class FakeClient:
    def __init__(self, fail_on=None):
        self.calls = 0
        self.fail_on = fail_on

    def send_post(self, **kwargs):
        self.calls += 1
        if self.calls == self.fail_on:
            raise RuntimeError("temporary failure")
        return FakeResponse(self.calls)


class BotTests(unittest.TestCase):
    def test_link_facet_uses_utf8_byte_offsets(self):
        text = "準備 first: https://example.com/test."
        facet = bot.clickable_link_facets(text)[0]
        start = len("準備 first: ".encode())
        self.assertEqual(facet.index.byte_start, start)
        self.assertEqual(facet.index.byte_end, start + len("https://example.com/test".encode()))

    def test_rejects_more_than_300_graphemes(self):
        with self.assertRaisesRegex(bot.BotError, "exceeds Bluesky"):
            bot.validate_text("x" * 301, "Example")

    def test_accepts_a_four_image_post(self):
        raw = [{
            "id": "gallery", "enabled": False, "text": "Four pictures",
            "images": [{"path": f"images/{n}.jpg", "alt": f"Picture {n}"} for n in range(4)]
        }]
        self.assertEqual(len(bot.normalize_posts(raw)["gallery"]["parts"][0]["images"]), 4)

    def test_rejects_images_and_card_on_same_part(self):
        raw = [{
            "id": "bad-combination", "enabled": False, "text": "Example",
            "images": [{"path": "image.jpg", "alt": "Example"}],
            "link_card": {"url": "https://example.com"}
        }]
        with self.assertRaisesRegex(bot.BotError, "cannot combine"):
            bot.normalize_posts(raw)

    def test_edited_content_releases_quarantine(self):
        posts = bot.normalize_posts([{"id": "fixed-post", "text": "Repaired"}])
        state = bot.default_state()
        state["quarantined"]["fixed-post"] = {"fingerprint": "old"}
        state["used_ids"] = ["fixed-post"]
        released = bot.release_edited_quarantines(state, posts)
        self.assertEqual(released, ["fixed-post"])
        self.assertNotIn("fixed-post", state["quarantined"])
        self.assertNotIn("fixed-post", state["used_ids"])

    def test_new_post_is_eligible_mid_cycle(self):
        posts = bot.normalize_posts([
            {"id": "already-used", "text": "Old"},
            {"id": "new-post", "text": "New"},
        ])
        state = bot.default_state(); state["used_ids"] = ["already-used"]
        bot.choose_post(state, posts, "2026-09-10", 7)
        self.assertEqual(state["reservation"]["post_id"], "new-post")

    def test_new_cycle_avoids_immediate_repeat(self):
        posts = bot.normalize_posts([
            {"id": "first", "text": "One"},
            {"id": "second", "text": "Two"},
        ])
        state = bot.default_state(); state["used_ids"] = ["first", "second"]; state["last_post_id"] = "second"
        bot.choose_post(state, posts, "2026-09-10", 7)
        self.assertEqual(state["reservation"]["post_id"], "first")
        self.assertEqual(state["cycle"], 2)

    def test_thread_retry_resumes_after_completed_parts(self):
        post = bot.normalize_posts([{
            "id": "thread", "thread": [{"text": "Part one"}, {"text": "Part two"}]
        }])["thread"]
        state = bot.default_state()
        reservation = {"post_id": "thread"}
        first_client = FakeClient(fail_on=2)
        with mock.patch.object(bot, "save_state"):
            with self.assertRaisesRegex(RuntimeError, "temporary"):
                bot.publish_entry(first_client, post, reservation, state)
        self.assertEqual(len(reservation["thread_progress"]["uris"]), 1)

        second_client = FakeClient()
        with mock.patch.object(bot, "save_state"):
            uris = bot.publish_entry(second_client, post, reservation, state)
        self.assertEqual(second_client.calls, 1)
        self.assertEqual(len(uris), 2)


if __name__ == "__main__":
    unittest.main()
