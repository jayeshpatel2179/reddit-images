"""Reddit reader with a built-in rate limiter.

OAuth (client id + secret): up to 100 requests/minute -> we space requests >= 0.6s apart.
No credentials: public .json endpoint, ~10 requests/minute -> we space requests >= 6s apart.
On top of that we read Reddit's X-Ratelimit-* headers and pause when the quota is almost used.
"""
import logging
import time

import requests

from .media import ParsedPost, parse_post

log = logging.getLogger(__name__)

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"


class RedditClient:
    def __init__(self, client_id: str, client_secret: str, user_agent: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.oauth = bool(client_id and client_secret)
        self.base_url = "https://oauth.reddit.com" if self.oauth else "https://www.reddit.com"
        self.min_interval = 60 / 100 if self.oauth else 60 / 10
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self._last_request = 0.0
        self._token_expiry = 0.0

    def _authenticate(self) -> None:
        if time.time() < self._token_expiry - 60:
            return
        r = requests.post(
            TOKEN_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": self.session.headers["User-Agent"]},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        if "access_token" not in data:
            raise RuntimeError(f"Reddit authentication failed: {data}")
        self.session.headers["Authorization"] = f"bearer {data['access_token']}"
        self._token_expiry = time.time() + data.get("expires_in", 3600)
        log.info("Authenticated with Reddit (OAuth)")

    def _throttle(self) -> None:
        wait = self._last_request + self.min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    @staticmethod
    def _respect_rate_headers(r: requests.Response) -> None:
        remaining = r.headers.get("x-ratelimit-remaining")
        reset = r.headers.get("x-ratelimit-reset")
        if remaining is None or reset is None:
            return
        log.debug("Reddit rate limit: %s remaining, resets in %ss", remaining, reset)
        if float(remaining) < 2:
            log.warning("Reddit quota almost used, sleeping %ss until reset", reset)
            time.sleep(float(reset) + 1)

    def _get(self, path: str, params: dict) -> dict:
        url = self.base_url + path + ("" if self.oauth else ".json")
        for attempt in range(1, 5):
            if self.oauth:
                self._authenticate()
            self._throttle()
            try:
                r = self.session.get(url, params=params, timeout=30)
            except requests.RequestException as e:
                log.warning("Reddit request error (attempt %d): %s", attempt, e)
                time.sleep(5 * attempt)
                continue
            self._respect_rate_headers(r)
            if r.status_code == 429:
                wait = float(r.headers.get("retry-after") or r.headers.get("x-ratelimit-reset") or 60)
                log.warning("Reddit returned 429, sleeping %.0fs", wait)
                time.sleep(wait)
                continue
            if r.status_code == 401 and self.oauth:
                self._token_expiry = 0
                continue
            if r.status_code >= 500:
                time.sleep(5 * attempt)
                continue
            if r.status_code == 403 and not self.oauth:
                raise RuntimeError(
                    "Reddit blocked the public endpoint (403). Add REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET."
                )
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Reddit request failed after retries: {path}")

    def fetch_new_since(self, subreddit: str, since_utc: float, max_pages: int = 10) -> list[ParsedPost]:
        """All posts in r/<subreddit>/new created at or after since_utc, oldest first."""
        posts: list[dict] = []
        after = None
        for _ in range(max_pages):
            params = {"limit": 100, "raw_json": 1}
            if after:
                params["after"] = after
            data = self._get(f"/r/{subreddit}/new", params)["data"]
            children = [c["data"] for c in data.get("children", []) if c.get("kind") == "t3"]
            posts.extend(p for p in children if p["created_utc"] >= since_utc)
            after = data.get("after")
            if not children or not after or children[-1]["created_utc"] < since_utc:
                break
        posts.sort(key=lambda p: p["created_utc"])
        return [parse_post(p) for p in posts if not p.get("removed_by_category")]
