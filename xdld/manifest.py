"""aria2c input files: export the like list, import the finished downloads.

aria2c reads a plain text file with one URI per line.  Options for the URI
that follows it are written on the next lines, indented with white space
(``--`` prefixes are not allowed there).  That is what makes the output path
predictable::

    https://video.twimg.com/.../video.mp4
      dir=/Users/me/xdld/downloads
      out=2112022615082832063_EasonnLl_0.mp4

Everything xdld needs to remember about a file is stored as a JSON comment in
front of the URL, so :func:`xdld.record` can mark the downloads as done
afterwards and the next export will not list them again.

Note: aria2c does *not* accept global options in an input file (a line like
``split=4`` at the top is parsed as a URI and rejected), so the proxy and the
connection settings are repeated for every download.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

META_PREFIX = "# xdld-meta "

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)

# Options that are written for every single download.
_PER_DOWNLOAD = (
    ("continue", "true"),
    ("auto-file-renaming", "false"),
    ("allow-overwrite", "true"),
)


@dataclass
class ManifestEntry:
    """One download: where it comes from and where it has to end up."""

    url: str
    filename: str = ""
    directory: str = ""
    tweet_id: str = ""
    media_id: str = ""
    kind: str = "video"
    author: str = ""
    created_at: str = ""
    page: int = 0  # the page of the likes timeline this tweet lives on
    extra: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        """Where the file will be (or is) on disk."""
        if self.directory:
            return Path(self.directory) / self.filename
        return Path(self.filename)

    @property
    def key(self) -> str:
        """The key the state database uses for this media."""
        return self.media_id or f"{self.tweet_id}:{self.filename}"

    @property
    def meta(self) -> dict:
        return {
            "tweet_id": self.tweet_id,
            "media_id": self.media_id,
            "kind": self.kind,
            "author": self.author,
            "created_at": self.created_at,
            "url": self.url,
            "page": self.page,
        }


def _as_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def render_manifest(
    entries: list[ManifestEntry],
    proxy: str | None = None,
    generated_at: str | None = None,
) -> str:
    """Render the entries as an aria2c input file."""
    stamp = generated_at or time.strftime("%Y-%m-%d %H:%M:%S")
    lines = [
        "# xdld - aria2c input file",
        f"# generated: {stamp}",
        f"# files: {len(entries)}",
        "# usage: aria2c -c -x 4 -s 4 -j 4 -i <this file>",
        "# after the download: xdld record <this file>",
        "",
    ]
    for entry in entries:
        lines.append(META_PREFIX + json.dumps(entry.meta, ensure_ascii=False))
        lines.append(entry.url)
        if entry.directory:
            lines.append(f"  dir={entry.directory}")
        if entry.filename:
            lines.append(f"  out={entry.filename}")
        lines.append(f"  header=User-Agent: {USER_AGENT}")
        if proxy:
            lines.append(f"  all-proxy={proxy}")
        for name, value in _PER_DOWNLOAD:
            lines.append(f"  {name}={value}")
        lines.append("")
    return "\n".join(lines)


def write_manifest(
    path: str | Path,
    entries: list[ManifestEntry],
    proxy: str | None = None,
) -> Path:
    """Write the manifest to ``path`` (returns the path)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_manifest(entries, proxy=proxy), encoding="utf-8")
    return target


def parse_manifest(text: str) -> list[ManifestEntry]:
    """Parse an aria2c input file (ours or a hand written one).

    Comment lines starting with :data:`META_PREFIX` carry the metadata of
    the next URL.  Indented lines are the per download options - only ``dir``
    and ``out`` are interesting for us.
    """
    entries: list[ManifestEntry] = []
    pending: dict = {}
    current: ManifestEntry | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith(META_PREFIX.strip()):
            try:
                pending = json.loads(line[len(META_PREFIX.strip()) :])
            except json.JSONDecodeError:
                pending = {}
            continue
        if line.startswith("#"):
            continue
        if raw[:1].isspace():
            # an option for the current download
            if current is None or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip()
            if name == "dir":
                current.directory = value
            elif name == "out":
                current.filename = value
            elif name == "index-out":
                current.extra.setdefault("index-out", value)
            continue

        # a new URI
        meta = pending or {}
        pending = {}
        current = ManifestEntry(
            url=line,
            filename=str(meta.get("filename", "")),
            directory=str(meta.get("directory", "")),
            tweet_id=str(meta.get("tweet_id", "")),
            media_id=str(meta.get("media_id", "")),
            kind=str(meta.get("kind", "video")),
            author=str(meta.get("author", "")),
            created_at=str(meta.get("created_at", "")),
            page=_as_int(meta.get("page")),
        )
        entries.append(current)

    return entries


def load_manifest(path: str | Path) -> list[ManifestEntry]:
    """Read and parse a manifest file."""
    return parse_manifest(Path(path).read_text(encoding="utf-8"))
