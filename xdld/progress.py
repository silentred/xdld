"""Live progress output.

While the tool walks the likes and downloads videos there is not much to
print, so a single line is rewritten in place (on a terminal) to show what
is going on:

    1234.mp4  12.3/45.6 MB 27%  3.1 MB/s  | tweets 120  videos 34  ok 28  1.2 GB  03:12

On a terminal the line is erased and rewritten; when the output is piped
into a file the same information is written as a normal line every few
seconds. A heartbeat makes sure that nothing is printed for too long, so a
slow request never looks like a hang.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Awaitable
from typing import Any


def human_bytes(num: float) -> str:
    if num < 1024:
        return f"{num:.0f} B"
    for unit in ("KB", "MB", "GB", "TB"):
        num /= 1024
        if num < 1024 or unit == "TB":
            return f"{num:.1f} {unit}"
    return f"{num:.1f} TB"


def human_time(seconds: float) -> str:
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class Progress:
    """A one line status display that is rewritten in place."""

    def __init__(
        self,
        log=print,
        stream=None,
        min_interval: float = 0.15,
        heartbeat: float = 60.0,
        enabled: bool = True,
    ) -> None:
        self.log = log
        self.stream = stream if stream is not None else sys.stderr
        self.tty = bool(getattr(self.stream, "isatty", lambda: False)())
        self.min_interval = min_interval
        self.heartbeat = heartbeat
        self.enabled = enabled
        self.started = time.monotonic()
        self._file: dict[str, Any] | None = None
        self._counters: dict[str, Any] = {}
        self._last_draw = 0.0
        self._last_beat = time.monotonic()
        self._last_plain = 0.0
        self._last_speed = (0.0, time.monotonic())
        self._rate = 0.0

    # -- state -------------------------------------------------------------
    def counter(self, **kwargs: Any) -> None:
        """Update the counters shown at the end of the live line."""
        self._counters.update(kwargs)
        self.draw()

    def file_start(self, name: str, total: int | None = None) -> None:
        self._file = {
            "name": name,
            "written": 0,
            "total": total,
            "t0": time.monotonic(),
        }
        self.draw(force=True)

    def file_progress(self, written: int, total: int | None = None) -> None:
        if self._file is None:
            return
        self._file["written"] = written
        if total:
            self._file["total"] = total
        self._update_rate(written)
        self.draw()

    def file_end(self) -> None:
        self._file = None
        self._rate = 0.0
        self._last_speed = (0.0, time.monotonic())
        self._clear()

    # -- output ------------------------------------------------------------
    def note(self, message: str) -> None:
        """Print a message without breaking the live line."""
        self._clear()
        self.log(message)
        self._sync()
        if self.tty:
            self.draw(force=True)

    def retry(self, attempt: int, reason: str) -> None:
        """Tell the user that the transfer is being retried."""
        self.note(f"retry {attempt}: {reason}")

    def beat(self, message: str = "still working") -> None:
        """Print a heartbeat when nothing happened for a while."""
        if not self.enabled:
            return
        now = time.monotonic()
        if now - self._last_beat < self.heartbeat:
            return
        self._last_beat = now
        self._clear()
        self._sync()
        self.stream.write(
            f"... {message} ({human_time(now - self.started)} elapsed)\n"
        )
        self.stream.flush()
        if self.tty:
            self.draw(force=True)

    def draw(self, force: bool = False) -> None:
        if not self.enabled:
            return
        now = time.monotonic()
        if not force and now - self._last_draw < self.min_interval:
            return
        self._last_draw = now
        line = self._line()
        if self.tty:
            self._sync()
            self.stream.write("\r\x1b[2K" + line[:250])
            self.stream.flush()
        elif now - self._last_plain > 5:
            # Not a terminal: one plain line every few seconds, so the log
            # stays readable when the output is redirected to a file.
            self._last_plain = now
            self._sync()
            self.stream.write(line + "\n")
            self.stream.flush()

    def finish(self) -> None:
        self._clear()

    # -- internals ---------------------------------------------------------
    def _line(self) -> str:
        bits: list[str] = []
        current = self._file
        if current:
            written = current["written"]
            total = current["total"]
            name = current["name"]
            if total:
                percent = 100.0 * written / total
                bits.append(
                    f"{name} {human_bytes(written)}/{human_bytes(total)} "
                    f"{percent:.0f}%"
                )
            else:
                bits.append(f"{name} {human_bytes(written)}")
            if self._rate > 0:
                bits.append(f"{self._rate / 1e6:.1f} MB/s")
        counters = [f"{key} {value}" for key, value in self._counters.items()]
        if counters:
            bits.append(" | ".join(counters))
        bits.append(f"elapsed {human_time(time.monotonic() - self.started)}")
        return "  ".join(bits)

    def _clear(self) -> None:
        if self.enabled and self.tty:
            self._sync()
            self.stream.write("\r\x1b[2K")
            self.stream.flush()

    @staticmethod
    def _sync() -> None:
        """Keep stdout and stderr in order when the output is redirected."""
        with contextlib.suppress(Exception):
            sys.stdout.flush()

    def _update_rate(self, written: int) -> None:
        now = time.monotonic()
        prev_bytes, prev_time = self._last_speed
        elapsed = now - prev_time
        if elapsed >= 0.4:
            instant = (written - prev_bytes) / elapsed
            self._rate = 0.7 * self._rate + 0.3 * instant
            self._last_speed = (written, now)


async def with_heartbeat(
    awaitable: Awaitable[Any],
    progress: Progress,
    label: str,
    interval: float | None = None,
) -> Any:
    """Await ``awaitable`` and print a heartbeat while it takes too long."""
    task = asyncio.ensure_future(awaitable)
    timeout = interval or progress.heartbeat
    while True:
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if done:
            return task.result()
        progress.beat(f"waiting for {label}")
