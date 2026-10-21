"""Turning a tweet into downloadable media candidates.

X serves every video as a list of progressive ``video/mp4`` variants (plus an
optional HLS playlist). The variant with the highest bitrate is the one with
the highest resolution, so that is what we pick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MP4 = "mp4"
HLS = "hls"
IMAGE = "image"
GIF = "gif"


@dataclass
class Variant:
    url: str
    bitrate: int = 0
    content_type: str = ""
    kind: str = MP4

    @property
    def ext(self) -> str:
        if self.kind == HLS:
            return ".mp4"  # remuxed by the HLS fallback
        if "mp4" in self.content_type:
            return ".mp4"
        if "webm" in self.content_type:
            return ".webm"
        return ".mp4"


@dataclass
class MediaItem:
    """One media object attached to a tweet."""

    media_id: str
    kind: str  # video | gif | image
    width: int = 0
    height: int = 0
    variants: list[Variant] = field(default_factory=list)
    image_url: str = ""

    @property
    def key(self) -> str:
        return f"{self.media_id}"


def _as_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _variants_from_video_info(video_info: dict) -> list[Variant]:
    variants: list[Variant] = []
    for raw in (video_info or {}).get("variants", []) or []:
        url = raw.get("url")
        if not url:
            continue
        content_type = raw.get("content_type", "") or ""
        if content_type.startswith("video"):
            kind = MP4
        elif "mpegURL" in content_type:
            kind = HLS
        else:
            continue
        variants.append(
            Variant(
                url=url,
                bitrate=_as_int(raw.get("bitrate")),
                content_type=content_type,
                kind=kind,
            )
        )
    return variants


def best_variant(item: MediaItem) -> Variant | None:
    """Highest bitrate progressive variant, falling back to HLS."""
    mp4s = [v for v in item.variants if v.kind == MP4]
    if mp4s:
        return max(mp4s, key=lambda v: v.bitrate)
    hls = [v for v in item.variants if v.kind == HLS]
    if hls:
        return max(hls, key=lambda v: v.bitrate)
    return None


def _video_kind(media) -> str:
    """Tell an animated GIF apart from a regular video."""
    if getattr(media, "type", None) == "animated_gif":
        return GIF
    if type(media).__name__ == "AnimatedGif":
        return GIF
    return "video"


def extract_media(tweet) -> list[MediaItem]:
    """Collect the media of one tweet. Works for Video, AnimatedGif and Photo."""
    items: list[MediaItem] = []
    for media in getattr(tweet, "media", None) or []:
        media_id = str(getattr(media, "id", "") or "")
        width = _as_int(getattr(media, "width", 0))
        height = _as_int(getattr(media, "height", 0))
        media_type = getattr(media, "type", "") or ""
        video_info = getattr(media, "video_info", None)

        if media_type in ("video", "animated_gif") or (
            not media_type and video_info
        ):
            variants = _variants_from_video_info(video_info or {})
            items.append(
                MediaItem(
                    media_id=media_id,
                    kind=_video_kind(media),
                    width=width,
                    height=height,
                    variants=variants,
                )
            )
            continue

        image_url = getattr(media, "source_url", None) or getattr(media, "media_url", "")
        if image_url:
            items.append(
                MediaItem(
                    media_id=media_id,
                    kind=IMAGE,
                    width=width,
                    height=height,
                    image_url=image_url,
                )
            )
    return items


def image_extension(url: str) -> str:
    """Guess the extension of an image url (.jpg / .png / .webp)."""
    from urllib.parse import urlparse

    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"} else ".jpg"


def has_video(tweet) -> bool:
    return any(item.kind in ("video", "gif") for item in extract_media(tweet))
