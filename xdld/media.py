"""Turning a tweet into downloadable media candidates.

X serves every video as a list of progressive ``video/mp4`` variants (plus an
optional HLS playlist). The variant with the highest bitrate is the one with
the highest resolution, so that is what we pick - unless a size limit is
configured, then the best variant that still fits into the limit wins.

The API does not tell us how big a variant is (only its bitrate), so the
sizes have to be measured; :func:`select_variant` takes a ``probe`` for that.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from xdld.progress import human_bytes

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
    size: int = 0  # known content length in bytes (0 == unknown)

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
    thumb_url: str = ""  # the smaller version of an image
    duration_ms: int = 0

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
                size=_as_int(raw.get("content_length")),
            )
        )
    return variants


def ranked_variants(item: MediaItem) -> list[Variant]:
    """The variants of a video, best first.

    The progressive mp4 variants come first - among them the highest bitrate
    one has the highest resolution. The HLS playlist is the last resort.
    """
    mp4s = sorted(
        (v for v in item.variants if v.kind == MP4),
        key=lambda v: v.bitrate,
        reverse=True,
    )
    if mp4s:
        return mp4s
    return sorted(
        (v for v in item.variants if v.kind == HLS),
        key=lambda v: v.bitrate,
        reverse=True,
    )


def best_variant(item: MediaItem, max_bytes: int = 0) -> Variant | None:
    """Highest bitrate progressive variant, falling back to HLS.

    ``max_bytes`` only skips variants whose size is already known - use
    :func:`select_variant` when the sizes have to be measured first.
    """
    for variant in ranked_variants(item):
        if max_bytes and variant.size and variant.size > max_bytes:
            continue
        return variant
    return None


def estimate_size(variant: Variant, item: MediaItem) -> int:
    """A rough size of a variant: bitrate x duration.

    The bitrate of the API is the one of the quality ladder, not the one of
    the delivered file, so this is an *over* estimate (measured: 1.3x to
    3.2x too big on 45 samples, never too small). That makes it usable to
    accept a variant, but never to reject one.
    """
    if not variant.bitrate or not item.duration_ms:
        return 0
    return int(variant.bitrate * item.duration_ms / 8000)


async def select_variant(
    item: MediaItem,
    max_bytes: int = 0,
    probe=None,
    log=None,
    strict: bool = False,
) -> tuple[Variant | None, int]:
    """Pick the variant to download and return it together with its size.

    Without a limit this is simply the best variant. With one the variants
    are walked from the best to the worst and the first one that fits into
    ``max_bytes`` wins.

    When not even the smallest variant fits, the behaviour depends on
    ``strict``: by default the smallest variant is downloaded anyway (a
    video is not thrown away because of its size), with ``strict`` the
    video is skipped instead and the limit is a hard rule.

    ``probe`` is awaited with a url to measure its size; when it is missing
    (or fails) the size is estimated from the bitrate and the duration of
    the video.
    """
    candidates = ranked_variants(item)
    if not candidates:
        return None, 0
    if max_bytes <= 0:
        return candidates[0], candidates[0].size

    if candidates[0].kind == HLS:
        # The size of an HLS stream is not known upfront (it is a playlist
        # of segments), so it cannot be limited.
        if log:
            log("an HLS stream cannot be limited")
        if strict:
            return None, 0
        return candidates[0], 0

    sizes: list[int] = [c.size for c in candidates]

    async def measure(index: int) -> int:
        variant = candidates[index]
        if sizes[index]:
            return sizes[index]
        if probe is None:
            return 0
        found = await probe(variant.url) or 0
        sizes[index] = found
        return found

    # The best variant first: in the common case it fits and the whole tweet
    # costs a single request.
    await measure(0)
    if sizes[0] and sizes[0] <= max_bytes:
        return candidates[0], sizes[0]

    # It does not fit (or could not be measured): the rest is measured in one
    # go, so a tweet costs one more round trip and not one per variant.
    if len(candidates) > 1:
        await asyncio.gather(*(measure(i) for i in range(1, len(candidates))))

    # The best variant that fits.
    for index, variant in enumerate(candidates):
        if sizes[index] and sizes[index] <= max_bytes:
            return variant, sizes[index]

    # Nothing fits. A variant we could not measure might still fit, and the
    # estimate is an over estimate, so it can be accepted.
    for index, variant in enumerate(candidates):
        if sizes[index]:
            continue
        guess = estimate_size(variant, item)
        if guess and guess <= max_bytes:
            if log:
                log(f"size unknown, estimated {human_bytes(guess)}")
            return variant, guess

    if strict:
        if log:
            log("nothing fits into the limit")
        return None, 0

    # Not a single variant fits: take the smallest one. A video is not
    # thrown away because of its size.
    index = len(candidates) - 1
    if log:
        log(
            f"nothing fits into the limit, taking the smallest one "
            f"({human_bytes(sizes[index])})"
        )
    return candidates[index], sizes[index]


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
                    duration_ms=_as_int((video_info or {}).get("duration_millis")),
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
                    thumb_url=getattr(media, "media_url", "") or "",
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
