#!/usr/bin/env python3
"""Export x.com cookies without a browser extension.

Opens a real Chromium window, waits until you log in by hand, then writes
``cookies.json`` (the format ``xdld`` understands) next to this script.

    .venv/bin/pip install playwright
    .venv/bin/playwright install chromium
    .venv/bin/python tools/cookies_via_playwright.py

The browser window is yours - nothing is typed for you, X still requires the
interactive login (password, 2FA, captcha). Cookies are only written to the
local file, they are never sent anywhere else.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

OUT = Path(__file__).resolve().parent.parent / "cookies.json"


async def main() -> int:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto("https://x.com/i/flow/login", wait_until="domcontentloaded")

        print("A browser window is open - log in to x.com there.")
        print("Waiting for the login to finish (up to 5 minutes)...\n")

        deadline = 300
        cookies = []
        while deadline > 0:
            await asyncio.sleep(3)
            deadline -= 3
            cookies = await context.cookies("https://x.com")
            names = {c["name"] for c in cookies}
            if {"auth_token", "ct0"} <= names:
                break
            status = "yes" if "auth_token" in names else "no"
            print(f"  ...still waiting (auth_token: {status})", end="\r")

        await browser.close()

    names = {c["name"] for c in cookies}
    if not {"auth_token", "ct0"} <= names:
        print("\nLogin did not finish - no auth_token/ct0 in the cookie jar.")
        return 1

    OUT.write_text(
        json.dumps(
            [
                {"name": c["name"], "value": c["value"], "domain": c["domain"]}
                for c in cookies
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\nWrote {len(cookies)} cookies to {OUT}")
    print("Next: .venv/bin/xdld check")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
