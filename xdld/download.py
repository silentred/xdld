"""Downloading media through the local proxy."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from pathlib import Path

import httpx

ProgressFn = Callable[[int, int | None], None]
RetryFn = Callable[[int, str], None]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


class DownloadError(RuntimeError):
    pass


def _client(proxy: str | None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        proxy=proxy or None,
        follow_redirects=True,
        timeout=httpx.Timeout(connect=30.0, read=120.0, write=120.0, pool=30.0),
        headers={"User-Agent": USER_AGENT},
    )


async def download_file(
    url: str,
    dest: Path,
    proxy: str | None = None,
    overwrite: bool = False,
    on_progress: ProgressFn | None = None,
    on_retry: RetryFn | None = None,
    attempts: int = 3,
    retry_delay: float = 3.0,
) -> int:
    """Stream ``url`` to ``dest``. Returns the number of bytes written.

    The file is written to a ``.part`` sibling and moved into place only after
    a successful transfer, so an interrupted run never leaves a truncated
    video behind. When a ``.part`` file is left over from an earlier run the
    transfer continues with a ``Range`` request instead of starting over, and
    a dropped connection is retried the same way.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists() and not overwrite:
        return dest.stat().st_size

    tmp = dest.with_name(dest.name + ".part")
    if overwrite:
        tmp.unlink(missing_ok=True)

    last_exc: BaseException | None = None
    for attempt in range(max(1, attempts)):
        written = 0
        headers: dict[str, str] = {}
        if tmp.exists():
            written = tmp.stat().st_size
            if written:
                headers["Range"] = f"bytes={written}-"
        try:
            async with _client(proxy) as client, client.stream(
                "GET", url, headers=headers
            ) as response:
                if response.status_code == 416 and written:
                    # The local part is longer than the remote file: it is
                    # useless, start over.
                    tmp.unlink(missing_ok=True)
                    last_exc = None
                    continue
                if response.status_code >= 400:
                    raise DownloadError(f"HTTP {response.status_code} for {url}")
                if written and response.status_code != 206:
                    # The server ignored the Range header: start over.
                    written = 0
                length = response.headers.get("content-length")
                total = None
                if length and length.isdigit():
                    total = int(length) + written
                with tmp.open("ab" if written else "wb") as fh:
                    async for chunk in response.aiter_bytes(chunk_size=256 * 1024):
                        if not chunk:
                            continue
                        fh.write(chunk)
                        written += len(chunk)
                        if on_progress:
                            on_progress(written, total)
            if written == 0:
                raise DownloadError(f"Empty response for {url}")
            os.replace(tmp, dest)
            return written
        except DownloadError:
            tmp.unlink(missing_ok=True)
            raise
        except Exception as exc:  # noqa: BLE001 - the network is not always well
            last_exc = exc
            if attempt + 1 >= max(1, attempts):
                break
            if on_retry:
                on_retry(attempt + 1, f"{type(exc).__name__}: {exc}")
            await asyncio.sleep(retry_delay)

    if last_exc is not None:
        raise last_exc
    raise DownloadError(f"Could not download {url}")


def download_hls(
    url: str,
    dest: Path,
    proxy: str | None = None,
    overwrite: bool = False,
) -> int:
    """Fallback for tweets that only expose an HLS playlist.

    Uses yt-dlp when it is installed; the HLS stream is remuxed into an mp4.
    """
    try:
        import yt_dlp  # type: ignore[import-not-found]
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise DownloadError(
            "This tweet only offers an HLS stream. Install the optional "
            'dependency with: pip install "xdld[hls]"'
        ) from exc

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not overwrite:
        return dest.stat().st_size

    opts = {
        "format": "bestvideo+bestaudio/best",
        "outtmpl": str(dest.with_suffix("")),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "merge_output_format": "mp4",
        "overwrites": True,
    }
    if proxy:
        opts["proxy"] = proxy
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    if not dest.exists():
        candidates = sorted(dest.parent.glob(dest.stem + ".*"))
        if not candidates:
            raise DownloadError(f"yt-dlp produced no file for {url}")
        candidates[0].replace(dest)
    return dest.stat().st_size


async def download_hls_async(
    url: str,
    dest: Path,
    proxy: str | None = None,
    overwrite: bool = False,
) -> int:
    return await asyncio.to_thread(download_hls, url, dest, proxy, overwrite)
