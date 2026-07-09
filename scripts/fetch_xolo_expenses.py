from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
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
    args = parser.parse_args()

    cookie = os.environ.get("XOLO_COOKIE")
    csrf = os.environ.get("XOLO_CSRF")
    if not cookie or not csrf:
        raise SystemExit("Set XOLO_COOKIE and XOLO_CSRF environment variables; do not commit them")

    pages: list[dict[str, Any]] = []
    rows: list[dict[str, str]] = []
    start = args.start
    for draw in range(1, args.max_pages + 1):
        payload = _payload(draw=draw, start=start, length=args.length)
        page = _post_page(cookie, csrf, payload)
        data = page.get("data") or []
        pages.append(page)
        for item in data:
            rows.append(normalize_xolo_api_row(item))
        total = int(page.get("recordsFiltered") or page.get("recordsTotal") or len(rows))
        start += args.length
        if not data or start >= total:
            break

    write_combined_xolo_api_json(args.out_json, pages)
    write_raw_xolo_expense_csv(args.out_csv, rows)

    print(f"Fetched {len(rows)} expense rows into {args.out_csv}")
    return 0


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


def _plain_text(value: str) -> str:
    import html
    import re

    text = re.sub(r"<[^>]*>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


if __name__ == "__main__":
    raise SystemExit(main())
