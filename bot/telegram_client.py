"""Minimal Telegram Bot API client with flood-control (429) handling."""
import json
import logging
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)


class TelegramError(Exception):
    """Telegram rejected this specific request (bad file, too big, ...)."""


class TelegramUnavailable(Exception):
    """Telegram could not be reached at all; the run should stop and retry later."""


Media = str | Path  # URL or local file


class TelegramClient:
    def __init__(self, token: str, chat_id: str):
        self.api = f"https://api.telegram.org/bot{token}/"
        self.chat_id = chat_id
        self.session = requests.Session()

    def _call(self, method: str, data: dict | None = None, files: dict | None = None):
        for attempt in range(1, 6):
            try:
                r = self.session.post(self.api + method, data=data, files=files, timeout=180)
            except requests.RequestException as e:
                log.warning("Telegram network error on %s (attempt %d): %s", method, attempt, e)
                time.sleep(5 * attempt)
                continue
            try:
                body = r.json()
            except ValueError:
                body = {"ok": False, "description": r.text[:200]}
            if body.get("ok"):
                return body["result"]
            if r.status_code == 429:
                wait = body.get("parameters", {}).get("retry_after", 5)
                log.warning("Telegram flood control, sleeping %ss", wait)
                time.sleep(wait + 1)
                continue
            if r.status_code >= 500:
                time.sleep(5 * attempt)
                continue
            raise TelegramError(f"{method}: {body.get('description')}")
        raise TelegramUnavailable(f"{method}: Telegram unreachable after retries")

    def _send_media(self, method: str, field: str, media: Media, caption: str, spoiler: bool, **extra):
        data = {"chat_id": self.chat_id, "caption": caption, "parse_mode": "HTML", **extra}
        if spoiler:
            data["has_spoiler"] = "true"
        if isinstance(media, Path):
            return self._call(method, data, files={field: (media.name, media.read_bytes())})
        data[field] = media
        return self._call(method, data)

    def send_message(self, text: str, preview: bool = True):
        return self._call("sendMessage", {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
            "link_preview_options": json.dumps({"is_disabled": not preview}),
        })

    def send_photo(self, photo: Media, caption: str, spoiler: bool = False):
        return self._send_media("sendPhoto", "photo", photo, caption, spoiler)

    def send_video(self, video: Media, caption: str, spoiler: bool = False):
        return self._send_media("sendVideo", "video", video, caption, spoiler, supports_streaming="true")

    def send_animation(self, animation: Media, caption: str, spoiler: bool = False):
        return self._send_media("sendAnimation", "animation", animation, caption, spoiler)

    def send_media_group(self, items: list[tuple[str, Media]], caption: str, spoiler: bool = False):
        """items: 2-10 (kind, media) pairs; kind is "photo" or "video"."""
        media, files = [], {}
        for i, (kind, src) in enumerate(items):
            entry = {"type": kind}
            if isinstance(src, Path):
                files[f"file{i}"] = (src.name, src.read_bytes())
                entry["media"] = f"attach://file{i}"
            else:
                entry["media"] = src
            if i == 0:
                entry.update(caption=caption, parse_mode="HTML")
            if spoiler:
                entry["has_spoiler"] = True
            media.append(entry)
        data = {"chat_id": self.chat_id, "media": json.dumps(media)}
        return self._call("sendMediaGroup", data, files=files or None)

    def get_updates(self):
        return self._call("getUpdates", {"timeout": 0})
