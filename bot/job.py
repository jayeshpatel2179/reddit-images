"""One run: fetch new posts since the last checkpoint, send the unsent ones, save progress.

No duplicates: every sent post id is stored in SQLite and skipped next time.
No misses: we fetch from (last successful run - overlap), not a fixed "last 2 hours",
so late or failed runs are caught up automatically.
"""
import logging
import time
from typing import Protocol

from .config import Config
from .media import ParsedPost
from .reddit_client import RedditClient
from .rss_client import RssClient
from .sender import PostSender
from .storage import Storage
from .telegram_client import TelegramUnavailable

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3


class PostSource(Protocol):
    def fetch_new_since(self, subreddit: str, since_utc: float) -> list[ParsedPost]: ...


def make_source(cfg: Config) -> PostSource:
    if cfg.reddit_source == "api":
        if not (cfg.reddit_client_id and cfg.reddit_client_secret):
            raise SystemExit("REDDIT_SOURCE=api needs REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET")
        log.info("Reddit source: official API (OAuth, 100 req/min)")
        return RedditClient(cfg.reddit_client_id, cfg.reddit_client_secret, cfg.reddit_user_agent)
    log.info("Reddit source: public RSS feed")
    return RssClient(cfg.reddit_user_agent)


def fetch_pending(cfg: Config, source: PostSource, store: Storage) -> tuple[list[ParsedPost], float]:
    run_started = time.time()
    last_run = store.get_meta("last_run_utc")
    if last_run:
        since = float(last_run) - cfg.overlap_minutes * 60
    else:
        since = run_started - cfg.first_run_lookback_hours * 3600
        log.info("First run: only looking back %.1f hours", cfg.first_run_lookback_hours)

    posts = source.fetch_new_since(cfg.subreddit, since)
    pending = [p for p in posts if not store.is_sent(p.id)]
    log.info("r/%s: %d posts since %s, %d not yet sent",
             cfg.subreddit, len(posts), time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(since)), len(pending))
    return pending, run_started


def run_once(cfg: Config, source: PostSource, sender: PostSender, store: Storage) -> None:
    pending, run_started = fetch_pending(cfg, source, store)
    checkpoint = run_started
    sent = 0

    for post in pending:
        if cfg.skip_text_posts and post.kind == "text":
            store.mark_sent(post.id, post.created_utc, status="skipped")
            log.info("Skipped text post %s %s", post.id, post.title[:80])
            continue
        try:
            sender.send(post)
        except TelegramUnavailable:
            log.error("Telegram unreachable; stopping this run, will retry next time")
            raise
        except Exception:
            attempts = store.record_failure(post.id)
            log.exception("Failed to send %s (attempt %d/%d)", post.id, attempts, MAX_ATTEMPTS)
            if attempts >= MAX_ATTEMPTS:
                store.mark_sent(post.id, post.created_utc, status="gave_up")
            else:
                # Keep the checkpoint early enough that this post is fetched again next run.
                checkpoint = min(checkpoint, post.created_utc + cfg.overlap_minutes * 60 - 1)
            continue
        store.mark_sent(post.id, post.created_utc)
        sent += 1
        log.info("Sent %s [%s] %s", post.id, post.kind, post.title[:80])
        time.sleep(cfg.post_delay_seconds)

    store.set_meta("last_run_utc", str(checkpoint))
    store.prune()
    log.info("Run finished: %d/%d posts sent", sent, len(pending))
