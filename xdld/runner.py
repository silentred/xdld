"""The download pipeline: walk the likes, grab the best video of every tweet."""

from __future__ import annotations

import json
import re
import string
import time
from dataclasses import dataclass, field
from pathlib import Path

from xdld.client import build_client, check_session, resolve_user_id
from xdld.config import Config
from xdld.diag import describe_exception, hints
from xdld.download import DownloadError, download_file, download_hls_async
from xdld.likes import LikesOptions, WalkState, iter_likes
from xdld.media import (
    HLS,
    IMAGE,
    MediaItem,
    Variant,
    best_variant,
    extract_media,
    image_extension,
)
from xdld.progress import Progress, human_bytes, with_heartbeat
from xdld.store import Store

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(value: str, fallback: str = "unknown") -> str:
    cleaned = _UNSAFE.sub("_", (value or "").strip())
    cleaned = cleaned.strip("._")
    return cleaned or fallback


TEMPLATE_KEYS = {
    "tweet_id",
    "user",
    "index",
    "ext",
    "date",
    "media_id",
}


def validate_template(template: str) -> None:
    """Raise a helpful error when the template uses an unknown placeholder."""
    unknown = {
        field
        for _, field, _, _ in string.Formatter().parse(template)
        if field and field not in TEMPLATE_KEYS
    }
    if unknown:
        raise ValueError(
            f"unknown placeholder(s) in the filename template: "
            f"{', '.join(sorted(unknown))}. "
            f"available: {', '.join(sorted(TEMPLATE_KEYS))}"
        )


def render_filename(template: str, context: dict[str, str]) -> str:
    """Render the filename template."""
    validate_template(template)
    rendered = template.format_map(context)
    return safe_name(rendered, fallback="media")


@dataclass
class RunStats:
    tweets_seen: int = 0
    tweets_with_video: int = 0
    candidates: int = 0
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0
    bytes: int = 0
    skipped_start: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        data = {
            "tweets_seen": self.tweets_seen,
            "tweets_with_video": self.tweets_with_video,
            "candidates": self.candidates,
            "downloaded": self.downloaded,
            "skipped": self.skipped,
            "failed": self.failed,
            "bytes": self.bytes,
        }
        if self.skipped_start:
            data["skipped_start"] = self.skipped_start
        return data


def _tweet_author(tweet) -> str:
    user = getattr(tweet, "user", None)
    return getattr(user, "screen_name", "") or ""


def _tweet_created(tweet) -> str:
    stamp = getattr(tweet, "created_at", None)
    return stamp or ""


def _media_key(tweet_id: str, item: MediaItem, index: int) -> str:
    return item.media_id or f"{tweet_id}:{index}"


@dataclass
class MediaCandidate:
    """One downloadable file of a tweet."""

    item: MediaItem
    variant: Variant | None
    index: int
    key: str
    url: str
    ext: str
    filename: str
    dest: Path
    is_image: bool


@dataclass
class TweetCandidate:
    """A tweet and everything we could download from it."""

    tweet_id: str
    author: str
    created_at: str
    tweet: object
    media: list[MediaCandidate] = field(default_factory=list)
    has_video: bool = False


def build_tweet_candidates(
    cfg: Config,
    tweet,
    out_dir: Path,
    download_images: bool | None = None,
) -> TweetCandidate:
    """Pick the files of a tweet: the best variant of every video.

    Shared by ``xdld run`` and ``xdld export`` so both agree on the file
    name, the url and the target path.
    """
    if download_images is None:
        download_images = cfg.download_images

    tweet_id = str(tweet.id)
    author = _tweet_author(tweet)
    created = _tweet_created(tweet)
    items = extract_media(tweet)
    video_items = [i for i in items if i.kind in ("video", "gif")]

    candidates: list[MediaCandidate] = []
    for index, item in enumerate(items):
        is_image = item.kind == IMAGE
        if is_image and not download_images:
            continue
        if not is_image and item.kind not in ("video", "gif"):
            continue

        variant = None if is_image else best_variant(item)
        if not is_image and variant is None:
            continue

        url = item.image_url if is_image else variant.url
        ext = (
            image_extension(item.image_url)
            if is_image
            else (variant.ext if variant else ".mp4")
        )
        filename = render_filename(
            cfg.filename_template,
            {
                "tweet_id": tweet_id,
                "user": author,
                "index": index,
                "ext": ext,
                "date": (created[:10] if created else ""),
                "media_id": item.media_id,
            },
        )
        candidates.append(
            MediaCandidate(
                item=item,
                variant=variant,
                index=index,
                key=_media_key(tweet_id, item, index),
                url=url,
                ext=ext,
                filename=filename,
                dest=out_dir / filename,
                is_image=is_image,
            )
        )

    return TweetCandidate(
        tweet_id=tweet_id,
        author=author,
        created_at=created,
        tweet=tweet,
        media=candidates,
        has_video=bool(video_items),
    )


