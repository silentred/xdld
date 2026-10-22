"""The `export` and `record` commands (aria2c hand off)."""

from __future__ import annotations

from types import SimpleNamespace

from xdld import cli
from xdld import client as client_mod
from xdld.store import Store


class FakeMedia:
    def __init__(self, media_id, variants):
        self.id = media_id
        self.width = 1280
        self.height = 720
        self.type = "video"
        self.video_info = {"variants": variants}


class FakeTweet:
    def __init__(self, tweet_id="111", media_id="m1"):
        self.id = tweet_id
        self.text = "hello"
        self.created_at = "2026-01-01T00:00:00Z"
        self.user = SimpleNamespace(screen_name="alice")
        self.media = [
            FakeMedia(
                media_id,
                [
                    {
                        "url": "https://v/low.mp4",
                        "bitrate": 832000,
                        "content_type": "video/mp4",
                    },
                    {
                        "url": "https://v/high.mp4",
                        "bitrate": 2176000,
                        "content_type": "video/mp4",
                    },
                ],
            )
        ]


def _patch(monkeypatch, tweets):
    async def fake_session(c):
        return client_mod.SessionInfo(
            True, user_id="42", screen_name="me", name="Me", detail="ok"
        )

    async def fake_resolve(client, screen_name):
        return "42", "me"

    async def fake_likes(client, user_id, opts, store=None, log=print, state=None):
        for tweet in tweets:
            if state is not None:
                state.current_page = 1
            yield tweet

    monkeypatch.setattr(client_mod, "build_client", lambda cfg: object())
    monkeypatch.setattr(client_mod, "check_session", fake_session)
    monkeypatch.setattr(client_mod, "resolve_user_id", fake_resolve)
    monkeypatch.setattr("xdld.likes.iter_likes", fake_likes)


def test_export_writes_an_aria2_manifest(monkeypatch, tmp_path):
    _patch(monkeypatch, [FakeTweet()])
    manifest = tmp_path / "aria2.txt"
    args = cli.build_parser().parse_args(
        [
            "export",
            "--config",
            str(tmp_path / "no.toml"),
            "--out",
            str(tmp_path / "dl"),
            "--db",
            str(tmp_path / "s.db"),
            "-o",
            str(manifest),
        ]
    )

    assert cli.cmd_export(args) == 0
    text = manifest.read_text()
    assert "https://v/high.mp4" in text
    assert "  out=111_alice_0.mp4" in text
    assert '"tweet_id": "111"' in text
    assert '"page": 1' in text


def test_export_prints_the_last_tweet_and_its_page(monkeypatch, tmp_path, capsys):
    _patch(monkeypatch, [FakeTweet()])
    manifest = tmp_path / "aria2.txt"
    args = cli.build_parser().parse_args(
        [
            "export",
            "--config",
            str(tmp_path / "no.toml"),
            "--out",
            str(tmp_path / "dl"),
            "--db",
            str(tmp_path / "s.db"),
            "-o",
            str(manifest),
        ]
    )
    assert cli.cmd_export(args) == 0
    out = capsys.readouterr().out
    assert "tweet 111 (page 1)" in out
    assert "--since-tweet 111" in out
    assert "--from-page 1" in out


def test_record_marks_the_manifest_as_downloaded(monkeypatch, tmp_path):
    _patch(monkeypatch, [FakeTweet()])
    dl = tmp_path / "dl"
    manifest = tmp_path / "aria2.txt"
    db = tmp_path / "s.db"

    export_args = cli.build_parser().parse_args(
        [
            "export",
            "--config",
            str(tmp_path / "no.toml"),
            "--out",
            str(dl),
            "--db",
            str(db),
            "-o",
            str(manifest),
        ]
    )
    assert cli.cmd_export(export_args) == 0

    # aria2c downloaded the file
    (dl / "111_alice_0.mp4").write_bytes(b"x" * 100)

    record_args = cli.build_parser().parse_args(
        ["record", "--config", str(tmp_path / "no.toml"), "--db", str(db), "-i", str(manifest)]
    )
    assert cli.cmd_record(record_args) == 0

    with Store(db) as store:
        assert store.media_status("m1") == "done"
        assert store.media_path("m1").endswith("111_alice_0.mp4")


def test_export_skips_what_is_already_recorded(monkeypatch, tmp_path):
    _patch(monkeypatch, [FakeTweet()])
    dl = tmp_path / "dl"
    manifest = tmp_path / "aria2.txt"
    db = tmp_path / "s.db"

    common = [
        "export",
        "--config",
        str(tmp_path / "no.toml"),
        "--out",
        str(dl),
        "--db",
        str(db),
        "-o",
        str(manifest),
    ]
    assert cli.cmd_export(cli.build_parser().parse_args(common)) == 0

    (dl / "111_alice_0.mp4").write_bytes(b"x" * 100)
    record_args = cli.build_parser().parse_args(
        ["record", "--config", str(tmp_path / "no.toml"), "--db", str(db), "-i", str(manifest)]
    )
    assert cli.cmd_record(record_args) == 0

    manifest.unlink()
    assert cli.cmd_export(cli.build_parser().parse_args(common)) == 0
    assert "https://v/high.mp4" not in manifest.read_text()


def test_record_reports_missing_files(monkeypatch, tmp_path, capsys):
    _patch(monkeypatch, [FakeTweet()])
    dl = tmp_path / "dl"
    manifest = tmp_path / "aria2.txt"
    db = tmp_path / "s.db"

    export_args = cli.build_parser().parse_args(
        [
            "export",
            "--config",
            str(tmp_path / "no.toml"),
            "--out",
            str(dl),
            "--db",
            str(db),
            "-o",
            str(manifest),
        ]
    )
    assert cli.cmd_export(export_args) == 0

    record_args = cli.build_parser().parse_args(
        ["record", "--config", str(tmp_path / "no.toml"), "--db", str(db), "-i", str(manifest)]
    )
    assert cli.cmd_record(record_args) == 0
    out = capsys.readouterr().out
    assert "missing" in out

    with Store(db) as store:
        assert store.media_status("m1") is None


def test_record_prints_the_last_tweet_and_its_page(monkeypatch, tmp_path, capsys):
    """The user needs the last id + page to start the next round."""
    _patch(monkeypatch, [FakeTweet()])
    dl = tmp_path / "dl"
    manifest = tmp_path / "aria2.txt"
    db = tmp_path / "s.db"

    export_args = cli.build_parser().parse_args(
        [
            "export",
            "--config",
            str(tmp_path / "no.toml"),
            "--out",
            str(dl),
            "--db",
            str(db),
            "-o",
            str(manifest),
        ]
    )
    assert cli.cmd_export(export_args) == 0
    capsys.readouterr()

    (dl / "111_alice_0.mp4").write_bytes(b"x" * 100)
    record_args = cli.build_parser().parse_args(
        ["record", "--config", str(tmp_path / "no.toml"), "--db", str(db), "-i", str(manifest)]
    )
    assert cli.cmd_record(record_args) == 0

    out = capsys.readouterr().out
    assert "tweet 111 (page 1)" in out
    assert "xdld export --since-tweet 111" in out
    assert "xdld export --from-page 1" in out
