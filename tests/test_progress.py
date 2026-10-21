"""Tests for the start-point options and the live progress display."""

from __future__ import annotations

import asyncio
import io

from xdld.likes import LikesOptions, WalkState, iter_likes
from xdld.progress import Progress, human_bytes, human_time, with_heartbeat


class FakeTweet:
    def __init__(self, tweet_id):
        self.id = tweet_id
        self.media = []
        self.user = None
        self.text = ""
        self.created_at = ""


class FakeResult:
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
        self.calls = 0

    async def get_user_tweets(self, user_id, tweet_type, count=40, cursor=None):
        self.calls += 1
        return self._pages["first"]


def _three_pages():
    page3 = FakeResult([FakeTweet("5"), FakeTweet("6")])
    page2 = FakeResult(
        [FakeTweet("3"), FakeTweet("4")], next_cursor="c2", pages={"c2": page3}
    )
    page1 = FakeResult(
        [FakeTweet("1"), FakeTweet("2")], next_cursor="c1", pages={"c1": page2}
    )
    return {"first": page1}


# --------------------------------------------------------------- start point
async def test_since_tweet_starts_at_that_tweet():
    client = FakeClient(_three_pages())
    logs: list[str] = []
    opts = LikesOptions(since_tweet="4", sleep_min=0, sleep_max=0)
    got = [t.id async for t in iter_likes(client, "42", opts, log=logs.append)]
    assert got == ["4", "5", "6"]
    assert any("start point reached" in line for line in logs)


async def test_since_tweet_that_does_not_exist():
    client = FakeClient(_three_pages())
    logs: list[str] = []
    opts = LikesOptions(since_tweet="999", sleep_min=0, sleep_max=0)
    got = [t.id async for t in iter_likes(client, "42", opts, log=logs.append)]
    assert got == []
    assert any("was not found" in line for line in logs)


async def test_from_page_skips_the_earlier_pages():
    client = FakeClient(_three_pages())
    logs: list[str] = []
    opts = LikesOptions(from_page=2, sleep_min=0, sleep_max=0)
    got = [t.id async for t in iter_likes(client, "42", opts, log=logs.append)]
    assert got == ["3", "4", "5", "6"]
    assert any("skipping page 1" in line for line in logs)


async def test_from_page_and_since_tweet_combined():
    client = FakeClient(_three_pages())
    opts = LikesOptions(from_page=2, since_tweet="5", sleep_min=0, sleep_max=0)
    got = [t.id async for t in iter_likes(client, "42", opts)]
    assert got == ["5", "6"]


async def test_max_likes_counts_only_the_yielded_tweets():
    client = FakeClient(_three_pages())
    opts = LikesOptions(since_tweet="2", max_likes=2, sleep_min=0, sleep_max=0)
    got = [t.id async for t in iter_likes(client, "42", opts)]
    assert got == ["2", "3"]


async def test_walk_state_reports_the_skipped_tweets():
    client = FakeClient(_three_pages())
    state = WalkState()
    opts = LikesOptions(since_tweet="5", max_likes=1, sleep_min=0, sleep_max=0)
    got = [t.id async for t in iter_likes(client, "42", opts, state=state)]
    assert got == ["5"]
    assert state.skipped == 4
    assert state.start_found is True
    assert state.pages_walked == 3  # tweet 5 lives on the third page


# ------------------------------------------------------------------ progress
def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(5 * 1024 * 1024) == "5.0 MB"
    assert human_bytes(3 * 1024**3) == "3.0 GB"


def test_human_time():
    assert human_time(5) == "00:05"
    assert human_time(65) == "01:05"
    assert human_time(3725) == "1:02:05"


def test_progress_writes_a_live_line():
    class FakeTTY(io.StringIO):
        def isatty(self):
            return True

    stream = FakeTTY()
    progress = Progress(log=lambda m: None, stream=stream, min_interval=0)
    progress.counter(tweets=12, ok=3)
    progress.file_start("clip.mp4", total=1000)
    progress.file_progress(500, 1000)
    progress.file_end()

    text = stream.getvalue()
    assert "clip.mp4" in text
    assert "50%" in text
    assert "tweets 12" in text
    # on a terminal the line is rewritten in place
    assert "\r" in text


def test_progress_writes_periodic_lines_when_not_a_terminal():
    stream = io.StringIO()
    progress = Progress(log=lambda m: None, stream=stream, min_interval=0)
    progress.file_start("clip.mp4", total=1000)
    progress.file_progress(500, 1000)
    text = stream.getvalue()
    assert text.count("\n") == 1  # one plain line, then throttled
    assert "clip.mp4" in text


def test_progress_note_keeps_the_live_line():
    stream = io.StringIO()
    messages: list[str] = []
    progress = Progress(log=messages.append, stream=stream, min_interval=0)
    progress.file_start("clip.mp4", total=10)
    progress.note("hello")
    assert messages == ["hello"]


def test_progress_quiet_writes_nothing():
    stream = io.StringIO()
    progress = Progress(log=lambda m: None, stream=stream, enabled=False)
    progress.counter(tweets=1)
    progress.file_start("x.mp4")
    progress.file_progress(10, 20)
    progress.finish()
    assert stream.getvalue() == ""


async def test_with_heartbeat_beats_while_waiting():
    stream = io.StringIO()
    progress = Progress(log=lambda m: None, stream=stream, heartbeat=0.05)

    async def slow():
        await asyncio.sleep(0.2)
        return "done"

    result = await with_heartbeat(slow(), progress, "the video")
    assert result == "done"
    assert "waiting for the video" in stream.getvalue()


async def test_with_heartbeat_propagates_errors():
    progress = Progress(log=lambda m: None, stream=io.StringIO(), heartbeat=0.05)

    async def boom():
        raise ValueError("nope")

    try:
        await with_heartbeat(boom(), progress, "x")
    except ValueError as exc:
        assert str(exc) == "nope"
    else:  # pragma: no cover
        raise AssertionError("the error should have been raised")