async def run_download(
    cfg: Config,
    screen_name: str | None = None,
    dry_run: bool = False,
    limit: int | None = None,
    since_tweet: str | None = None,
    from_page: int | None = None,
    quiet: bool = False,
    verbose: bool = False,
    log=print,
) -> RunStats:
    stats = RunStats()
    validate_template(cfg.filename_template)
    client = build_client(cfg)
    progress = Progress(log=log, enabled=not quiet)

    session = await check_session(client)
    if not session.logged_in:
        raise RuntimeError(f"Not logged in: {session.detail}")
    progress.note(f"logged in as @{session.screen_name}")

    user_id, target = await resolve_user_id(client, screen_name)
    progress.note(f"walking the likes of @{target} (id={user_id})")

    out_dir = Path(cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "index.jsonl"

    max_likes = limit if limit is not None else cfg.max_likes
    start_page = from_page if from_page is not None else cfg.from_page
    opts = LikesOptions(
        max_likes=max_likes,
        page_size=cfg.page_size,
        sleep_min=cfg.sleep_min,
        sleep_max=cfg.sleep_max,
        stop_after_known=cfg.stop_after_known,
        since_tweet=since_tweet,
        from_page=start_page,
    )
    if since_tweet:
        progress.note(f"start point: tweet {since_tweet}")
    if start_page > 1:
        progress.note(f"start page: {start_page}")

    with Store(cfg.db) as store:
        state = WalkState()
        async for tweet in iter_likes(
            client, user_id, opts, store, log=progress.note, state=state
        ):
            cand = build_tweet_candidates(cfg, tweet, out_dir)
            tweet_id = cand.tweet_id
            stats.tweets_seen += 1
            author = cand.author
            created = cand.created_at

            if cand.has_video:
                stats.tweets_with_video += 1

            store.record_tweet(
                tweet_id,
                author=author,
                created_at=created,
                has_video=cand.has_video,
            )
            progress.counter(
                tweets=stats.tweets_seen,
                videos=stats.tweets_with_video,
                ok=stats.downloaded,
                skip=stats.skipped,
                fail=stats.failed,
                size=human_bytes(stats.bytes),
            )

            for media in cand.media:
                item = media.item
                variant = media.variant
                is_image = media.is_image

                stats.candidates += 1
                key = media.key
                url = media.url
                filename = media.filename
                dest = media.dest

                status = store.media_status(key)
                if status == "done":
                    previous = store.media_path(key)
                    if previous and Path(previous).exists():
                        stats.skipped += 1
                        progress.note(
                            f"skip  {tweet_id} (already downloaded: {filename})"
                        )
                        continue
                    progress.note(f"redo  {tweet_id} (previous file is gone)")
                elif dest.exists() and not cfg.overwrite:
                    # The file is on disk but the state does not know it (the
                    # database was deleted, a different machine, ...). Files are
                    # renamed into place atomically, so an existing one is
                    # complete - register it instead of downloading it again.
                    size = dest.stat().st_size
                    store.record_media(
                        key,
                        tweet_id,
                        item.media_id,
                        item.kind,
                        url,
                        status="done",
                        path=str(dest),
                        size=size,
                    )
                    stats.skipped += 1
                    stats.bytes += size
                    progress.note(f"skip  {tweet_id} (already on disk: {filename})")
                    continue

                if dry_run:
                    progress.note(f"plan  {tweet_id} -> {filename}  ({url})")
                    continue

                if verbose:
                    progress.note(
                        f"get   {tweet_id} {item.kind} "
                        f"{item.width}x{item.height} "
                        f"{variant.content_type if variant else ''} "
                        f"bitrate={variant.bitrate if variant else '-'}"
                    )

                progress.file_start(filename)
                try:
                    if is_image:
                        size = await with_heartbeat(
                            download_file(
                                url,
                                dest,
                                proxy=cfg.proxy,
                                overwrite=cfg.overwrite,
                                on_progress=progress.file_progress,
                                on_retry=progress.retry,
                            ),
                            progress,
                            filename,
                        )
                    elif variant.kind == HLS:
                        size = await with_heartbeat(
                            download_hls_async(
                                url, dest, proxy=cfg.proxy, overwrite=cfg.overwrite
                            ),
                            progress,
                            filename,
                        )
                    else:
                        size = await with_heartbeat(
                            download_file(
                                url,
                                dest,
                                proxy=cfg.proxy,
                                overwrite=cfg.overwrite,
                                on_progress=progress.file_progress,
                                on_retry=progress.retry,
                            ),
                            progress,
                            filename,
                        )
                except (DownloadError, Exception) as exc:  # noqa: BLE001
                    stats.failed += 1
                    message = f"{tweet_id}: {type(exc).__name__}: {exc}"
                    stats.errors.append(message)
                    store.record_media(
                        key,
                        tweet_id,
                        item.media_id,
                        item.kind,
                        url,
                        status="failed",
                        error=str(exc),
                    )
                    progress.file_end()
                    progress.note(f"FAIL  {message}")
                    if verbose:
                        progress.note(describe_exception(exc))
                        for hint in hints(exc):
                            progress.note(f"      hint: {hint}")
                    continue
                finally:
                    progress.file_end()

                stats.downloaded += 1
                stats.bytes += size
                store.record_media(
                    key,
                    tweet_id,
                    item.media_id,
                    item.kind,
                    url,
                    status="done",
                    path=str(dest),
                    size=size,
                )
                if cfg.write_index:
                    with index_path.open("a", encoding="utf-8") as fh:
                        fh.write(
                            json.dumps(
                                {
                                    "tweet_id": tweet_id,
                                    "media_id": item.media_id,
                                    "author": author,
                                    "created_at": created,
                                    "url": url,
                                    "path": str(dest),
                                    "bytes": size,
                                    "downloaded_at": time.strftime(
                                        "%Y-%m-%dT%H:%M:%S"
                                    ),
                                },
                                ensure_ascii=False,
                            )
                            + "\n"
                        )
                progress.counter(
                    tweets=stats.tweets_seen,
                    videos=stats.tweets_with_video,
                    ok=stats.downloaded,
                    skip=stats.skipped,
                    fail=stats.failed,
                    size=human_bytes(stats.bytes),
                )
                progress.note(f"ok    {tweet_id} -> {filename}  ({size / 1e6:.1f} MB)")

    progress.finish()
    stats.skipped_start = state.skipped
    return stats
