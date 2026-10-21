"""The aria2c manifest: format, round trip and a real aria2c run."""

from __future__ import annotations

import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from xdld.manifest import (
    ManifestEntry,
    load_manifest,
    parse_manifest,
    render_manifest,
    write_manifest,
)


def _entry(url="https://video.twimg.com/x/vid.mp4", name="111_alice_0.mp4"):
    return ManifestEntry(
        url=url,
        filename=name,
        directory="/tmp/downloads",
        tweet_id="111",
        media_id="222",
        kind="video",
        author="alice",
        created_at="2026-01-01T00:00:00Z",
    )


def test_manifest_has_the_url_and_the_output_path():
    text = render_manifest([_entry()])
    assert "https://video.twimg.com/x/vid.mp4" in text
    assert "  dir=/tmp/downloads" in text
    assert "  out=111_alice_0.mp4" in text


def test_manifest_keeps_the_metadata_in_a_comment():
    text = render_manifest([_entry()])
    assert "# xdld-meta " in text
    assert '"media_id": "222"' in text


def test_manifest_carries_the_proxy():
    text = render_manifest([_entry()], proxy="http://127.0.0.1:7897")
    assert "  all-proxy=http://127.0.0.1:7897" in text


def test_manifest_round_trip(tmp_path):
    entries = [_entry(), _entry(url="https://video.twimg.com/y/2.mp4", name="222_bob_0.mp4")]
    path = write_manifest(tmp_path / "aria2.txt", entries, proxy="http://127.0.0.1:7897")
    parsed = load_manifest(path)

    assert len(parsed) == 2
    assert parsed[0].url == entries[0].url
    assert parsed[0].filename == "111_alice_0.mp4"
    assert parsed[0].directory == "/tmp/downloads"
    assert parsed[0].tweet_id == "111"
    assert parsed[0].media_id == "222"
    assert parsed[0].author == "alice"
    assert parsed[1].filename == "222_bob_0.mp4"


def test_parse_manifest_understands_a_plain_aria2_file():
    text = """
# a hand written list
http://example.com/a.mp4
  dir=/tmp/x
  out=a.mp4
http://example.com/b.mp4
  dir=/tmp/x
  out=b.mp4
"""
    entries = parse_manifest(text)
    assert [e.url for e in entries] == [
        "http://example.com/a.mp4",
        "http://example.com/b.mp4",
    ]
    assert entries[1].filename == "b.mp4"
    assert entries[1].media_id == ""


def test_parse_manifest_ignores_comments_and_blank_lines():
    text = "# hello\n\nhttp://example.com/a.mp4\n  out=a.mp4\n"
    entries = parse_manifest(text)
    assert len(entries) == 1
    assert entries[0].filename == "a.mp4"


@pytest.mark.skipif(shutil.which("aria2c") is None, reason="aria2c is not installed")
def test_aria2c_downloads_the_file_to_the_given_path(tmp_path):
    """The real thing: aria2c reads our manifest and saves the file where we
    said it should go."""
    payload = b"x" * 4096
    serve_dir = tmp_path / "serve"
    serve_dir.mkdir()
    (serve_dir / "video.mp4").write_bytes(payload)

    handler = partial(SimpleHTTPRequestHandler, directory=str(serve_dir))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        target_dir = tmp_path / "dl"
        entries = [
            ManifestEntry(
                url=f"http://127.0.0.1:{port}/video.mp4",
                filename="111_alice_0.mp4",
                directory=str(target_dir),
                tweet_id="111",
                media_id="222",
            )
        ]
        manifest = write_manifest(tmp_path / "aria2.txt", entries)
        result = subprocess.run(
            ["aria2c", "-i", str(manifest), "--console-log-level=warn"],
            capture_output=True,
            check=False,
            text=True,
            timeout=60,
        )
    finally:
        server.shutdown()

    assert result.returncode == 0, result.stdout + result.stderr
    downloaded = target_dir / "111_alice_0.mp4"
    assert downloaded.exists()
    assert downloaded.read_bytes() == payload
