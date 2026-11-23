"""Reads new subreddit posts from Arctic Shift (https://arctic-shift.photon-reddit.com),
a public archive that copies Reddit posts within about a minute. No key needed.

Used because Reddit shut down its public RSS feeds in November 2026. Posts come back in
Reddit's own JSON format, so they go through the same parser as the official API.
"""
import logging
import time

import requests

from .media import ParsedPost, parse_post

log = logging.getLogger(__name__)

SEARCH_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
PAGE_SIZE = 100


class ArcticShiftClient:
    min_interval = 2.0  # be polite; a normal run needs only a few requests

    def __init__(self, user_agent: str):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0

    def _get(self, params: dict) -> list[dict]:
        for attempt in range(1, 5):
            wait = self._last_request + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                r = self.session.get(SEARCH_URL, params=params, timeout=60)
            except requests.RequestException as e:
                log.warning("Arctic Shift request error (attempt %d): %s", attempt, e)
                time.sleep(30 * attempt)
                continue
            if r.status_code == 429:
                retry_after = float(r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset") or 60 * attempt)
                log.warning("Arctic Shift throttled (429), waiting %.0fs", retry_after)
                time.sleep(retry_after)
                continue
            if r.status_code >= 500:
                log.warning("Arctic Shift server error %d (attempt %d)", r.status_code, attempt)
                time.sleep(30 * attempt)
                continue
            r.raise_for_status()
            body = r.json()
            if body.get("error"):
                raise RuntimeError(f"Arctic Shift error: {body['error']}")
            return body.get("data") or []
        raise RuntimeError("Arctic Shift unavailable after retries")

    def fetch_new_since(self, subreddit: str, since_utc: float, max_pages: int = 20) -> list[ParsedPost]:
        """All posts in r/<subreddit> created at or after since_utc, oldest first."""
        posts: dict[str, dict] = {}
        after = int(since_utc) - 1
        for _ in range(max_pages):
            page = self._get({"subreddit": subreddit, "after": after, "sort": "asc", "limit": PAGE_SIZE})
            for p in page:
                if p["created_utc"] >= since_utc:
                    posts[p["id"]] = p
            if len(page) < PAGE_SIZE:
                break
            newest = int(max(p["created_utc"] for p in page))
            # Step back 1s so posts sharing the boundary second aren't lost; dupes are merged by id.
            after = newest - 1 if newest - 1 > after else newest
        else:
            log.warning("Hit the %d-page limit; some older posts in this window were not fetched", max_pages)

        result = []
        for p in sorted(posts.values(), key=lambda p: p["created_utc"]):
            if p.get("removed_by_category"):
                continue
            try:
                result.append(parse_post(p))
            except Exception:
                log.exception("Could not parse post %s", p.get("id"))
        return result
