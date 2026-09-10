#!/usr/bin/env python3
"""Publish scheduled, prewritten posts from posts.yml to Bluesky."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from atproto import Client, models


ROOT = Path(__file__).resolve().parent
POSTS_FILE = ROOT / "posts.yml"
STATE_FILE = ROOT / "state.json"
MAX_POST_BYTES = 3000  # Defensive limit; Bluesky's practical limit is 300 graphemes.
MAX_IMAGE_BYTES = 1_000_000
URL_RE = re.compile(r"https?://[^\s<>\"]+")
TRAILING_PUNCTUATION = ".,!?;:)]}"


class BotError(RuntimeError):
    """A user-correctable queue or configuration error."""


def load_yaml() -> dict[str, Any]:
    try:
        data = yaml.safe_load(POSTS_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise BotError(f"Missing queue file: {POSTS_FILE}") from exc
    except yaml.YAMLError as exc:
        raise BotError(f"Invalid YAML in {POSTS_FILE.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("posts", []), list):
        raise BotError("posts.yml must contain a 'posts' list.")
    return data


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {"posted": {}}
    try:
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BotError(f"Invalid JSON in {STATE_FILE.name}: {exc}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("posted", {}), dict):
        raise BotError("state.json must contain a 'posted' object.")
    state.setdefault("posted", {})
    return state


def save_state(state: dict[str, Any]) -> None:
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(STATE_FILE)


def parse_scheduled_at(value: Any, default_zone: ZoneInfo) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        normalized = value.strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(normalized)
        except ValueError as exc:
            raise BotError(f"Invalid scheduled_at value: {value!r}") from exc
    else:
        raise BotError("Every post needs a string scheduled_at value.")
    if result.tzinfo is None:
        result = result.replace(tzinfo=default_zone)
    return result


def clickable_link_facets(text: str) -> list[Any]:
    """Create Bluesky rich-text facets using UTF-8 byte offsets."""
    facets: list[Any] = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(TRAILING_PUNCTUATION)
        parsed = urlparse(url)
        if not parsed.netloc:
            continue
        start_char = match.start()
        end_char = start_char + len(url)
        start_byte = len(text[:start_char].encode("utf-8"))
        end_byte = len(text[:end_char].encode("utf-8"))
        facets.append(
            models.AppBskyRichtextFacet.Main(
                index=models.AppBskyRichtextFacet.ByteSlice(
                    byte_start=start_byte,
                    byte_end=end_byte,
                ),
                features=[models.AppBskyRichtextFacet.Link(uri=url)],
            )
        )
    return facets


def validate_post(post: Any, seen_ids: set[str], default_zone: ZoneInfo) -> dict[str, Any]:
    if not isinstance(post, dict):
        raise BotError("Every item in 'posts' must be an object.")
    post_id = str(post.get("id", "")).strip()
    if not post_id or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", post_id):
        raise BotError("Each post id must use lowercase letters, numbers, hyphens, or underscores.")
    if post_id in seen_ids:
        raise BotError(f"Duplicate post id: {post_id}")
    seen_ids.add(post_id)

    text = post.get("text")
    if not isinstance(text, str) or not text.strip():
        raise BotError(f"Post {post_id!r} has no text.")
    text = text.strip()
    if len(text.encode("utf-8")) > MAX_POST_BYTES:
        raise BotError(f"Post {post_id!r} is unusually long; shorten it before posting.")

    scheduled = parse_scheduled_at(post.get("scheduled_at"), default_zone)
    image = post.get("image")
    image_alt = post.get("image_alt")
    if image:
        image_path = (ROOT / str(image)).resolve()
        if ROOT not in image_path.parents:
            raise BotError(f"Post {post_id!r} image must be inside the repository.")
        if not image_path.is_file():
            raise BotError(f"Post {post_id!r} image does not exist: {image}")
        if image_path.stat().st_size > MAX_IMAGE_BYTES:
            raise BotError(f"Post {post_id!r} image exceeds Bluesky's 1 MB upload limit.")
        if not isinstance(image_alt, str) or not image_alt.strip():
            raise BotError(f"Post {post_id!r} needs image_alt text.")
    elif image_alt:
        raise BotError(f"Post {post_id!r} has image_alt but no image.")

    return {**post, "id": post_id, "text": text, "scheduled": scheduled}


def make_image_embed(client: Client, post: dict[str, Any]) -> Any | None:
    if not post.get("image"):
        return None
    image_path = ROOT / str(post["image"])
    mime_type = mimetypes.guess_type(image_path.name)[0] or "application/octet-stream"
    if not mime_type.startswith("image/"):
        raise BotError(f"Unsupported image type for {image_path.name}")
    uploaded = client.upload_blob(image_path.read_bytes())
    return models.AppBskyEmbedImages.Main(
        images=[
            models.AppBskyEmbedImages.Image(
                alt=str(post["image_alt"]).strip(),
                image=uploaded.blob,
            )
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Validate and show due posts without publishing.")
    parser.add_argument("--post-id", help="Publish one scheduled, unposted ID immediately (for testing).")
    args = parser.parse_args()

    queue = load_yaml()
    zone_name = str(queue.get("settings", {}).get("timezone", "America/New_York"))
    try:
        default_zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise BotError(f"Unknown timezone: {zone_name}") from exc

    state = load_state()
    seen_ids: set[str] = set()
    posts = [validate_post(post, seen_ids, default_zone) for post in queue.get("posts", [])]
    now = datetime.now(timezone.utc)

    if args.post_id:
        due = [post for post in posts if post["id"] == args.post_id]
        if not due:
            raise BotError(f"No post found with id {args.post_id!r}.")
    else:
        due = [post for post in posts if post["scheduled"].astimezone(timezone.utc) <= now]
    due = [post for post in due if post["id"] not in state["posted"]]
    due.sort(key=lambda post: post["scheduled"])

    if not due:
        print("No unposted entries are due.")
        return 0
    if args.dry_run:
        for post in due:
            print(f"DUE {post['id']}: {post['scheduled'].isoformat()} | {post['text']}")
        return 0

    handle = os.environ.get("BLUESKY_HANDLE", "").strip()
    app_password = os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    if not handle or not app_password:
        raise BotError("Set BLUESKY_HANDLE and BLUESKY_APP_PASSWORD before publishing.")

    client = Client()
    client.login(handle, app_password)
    for post in due:
        embed = make_image_embed(client, post)
        response = client.send_post(
            text=post["text"],
            facets=clickable_link_facets(post["text"]) or None,
            embed=embed,
        )
        state["posted"][post["id"]] = {
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "uri": response.uri,
            "cid": response.cid,
        }
        save_state(state)
        print(f"Posted {post['id']}: {response.uri}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BotError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)

