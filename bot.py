#!/usr/bin/env python3
"""Publish one randomly selected Dandorism post each day."""
from __future__ import annotations

import argparse, hashlib, html, ipaddress, json, mimetypes, os, random, re, socket, sys
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx, regex, yaml
from atproto import Client, models

ROOT = Path(__file__).resolve().parent
POSTS_FILE, STATE_FILE, RESULT_FILE = ROOT / "posts.yml", ROOT / "state.json", ROOT / ".bot-result.json"
EST = timezone(timedelta(hours=-5), name="EST")
MAX_GRAPHEMES, MAX_TEXT_BYTES, MAX_IMAGE_BYTES, MAX_IMAGES = 300, 3000, 1_000_000, 4
URL_RE = re.compile(r"https?://[^\s<>\"]+")
TRAILING_PUNCTUATION = ".,!?;:)]}"


class BotError(RuntimeError):
    pass


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(); self.metadata: dict[str, str] = {}; self.in_title = False; self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key and values.get("content"): self.metadata[key] = values["content"].strip()
        elif tag.lower() == "title": self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title": self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title: self.title_parts.append(data)


def write_result(status: str, **values: Any) -> None:
    RESULT_FILE.write_text(json.dumps({"status": status, **values}, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_queue() -> dict[str, Any]:
    try: data = yaml.safe_load(POSTS_FILE.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc: raise BotError(f"Missing {POSTS_FILE.name}.") from exc
    except yaml.YAMLError as exc: raise BotError(f"Invalid YAML in {POSTS_FILE.name}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("posts", []), list): raise BotError("posts.yml must contain a 'posts' list.")
    if not isinstance(data.get("settings", {}), dict): raise BotError("The 'settings' value must be an object.")
    return data


def default_state() -> dict[str, Any]:
    return {"cycle": 1, "used_ids": [], "last_post_id": None, "reservation": None, "quarantined": {}, "daily_results": {}, "history": []}


def load_state() -> dict[str, Any]:
    state = default_state()
    if STATE_FILE.exists():
        try: saved = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc: raise BotError(f"Invalid JSON in {STATE_FILE.name}: {exc}") from exc
        if not isinstance(saved, dict): raise BotError("state.json must contain an object.")
        state.update(saved)
    if not isinstance(state["quarantined"], dict) or not isinstance(state["used_ids"], list): raise BotError("state.json has an invalid structure.")
    return state


def save_state(state: dict[str, Any]) -> None:
    state["history"] = state.get("history", [])[-500:]
    state["daily_results"] = dict(sorted(state.get("daily_results", {}).items())[-90:])
    temporary = STATE_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"); temporary.replace(STATE_FILE)


def content_fingerprint(entry: dict[str, Any]) -> str:
    meaningful = {key: value for key, value in entry.items() if key not in {"id", "enabled"}}
    return hashlib.sha256(json.dumps(meaningful, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def validate_text(text: Any, label: str) -> str:
    if not isinstance(text, str) or not text.strip(): raise BotError(f"{label} has no text.")
    result = text.strip(); graphemes = len(regex.findall(r"\X", result))
    if graphemes > MAX_GRAPHEMES or len(result.encode()) > MAX_TEXT_BYTES:
        raise BotError(f"{label} exceeds Bluesky's limit ({graphemes}/{MAX_GRAPHEMES} graphemes). Make it an intentional thread.")
    return result


def normalize_images(value: Any, label: str, check_files: bool) -> list[dict[str, str]]:
    if value is None: return []
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_IMAGES: raise BotError(f"{label} images must contain 1 to {MAX_IMAGES} items.")
    images = []
    for number, item in enumerate(value, 1):
        if not isinstance(item, dict): raise BotError(f"{label} image {number} must contain path and alt values.")
        relative, alt = str(item.get("path", "")).strip(), str(item.get("alt", "")).strip()
        if not relative or not alt: raise BotError(f"{label} image {number} requires both path and alt text.")
        path = (ROOT / relative).resolve()
        if ROOT not in path.parents: raise BotError(f"{label} image {number} must be inside the repository.")
        if check_files:
            if not path.is_file(): raise BotError(f"{label} image does not exist: {relative}")
            if path.stat().st_size > MAX_IMAGE_BYTES: raise BotError(f"{label} image exceeds 1 MB: {relative}")
            if not (mimetypes.guess_type(path.name)[0] or "").startswith("image/"): raise BotError(f"{label} has an unsupported image type: {relative}")
        images.append({"path": relative, "alt": alt})
    return images


def normalize_card(value: Any, label: str, check_files: bool) -> dict[str, str] | None:
    if value is None: return None
    if not isinstance(value, dict): raise BotError(f"{label} link_card must be an object.")
    url = str(value.get("url", "")).strip(); parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc: raise BotError(f"{label} link_card requires a complete URL.")
    result = {"url": url}
    for key in ("title", "description", "image"):
        if value.get(key) is not None: result[key] = str(value[key]).strip()
    if result.get("image"):
        path = (ROOT / result["image"]).resolve()
        if ROOT not in path.parents: raise BotError(f"{label} preview image must be inside the repository.")
        if check_files and (not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES): raise BotError(f"{label} preview image is missing or exceeds 1 MB: {result['image']}")
    return result


def normalize_part(value: Any, label: str, check_files: bool) -> dict[str, Any]:
    if not isinstance(value, dict): raise BotError(f"{label} must be an object.")
    images = normalize_images(value.get("images"), label, check_files); card = normalize_card(value.get("link_card"), label, check_files)
    if images and card: raise BotError(f"{label} cannot combine images with a preview card (a Bluesky embed limitation).")
    return {"text": validate_text(value.get("text"), label), "images": images, "link_card": card}


def normalize_posts(raw_posts: list[Any]) -> dict[str, dict[str, Any]]:
    posts = {}
    for raw in raw_posts:
        if not isinstance(raw, dict): raise BotError("Every item in 'posts' must be an object.")
        post_id = str(raw.get("id", "")).strip()
        if not post_id or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", post_id): raise BotError("Post IDs may use lowercase letters, numbers, hyphens, and underscores.")
        if post_id in posts: raise BotError(f"Duplicate post id: {post_id}")
        enabled = raw.get("enabled", True)
        if not isinstance(enabled, bool): raise BotError(f"Post {post_id!r} enabled must be true or false.")
        check_files = enabled
        if "thread" in raw:
            if any(key in raw for key in ("text", "images", "link_card")): raise BotError(f"Thread {post_id!r} cannot also use top-level content.")
            thread = raw["thread"]
            if not isinstance(thread, list) or len(thread) < 2: raise BotError(f"Thread {post_id!r} needs at least two parts.")
            parts = [normalize_part(part, f"Thread {post_id!r} part {i}", check_files) for i, part in enumerate(thread, 1)]
        else: parts = [normalize_part(raw, f"Post {post_id!r}", check_files)]
        posts[post_id] = {"id": post_id, "enabled": enabled, "fingerprint": content_fingerprint(raw), "parts": parts}
    return posts


def release_edited_quarantines(state: dict[str, Any], posts: dict[str, dict[str, Any]]) -> list[str]:
    released = []
    for post_id, record in list(state["quarantined"].items()):
        if post_id not in posts or posts[post_id]["fingerprint"] != record.get("fingerprint"):
            del state["quarantined"][post_id]; state["used_ids"] = [x for x in state["used_ids"] if x != post_id]; released.append(post_id)
    return released


def choose_post(state: dict[str, Any], posts: dict[str, dict[str, Any]], today: str, hour: int) -> None:
    used = set(state["used_ids"])
    eligible = [pid for pid, post in posts.items() if post["enabled"] and pid not in state["quarantined"] and pid not in used]
    if not eligible:
        active = [pid for pid, post in posts.items() if post["enabled"] and pid not in state["quarantined"]]
        if not active: return
        state["cycle"] = int(state.get("cycle", 1)) + 1; state["used_ids"] = []; eligible = active
        if len(eligible) > 1 and state.get("last_post_id") in eligible: eligible.remove(state["last_post_id"])
    picker = random.SystemRandom(); post_id = picker.choice(eligible)
    state["reservation"] = {"post_id": post_id, "fingerprint": posts[post_id]["fingerprint"], "date_selected": today, "target_hour": picker.choice(list(range(max(7, hour), 14))), "attempts": []}


def clickable_link_facets(text: str) -> list[Any]:
    facets = []
    for match in URL_RE.finditer(text):
        url = match.group(0).rstrip(TRAILING_PUNCTUATION)
        if not urlparse(url).netloc: continue
        start, end = match.start(), match.start() + len(url)
        facets.append(models.AppBskyRichtextFacet.Main(index=models.AppBskyRichtextFacet.ByteSlice(byte_start=len(text[:start].encode()), byte_end=len(text[:end].encode())), features=[models.AppBskyRichtextFacet.Link(uri=url)]))
    return facets


def assert_public_web_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname: raise BotError(f"Invalid preview URL: {url}")
    try: addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc: raise BotError(f"Could not resolve preview host: {parsed.hostname}") from exc
    if any(not ipaddress.ip_address(address[4][0]).is_global for address in addresses): raise BotError("Preview URLs must resolve to a public address.")


def retrieve_card_metadata(url: str) -> dict[str, str]:
    assert_public_web_url(url)
    with httpx.Client(timeout=10, follow_redirects=True, headers={"User-Agent": "DandorismBlueskyBot/1.0"}) as web:
        response = web.get(url); response.raise_for_status(); parser = MetadataParser(); parser.feed(response.text[:1_000_000]); meta = parser.metadata
        return {"title": html.unescape(meta.get("og:title") or "".join(parser.title_parts).strip()), "description": html.unescape(meta.get("og:description") or meta.get("description") or ""), "remote_image": urljoin(str(response.url), meta.get("og:image", ""))}


def upload_image(client: Client, path: Path) -> Any:
    data = path.read_bytes()
    if len(data) > MAX_IMAGE_BYTES: raise BotError(f"Image exceeds 1 MB: {path.relative_to(ROOT)}")
    return client.upload_blob(data).blob


def build_embed(client: Client, part: dict[str, Any]) -> Any | None:
    if part["images"]:
        return models.AppBskyEmbedImages.Main(images=[models.AppBskyEmbedImages.Image(alt=item["alt"], image=upload_image(client, ROOT / item["path"])) for item in part["images"]])
    card = part["link_card"]
    if not card: return None
    metadata = {}
    if not card.get("title") or not card.get("description") or not card.get("image"):
        try: metadata = retrieve_card_metadata(card["url"])
        except Exception as exc: print(f"Preview metadata warning: {exc}", file=sys.stderr)
    title = card.get("title") or metadata.get("title") or urlparse(card["url"]).netloc
    description = card.get("description") or metadata.get("description") or ""; thumb = None
    if card.get("image"): thumb = upload_image(client, ROOT / card["image"])
    elif metadata.get("remote_image"):
        try:
            assert_public_web_url(metadata["remote_image"]); response = httpx.get(metadata["remote_image"], timeout=10, follow_redirects=True); response.raise_for_status()
            if len(response.content) <= MAX_IMAGE_BYTES: thumb = client.upload_blob(response.content).blob
        except Exception as exc: print(f"Preview thumbnail warning: {exc}", file=sys.stderr)
    return models.AppBskyEmbedExternal.Main(external=models.AppBskyEmbedExternal.External(uri=card["url"], title=title[:300], description=description[:1000], thumb=thumb))


def publish_entry(client: Client, post: dict[str, Any], reservation: dict[str, Any], state: dict[str, Any]) -> list[str]:
    progress = reservation.setdefault("thread_progress", {"uris": [], "root": None, "parent": None})
    uris = progress["uris"]
    root_ref = models.ComAtprotoRepoStrongRef.Main(**progress["root"]) if progress.get("root") else None
    parent_ref = models.ComAtprotoRepoStrongRef.Main(**progress["parent"]) if progress.get("parent") else None
    for part in post["parts"][len(uris):]:
        reply = models.AppBskyFeedPost.ReplyRef(root=root_ref, parent=parent_ref) if root_ref and parent_ref else None
        response = client.send_post(text=part["text"], facets=clickable_link_facets(part["text"]) or None, embed=build_embed(client, part), reply_to=reply)
        current = models.ComAtprotoRepoStrongRef.Main(uri=response.uri, cid=response.cid); root_ref = root_ref or current; parent_ref = current; uris.append(response.uri)
        progress["root"] = {"uri": root_ref.uri, "cid": root_ref.cid}
        progress["parent"] = {"uri": parent_ref.uri, "cid": parent_ref.cid}
        save_state(state)
    return uris


def run(args: argparse.Namespace) -> int:
    queue = load_queue(); settings = queue.get("settings", {}); enabled = settings.get("enabled", False)
    if not isinstance(enabled, bool): raise BotError("settings.enabled must be true or false.")
    posts = normalize_posts(queue.get("posts", [])); state = load_state(); released = release_edited_quarantines(state, posts)
    now = datetime.now(EST); today = now.date().isoformat()
    if args.dry_run:
        active = [p for p in posts.values() if p["enabled"] and p["id"] not in state["quarantined"]]
        print(f"Validation passed: {len(posts)} total, {len(active)} active, {len(state['quarantined'])} quarantined.")
        if args.post_id:
            if args.post_id not in posts: raise BotError(f"No post found with id {args.post_id!r}.")
            print(f"Dry run selected {args.post_id}: {len(posts[args.post_id]['parts'])} part(s).")
        write_result("dry_run", released_ids=released); return 0
    if not enabled and not args.post_id:
        save_state(state); write_result("paused", released_ids=released); print("Automatic posting is paused."); return 0
    if args.post_id:
        if args.post_id not in posts or not posts[args.post_id]["enabled"]: raise BotError(f"Post {args.post_id!r} does not exist or is disabled.")
        if today in state["daily_results"]: raise BotError(f"A daily result is already recorded for {today}.")
        state["reservation"] = {"post_id": args.post_id, "fingerprint": posts[args.post_id]["fingerprint"], "date_selected": today, "target_hour": now.hour, "attempts": []}
    elif today in state["daily_results"]:
        save_state(state); write_result("already_finished", released_ids=released); return 0
    elif state.get("reservation") is None:
        if not 7 <= now.hour <= 13: write_result("outside_window", released_ids=released); return 0
        choose_post(state, posts, today, now.hour); save_state(state)
    reservation = state.get("reservation")
    if not reservation:
        save_state(state); write_result("no_active_posts", released_ids=released); return 0
    post_id = reservation["post_id"]; post = posts.get(post_id)
    if not post or not post["enabled"]:
        state["reservation"] = None; save_state(state); write_result("reservation_removed", post_id=post_id, released_ids=released); return 0
    if post["fingerprint"] != reservation.get("fingerprint"):
        if reservation.get("thread_progress", {}).get("uris"):
            raise BotError(f"Reserved thread {post_id!r} was edited after part of it published. Restore it until the retry finishes.")
        reservation["fingerprint"], reservation["attempts"] = post["fingerprint"], []
    if not args.post_id and reservation.get("date_selected") == today and now.hour < int(reservation["target_hour"]):
        save_state(state); write_result("waiting", post_id=post_id, target_hour=reservation["target_hour"], released_ids=released); return 0
    handle, password = os.environ.get("BLUESKY_HANDLE", "").strip(), os.environ.get("BLUESKY_APP_PASSWORD", "").strip()
    if not handle or not password: error = "Missing BLUESKY_HANDLE or BLUESKY_APP_PASSWORD repository secret."
    else:
        try:
            client = Client(); client.login(handle, password); uris = publish_entry(client, post, reservation, state)
        except Exception as exc: error = f"{type(exc).__name__}: {exc}"
        else:
            state["used_ids"] = sorted(set(state["used_ids"]) | {post_id}); state["last_post_id"] = post_id; state["reservation"] = None
            state["daily_results"][today] = {"status": "posted", "post_id": post_id}; state["history"].append({"status": "posted", "post_id": post_id, "at": datetime.now(timezone.utc).isoformat(), "uris": uris})
            save_state(state); write_result("posted", post_id=post_id, uris=uris, released_ids=released); print(f"Published {post_id} ({len(uris)} part(s))."); return 0
    attempt = {"at": datetime.now(timezone.utc).isoformat(), "error": error[:2000]}; reservation.setdefault("attempts", []).append(attempt); attempts = len(reservation["attempts"])
    status = "retrying"
    if attempts >= 3:
        state["quarantined"][post_id] = {"fingerprint": post["fingerprint"], "quarantined_at": attempt["at"], "attempts": reservation["attempts"]}
        state["used_ids"] = sorted(set(state["used_ids"]) | {post_id}); state["reservation"] = None; state["daily_results"][today] = {"status": "quarantined", "post_id": post_id}; status = "quarantined"
    state["history"].append({"status": status, "post_id": post_id, **attempt}); save_state(state); write_result(status, post_id=post_id, attempt=attempts, error=error, released_ids=released)
    print(f"{post_id} failed attempt {attempts}/3: {error}", file=sys.stderr); return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--post-id")
    try: return run(parser.parse_args())
    except BotError as exc: write_result("configuration_error", error=str(exc)); print(f"ERROR: {exc}", file=sys.stderr); return 2


if __name__ == "__main__": raise SystemExit(main())
