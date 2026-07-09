from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autonomo_taxes.xolo_api_export import (  # noqa: E402
    normalize_xolo_api_row,
    write_combined_xolo_api_json,
    write_raw_xolo_expense_csv,
)


ENDPOINT = "https://app.xolo.io/selfservice/expense/data"

COLUMNS = [
    ("party", "party"),
    ("categoryText", "categoryText"),
    ("number", "number"),
    ("date", "date"),
    ("paymentDate", "paymentDate"),
    ("amount", "amount"),
    ("status", "status"),
]


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xolo expense table pages through the self-service API")
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-csv", type=Path, required=True)
    parser.add_argument("--length", type=int, default=100)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument(
        "--storage-state",
        type=Path,
        help="Optional Playwright storageState from scripts/xolo_playwright_login.py. "
        "When set, CSRF is read from the live Xolo expense page and XOLO_COOKIE/XOLO_CSRF are not needed.",
    )
    args = parser.parse_args()

    pages = (
        _fetch_pages_with_playwright(args.storage_state, args.start, args.length, args.max_pages)
        if args.storage_state
        else _fetch_pages_with_env(args.start, args.length, args.max_pages)
    )

    rows: list[dict[str, str]] = []
    for page in pages:
        for item in page.get("data") or []:
            rows.append(normalize_xolo_api_row(item))

    write_combined_xolo_api_json(args.out_json, pages)
    write_raw_xolo_expense_csv(args.out_csv, rows)

    print(f"Fetched {len(rows)} expense rows into {args.out_csv}")
    return 0


def _fetch_pages_with_env(start: int, length: int, max_pages: int) -> list[dict[str, Any]]:
    cookie = os.environ.get("XOLO_COOKIE")
    csrf = os.environ.get("XOLO_CSRF")
    if not cookie or not csrf:
        raise SystemExit("Set XOLO_COOKIE and XOLO_CSRF environment variables; do not commit them")
    pages: list[dict[str, Any]] = []
    for draw in range(1, max_pages + 1):
        payload = _payload(draw=draw, start=start, length=length)
        page = _post_page(cookie, csrf, payload)
        data = page.get("data") or []
        pages.append(page)
        total = int(page.get("recordsFiltered") or page.get("recordsTotal") or start + len(data))
        start += length
        if not data or start >= total:
            break
    return pages


def _fetch_pages_with_playwright(storage_state: Path, start: int, length: int, max_pages: int) -> list[dict[str, Any]]:
    if not storage_state.exists():
        raise SystemExit(f"Storage state not found: {storage_state}. Run scripts/xolo_playwright_login.py first.")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment guard.
        raise SystemExit("Playwright is required for --storage-state mode") from exc

    pages: list[dict[str, Any]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(storage_state), viewport={"width": 1600, "height": 1200})
        page = context.new_page()
        page.goto("https://app.xolo.io/selfservice/expense", wait_until="networkidle", timeout=60_000)
        if "/hub/login" in page.url:
            raise SystemExit("Xolo session is not authenticated; rerun scripts/xolo_playwright_login.py")
        csrf = _extract_csrf(page.content())
        for draw in range(1, max_pages + 1):
            payload = _payload(draw=draw, start=start, length=length)
            api_page = _post_page_in_browser(page, csrf, payload)
            data = api_page.get("data") or []
            pages.append(api_page)
            total = int(api_page.get("recordsFiltered") or api_page.get("recordsTotal") or start + len(data))
            start += length
            if not data or start >= total:
                break
        browser.close()
    return pages


def _post_page(cookie: str, csrf: str, payload: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        f"{ENDPOINT}?_csrf={csrf}",
        data=body,
        headers={
            "accept": "application/json, text/javascript, */*; q=0.01",
            "content-type": "application/json; charset=UTF-8",
            "cookie": cookie,
            "origin": "https://app.xolo.io",
            "referer": "https://app.xolo.io/selfservice/expense",
            "user-agent": "Mozilla/5.0",
            "x-csrf-token": csrf,
            "x-requested-with": "XMLHttpRequest",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                content_type = response.headers.get("content-type", "")
                raise SystemExit(
                    "Xolo request returned non-JSON response "
                    f"(HTTP {response.status}, content-type {content_type}). "
                    "The session is likely expired or redirected to login. "
                    f"Response excerpt: {_plain_text(text)[:300]}"
                ) from exc
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Xolo request failed: HTTP {exc.code}: {detail[:500]}") from exc


def _post_page_in_browser(page: Any, csrf: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = page.evaluate(
        """async ({payload, csrf}) => {
          const res = await fetch('/selfservice/expense/data?_csrf=' + encodeURIComponent(csrf), {
            method: 'POST',
            headers: {
              'accept': 'application/json, text/javascript, */*; q=0.01',
              'content-type': 'application/json; charset=UTF-8',
              'x-csrf-token': csrf,
              'x-requested-with': 'XMLHttpRequest'
            },
            body: JSON.stringify(payload)
          });
          const text = await res.text();
          return {status: res.status, contentType: res.headers.get('content-type') || '', text};
        }""",
        {"payload": payload, "csrf": csrf},
    )
    if int(result["status"]) >= 400:
        raise SystemExit(
            "Xolo request failed in Playwright mode: "
            f"HTTP {result['status']}, content-type {result['contentType']}. "
            f"Response excerpt: {_plain_text(result['text'])[:500]}"
        )
    try:
        parsed = json.loads(result["text"])
    except json.JSONDecodeError as exc:
        raise SystemExit(
            "Xolo request returned non-JSON response in Playwright mode "
            f"(HTTP {result['status']}, content-type {result['contentType']}). "
            f"Response excerpt: {_plain_text(result['text'])[:300]}"
        ) from exc
    if not isinstance(parsed, dict):
        raise SystemExit(f"Unexpected Xolo API response shape: {type(parsed).__name__}")
    return parsed


def _payload(draw: int, start: int, length: int) -> dict[str, Any]:
    return {
        "draw": draw,
        "columns": [
            {
                "data": data,
                "name": name,
                "searchable": True,
                "orderable": False,
                "search": {"value": "", "regex": False},
            }
            for data, name in COLUMNS
        ],
        "order": [],
        "start": start,
        "length": length,
        "search": {"value": "", "regex": False},
    }


def _extract_csrf(page_html: str) -> str:
    patterns = [
        r'name="_csrf"\s+value="([^"]+)"',
        r'value="([^"]+)"\s+name="_csrf"',
        r'csrf-token"\s+content="([^"]+)"',
        r'content="([^"]+)"\s+name="csrf-token"',
    ]
    for pattern in patterns:
        match = re.search(pattern, page_html)
        if match:
            return match.group(1)
    raise SystemExit("Could not find Xolo CSRF token on the expense page")


def _plain_text(value: str) -> str:
    import html

    text = re.sub(r"<[^>]*>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


if __name__ == "__main__":
    raise SystemExit(main())
