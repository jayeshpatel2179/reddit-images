"""Reads r/<subreddit>/new through Reddit's public Atom/RSS feed (no API key needed).

The feed throttles hard (~5 quick requests -> 429), so requests are spaced >= 15s apart
and 429s back off with growing waits. A normal run needs just 1 request (100 posts).
"""
import html
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse

import requests

from .media import IMAGE_EXTS, MediaItem, ParsedPost

log = logging.getLogger(__name__)

ATOM = "{http://www.w3.org/2005/Atom}"
FEED_URL = "https://www.reddit.com/r/{sub}/new/.rss"

LINK_RE = re.compile(r'<a href="([^"]+)">\[link\]</a>')
BODY_RE = re.compile(r'<div class="md">(.*?)</div>\s*<!-- SC_ON -->', re.S)
BODY_IMAGE_RE = re.compile(r'href="(https://(?:preview|i)\.redd\.it/[^"]+)"')
TAG_RE = re.compile(r"<[^>]+>")


def html_to_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>|</li>|</h\d>", "\n", fragment)
    text = re.sub(r"<li>", "• ", text)
    text = html.unescape(TAG_RE.sub("", text))
    text = "\n".join(line.strip() for line in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def parse_entry(entry: ET.Element) -> ParsedPost:
    content = entry.findtext(f"{ATOM}content") or ""
    permalink = entry.find(f"{ATOM}link").get("href")
    link_match = LINK_RE.search(content)
    link = html.unescape(link_match.group(1)) if link_match else permalink
    body_match = BODY_RE.search(content)
    body_html = body_match.group(1) if body_match else ""

    post = ParsedPost(
        id=(entry.findtext(f"{ATOM}id") or "").removeprefix("t3_"),
        title=entry.findtext(f"{ATOM}title") or "",
        author=(entry.findtext(f"{ATOM}author/{ATOM}name") or "/u/[deleted]").removeprefix("/u/"),
        permalink=permalink,
        flair="",
        link_url=link,
        selftext=html_to_text(body_html),
        created_utc=datetime.fromisoformat(entry.findtext(f"{ATOM}published")).timestamp(),
        spoiler=False,
        kind="text",
    )

    parsed = urlparse(link)
    host, path = parsed.netloc.lower(), parsed.path.lower()

    if host == "v.redd.it":
        base = link.rstrip("/")
        post.kind = "video"
        post.dash_url = f"{base}/DASHPlaylist.mpd"
        post.items.append(MediaItem("video", f"{base}/HLSPlaylist.m3u8"))
    elif host.endswith("redd.it") or host == "i.imgur.com":
        if path.endswith(".gif"):
            post.kind = "animation"
            post.items.append(MediaItem("video", link))
        elif path.endswith(IMAGE_EXTS):
            post.kind = "photo"
            post.items.append(MediaItem("photo", link))
    elif "/gallery/" in path:
        post.kind = "gallery_link"
    elif link == permalink:
        # Self post; images pasted into the body are still sent as media.
        images = list(dict.fromkeys(html.unescape(u) for u in BODY_IMAGE_RE.findall(body_html)))[:10]
        if len(images) == 1:
            post.kind = "photo"
        elif len(images) > 1:
            post.kind = "gallery"
        post.items = [MediaItem("photo", u) for u in images]
        for u in images:  # pasted image URLs also show up as plain text in the body
            post.selftext = post.selftext.replace(u, "")
        post.selftext = re.sub(r"\n{3,}", "\n\n", post.selftext).strip()
    return post


class RssClient:
    min_interval = 15.0

    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0

    def _get(self, url: str, params: dict) -> bytes:
        for attempt in range(1, 5):
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                r = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as e:
                log.warning("RSS request error (attempt %d): %s", attempt, e)
                time.sleep(30 * attempt)
                continue
            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after") or 60 * attempt)
                log.warning("RSS feed throttled (429), waiting %.0fs", retry_after)
                time.sleep(retry_after)
                continue
            if r.status_code >= 500:
                time.sleep(30 * attempt)
                continue
            if r.status_code == 403:
                raise RuntimeError("Reddit blocked the RSS feed (403) from this server/IP.")
            r.raise_for_status()
            return r.content
        raise RuntimeError("RSS feed unavailable after retries")

    def fetch_new_since(self, subreddit: str, since_utc: float, max_pages: int = 3) -> list[ParsedPost]:
        posts: dict[str, ParsedPost] = {}
        after = None
        for _ in range(max_pages):
            params = {"limit": 100}
            if after:
                params["after"] = after
            root = ET.fromstring(self._get(FEED_URL.format(sub=subreddit), params))
            entries = []
            for e in root.findall(f"{ATOM}entry"):
                try:
                    entries.append(parse_entry(e))
                except Exception:
                    log.exception("Could not parse RSS entry %s", e.findtext(f"{ATOM}id"))
            for p in entries:
                if p.created_utc >= since_utc:
                    posts[p.id] = p
            if not entries or min(p.created_utc for p in entries) < since_utc:
                break
            after = "t3_" + entries[-1].id
        return sorted(posts.values(), key=lambda p: p.created_utc)
