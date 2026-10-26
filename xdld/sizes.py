"""Measuring how big a remote file is before it is downloaded.

The X API does not tell us the size of a video variant - only its bitrate.
To honour a size limit we ask the CDN for the ``Content-Length`` of the
candidate variants (a HEAD request, no body) and pick the best one that still
fits into the limit.

The files on the CDN never change, so a size that was measured once stays
valid: the answers are kept in memory and, when a state database is around,
in it as well.

A probe is a tiny request (a few hundred bytes of headers, no body), but it
costs a round trip, so the probe reuses one HTTP client for the whole run
(keep alive) and counts what it spent.
"""

from __future__ import annotations

import httpx

from xdld.download import USER_AGENT


def _content_length(response: httpx.Response) -> int:
    """The ``Content-Length`` of a response (0 when there is none)."""
    length = response.headers.get("content-length", "")
    return int(length) if length.isdigit() else 0


def _range_total(response: httpx.Response) -> int:
    """The total size announced by a ``Content-Range`` header."""
    header = response.headers.get("content-range", "")
    if "/" in header:
        total = header.rsplit("/", 1)[-1].strip()
        if total.isdigit():
            return int(total)
    if response.status_code == 200:
        return _content_length(response)
    return 0


def _header_bytes(response: httpx.Response) -> int:
    """How many bytes of headers a request/response pair used."""
    total = 0
    for headers in (response.request.headers, response.headers):
        for key, value in headers.raw:
            total += len(key) + len(value) + 4
    return total


async def _measure(client: httpx.AsyncClient, url: str) -> tuple[int, int, int]:
    """The size of the file behind ``url`` (0 when unknown).

    Returns the size, the number of requests and the number of bytes of
    headers that were used.
    """
    try:
        response = await client.head(url)
    except Exception:  # noqa: BLE001 - a failed HEAD is not fatal
        return 0, 1, 0
    if response.status_code < 400:
        size = _content_length(response)
        if size:
            return size, 1, _header_bytes(response)

    # Some servers do not answer a HEAD request with a length: ask for
    # the first byte and read the total size out of the range header.
    try:
        async with client.stream(
            "GET", url, headers={"Range": "bytes=0-0"}
        ) as response:
            if response.status_code < 400:
                size = _range_total(response)
                if size:
                    return size, 2, _header_bytes(response)
    except Exception:  # noqa: BLE001 - the size stays unknown
        return 0, 2, 0
    return 0, 2, 0


async def remote_size(
    url: str,
    proxy: str | None = None,
    timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
    stats: dict | None = None,
) -> int | None:
    """The size of the file behind ``url`` in bytes (None when unknown).

    When a ``client`` is given it is used (and kept open), so a whole run can
    share one connection pool. ``stats`` collects the cost of the request.
    """
    if client is not None:
        size, requests, spent = await _measure(client, url)
    else:
        timeout_cfg = httpx.Timeout(
            connect=15.0, read=timeout, write=timeout, pool=15.0
        )
        async with httpx.AsyncClient(
            proxy=proxy or None,
            follow_redirects=True,
            timeout=timeout_cfg,
            headers={"User-Agent": USER_AGENT},
        ) as own:
            size, requests, spent = await _measure(own, url)
    if stats is not None:
        stats["requests"] = stats.get("requests", 0) + requests
        stats["bytes"] = stats.get("bytes", 0) + spent
    return size or None


class SizeProbe:
    """Measures remote files and remembers the answers.

    The probe is passed to :func:`xdld.media.select_variant` as a callable,
    so the selection logic does not have to know about the network. One HTTP
    client is shared by all the probes of a run, which keeps the connection
    alive and saves a TLS handshake per request.
    """

    def __init__(
        self,
        proxy: str | None = None,
        store=None,
        log=None,
        timeout: float = 30.0,
    ) -> None:
        self.proxy = proxy
        self.store = store
        self.log = log
        self.timeout = timeout
        self.requests = 0  # how many requests went to the CDN
        self.bytes = 0  # how many bytes of headers they used
        self._cache: dict[str, int] = {}
        self._client: httpx.AsyncClient | None = None
        self._stats: dict = {"requests": 0, "bytes": 0}

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                proxy=self.proxy or None,
                follow_redirects=True,
                timeout=httpx.Timeout(
                    connect=15.0,
                    read=self.timeout,
                    write=self.timeout,
                    pool=15.0,
                ),
                headers={"User-Agent": USER_AGENT},
            )
        return self._client

    async def aclose(self) -> None:
        """Close the HTTP client (if one was opened)."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "SizeProbe":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def size(self, url: str) -> int | None:
        """The size of the file behind ``url`` (None when unknown)."""
        if not url:
            return None
        known = self._cache.get(url)
        if known:
            return known
        if self.store is not None:
            known = self.store.known_size(url)
            if known:
                self._cache[url] = known
                return known

        size = await remote_size(
            url,
            proxy=self.proxy,
            timeout=self.timeout,
            client=self._get_client(),
            stats=self._stats,
        )
        if size:
            self._cache[url] = size
            if self.store is not None:
                self.store.remember_size(url, size)
            if self.log:
                self.log(f"size {url.rsplit('/', 1)[-1]}: {size} bytes")
        return size

    async def __call__(self, url: str) -> int | None:
        """The probe is used as a plain callable by the selection logic."""
        return await self.size(url)

    def report(self) -> str:
        """What the probing cost (for the summary at the end of a run)."""
        requests = self._stats.get("requests", 0)
        spent = self._stats.get("bytes", 0)
        return f"{requests} request(s), {spent / 1024:.1f} KB of headers"
