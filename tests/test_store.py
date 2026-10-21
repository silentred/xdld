from xdld.store import Store


def test_tweet_dedup(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert store.known_tweet("1") is False
        store.record_tweet("1", author="alice")
        assert store.known_tweet("1") is True
        assert store.stats()["tweets_seen"] == 1


def test_media_status_lifecycle(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert store.media_status("k1") is None
        store.record_media(
            "k1", "1", "m1", "video", "https://v/x.mp4", status="done",
            path="/tmp/x.mp4", size=1234,
        )
        assert store.media_status("k1") == "done"
        assert store.media_path("k1") == "/tmp/x.mp4"
        assert store.stats()["media_done"] == 1
        assert store.stats()["bytes"] == 1234


def test_failed_media_can_be_retried(tmp_path):
    with Store(tmp_path / "s.db") as store:
        store.record_media("k1", "1", "m1", "video", "u", status="failed", error="boom")
        assert store.media_status("k1") == "failed"
        store.record_media("k1", "1", "m1", "video", "u", status="done", size=10)
        assert store.media_status("k1") == "done"
        assert store.stats()["media_failed"] == 0
        assert store.stats()["media_done"] == 1


def test_state_roundtrip(tmp_path):
    with Store(tmp_path / "s.db") as store:
        assert store.get_state("cursor") is None
        store.set_state("cursor", "abc")
        assert store.get_state("cursor") == "abc"
        store.set_state("cursor", "def")
        assert store.get_state("cursor") == "def"
