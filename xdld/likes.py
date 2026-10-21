"""Paginated access to a user's liked tweets."""

from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from dataclasses import dataclass

from xdld.store import Store


def _is_not_found(exc: BaseException) -> bool:
    """True for the 404 that X returns for a stale GraphQL query id."""
    name = type(exc).__name__
    if name == "NotFound":
        return True
    return "status: 404" in str(exc)


async def fetch_likes(client, user_id: str, count: int = 40, cursor: str | None = None):
    """Fetch one page of likes, repairing a stale query id if needed.

    X rotates the id of the ``Likes`` operation every few weeks; a stale id
    shows up as a 404. When that happens we look the current id up in the web
    bundles and try again.
    """
    try:
        return await client.get_user_tweets(
            user_id, "Likes", count=count, cursor=cursor
        )
    except Exception as exc:
        if not _is_not_found(exc):
            raise
        from xdld.discover import repair_likes_endpoint

        if not await repair_likes_endpoint(client):
            raise
        return await client.get_user_tweets(
            user_id, "Likes", count=count, cursor=cursor
        )


async def _page(client, user_id: str, opts: LikesOptions, cursor: str | None):
    return await fetch_likes(client, user_id, count=opts.page_size, cursor=cursor)


@dataclass
class LikesOptions:
    max_likes: int = 0  # 0 == unlimited
    page_size: int = 40
    sleep_min: float = 1.0
    sleep_max: float = 2.5
    stop_after_known: int = 50
    since_tweet: str | None = None  # start at this tweet (inclusive)
    from_page: int = 1  # 1 == the newest page


@dataclass
class WalkState:
    """What happened during the walk (filled in by :func:`iter_likes`)."""

    pages_walked: int = 0
    skipped: int = 0  # tweets before the start point
    start_found: bool = True


async def _next_page(client, user_id: str, opts: LikesOptions, result):
    """Advance to the next page, repairing a stale query id if needed."""
    try:
        return await result.next()
    except Exception as exc:
        if not _is_not_found(exc):
            raise
        from xdld.discover import repair_likes_endpoint

        if not await repair_likes_endpoint(client):
            raise
        return await _page(client, user_id, opts, result.next_cursor)


async def iter_likes(
    client,
    user_id: str,
    opts: LikesOptions,
    store: Store | None = None,
    log=print,
    state: WalkState | None = None,
) -> AsyncIterator:
    """Yield liked tweets, newest first, one page at a time.

    Stops when any of these is true:

    * ``max_likes`` tweets were produced (0 disables the limit),
    * the timeline ends (no more cursors),
    * ``stop_after_known`` already-known tweets were seen back to back
      (the crawl has caught up with a previous run).

    ``from_page`` skips whole pages at the beginning, ``since_tweet`` skips
    every tweet before the given one - both let a run continue where the
    previous one stopped.
    """
    produced = 0
    consecutive_known = 0
    page = 0
    skipped = 0
    started = opts.since_tweet is None
    # An explicit start point wins over the "I already know these" stop:
    # the user asked for this range, so keep walking it.
    stop_after_known = 0 if opts.since_tweet else opts.stop_after_known
    if opts.since_tweet and opts.stop_after_known:
        log("the 'stop after known tweets' rule is disabled when --since-tweet is used")

    result = await _page(client, user_id, opts, None)

    while result is not None:
        page += 1
        page_items = list(result)
        if not page_items:
            break
        if state is not None:
            state.pages_walked = page

        if page < opts.from_page:
            log(f"skipping page {page} (--from-page {opts.from_page})")
            await asyncio.sleep(random.uniform(opts.sleep_min, opts.sleep_max))
            result = await _next_page(client, user_id, opts, result)
            continue

        for tweet in page_items:
            tweet_id = str(tweet.id)
            if not started:
                if tweet_id == opts.since_tweet:
                    started = True
                    log(f"start point reached: tweet {tweet_id}")
                else:
                    skipped += 1
                    if state is not None:
                        state.skipped = skipped
                    continue
            known = store.known_tweet(tweet_id) if store else False
            if known:
                consecutive_known += 1
                if stop_after_known and consecutive_known >= stop_after_known:
                    log(
                        f"stopping after {consecutive_known} already-known tweets"
                    )
                    return
            else:
                consecutive_known = 0

            yield tweet
            produced += 1
            if opts.max_likes and produced >= opts.max_likes:
                return

        if not result.next_cursor:
            break

        await asyncio.sleep(random.uniform(opts.sleep_min, opts.sleep_max))
        result = await _next_page(client, user_id, opts, result)

    if skipped:
        log(f"skipped {skipped} tweet(s) before the start point")
    if opts.since_tweet and not started:
        log(f"tweet {opts.since_tweet} was not found in the {page} page(s) walked")
    if state is not None:
        state.skipped = skipped
        state.pages_walked = page
        state.start_found = started
