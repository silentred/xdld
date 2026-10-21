"""Recovering the GraphQL query id of the Likes endpoint.

X rotates the operation ids of its web GraphQL API every few weeks. When an
id goes stale the endpoint answers ``404`` and every scraper built on it
breaks. The ids live in the javascript bundles the web client downloads, so
we can look them up and patch the client at runtime.

The web client is bundled with Vite, so the entry file lists the assets it
may load:

    const __vite__mapDeps=(i,m=__vite__mapDeps,d=(m.f||(m.f=[
      "assets/xxx.js","assets/yyy.js"])))=>...

and every module imports its dependencies with

    import{a as b}from"./assets/zzz.js";

The query id of a persisted operation sits next to the operation name:

    {id:"BEthBswU1Bt209H5xptp4Q",name:"UserLikes",operationKind:"query"}
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass

DOMAIN = "x.com"
ASSET_ROOT = "https://abs.twimg.com/x-web/x-web/"

_ID = r"[A-Za-z0-9_-]{8,40}"
_Q = r"[\"'`]"

# Pairs of a query id and an operation name, in both orders and both quote
# styles (the new bundles use backticks).
PATTERNS = [
    re.compile(rf"queryId:{_Q}({_ID}){_Q}[^}}]{{0,160}}operationName:{_Q}Likes{_Q}"),
    re.compile(rf"operationName:{_Q}Likes{_Q}[^}}]{{0,160}}queryId:{_Q}({_ID}){_Q}"),
    re.compile(rf"queryId:{_Q}({_ID}){_Q}[^}}]{{0,160}}operationName:{_Q}UserLikes{_Q}"),
    re.compile(rf"operationName:{_Q}UserLikes{_Q}[^}}]{{0,160}}queryId:{_Q}({_ID}){_Q}"),
    re.compile(rf"id:{_Q}({_ID}){_Q}[^}}]{{0,240}}name:{_Q}UserLikes{_Q}"),
    re.compile(rf"name:{_Q}UserLikes{_Q}[^}}]{{0,240}}id:{_Q}({_ID}){_Q}"),
    re.compile(rf"id:{_Q}({_ID}){_Q}[^}}]{{0,240}}operationName:{_Q}Likes{_Q}"),
    re.compile(rf"{_Q}({_ID})/Likes{_Q}"),
    re.compile(rf"{_Q}({_ID})/UserLikes{_Q}"),
]

# Fallback: an operation name with an id somewhere in the neighbourhood.
_NAME = re.compile(rf"name:{_Q}(UserLikes|Likes){_Q}")
_NEARBY_ID = re.compile(rf"(?:id|queryId):{_Q}({_ID}){_Q}")

_SCRIPT_SRC = re.compile(r"<script[^>]+src=\"([^\"]+\.js[^\"]*)\"")
# "assets/x.js", "./assets/x.js", `assets/x.js`
_ASSET = re.compile(rf"{_Q}(\.?/?[A-Za-z0-9_./-]{{3,160}}\.js){_Q}")

MAX_ASSETS = 500
MAX_DEPTH = 2
CONCURRENCY = 24
NEEDLES = ("UserLikes", "Likes")


@dataclass
class Discovery:
    query_id: str | None = None
    assets_scanned: int = 0
    source: str = ""


def _absolute(base: str, href: str) -> str:
    if href.startswith("http"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return f"https://{DOMAIN}{href}"
    return f"{base}/{href.lstrip('./')}"


def _ids_in(text: str) -> set[str]:
    found: set[str] = set()
    for pattern in PATTERNS:
        found.update(pattern.findall(text))
    if found:
        return found

    # No explicit pair - look for an id next to the operation name.
    for match in _NAME.finditer(text):
        window = text[max(0, match.start() - 400) : match.end() + 400]
        nearby = _NEARBY_ID.search(window)
        if nearby:
            found.add(nearby.group(1))
    return found


async def _fetch(client, url: str) -> str | None:
    """Fetch a page or bundle with the client's session (cookies + proxy)."""
    try:
        response = await client.http.get(url, follow_redirects=True)
    except Exception:  # noqa: BLE001 - a missing bundle is not fatal
        return None
    if response.status_code != 200:
        return None
    return response.text


