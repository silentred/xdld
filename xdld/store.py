"""SQLite-backed state: what we have already seen and what we already saved.

Dedup lives here so that re-running the tool never downloads the same video
twice, even across separate invocations.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Self

SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets (
    tweet_id    TEXT PRIMARY KEY,
    author      TEXT,
    created_at  TEXT,
    has_video   INTEGER DEFAULT 0,
    first_seen  TEXT
);

CREATE TABLE IF NOT EXISTS media (
    media_key   TEXT PRIMARY KEY,
    tweet_id    TEXT,
    media_id    TEXT,
    kind        TEXT,
    url         TEXT,
    path        TEXT,
    bytes       INTEGER DEFAULT 0,
    status      TEXT,
    error       TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_media_tweet ON media(tweet_id);

CREATE TABLE IF NOT EXISTS state (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class Store:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ------------------------------------------------------------------ tweets
    def known_tweet(self, tweet_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM tweets WHERE tweet_id = ?", (tweet_id,)
        ).fetchone()
        return row is not None

    def record_tweet(
        self,
        tweet_id: str,
        author: str | None = None,
        created_at: str | None = None,
        has_video: bool = False,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO tweets (tweet_id, author, created_at, has_video, first_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(tweet_id) DO UPDATE SET
                author     = COALESCE(excluded.author, tweets.author),
                created_at = COALESCE(excluded.created_at, tweets.created_at),
                has_video  = MAX(tweets.has_video, excluded.has_video)
            """,
            (tweet_id, author, created_at, int(has_video), _now()),
        )
        self.conn.commit()

    # ------------------------------------------------------------------- media
    def media_status(self, media_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT status FROM media WHERE media_key = ?", (media_key,)
        ).fetchone()
        return row["status"] if row else None

    def record_media(
        self,
        media_key: str,
        tweet_id: str,
        media_id: str,
        kind: str,
        url: str,
        status: str,
        path: str | None = None,
        size: int = 0,
        error: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO media
                (media_key, tweet_id, media_id, kind, url, path, bytes, status, error, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(media_key) DO UPDATE SET
                path       = COALESCE(excluded.path, media.path),
                bytes      = CASE WHEN excluded.status = 'done'
                                  THEN excluded.bytes ELSE media.bytes END,
                status     = excluded.status,
                error      = excluded.error,
                url        = excluded.url,
                updated_at = excluded.updated_at
            """,
            (
                media_key,
                tweet_id,
                media_id,
                kind,
                url,
                path,
                size,
                status,
                error,
                _now(),
            ),
        )
        self.conn.commit()

    def media_path(self, media_key: str) -> str | None:
        row = self.conn.execute(
            "SELECT path FROM media WHERE media_key = ?", (media_key,)
        ).fetchone()
        return row["path"] if row else None

    # ------------------------------------------------------------------- state
    def set_state(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO state (k, v) VALUES (?, ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (key, value),
        )
        self.conn.commit()

    def get_state(self, key: str) -> str | None:
        row = self.conn.execute("SELECT v FROM state WHERE k = ?", (key,)).fetchone()
        return row["v"] if row else None

    # ------------------------------------------------------------------- stats
    def stats(self) -> dict[str, int]:
        tweets = self.conn.execute("SELECT COUNT(*) AS c FROM tweets").fetchone()["c"]
        done = self.conn.execute(
            "SELECT COUNT(*) AS c FROM media WHERE status = 'done'"
        ).fetchone()["c"]
        failed = self.conn.execute(
            "SELECT COUNT(*) AS c FROM media WHERE status = 'failed'"
        ).fetchone()["c"]
        skipped = self.conn.execute(
            "SELECT COUNT(*) AS c FROM media WHERE status = 'skipped'"
        ).fetchone()["c"]
        total_bytes = self.conn.execute(
            "SELECT COALESCE(SUM(bytes), 0) AS c FROM media WHERE status = 'done'"
        ).fetchone()["c"]
        return {
            "tweets_seen": tweets,
            "media_done": done,
            "media_failed": failed,
            "media_skipped": skipped,
            "bytes": total_bytes,
        }

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
