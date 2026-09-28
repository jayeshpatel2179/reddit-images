"""Sends one parsed Reddit post to Telegram, falling back step by step:
URL -> download & upload -> plain text message with the Reddit link."""
import logging
import time
from html import escape
from pathlib import Path

from .config import Config
from .downloader import download_file, download_reddit_video
from .media import ParsedPost
from .telegram_client import TelegramClient, TelegramError

log = logging.getLogger(__name__)

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4096
PHOTO_UPLOAD_LIMIT = 10 * 1_000_000


def _footer(post: ParsedPost) -> str:
    lines = []
    if post.flair:
        lines.append(f"🏷 {escape(post.flair)}")
    lines.append(f"👤 u/{escape(post.author)}")
    lines.append(f'🔗 <a href="{escape(post.permalink)}">View on Reddit</a>')
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: max(limit - 1, 0)] + "…"


def build_caption(post: ParsedPost) -> str:
    footer = _footer(post)
    caption = f"<b>{escape(_clip(post.title, 300))}</b>"
    room = CAPTION_LIMIT - len(caption) - len(footer) - 40
    body = post.selftext.strip()
    if body and room > 50:
        caption += "\n\n" + escape(_clip(body, min(room, 600)))
    return f"{caption}\n\n{footer}"


def build_text_message(post: ParsedPost) -> str:
    parts = [f"<b>{escape(post.title)}</b>"]
    body = post.selftext.strip()
    if body:
        if len(body) > 2500:
            body = body[:2500] + "…"
        parts.append(escape(body))
    if post.link_url and "reddit.com" not in post.link_url and "redd.it" not in post.link_url:
        parts.append(f'🌐 <a href="{escape(post.link_url)}">{escape(post.link_url[:80])}</a>')
    if post.kind == "gallery_link":
        parts.append("🖼 Gallery post, tap <b>View on Reddit</b> to see all images")
    parts.append(_footer(post))
    return "\n\n".join(parts)[:MESSAGE_LIMIT]


def wants_preview(post: ParsedPost) -> bool:
    """Link previews help for galleries and external links; they're noise for plain text posts."""
    return post.kind != "text" or post.link_url != post.permalink


class PostSender:
    def __init__(self, tg: TelegramClient, cfg: Config):
        self.tg = tg
        self.cfg = cfg
        self.max_bytes = cfg.max_upload_mb * 1_000_000
        self._temp: list[Path] = []

    def _download(self, url: str, name: str, max_bytes: int) -> Path | None:
        ext = Path(url.split("?")[0]).suffix or ".bin"
        path = download_file(url, self.cfg.download_dir / f"{name}{ext}", max_bytes)
        if path:
            self._temp.append(path)
        return path

    def _cleanup(self) -> None:
        for p in self._temp:
            p.unlink(missing_ok=True)
        self._temp.clear()

    # --- per-type senders ---------------------------------------------------

    def _photo(self, post: ParsedPost, caption: str) -> None:
        url = post.items[0].url
        try:
            self.tg.send_photo(url, caption, post.spoiler)
        except TelegramError as e:
            log.info("Photo by URL failed (%s), uploading instead", e)
            path = self._download(url, post.id, PHOTO_UPLOAD_LIMIT)
            if not path:
                raise
            self.tg.send_photo(path, caption, post.spoiler)

    def _animation(self, post: ParsedPost, caption: str) -> None:
        url = post.items[0].url
        try:
            self.tg.send_animation(url, caption, post.spoiler)
        except TelegramError:
            path = self._download(url, post.id, self.max_bytes)
            if not path:
                raise
            self.tg.send_animation(path, caption, post.spoiler)

    def _video(self, post: ParsedPost, caption: str) -> None:
        fallback = post.items[0].url
        path = download_reddit_video(post.dash_url, fallback, self.cfg.download_dir, post.id, self.max_bytes)
        if path:
            self._temp.append(path)
            self.tg.send_video(path, caption, post.spoiler)
        elif fallback.split("?")[0].endswith(".mp4"):
            log.info("Video download failed for %s, trying direct URL (may have no sound)", post.id)
            self.tg.send_video(fallback, caption, post.spoiler)
        else:
            raise TelegramError(f"video download failed for {post.id}")

    def _gallery(self, post: ParsedPost, caption: str) -> None:
        chunks = [post.items[i:i + 10] for i in range(0, len(post.items), 10)]
        for n, chunk in enumerate(chunks):
            chunk_caption = caption if n == 0 else f"({n + 1}/{len(chunks)}) {escape(post.title[:200])}"
            if len(chunk) == 1:
                item = chunk[0]
                send = self.tg.send_photo if item.kind == "photo" else self.tg.send_video
                send(item.url, chunk_caption, post.spoiler)
                continue
            try:
                self.tg.send_media_group([(i.kind, i.url) for i in chunk], chunk_caption, post.spoiler)
            except TelegramError as e:
                log.info("Gallery by URL failed (%s), uploading instead", e)
                files = []
                for j, item in enumerate(chunk):
                    limit = PHOTO_UPLOAD_LIMIT if item.kind == "photo" else self.max_bytes
                    path = self._download(item.url, f"{post.id}_{n}_{j}", limit)
                    if path:
                        files.append((item.kind, path))
                if len(files) < 2:
                    raise
                self.tg.send_media_group(files, chunk_caption, post.spoiler)
            if n + 1 < len(chunks):
                time.sleep(self.cfg.post_delay_seconds)

    # --- entry point ----------------------------------------------------------

    def send(self, post: ParsedPost) -> None:
        caption = build_caption(post)
        handlers = {
            "photo": self._photo,
            "animation": self._animation,
            "video": self._video,
            "gallery": self._gallery,
        }
        try:
            handler = handlers.get(post.kind)
            if handler:
                try:
                    handler(post, caption)
                    return
                except TelegramError as e:
                    log.warning("Sending %s %s as media failed (%s); sending as text", post.kind, post.id, e)
            self.tg.send_message(build_text_message(post), preview=wants_preview(post))
        finally:
            self._cleanup()
