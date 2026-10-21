"""Cookie loading.

X retired the password login flow, so the only supported way in is a cookie
jar exported from a real browser session. Several export formats are accepted.
"""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED = ("auth_token", "ct0")


class CookieError(RuntimeError):
    pass


def _from_netscape(text: str) -> dict[str, str]:
    """Parse a cookies.txt file (the format every browser extension exports)."""
    jar: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 7:
            name, value = parts[5], parts[6]
        else:
            bits = line.split()
            if len(bits) < 7:
                continue
            name, value = bits[5], bits[6]
        jar[name] = value
    return jar


def _from_raw(text: str) -> dict[str, str]:
    """Parse a ``name=value; name=value`` string (what you get from devtools)."""
    jar: dict[str, str] = {}
    for chunk in text.replace("\n", ";").split(";"):
        chunk = chunk.strip()
        if not chunk or "=" not in chunk:
            continue
        name, _, value = chunk.partition("=")
        jar[name.strip()] = value.strip()
    return jar


def parse_cookie_text(text: str) -> dict[str, str]:
    """Best-effort parse of any of the supported cookie representations."""
    text = text.strip()
    if not text:
        return {}
    if text[0] in "[{":
        data = json.loads(text)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
        if isinstance(data, list):
            return {
                str(c["name"]): str(c["value"])
                for c in data
                if isinstance(c, dict) and "name" in c and "value" in c
            }
        raise CookieError("Unsupported cookie JSON structure")
    if "\t" in text or "x.com\t" in text:
        jar = _from_netscape(text)
        if jar:
            return jar
    return _from_raw(text)


def load_cookies(path: str | Path) -> dict[str, str]:
    """Read a cookie jar from ``path``.

    Accepts a browser extension JSON export (list of objects), a flat JSON
    object, a Netscape ``cookies.txt`` or a raw ``name=value; ...`` string.
    """
    p = Path(path)
    if not p.is_file():
        raise CookieError(
            f"Cookie file not found: {p}\n"
            "Export cookies from a logged-in x.com browser session "
            "(extension 'Get cookies.txt LOCALLY' or devtools Application > Cookies)."
        )
    try:
        jar = parse_cookie_text(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise CookieError(f"{p} is not valid JSON: {exc}") from exc
    if not jar:
        raise CookieError(f"No cookies found in {p}")
    return jar


def missing_auth(jar: dict[str, str]) -> list[str]:
    return [name for name in REQUIRED if not jar.get(name)]


def describe(jar: dict[str, str]) -> str:
    names = sorted(jar)
    shown = ", ".join(names[:8]) + (" ..." if len(names) > 8 else "")
    return f"{len(names)} cookie(s): {shown}"