async def _scan(
    client,
    urls: list[str],
    needles: tuple[str, ...] = (),
    hits: dict[str, list[str]] | None = None,
) -> tuple[str, set[str]] | None:
    """Fetch every url and return the first one that carries a query id.

    The urls that contain one of the ``needles`` are collected in ``hits``
    so the caller can look at their imports.
    """
    sem = asyncio.Semaphore(CONCURRENCY)

    async def one(url: str):
        async with sem:
            return url, await _fetch(client, url)

    for batch in _batches(urls, CONCURRENCY * 4):
        for url, text in await asyncio.gather(*(one(u) for u in batch)):
            if not text:
                continue
            for needle in needles:
                if hits is not None and needle in text:
                    hits.setdefault(needle, []).append(url)
            ids = _ids_in(text)
            if ids:
                return url, ids
    return None


def _batches(items: list[str], size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


async def discover_likes_query_id(client, log=print) -> Discovery:
    """Look for the current id of the ``Likes`` operation in the web bundles."""
    hits: dict[str, list[str]] = {}

    html = await _fetch(client, f"https://{DOMAIN}/")
    if not html:
        log("could not load the x.com home page")
        return Discovery()

    entries: list[str] = []
    for src in _SCRIPT_SRC.findall(html):
        entries.append(_absolute(f"https://{DOMAIN}", src))
    entries.append(ASSET_ROOT + "entry-client.js")  # last resort
    entries = [e for e in dict.fromkeys(entries) if e.endswith(".js")]

    # Level 0: the entry bundles themselves.
    seen: set[str] = set()
    level0 = [u for u in entries if not (u in seen or seen.add(u))]
    result = await _scan(client, level0, needles=NEEDLES, hits=hits)
    if result:
        url, ids = result
        return Discovery(query_id=next(iter(ids)), assets_scanned=1, source=url)

    # Level 1: the assets the entry bundles point at.
    assets: list[str] = []
    for url in level0:
        text = await _fetch(client, url)
        if not text:
            continue
        base = url.rsplit("/", 1)[0]
        for ref in _ASSET.findall(text):
            assets.append(_absolute(base, ref))
    assets = [a for a in dict.fromkeys(assets) if a not in seen][:MAX_ASSETS]
    log(f"scanning {len(assets)} asset(s) for the Likes query id...")

    result = await _scan(client, assets, needles=NEEDLES, hits=hits)
    if result:
        url, ids = result
        return Discovery(query_id=next(iter(ids)), assets_scanned=len(assets), source=url)

    # Level 2: the operation may live in a shared chunk that only the route
    # chunk imports - follow the imports of the assets that talk about the
    # likes route.
    candidates = hits.get("UserLikes") or hits.get("Likes") or []
    deeper: list[str] = []
    for url in candidates[:20]:
        text = await _fetch(client, url)
        if not text:
            continue
        base = url.rsplit("/", 1)[0]
        for ref in _ASSET.findall(text):
            deeper.append(_absolute(base, ref))
    deeper = [
        a
        for a in dict.fromkeys(deeper)
        if a not in seen and a not in assets
    ][:200]
    if deeper:
        log(f"scanning {len(deeper)} nested asset(s)...")
        result = await _scan(client, deeper, needles=NEEDLES, hits=hits)
        if result:
            url, ids = result
            return Discovery(
                query_id=next(iter(ids)),
                assets_scanned=len(assets) + len(deeper),
                source=url,
            )

    return Discovery(assets_scanned=len(assets))


def current_query_id() -> str:
    """The query id the installed client is currently using."""
    from twikit.client.gql import Endpoint

    url = Endpoint.USER_LIKES  # .../i/api/graphql/<id>/Likes
    return url.rsplit("/Likes", 1)[0].rsplit("/", 1)[-1]


def apply_query_id(query_id: str) -> None:
    """Patch the client so the next request uses ``query_id``."""
    from twikit.client.gql import Endpoint

    Endpoint.USER_LIKES = Endpoint.url(f"{query_id}/Likes")


async def repair_likes_endpoint(client, log=print) -> bool:
    """Try to fix a stale Likes query id. Returns True when it worked."""
    old = current_query_id()
    log("Likes endpoint answered 404 - looking for the current query id")
    discovery = await discover_likes_query_id(client, log=log)
    if not discovery.query_id:
        log(
            "could not find a new query id in the web bundles "
            f"({discovery.assets_scanned} asset(s) scanned) - "
            "re-export the cookies and try again"
        )
        return False
    if discovery.query_id == old:
        log(f"the web client still uses {old}")
        return False
    apply_query_id(discovery.query_id)
    log(f"patched Likes query id: {old} -> {discovery.query_id}")
    return True
