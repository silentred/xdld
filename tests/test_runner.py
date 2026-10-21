"""Tests for the likes pagination and the whole download pipeline.

The X client is faked: the point is to verify our own logic (pagination,
dedup, variant selection, file naming), not X itself.
"""

from __future__ import annotations

from types import SimpleNamespace

from xdld.config import Config
from xdld.likes import LikesOptions, iter_likes
from xdld.runner import run_download


# --------------------------------------------------------------- fake objects
class FakeMedia:
    def __init__(self, media_id, variants, width=1280, height=720, kind="video"):
        self.id = media_id
        self.width = width
        self.height = height
        self.type = kind
        self.video_info = {"variants": variants}


class FakeTweet:
    def __init__(self, tweet_id, media, author="alice", text="hello", created_at="2026-01-01T00:00:00Z"):
        self.id = tweet_id
        self.media = media
        self.user = SimpleNamespace(screen_name=author)
        self.text = text
        self.created_at = created_at


class FakeResult:
    """Mimics twikit's Result: a page of items plus a next cursor."""

    def __init__(self, items, next_cursor=None, pages=None):
        self._items = items
        self.next_cursor = next_cursor
        self._pages = pages or {}

    def __iter__(self):
        return iter(self._items)

    async def next(self):
        if not self.next_cursor:
            return FakeResult([])
        return self._pages[self.next_cursor]


class FakeClient:
    def __init__(self, pages):
        self._pages = pages
        self.calls = []

    async def get_user_tweets(self, user_id, tweet_type, count=40, cursor=None):
        self.calls.append((user_id, tweet_type, count))
        return self._pages["first"]


def _video(media_id, high="https://v/high.mp4", low="https://v/low.mp4"):
    return FakeMedia(
        media_id,
        [
            {"url": low, "bitrate": 832000, "content_type": "video/mp4"},
            {"url": high, "bitrate": 2176000, "content_type": "video/mp4"},
        ],
    )


# -------------------------------------------------------------------- tests
async def test_iter_likes_follows_the_cursor():
    page1 = FakeResult([FakeTweet("1", [])], next_cursor="c1", pages={"c1": FakeResult([FakeTweet("2", [])])})
    client = FakeClient({"first": page1})

    got = [t.id async for t in iter_likes(client, "42", LikesOptions())]
    assert got == ["1", "2"]


async def test_iter_likes_respects_max_likes():
    client = FakeClient({"first": FakeResult([FakeTweet(str(i), []) for i in range(10)])})
    got = [t.id async for t in iter_likes(client, "42", LikesOptions(max_likes=3))]
    assert got == ["0", "1", "2"]


async def test_iter_likes_stops_on_known_tweets(tmp_path):
    """Known tweets are still yielded (the runner dedups per media), but the
    walk stops once `stop_after_known` of them show up back to back."""
    from xdld.store import Store

    with Store(tmp_path / "s.db") as store:
        for i in range(10):
            store.record_tweet(str(i))
        client = FakeClient(
            {"first": FakeResult([FakeTweet(str(i), []) for i in range(10)])}
        )
        got = [
            t.id
            async for t in iter_likes(
                client, "42", LikesOptions(stop_after_known=3), store
            )
        ]
    assert got == ["0", "1"]


async def test_run_download_picks_the_highest_bitrate(tmp_path, monkeypatch):
    tweet = FakeTweet("111", [_video("m1")])

    async def fake_get_user_tweets(user_id, tweet_type, count=40, cursor=None):
        assert tweet_type == "Likes"
        return FakeResult([tweet])

    downloads = []

    async def fake_download(url, dest, proxy=None, overwrite=False, on_progress=None, on_retry=None):
        downloads.append((url, str(dest), proxy))
        Path_write(dest)
        return 1024

    def Path_write(dest):
        from pathlib import Path

        p = Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 1024)

    client = SimpleNamespace(
        get_user_tweets=fake_get_user_tweets,
        is_logged_in=lambda: _true(),
        user=lambda: _user(),
        user_id=lambda: _user_id(),
    )

    monkeypatch.setattr("xdld.runner.build_client", lambda cfg: client)
    monkeypatch.setattr("xdld.runner.download_file", fake_download)

    cfg = Config(
        proxy="http://127.0.0.1:7897",
        cookies=str(tmp_path / "c.json"),
        output_dir=str(tmp_path / "out"),
        db=str(tmp_path / "s.db"),
        sleep_min=0,
        sleep_max=0,
    )
    stats = await run_download(cfg, log=lambda *a: None)

    assert stats.downloaded == 1
    assert downloads[0][0] == "https://v/high.mp4"
    assert downloads[0][2] == "http://127.0.0.1:7897"
    assert downloads[0][1].endswith("111_alice_0.mp4")


async def test_run_download_is_idempotent(tmp_path, monkeypatch):
    tweet = FakeTweet("222", [_video("m1")])
    calls = {"n": 0}

    async def fake_get_user_tweets(user_id, tweet_type, count=40, cursor=None):
        return FakeResult([tweet])

    async def fake_download(url, dest, proxy=None, overwrite=False, on_progress=None, on_retry=None):
        calls["n"] += 1
        from pathlib import Path

        p = Path(dest)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
        return 10

    client = SimpleNamespace(
        get_user_tweets=fake_get_user_tweets,
        is_logged_in=lambda: _true(),
        user=lambda: _user(),
        user_id=lambda: _user_id(),
    )
    monkeypatch.setattr("xdld.runner.build_client", lambda cfg: client)
    monkeypatch.setattr("xdld.runner.download_file", fake_download)

    cfg = Config(
        cookies=str(tmp_path / "c.json"),
        output_dir=str(tmp_path / "out"),
        db=str(tmp_path / "s.db"),
        sleep_min=0,
        sleep_max=0,
    )
    first = await run_download(cfg, log=lambda *a: None)
    second = await run_download(cfg, log=lambda *a: None)

    assert first.downloaded == 1
    assert second.downloaded == 0
    assert second.skipped == 1
    assert calls["n"] == 1


def _true():
    async def _inner():
        return True

    return _inner()


def _user():
    async def _inner():
        return SimpleNamespace(id="42", screen_name="me", name="Me")

    return _inner()


def _user_id():
    async def _inner():
        return "42"

    return _inner()
