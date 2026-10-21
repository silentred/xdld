"""Building the X client and checking that the session works."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from xdld.config import Config
from xdld.cookies import CookieError, describe, load_cookies, missing_auth


@dataclass
class SessionInfo:
    logged_in: bool
    user_id: str | None = None
    screen_name: str | None = None
    name: str | None = None
    detail: str = ""


def build_client(cfg: Config):
    """Create a twikit client wired to the local proxy and the cookie jar."""
    from twikit import Client  # imported lazily so --help works without deps

    kwargs: dict = {}
    if cfg.impersonate:
        kwargs["impersonate"] = cfg.impersonate
    client = Client(cfg.language, proxy=cfg.proxy or None, **kwargs)

    jar = load_cookies(cfg.cookies)
    missing = missing_auth(jar)
    if missing:
        raise CookieError(
            f"{cfg.cookies} is missing the cookie(s) {', '.join(missing)}. "
            "auth_token and ct0 are required - export them from a logged-in "
            "x.com browser session."
        )
    client.set_cookies(jar)
    client.cookie_summary = describe(jar)  # type: ignore[attr-defined]
    return client


async def probe_proxy(proxy: str | None, timeout: float = 10.0) -> tuple[bool, str]:
    """Check that the proxy can reach x.com."""
    if not proxy:
        return True, "no proxy configured"
    try:
        async with httpx.AsyncClient(
            proxy=proxy, follow_redirects=True, timeout=timeout
        ) as client:
            response = await client.get("https://x.com/")
        return response.status_code < 400, f"HTTP {response.status_code} from x.com"
    except Exception as exc:  # noqa: BLE001 - the reason is what matters
        return False, f"{type(exc).__name__}: {exc}"


async def check_session(client, retries: int = 3, delay: float = 2.0) -> SessionInfo:
    """Verify the cookies are still accepted by X.

    The local proxy occasionally drops a connection; a couple of retries
    turn such a hiccup into a short pause instead of a failed run.
    """
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            logged_in = await client.is_logged_in()
            last = None
            break
        except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
            last = exc
            if attempt + 1 < retries:
                await asyncio.sleep(delay)
    if last is not None:
        return SessionInfo(False, detail=f"{type(last).__name__}: {last}")

    if not logged_in:
        return SessionInfo(
            False, detail="cookies rejected by X - export a fresh cookie jar"
        )

    user = None
    last: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            user = await client.user()
            last = None
            break
        except Exception as exc:  # noqa: BLE001 - any failure means "not usable"
            last = exc
            if attempt + 1 < retries:
                await asyncio.sleep(delay)
    if user is None:
        return SessionInfo(
            False,
            detail=(
                f"logged in, but the account could not be read: "
                f"{type(last).__name__}: {last}"
            ),
        )

    return SessionInfo(
        True,
        user_id=str(user.id),
        screen_name=user.screen_name,
        name=user.name,
        detail="ok",
    )


async def resolve_user_id(client, screen_name: str | None) -> tuple[str, str]:
    """Return ``(user_id, screen_name)`` for the target account."""
    if not screen_name:
        try:
            user = await client.user()
        except Exception as exc:
            raise RuntimeError(
                f"Could not read the logged-in account: {type(exc).__name__}: {exc}"
            ) from exc
        if user is None or user.id is None:
            raise RuntimeError(
                "X did not return an account id - the session is incomplete, "
                "export a fresh cookie jar"
            )
        return str(user.id), user.screen_name

    user = await client.get_user_by_screen_name(screen_name)
    if user is None:
        raise RuntimeError(f"Could not resolve @{screen_name}")
    return str(user.id), user.screen_name


def run(coro):
    """Tiny helper so the CLI does not have to import asyncio everywhere."""
    return asyncio.run(coro)
