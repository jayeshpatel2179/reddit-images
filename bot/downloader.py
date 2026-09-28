"""Downloads media so it can be uploaded to Telegram (needed for Reddit videos with sound
and for files Telegram refuses to fetch by URL)."""
import logging
from pathlib import Path

import requests
import yt_dlp

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (reddit-telegram-forwarder)"


def download_file(url: str, dest: Path, max_bytes: int) -> Path | None:
    try:
        with requests.get(url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            size = 0
            with open(dest, "wb") as f:
                for chunk in r.iter_content(64 * 1024):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError(f"file larger than {max_bytes // 1_000_000} MB")
                    f.write(chunk)
        return dest
    except Exception as e:
        log.warning("Download failed for %s: %s", url, e)
        dest.unlink(missing_ok=True)
        return None


def _ytdlp(url: str, dest_dir: Path, name: str, max_height: int) -> Path | None:
    opts = {
        "outtmpl": str(dest_dir / f"{name}.%(ext)s"),
        "format": f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b",
        "merge_output_format": "mp4",
        "overwrites": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.extract_info(url, download=True)
    except Exception as e:
        log.warning("yt-dlp failed for %s: %s", url, e)
        return None
    files = sorted(dest_dir.glob(f"{name}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def download_reddit_video(dash_url: str | None, fallback_url: str, dest_dir: Path, name: str,
                          max_bytes: int) -> Path | None:
    """Reddit stores video and audio separately; yt-dlp + ffmpeg merge them into one mp4.
    Tries lower resolutions until the file fits Telegram's upload limit."""
    for url in filter(None, (dash_url, fallback_url)):
        for height in (1080, 720, 480, 360):
            path = _ytdlp(url, dest_dir, name, height)
            if path is None:
                break  # this URL doesn't work at all, try the next one
            if path.stat().st_size <= max_bytes:
                return path
            log.info("Video %s is %.1f MB at <=%dp, retrying smaller", name, path.stat().st_size / 1e6, height)
            path.unlink(missing_ok=True)
    return None
