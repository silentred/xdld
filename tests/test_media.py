from types import SimpleNamespace

from xdld.media import HLS, IMAGE, MP4, best_variant, extract_media


def _video(variants, media_id="1", width=1280, height=720):
    return SimpleNamespace(
        id=media_id,
        width=width,
        height=height,
        video_info={"variants": variants},
    )


def test_picks_highest_bitrate_mp4():
    tweet = SimpleNamespace(
        media=[
            _video(
                [
                    {"url": "https://v/low.mp4", "bitrate": 832000, "content_type": "video/mp4"},
                    {"url": "https://v/high.mp4", "bitrate": 2176000, "content_type": "video/mp4"},
                    {"url": "https://v/mid.mp4", "bitrate": 1382400, "content_type": "video/mp4"},
                ]
            )
        ]
    )
    items = extract_media(tweet)
    assert len(items) == 1
    assert items[0].kind == "video"
    assert best_variant(items[0]).url == "https://v/high.mp4"
    assert best_variant(items[0]).ext == ".mp4"


def test_hls_is_only_used_as_fallback():
    tweet = SimpleNamespace(
        media=[
            _video(
                [
                    {"url": "https://v/x.m3u8", "bitrate": 0, "content_type": "application/x-mpegURL"},
                ]
            )
        ]
    )
    item = extract_media(tweet)[0]
    variant = best_variant(item)
    assert variant.kind == HLS
    assert variant.ext == ".mp4"


def test_photo_is_detected_as_image():
    photo = SimpleNamespace(
        id="7",
        width=2048,
        height=1536,
        source_url="https://p.example/orig",
        media_url="https://p.example/small",
    )
    tweet = SimpleNamespace(media=[photo])
    item = extract_media(tweet)[0]
    assert item.kind == IMAGE
    assert item.image_url == "https://p.example/orig"
    assert best_variant(item) is None


def test_animated_gif_is_a_video_kind():
    gif = SimpleNamespace(
        id="9",
        type="animated_gif",
        width=320,
        height=240,
        video_info={
            "variants": [
                {"url": "https://g/g.mp4", "bitrate": 0, "content_type": "video/mp4"}
            ]
        },
    )
    tweet = SimpleNamespace(media=[gif])
    item = extract_media(tweet)[0]
    assert item.kind == "gif"
    assert best_variant(item).kind == MP4


def test_tweet_without_media():
    assert extract_media(SimpleNamespace(media=None)) == []
    assert extract_media(SimpleNamespace(media=[])) == []


def test_video_without_video_info_is_not_treated_as_an_image():
    """A video whose video_info is missing must not fall back to its thumbnail."""
    video = SimpleNamespace(
        id="11",
        type="video",
        width=1280,
        height=720,
        video_info=None,
        source_url="https://p.example/thumb.jpg",
        media_url="https://p.example/thumb.jpg",
    )
    tweet = SimpleNamespace(media=[video])
    item = extract_media(tweet)[0]
    assert item.kind == "video"
    assert item.variants == []
    assert best_variant(item) is None


def test_image_extension():
    from xdld.media import image_extension

    assert image_extension("https://p.example/a/b.jpg?x=1") == ".jpg"
    assert image_extension("https://p.example/a/b.png") == ".png"
    assert image_extension("https://p.example/a/b") == ".jpg"
