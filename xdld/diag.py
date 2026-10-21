"""Human readable diagnostics for failed X requests.

X answers a blocked request with a fairly cryptic body (``status: 422,
message: "{"errors":[...]}``). When something goes wrong it is usually the
risk control system, a stale GraphQL query id or a rate limit - and the
difference matters, because the fix is different for each of them.

:func:`describe_exception` turns the exception into a report (status code,
raw body, interesting response headers) and :func:`hints` explains what the
status code most likely means.
"""

from __future__ import annotations

import re

_STATUS_RE = re.compile(r"status:\s*(\d{3})")
_BODY_RE = re.compile(r'message:\s*"(.*)"\s*$', re.DOTALL)

INTERESTING_HEADERS = (
    "x-rate-limit-limit",
    "x-rate-limit-remaining",
    "x-rate-limit-reset",
    "x-client-transaction-id",
    "x-twitter-active-tw",
    "x-twitter-client-language",
    "cf-ray",
    "content-type",
)

_STATUS_BY_NAME = {
    "BadRequest": 400,
    "Unauthorized": 401,
    "Forbidden": 403,
    "NotFound": 404,
    "RequestTimeout": 408,
    "TooManyRequests": 429,
    "ServerError": 500,
}


def status_code(exc: BaseException) -> int | None:
    """The HTTP status behind the exception, if there is one."""
    match = _STATUS_RE.search(str(exc))
    if match:
        return int(match.group(1))
    return _STATUS_BY_NAME.get(type(exc).__name__)


def response_body(exc: BaseException) -> str:
    """The raw body X sent back (the exception message carries it)."""
    text = str(exc)
    match = _BODY_RE.search(text)
    if match:
        return match.group(1)
    return text


def _headers(exc: BaseException) -> dict[str, str]:
    headers = getattr(exc, "headers", None) or {}
    interesting = {}
    for key, value in headers.items():
        if str(key).lower() in INTERESTING_HEADERS:
            interesting[str(key)] = str(value)
    return interesting


def describe_exception(exc: BaseException, limit: int = 1500) -> str:
    """A multi line report about a failed request."""
    lines = [f"{type(exc).__name__}: {exc}"]

    code = status_code(exc)
    if code:
        lines.append(f"http status : {code}")

    body = response_body(exc).strip()
    if body and body != str(exc).strip():
        if len(body) > limit:
            body = body[:limit] + " ...(truncated)"
        lines.append(f"body        : {body}")

    headers = _headers(exc)
    if headers:
        lines.append("headers     :")
        lines.extend(f"  {key}: {value}" for key, value in headers.items())

    cause = exc.__cause__ or exc.__context__
    if cause is not None and cause is not exc:
        lines.append(f"caused by   : {type(cause).__name__}: {cause}")

    return "\n".join(lines)


def hints(exc: BaseException) -> list[str]:
    """What the status code most likely means and what to do about it."""
    code = status_code(exc)
    text = str(exc).lower()
    name = type(exc).__name__.lower()
    out: list[str] = []

    if code == 403:
        out.append(
            "403 - X refused the request. This is the risk control system (or a "
            "Cloudflare challenge): wait a few minutes, slow down "
            "(--sleep 5 10) and try `--impersonate chrome124` if you installed "
            "the [impersonate] extra."
        )
    if code == 401:
        out.append(
            "401 - the cookies are not accepted any more, export a fresh cookie "
            "jar (see section 3 of the README)."
        )
    if code == 404:
        out.append(
            "404 - the GraphQL query id is outdated. `xdld refresh-ops` looks the "
            "current one up; check and run also repair it automatically."
        )
    if code == 422 or "cannot be null" in text:
        out.append(
            "422 - X rejected the GraphQL variables. A null userId means the "
            "account id could not be read (the login check failed); a stale "
            "query id also shows up here."
        )
    if code == 429 or "too many requests" in text:
        out.append(
            "429 - rate limited. Increase the pause between pages "
            "(`--sleep 5 10`) and lower `--page-size`."
        )
    if code is not None and 500 <= code < 600:
        out.append(f"{code} - X is having a bad day, just retry later.")
    if code is None and ("connect" in name or "timeout" in name or "connect" in text):
        out.append(
            "The connection failed - check that the local proxy is running on "
            "127.0.0.1:7897."
        )
    if "graphql_validation_failed" in text:
        out.append(
            "GRAPHQL_VALIDATION_FAILED - the request itself was wrong: usually a "
            "stale query id or a missing variable."
        )
    if "accountlocked" in name or "captcha" in text:
        out.append(
            "X wants a captcha / unlocked account - slow down, change the IP and "
            "try again later."
        )
    return out
