from __future__ import annotations

import csv
import html
import json
from pathlib import Path
import re
from typing import Any


RAW_XOLO_EXPENSE_FIELDS = [
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


def import_xolo_expense_api_json(paths: list[Path]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Load saved Xolo `/selfservice/expense/data` JSON responses.

    The importer intentionally works from already-saved JSON files instead of
    credentials. Live cookies stay in env vars or the browser and never become
    project artifacts.
    """

    pages: list[dict[str, Any]] = []
    rows: list[dict[str, str]] = []
    for path in paths:
        for page in _load_pages(path):
            data = page.get("data")
            if not isinstance(data, list):
                raise ValueError(f"Xolo API JSON page has no data array: {path}")
            pages.append(page)
            for item in data:
                if not isinstance(item, dict):
                    raise ValueError(f"Xolo API row is not an object in {path}: {item!r}")
                rows.append(normalize_xolo_api_row(item))
    return pages, rows


def write_raw_xolo_expense_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_XOLO_EXPENSE_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in RAW_XOLO_EXPENSE_FIELDS})


def write_combined_xolo_api_json(path: Path, pages: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pages": pages}, indent=2, ensure_ascii=False), encoding="utf-8")


def normalize_xolo_api_row(item: dict[str, Any]) -> dict[str, str]:
    party_html = str(item.get("party") or "")
    amount_text = _text(item.get("amountString") or item.get("amount"))
    amount, detected_currency = _split_amount(amount_text)
    xolo_id = str(item.get("id") or _id_from_party(party_html))
    currency = _text(item.get("currency")) or detected_currency
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
        "currency": currency.upper(),
        "gross_amount": _text(item.get("amount")),
        "match_amount": _text(item.get("matchAmount")),
        "subtotal_amount": _text(item.get("subTotalAmount")),
        "vat_amount": _text(item.get("vatAmount")),
        "vat_percentages": _text(item.get("vatPercentages")),
        "irpf_amount": _text(item.get("irpfAmount")),
        "irpf_percentage": _text(item.get("irpfPercentage")),
        "status": _text(item.get("status")),
    }


def _load_pages(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Cannot parse {path} as JSON. Save the Xolo API response body, not the copied curl command or login HTML."
        ) from exc
    if isinstance(payload, dict) and isinstance(payload.get("pages"), list):
        pages = payload["pages"]
    elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
        pages = [payload]
    elif isinstance(payload, list):
        pages = payload
    else:
        raise ValueError(f"Unsupported Xolo API JSON shape in {path}; expected a page object, list, or {{\"pages\": [...]}}")
    output: list[dict[str, Any]] = []
    for index, page in enumerate(pages, start=1):
        if not isinstance(page, dict):
            raise ValueError(f"Xolo API page {index} in {path} is not an object")
        output.append(page)
    return output


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
