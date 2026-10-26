"""The CLI must not reuse a client across event loops.

``httpx`` keeps its connection pool (and the cookies) on the loop that first
used it, so every command has to do all of its async work inside a single
``asyncio.run()``. Running the second step on a fresh loop fails with
``RuntimeError: Event loop is closed``.
"""

from __future__ import annotations

import asyncio

from xdld import cli
from xdld import client as client_mod


class FakeClient:
    """Just enough of the twikit client for the CLI."""

    cookie_summary = "2 cookie(s): auth_token, ct0"

    def __init__(self) -> None:
        self.loops: list[int] = []

    async def get_user_tweets(self, *args, **kwargs):
        self.loops.append(id(asyncio.get_running_loop()))
        return []


def _patch(monkeypatch, client: FakeClient) -> None:
    loops = client.loops

    async def fake_probe(proxy, timeout=10.0):
        loops.append(id(asyncio.get_running_loop()))
        return True, "HTTP 200 from x.com"

    async def fake_session(c):
        loops.append(id(asyncio.get_running_loop()))
        return client_mod.SessionInfo(
            True, user_id="1", screen_name="me", name="Me", detail="ok"
        )

    monkeypatch.setattr(client_mod, "build_client", lambda cfg: client)
    monkeypatch.setattr(client_mod, "probe_proxy", fake_probe)
    monkeypatch.setattr(client_mod, "check_session", fake_session)


def test_check_uses_a_single_event_loop(monkeypatch, tmp_path, capsys):
    client = FakeClient()
    _patch(monkeypatch, client)

    args = cli.build_parser().parse_args(["check", "--config", str(tmp_path / "no.toml")])
    assert cli.cmd_check(args) == 0
    # proxy probe + session check + likes probe must share one loop
    assert len(set(client.loops)) == 1
    assert len(client.loops) == 3


def test_check_reports_a_broken_likes_endpoint(monkeypatch, tmp_path, capsys):
    client = FakeClient()

    async def boom(*args, **kwargs):
        raise RuntimeError("status: 404, message: 'Not Found'")

    client.get_user_tweets = boom  # type: ignore[method-assign]
    _patch(monkeypatch, client)

    args = cli.build_parser().parse_args(["check", "--config", str(tmp_path / "no.toml")])
    assert cli.cmd_check(args) == 2
    out = capsys.readouterr().out
    assert "likes   : FAIL" in out
    assert "refresh-ops" in out


def test_run_accepts_the_start_point_options(tmp_path):
    parser = cli.build_parser()
    args = parser.parse_args(
        [
            "run",
            "--config",
            str(tmp_path / "no.toml"),
            "--since-tweet",
            "1234",
            "--from-page",
            "3",
            "--quiet",
        ]
    )
    assert args.since_tweet == "1234"
    assert args.from_page == 3
    assert args.quiet is True


def test_run_passes_the_start_point_to_the_runner(monkeypatch, tmp_path):
    from xdld import runner as runner_mod

    seen = {}

    async def fake_run_download(cfg, **kwargs):
        seen.update(kwargs)
        return runner_mod.RunStats()

    monkeypatch.setattr(runner_mod, "run_download", fake_run_download)

    args = cli.build_parser().parse_args(
        [
            "run",
            "--config",
            str(tmp_path / "no.toml"),
            "--since-tweet",
            "99",
            "--from-page",
            "2",
            "--limit",
            "5",
            "--dry-run",
        ]
    )
    assert cli.cmd_run(args) == 0
    assert seen["since_tweet"] == "99"
    assert seen["from_page"] == 2
    assert seen["limit"] == 5
    assert seen["dry_run"] is True


def test_max_size_is_read_as_a_number_of_bytes():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--max-size", "50MB"])
    assert args.max_size == 50 * 1024 * 1024

    args = parser.parse_args(["export", "--max-size", "1.5GB"])
    assert args.max_size == int(1.5 * 1024**3)

    args = parser.parse_args(["likes", "--max-size", "0"])
    assert args.max_size == 0


def test_a_broken_max_size_is_reported():
    parser = cli.build_parser()
    try:
        parser.parse_args(["run", "--max-size", "50 potatoes"])
    except SystemExit:
        return
    raise AssertionError("expected the parser to reject the size")


def test_the_max_size_reaches_the_config(monkeypatch, tmp_path):
    from xdld import runner as runner_mod

    seen = {}

    async def fake_run_download(cfg, **kwargs):
        seen["max_size"] = cfg.max_size
        return runner_mod.RunStats()

    monkeypatch.setattr(runner_mod, "run_download", fake_run_download)

    args = cli.build_parser().parse_args(
        ["run", "--config", str(tmp_path / "no.toml"), "--max-size", "25MB"]
    )
    assert cli.cmd_run(args) == 0
    assert seen["max_size"] == 25 * 1024 * 1024
