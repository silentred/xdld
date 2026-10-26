"""The size limit: pick the best variant that still fits into it."""

from __future__ import annotations

from types import SimpleNamespace

from xdld.media import HLS, MP4, extract_media, select_variant


def _video(variants, media_id="1", width=1280, height=720, duration_ms=0):
    return SimpleNamespace(
        id=media_id,
        width=width,
        height=height,
        video_info={"variants": variants, "duration_millis": duration_ms},
    )


def _item(variants, duration_ms=0):
    tweet = SimpleNamespace(media=[_video(variants, duration_ms=duration_ms)])
    return extract_media(tweet)[0]


def _probe(sizes: dict[str, int]):
    """A stand-in for :class:`xdld.sizes.SizeProbe`."""

    async def probe(url: str) -> int | None:
        return sizes.get(url)

    return probe


HIGH = "https://v/high.mp4"
MID = "https://v/mid.mp4"
LOW = "https://v/low.mp4"

VARIANTS = [
    {"url": LOW, "bitrate": 256000, "content_type": "video/mp4"},
    {"url": MID, "bitrate": 832000, "content_type": "video/mp4"},
    {"url": HIGH, "bitrate": 2176000, "content_type": "video/mp4"},
]


async def test_without_a_limit_the_best_variant_wins():
    variant, size = await select_variant(_item(VARIANTS))
    assert variant.url == HIGH
    assert size == 0  # unknown - nobody measured it


async def test_the_biggest_variant_that_fits_is_taken():
    sizes = {HIGH: 60 * 1024 * 1024, MID: 12 * 1024 * 1024, LOW: 3 * 1024 * 1024}
    variant, size = await select_variant(
        _item(VARIANTS), max_bytes=20 * 1024 * 1024, probe=_probe(sizes)
    )
    assert variant.url == MID
    assert size == 12 * 1024 * 1024


async def test_the_biggest_variant_is_taken_when_it_fits():
    sizes = {HIGH: 60 * 1024 * 1024, MID: 12 * 1024 * 1024}
    variant, size = await select_variant(
        _item(VARIANTS), max_bytes=100 * 1024 * 1024, probe=_probe(sizes)
    )
    assert variant.url == HIGH
    assert size == 60 * 1024 * 1024


async def test_a_video_that_is_too_big_everywhere_keeps_its_smallest_variant():
    """Nothing fits, but the video is not thrown away: the smallest one
    is downloaded anyway."""
    sizes = {HIGH: 60 * 1024 * 1024, MID: 40 * 1024 * 1024, LOW: 30 * 1024 * 1024}
    variant, size = await select_variant(
        _item(VARIANTS), max_bytes=10 * 1024 * 1024, probe=_probe(sizes)
    )
    assert variant is not None
    assert variant.url == LOW
    assert size == 30 * 1024 * 1024


async def test_a_strict_limit_skips_the_video():
    """With max_size_strict the limit is a hard rule."""
    sizes = {HIGH: 60 * 1024 * 1024, MID: 40 * 1024 * 1024, LOW: 30 * 1024 * 1024}
    variant, size = await select_variant(
        _item(VARIANTS),
        max_bytes=10 * 1024 * 1024,
        probe=_probe(sizes),
        strict=True,
    )
    assert variant is None
    assert size == 0


async def test_only_the_variants_that_are_measured_are_asked_about():
    """The best variant is asked about first; the rest is asked about in
    one go, so a tweet costs at most two round trips."""
    asked: list[str] = []

    async def probe(url: str) -> int | None:
        asked.append(url)
        return {HIGH: 60_000_000, MID: 12_000_000}.get(url)

    variant, _ = await select_variant(
        _item(VARIANTS), max_bytes=20_000_000, probe=probe
    )
    assert variant.url == MID
    assert asked[0] == HIGH  # the best one first
    assert set(asked[1:]) == {MID, LOW}  # the rest in parallel


async def test_a_variant_that_fits_costs_a_single_request():
    asked: list[str] = []

    async def probe(url: str) -> int | None:
        asked.append(url)
        return 12_000_000

    variant, _ = await select_variant(
        _item(VARIANTS), max_bytes=20_000_000, probe=probe
    )
    assert variant.url == HIGH
    assert asked == [HIGH]


async def test_the_size_is_estimated_when_the_probe_fails():
    """bitrate x duration is rough, but better than nothing."""
    variant, size = await select_variant(
        _item(VARIANTS, duration_ms=10_000),
        max_bytes=2_000_000,
        probe=_probe({}),
    )
    # high: 2176000 bit/s x 10s = 2.7 MB (too big)
    # mid:   832000 bit/s x 10s = 1.0 MB (fits)
    assert variant.url == MID
    assert size == 1_040_000


async def test_an_unknown_size_does_not_stop_the_video():
    """Without a bitrate and without a probe the size cannot be told -
    the video is downloaded anyway instead of being skipped."""
    variant, size = await select_variant(
        _item([{"url": HIGH, "bitrate": 0, "content_type": "video/mp4"}]),
        max_bytes=1024,
        probe=_probe({}),
    )
    assert variant is not None
    assert size == 0


async def test_a_variant_with_a_known_size_is_not_measured_again():
    """The API sometimes knows the size - then no request is needed."""
    variants = [
        {
            "url": HIGH,
            "bitrate": 2176000,
            "content_type": "video/mp4",
            "content_length": 60_000_000,
        },
        {"url": MID, "bitrate": 832000, "content_type": "video/mp4"},
    ]
    asked: list[str] = []

    async def probe(url: str) -> int | None:
        asked.append(url)
        return 12_000_000

    variant, size = await select_variant(
        _item(variants), max_bytes=20_000_000, probe=probe
    )
    assert variant.url == MID
    assert asked == [MID]  # the high variant was already known to be too big


async def test_hls_cannot_be_limited():
    tweet = SimpleNamespace(
        media=[
            _video(
                [
                    {
                        "url": "https://v/x.m3u8",
                        "bitrate": 0,
                        "content_type": "application/x-mpegURL",
                    }
                ]
            )
        ]
    )
    item = extract_media(tweet)[0]
    # a hard limit cannot be kept -> the video is skipped
    variant, size = await select_variant(
        item, max_bytes=1024, probe=_probe({}), strict=True
    )
    assert variant is None

    # without a strict limit the stream is used, its size is unknown
    variant, size = await select_variant(item, max_bytes=1024, probe=_probe({}))
    assert variant.kind == HLS
    assert size == 0

    # without a limit it is still used
    variant, _ = await select_variant(item)
    assert variant.kind == HLS


async def test_the_mp4_variants_win_over_hls():
    tweet = SimpleNamespace(
        media=[
            _video(
                [
                    {
                        "url": "https://v/x.m3u8",
                        "bitrate": 0,
                        "content_type": "application/x-mpegURL",
                    },
                    {"url": HIGH, "bitrate": 2176000, "content_type": "video/mp4"},
                ]
            )
        ]
    )
    item = extract_media(tweet)[0]
    variant, _ = await select_variant(item)
    assert variant.kind == MP4
    assert variant.url == HIGH
