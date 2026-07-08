from __future__ import annotations

import argparse
import csv
import html
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen


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
            rows.append(_normalize_row(item))
        total = int(page.get("recordsFiltered") or page.get("recordsTotal") or len(rows))
        start += args.length
        if not data or start >= total:
            break

    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps({"pages": pages}, indent=2, ensure_ascii=False), encoding="utf-8")
    args.out_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.out_csv.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "xolo_url",
            "xolo_id",
            "recipient",
            "type",
            "number",
            "date",
            "payment_date",
            "amount_text",
            "amount_original",
            "currency",
            "gross_amount",
            "match_amount",
            "subtotal_amount",
            "vat_amount",
            "vat_percentages",
            "irpf_amount",
            "irpf_percentage",
            "status",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

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
            return json.loads(response.read().decode("utf-8"))
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


def _normalize_row(item: dict[str, Any]) -> dict[str, str]:
    party_html = str(item.get("party") or "")
    amount_text = _text(item.get("amountString") or item.get("amount"))
    amount, detected_currency = _split_amount(amount_text)
    xolo_id = str(item.get("id") or _id_from_party(party_html))
    return {
        "xolo_url": _url_from_id(xolo_id) or _url_from_party(party_html),
        "xolo_id": xolo_id,
        "recipient": _text(party_html),
        "type": _text(item.get("categoryText")),
        "number": _text(item.get("number")),
        "date": _text(item.get("dateString") or item.get("date")),
        "payment_date": _text(item.get("paymentDateString") or item.get("paymentDate")),
        "amount_text": amount_text,
        "amount_original": _text(item.get("amount") if item.get("amount") is not None else amount),
        "currency": _text(item.get("currency")) or detected_currency,
        "gross_amount": _text(item.get("amount")),
        "match_amount": _text(item.get("matchAmount")),
        "subtotal_amount": _text(item.get("subTotalAmount")),
        "vat_amount": _text(item.get("vatAmount")),
        "vat_percentages": _text(item.get("vatPercentages")),
        "irpf_amount": _text(item.get("irpfAmount")),
        "irpf_percentage": _text(item.get("irpfPercentage")),
        "status": _text(item.get("status")),
    }


def _text(value: Any) -> str:
    if value is None:
        return ""
    text = re.sub(r"<[^>]*>", "", str(value))
    return html.unescape(text).strip()


def _url_from_party(value: str) -> str:
    match = re.search(r'href="([^"]+)"', value)
    if not match:
        return ""
    url = html.unescape(match.group(1))
    if url.startswith("/"):
        return "https://app.xolo.io" + url
    return url


def _id_from_party(value: str) -> str:
    url = _url_from_party(value)
    match = re.search(r"/invoice/(\d+)/", url)
    return match.group(1) if match else ""


def _url_from_id(value: str) -> str:
    if not value:
        return ""
    return f"https://app.xolo.io/selfservice/expense/invoice/{value}/details?from=expense"


def _split_amount(value: str) -> tuple[str, str]:
    currency = "EUR" if "€" in value else "USD" if "$" in value else ""
    amount = value.replace("€", "").replace("$", "").replace("\u00a0", " ").strip()
    return amount, currency


if __name__ == "__main__":
    raise SystemExit(main())
