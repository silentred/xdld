"""The downloader must survive interruptions and resume where it stopped."""

from __future__ import annotations

import asyncio
import http.server
import threading
from typing import ClassVar

import httpx
import pytest

from xdld.download import DownloadError, download_file

PAYLOAD = b"0123456789" * 1000  # 10 000 bytes


class _Handler(http.server.BaseHTTPRequestHandler):
    payload = PAYLOAD
    support_range = True
    requests: ClassVar[list[str | None]] = []

    def do_GET(self):
        type(self).requests.append(self.headers.get("Range"))
        if not self.path.startswith("/v.mp4"):
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        rng = self.headers.get("Range")
        body = type(self).payload
        if rng and type(self).support_range:
            start = int(rng.split("=")[1].split("-")[0])
            chunk = body[start:]
            self.send_response(206)
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header(
                "Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}"
            )
            self.end_headers()
            self.wfile.write(chunk)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # silence the server
        pass


@pytest.fixture
def server():
    _Handler.requests = []
    _Handler.support_range = True
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}/v.mp4"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_download_writes_the_file(server, tmp_path):
    dest = tmp_path / "video.mp4"
    size = asyncio.run(download_file(server, dest, proxy=""))
    assert size == len(PAYLOAD)
    assert dest.read_bytes() == PAYLOAD
    assert not (tmp_path / "video.mp4.part").exists()


def test_download_resumes_a_partial_file(server, tmp_path):
    dest = tmp_path / "video.mp4"
    part = tmp_path / "video.mp4.part"
    part.write_bytes(PAYLOAD[:4000])

    size = asyncio.run(download_file(server, dest, proxy=""))

    assert size == len(PAYLOAD)
    assert dest.read_bytes() == PAYLOAD
    # the server got a Range request
    assert _Handler.requests == ["bytes=4000-"]


def test_download_starts_over_when_the_server_ignores_range(server, tmp_path):
    _Handler.support_range = False
    dest = tmp_path / "video.mp4"
    part = tmp_path / "video.mp4.part"
    part.write_bytes(PAYLOAD[:4000])

    size = asyncio.run(download_file(server, dest, proxy=""))

    assert size == len(PAYLOAD)
    assert dest.read_bytes() == PAYLOAD


def test_download_keeps_the_part_file_on_failure(tmp_path):
    dest = tmp_path / "video.mp4"
    with pytest.raises(httpx.ConnectError):
        asyncio.run(download_file("http://127.0.0.1:1/none.mp4", dest, proxy=""))
    # nothing was received, so there is no part file to resume
    assert not (tmp_path / "video.mp4.part").exists()


def test_download_reports_http_errors(server, tmp_path):
    dest = tmp_path / "video.mp4"
    missing = server.replace("/v.mp4", "/missing.mp4")
    with pytest.raises(DownloadError) as exc:
        asyncio.run(download_file(missing, dest, proxy=""))
    assert "404" in str(exc.value)
    assert not dest.exists()
