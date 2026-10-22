"""Command line interface."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from xdld.config import DEFAULT_CONFIG_NAME, load_config
from xdld.cookies import CookieError

SAMPLE_CONFIG = """\
# xdld configuration. Every key can also be set as an env var
# (XDLD_PROXY, XDLD_COOKIES, ...) or overridden per command line.

# All traffic to x.com / video.twimg.com goes through this proxy.
proxy = "http://127.0.0.1:7897"

# Browser cookie export (auth_token + ct0 are required).
cookies = "cookies.txt"

# Where videos are written.
output_dir = "downloads"

# Where the "what did I already download" state lives.
db = "xdld.db"

# 0 == walk the whole like list.
max_likes = 0

# Tweets requested per API page.
page_size = 40

# Seconds to wait between pages (randomised inside this range).
sleep_min = 1.0
sleep_max = 2.5

# Stop after this many already-downloaded tweets in a row.
stop_after_known = 50

# Start at this page of the likes timeline (1 == the newest page).
from_page = 1

# Also download images (off by default).
download_images = false

# {tweet_id} {user} {index} {ext} {date} {media_id}
filename_template = "{tweet_id}_{user}_{index}{ext}"

# Set to e.g. "chrome124" (needs: pip install "xdld[impersonate]").
impersonate = ""
"""


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", help=f"path to a config file (default: ./{DEFAULT_CONFIG_NAME})")
    parser.add_argument("--proxy", help="proxy url for every x.com request")
    parser.add_argument("--cookies", help="path to the exported cookie jar")
    parser.add_argument("--out", dest="output_dir", help="output directory")
    parser.add_argument("--db", help="path to the sqlite state file")
    parser.add_argument("-v", "--verbose", action="store_true", help="more output")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="xdld",
        description="Download the highest-resolution videos from a user's liked tweets.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="write a sample config file")
    p_init.add_argument("--force", action="store_true", help="overwrite an existing file")

    p_check = sub.add_parser("check", help="check the proxy and the login status")
    _add_common(p_check)

    p_run = sub.add_parser("run", help="walk the likes and download the videos")
    _add_common(p_run)
    p_run.add_argument("--user", help="screen name to walk (default: the logged-in account)")
    p_run.add_argument("--limit", type=int, help="stop after N liked tweets")
    p_run.add_argument("--dry-run", action="store_true", help="plan only, download nothing")
    p_run.add_argument("--images", action="store_true", help="also download images")
    p_run.add_argument("--overwrite", action="store_true", help="re-download existing files")
    p_run.add_argument("--template", help="filename template")
    p_run.add_argument("--impersonate", help="browser TLS fingerprint, e.g. chrome124")
    p_run.add_argument("--page-size", type=int, help="tweets per API page")
    p_run.add_argument("--stop-after-known", type=int, help="stop after N known tweets in a row")
    p_run.add_argument("--sleep", nargs=2, type=float, metavar=("MIN", "MAX"), help="pause between pages")
    p_run.add_argument(
        "--since-tweet",
        metavar="TWEET_ID",
        help="start at this tweet (inclusive) and walk towards the older ones",
    )
    p_run.add_argument(
        "--from-page",
        type=int,
        metavar="N",
        help="start at page N of the likes timeline (1 == the newest page)",
    )
    p_run.add_argument(
        "--quiet", action="store_true", help="do not show the live progress line"
    )

    p_likes = sub.add_parser("likes", help="list the liked tweets without downloading")
    _add_common(p_likes)
    p_likes.add_argument("--user", help="screen name to walk (default: the logged-in account)")
    p_likes.add_argument("--limit", type=int, help="stop after N liked tweets")
    p_likes.add_argument("--media", action="store_true", help="show the media candidates")
    p_likes.add_argument(
        "--since-tweet", metavar="TWEET_ID", help="start at this tweet (inclusive)"
    )
    p_likes.add_argument(
        "--from-page", type=int, metavar="N", help="start at page N (1 == the newest)"
    )

    p_status = sub.add_parser("status", help="show what has been downloaded so far")
    _add_common(p_status)

    p_export = sub.add_parser(
        "export", help="write an aria2c input file with the videos to download"
    )
    _add_common(p_export)
    p_export.add_argument("--user", help="screen name to walk (default: the logged-in account)")
    p_export.add_argument("--limit", type=int, help="stop after N liked tweets")
    p_export.add_argument(
        "-o",
        "--manifest",
        help="path of the manifest (default: <output_dir>/aria2.txt)",
    )
    p_export.add_argument(
        "--dir", help="directory aria2c should save into (default: output_dir)"
    )
    p_export.add_argument("--images", action="store_true", help="also export images")
    p_export.add_argument("--template", help="filename template")
    p_export.add_argument("--page-size", type=int, help="tweets per API page")
    p_export.add_argument(
        "--stop-after-known", type=int, help="stop after N known tweets in a row"
    )
    p_export.add_argument(
        "--sleep", nargs=2, type=float, metavar=("MIN", "MAX"), help="pause between pages"
    )
    p_export.add_argument(
        "--since-tweet", metavar="TWEET_ID", help="start at this tweet (inclusive)"
    )
    p_export.add_argument(
        "--from-page", type=int, metavar="N", help="start at page N (1 == the newest)"
    )
    p_export.add_argument(
        "--overwrite",
        action="store_true",
        help="also list files that are already on disk",
    )

    p_record = sub.add_parser(
        "record", help="mark the files of a manifest as downloaded"
    )
    _add_common(p_record)
    p_record.add_argument(
        "-i",
        "--manifest",
        help="path of the manifest (default: <output_dir>/aria2.txt, - for stdin)",
    )
    p_record.add_argument(
        "--assume-done",
        action="store_true",
        help="record the files even when they are not on disk",
    )

    p_ops = sub.add_parser(
        "refresh-ops", help="look up the current GraphQL query ids in the web bundles"
    )
    _add_common(p_ops)

    return parser


def cmd_init(args: argparse.Namespace) -> int:
    path = Path(DEFAULT_CONFIG_NAME)
    if path.exists() and not args.force:
        print(f"{path} already exists (use --force to overwrite)")
        return 1
    path.write_text(SAMPLE_CONFIG, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    from xdld.client import build_client, check_session, probe_proxy
    from xdld.diag import describe_exception, hints
    from xdld.discover import current_query_id
    from xdld.likes import fetch_likes

    verbose = getattr(args, "verbose", False)
    cfg = load_config(args.config, overrides=_overrides(args))
    print(f"proxy   : {cfg.proxy or '(none)'}")

    try:
        client = build_client(cfg)
    except CookieError as exc:
        print(f"cookies : FAIL - {exc}")
        return 1

    # Everything that talks to X has to share one event loop: the client
    # keeps its httpx connection pool (and the cookies) tied to the loop it
    # was first used in, so a second asyncio.run() would fail with
    # "Event loop is closed".
    async def _run():
        proxy_ok, proxy_detail = await probe_proxy(cfg.proxy)
        print(f"proxy ok: {proxy_ok} - {proxy_detail}")
        if not proxy_ok:
            print("hint   : start the proxy, e.g. `clash` / `surge` on port 7897")
        print(f"cookies : {getattr(client, 'cookie_summary', '')}")
        if verbose:
            print(f"query id: {current_query_id()}")
            print(f"config  : {cfg}")

        started = time.monotonic()
        session = await check_session(client)
        if not session.logged_in:
            print(f"login   : FAIL - {session.detail}")
            if verbose:
                print(describe_exception(RuntimeError(session.detail)))
            return 1
        print(f"login   : OK - @{session.screen_name} ({session.name})")
        if verbose:
            print(f"user id : {session.user_id}")
            print(f"login   : {time.monotonic() - started:.2f}s")

        if not session.user_id:
            print("likes   : FAIL - no account id (see the login line above)")
            return 2

        started = time.monotonic()
        try:
            await fetch_likes(client, session.user_id, count=1)
        except Exception as exc:  # noqa: BLE001 - the reason is what matters
            print(f"likes   : FAIL - {type(exc).__name__}: {exc}")
            if verbose:
                print(describe_exception(exc))
            for hint in hints(exc):
                print(f"hint   : {hint}")
            print("hint   : try `xdld refresh-ops`, then re-export the cookies")
            return 2
        if verbose:
            print(f"likes   : {time.monotonic() - started:.2f}s")
        print("likes   : OK - the Likes endpoint answered")
        return 0

    return asyncio.run(_run())


def cmd_run(args: argparse.Namespace) -> int:
    from xdld.diag import describe_exception, hints
    from xdld.runner import run_download

    verbose = getattr(args, "verbose", False)
    overrides = _overrides(args)
    if getattr(args, "images", False):
        overrides["download_images"] = True
    if getattr(args, "overwrite", False):
        overrides["overwrite"] = True
    if getattr(args, "template", None):
        overrides["filename_template"] = args.template
    if getattr(args, "impersonate", None):
        overrides["impersonate"] = args.impersonate
    if getattr(args, "page_size", None):
        overrides["page_size"] = args.page_size
    if getattr(args, "stop_after_known", None) is not None:
        overrides["stop_after_known"] = args.stop_after_known
    if getattr(args, "sleep", None):
        overrides["sleep_min"], overrides["sleep_max"] = args.sleep
    if getattr(args, "from_page", None):
        overrides["from_page"] = args.from_page

    cfg = load_config(args.config, overrides=overrides)
    if verbose:
        print(f"config  : {cfg}")
    try:
        stats = asyncio.run(
            run_download(
                cfg,
                screen_name=args.user,
                dry_run=args.dry_run,
                limit=args.limit,
                since_tweet=getattr(args, "since_tweet", None),
                from_page=getattr(args, "from_page", None),
                quiet=getattr(args, "quiet", False),
                verbose=verbose,
            )
        )
    except Exception as exc:  # noqa: BLE001 - the reason is what matters
        print(f"error: {type(exc).__name__}: {exc}")
        if verbose:
            print(describe_exception(exc))
        for hint in hints(exc):
            print(f"hint: {hint}")
        return 1
    print(json.dumps(stats.as_dict(), indent=2))
    return 0 if stats.failed == 0 else 2


def cmd_likes(args: argparse.Namespace) -> int:
    from xdld.client import build_client, check_session, resolve_user_id
    from xdld.diag import describe_exception, hints
    from xdld.likes import LikesOptions, WalkState, iter_likes
    from xdld.media import best_variant, extract_media
    from xdld.progress import Progress

    verbose = getattr(args, "verbose", False)
    cfg = load_config(args.config, overrides=_overrides(args))
    progress = Progress(log=print)

    async def _run() -> int:
        client = build_client(cfg)
        session = await check_session(client)
        if not session.logged_in:
            print(f"not logged in: {session.detail}")
            return 1
        user_id, target = await resolve_user_id(client, args.user)
        print(f"likes of @{target} (id={user_id})")
        opts = LikesOptions(
            max_likes=args.limit or 0,
            page_size=cfg.page_size,
            sleep_min=cfg.sleep_min,
            sleep_max=cfg.sleep_max,
            stop_after_known=0,
            since_tweet=getattr(args, "since_tweet", None),
            from_page=getattr(args, "from_page", None) or cfg.from_page,
        )
        count = 0
        state = WalkState()
        try:
            async for tweet in iter_likes(
                client, user_id, opts, log=progress.note, state=state
            ):
                count += 1
                user = getattr(tweet, "user", None)
                author = getattr(user, "screen_name", "") if user else ""
                progress.note(
                    f"[{count}] page {state.current_page} {tweet.id} "
                    f"@{author} - {(tweet.text or '')[:100]!r}"
                )
                progress.counter(tweets=count)
                if args.media:
                    for item in extract_media(tweet):
                        variant = best_variant(item)
                        if variant:
                            print(
                                f"      {item.kind} {item.media_id} "
                                f"{item.width}x{item.height} "
                                f"{variant.content_type} bitrate={variant.bitrate}"
                            )
                        else:
                            print(
                                f"      {item.kind} {item.media_id} "
                                f"{item.width}x{item.height}"
                            )
        except Exception as exc:  # noqa: BLE001 - the reason is what matters
            progress.finish()
            print(f"error: {type(exc).__name__}: {exc}")
            if verbose:
                print(describe_exception(exc))
            for hint in hints(exc):
                print(f"hint: {hint}")
            return 2
        progress.finish()
        print(f"total: {count}")
        return 0

    return asyncio.run(_run())


def cmd_refresh_ops(args: argparse.Namespace) -> int:
    from xdld.client import build_client
    from xdld.discover import (
        apply_query_id,
        current_query_id,
        discover_likes_query_id,
    )

    cfg = load_config(args.config, overrides=_overrides(args))
    client = build_client(cfg)
    old = current_query_id()
    print(f"current Likes query id: {old}")

    discovery = asyncio.run(discover_likes_query_id(client))
    if not discovery.query_id:
        print(
            f"no query id found ({discovery.bundles_scanned} bundle(s) scanned) - "
            "the cookies may be stale"
        )
        return 1
    print(f"found in {discovery.source}")
    if discovery.query_id == old:
        print("already up to date")
        return 0
    apply_query_id(discovery.query_id)
    print(f"patched: {old} -> {discovery.query_id}")
    print("note: the patch lives in this process only")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    """Write an aria2c input file with every video that is still missing."""
    from xdld.client import build_client, check_session, resolve_user_id
    from xdld.likes import LikesOptions, WalkState, iter_likes
    from xdld.manifest import ManifestEntry, write_manifest
    from xdld.progress import Progress
    from xdld.runner import build_tweet_candidates
    from xdld.store import Store

    overrides = _overrides(args)
    if getattr(args, "images", False):
        overrides["download_images"] = True
    if getattr(args, "template", None):
        overrides["filename_template"] = args.template
    if getattr(args, "page_size", None):
        overrides["page_size"] = args.page_size
    if getattr(args, "stop_after_known", None) is not None:
        overrides["stop_after_known"] = args.stop_after_known
    if getattr(args, "sleep", None):
        overrides["sleep_min"], overrides["sleep_max"] = args.sleep

    cfg = load_config(args.config, overrides=overrides)
    out_dir = Path(cfg.output_dir)
    target_dir = (
        Path(args.dir).expanduser().resolve() if args.dir else out_dir.resolve()
    )
    manifest_path = (
        Path(args.manifest).expanduser()
        if args.manifest
        else out_dir / "aria2.txt"
    )
    target_dir.mkdir(parents=True, exist_ok=True)

    progress = Progress(log=print)
    entries: list = []
    skipped = 0

    async def _run() -> None:
        nonlocal skipped
        client = build_client(cfg)
        session = await check_session(client)
        if not session.logged_in:
            raise RuntimeError(f"Not logged in: {session.detail}")
        progress.note(f"logged in as @{session.screen_name}")

        user_id, target = await resolve_user_id(client, args.user)
        progress.note(f"walking the likes of @{target} (id={user_id})")

        opts = LikesOptions(
            max_likes=args.limit if args.limit is not None else cfg.max_likes,
            page_size=cfg.page_size,
            sleep_min=cfg.sleep_min,
            sleep_max=cfg.sleep_max,
            stop_after_known=cfg.stop_after_known,
            since_tweet=getattr(args, "since_tweet", None),
            from_page=getattr(args, "from_page", None) or cfg.from_page,
        )

        with Store(cfg.db) as store:
            state = WalkState()
            async for tweet in iter_likes(
                client, user_id, opts, store, log=progress.note, state=state
            ):
                cand = build_tweet_candidates(cfg, tweet, target_dir)
                page = state.current_page
                for media in cand.media:
                    status = store.media_status(media.key)
                    if status == "done":
                        skipped += 1
                        continue
                    if media.dest.exists() and not cfg.overwrite:
                        # already on disk but not registered - register it
                        store.record_media(
                            media.key,
                            cand.tweet_id,
                            media.item.media_id,
                            media.item.kind,
                            media.url,
                            status="done",
                            path=str(media.dest),
                            size=media.dest.stat().st_size,
                        )
                        skipped += 1
                        continue
                    entries.append(
                        ManifestEntry(
                            url=media.url,
                            filename=media.filename,
                            directory=str(target_dir),
                            tweet_id=cand.tweet_id,
                            media_id=media.item.media_id,
                            kind=media.item.kind,
                            author=cand.author,
                            created_at=cand.created_at,
                            page=page,
                        )
                    )

    asyncio.run(_run())

    write_manifest(manifest_path, entries, proxy=cfg.proxy)
    progress.finish()
    print(f"manifest: {manifest_path}  ({len(entries)} file(s), {skipped} skipped)")
    print(f"download: aria2c -c -x 4 -s 4 -j 4 -i {manifest_path}")
    print(f"record  : xdld record -i {manifest_path}")
    if entries:
        last = entries[-1]
        print(f"last    : tweet {last.tweet_id} (page {last.page})")
        print(f"next    : xdld export --since-tweet {last.tweet_id}")
        if last.page:
            print(f"          xdld export --from-page {last.page}")
    return 0


def cmd_record(args: argparse.Namespace) -> int:
    """Mark the files of a manifest as downloaded (after aria2c finished)."""
    from xdld.manifest import load_manifest, parse_manifest
    from xdld.store import Store

    cfg = load_config(args.config, overrides=_overrides(args))
    manifest = getattr(args, "manifest", None)
    path = (
        Path(manifest).expanduser()
        if manifest and manifest != "-"
        else Path(cfg.output_dir) / "aria2.txt"
    )
    if manifest == "-":
        entries = parse_manifest(sys.stdin.read())
    else:
        if not path.exists():
            print(f"error: no such manifest: {path}")
            return 1
        entries = load_manifest(path)

    recorded = 0
    missing = 0
    unknown = 0
    last: object | None = None
    with Store(cfg.db) as store:
        for entry in entries:
            target = entry.path
            exists = target.exists()
            size = target.stat().st_size if exists else 0
            if not exists and not args.assume_done:
                missing += 1
                print(f"missing: {target}")
                last = entry
                continue
            key = entry.media_id or store.find_media_key(
                url=entry.url, path=str(target)
            )
            if not key:
                unknown += 1
                print(f"unknown: {entry.url} (no media id in the manifest)")
                last = entry
                continue
            store.record_media(
                key,
                entry.tweet_id,
                entry.media_id,
                entry.kind,
                entry.url,
                status="done",
                path=str(target),
                size=size,
            )
            if entry.tweet_id:
                store.record_tweet(
                    entry.tweet_id,
                    author=entry.author,
                    created_at=entry.created_at,
                    has_video=True,
                )
            recorded += 1
            last = entry

    print(
        f"recorded: {recorded} file(s)"
        + (f", missing: {missing}" if missing else "")
        + (f", unknown: {unknown}" if unknown else "")
    )
    print("the next `xdld export` will not list them again")
    if last is not None and last.tweet_id:
        page = last.page
        where = f"tweet {last.tweet_id}"
        if page:
            where += f" (page {page})"
        print(f"last    : {where}")
        print(f"next    : xdld export --since-tweet {last.tweet_id}")
        if page:
            print(f"          xdld export --from-page {page}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from xdld.store import Store

    cfg = load_config(args.config, overrides=_overrides(args))
    if not Path(cfg.db).exists():
        print("no state file yet")
        return 0
    with Store(cfg.db) as store:
        stats = store.stats()
    for key, value in stats.items():
        print(f"{key:16}: {value}")
    return 0


def _overrides(args: argparse.Namespace) -> dict:
    out = {}
    for name in ("proxy", "cookies", "output_dir", "db"):
        value = getattr(args, name, None)
        if value:
            out[name] = value
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "init": cmd_init,
        "check": cmd_check,
        "run": cmd_run,
        "likes": cmd_likes,
        "export": cmd_export,
        "record": cmd_record,
        "status": cmd_status,
        "refresh-ops": cmd_refresh_ops,
    }
    try:
        return handlers[args.command](args)
    except CookieError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        if getattr(args, "verbose", False):
            raise
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
