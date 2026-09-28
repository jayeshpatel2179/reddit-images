"""Turns a raw Reddit post into something we know how to send to Telegram."""
from dataclasses import dataclass, field
from html import unescape

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


@dataclass
class MediaItem:
    kind: str  # "photo" | "video"
    url: str


@dataclass
class ParsedPost:
    id: str
    title: str
    author: str
    permalink: str
    flair: str
    link_url: str
    selftext: str
    created_utc: float
    spoiler: bool
    kind: str  # "photo" | "gallery" | "video" | "animation" | "gallery_link" | "text"
    items: list[MediaItem] = field(default_factory=list)
    dash_url: str | None = None


def _reddit_video(src: dict) -> dict | None:
    for key in ("secure_media", "media"):
        rv = (src.get(key) or {}).get("reddit_video")
        if rv:
            return rv
    return (src.get("preview") or {}).get("reddit_video_preview")


def parse_post(post: dict) -> ParsedPost:
    # Crossposts keep their media on the original post.
    src = post["crosspost_parent_list"][0] if post.get("crosspost_parent_list") else post
    url = unescape(src.get("url_overridden_by_dest") or src.get("url") or "")

    parsed = ParsedPost(
        id=post["id"],
        title=unescape(post.get("title", "")),
        author=post.get("author", "[deleted]"),
        permalink="https://www.reddit.com" + post.get("permalink", ""),
        flair=unescape(post.get("link_flair_text") or ""),
        link_url=url,
        selftext=unescape(post.get("selftext") or ""),
        created_utc=post["created_utc"],
        spoiler=bool(post.get("over_18") or post.get("spoiler")),
        kind="text",
    )

    if src.get("is_gallery") and src.get("media_metadata"):
        meta = src["media_metadata"]
        order = [i["media_id"] for i in (src.get("gallery_data") or {}).get("items", [])] or list(meta)
        for media_id in order:
            m = meta.get(media_id) or {}
            if m.get("status") != "valid":
                continue
            s = m.get("s") or {}
            if s.get("mp4"):
                parsed.items.append(MediaItem("video", unescape(s["mp4"])))
            elif s.get("u"):
                parsed.items.append(MediaItem("photo", unescape(s["u"])))
            elif s.get("gif"):
                parsed.items.append(MediaItem("photo", unescape(s["gif"])))
        if parsed.items:
            parsed.kind = "gallery"
            return parsed

    rv = _reddit_video(src)
    path = url.lower().split("?")[0]

    if src.get("is_video") and rv:
        parsed.kind = "video"
        parsed.items.append(MediaItem("video", rv["fallback_url"]))
        parsed.dash_url = rv.get("dash_url")
    elif path.endswith(".gif"):
        parsed.kind = "animation"
        parsed.items.append(MediaItem("video", url))
    elif path.endswith(IMAGE_EXTS) or src.get("post_hint") == "image":
        parsed.kind = "photo"
        parsed.items.append(MediaItem("photo", url))
    elif rv:  # e.g. gifv / external clips that Reddit transcoded into a preview video
        parsed.kind = "video"
        parsed.items.append(MediaItem("video", rv["fallback_url"]))
        parsed.dash_url = rv.get("dash_url")

    return parsed
