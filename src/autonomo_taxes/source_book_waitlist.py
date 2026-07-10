from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path


SOURCE_BOOK_WAITLIST_FIELDS = [
    "period",
    "review_order",
    "priority",
    "acceptance_status",
    "target_casilla_02_delta",
    "source_book_reconciliation_status",
    "required_deliverables",
    "missing_deliverables",
    "matched_deliverables",
    "missing_count",
    "matched_count",
    "required_evidence",
    "next_action",
    "package_status",
    "dropzone",
]

FOUND_STATUSES = {"found", "ready_for_intake", "content_ready"}


def build_source_book_waitlist(
    *,
    quarter_acceptance_csv: Path,
    source_book_response_check_csv: Path,
    source_book_reconciliation_csv: Path,
    source_book_availability_csv: Path | None = None,
    response_root: Path | None = None,
) -> list[dict[str, str]]:
    acceptance_rows = _load_rows(quarter_acceptance_csv)
    response_rows = [
        row for row in _load_rows(source_book_response_check_csv) if row.get("check") != "package_verdict"
    ]
    reconciliation_by_period = {
        row.get("period", ""): row.get("status", "") for row in _load_rows(source_book_reconciliation_csv)
    }
    package_status = _package_status(source_book_availability_csv)
    dropzone = str(response_root or Path("evidence") / "xolo-source-books")

    rows: list[dict[str, str]] = []
    for acceptance in acceptance_rows:
        period = acceptance.get("period", "")
        if not period or period.count("-") != 1:
            continue
        required = [row for row in response_rows if _required_for_period(row, period)]
        missing = [row for row in required if row.get("status", "") not in FOUND_STATUSES]
        matched = [row for row in required if row.get("status", "") in FOUND_STATUSES]
        rows.append(
            {
                "period": period,
                "review_order": acceptance.get("review_order", ""),
                "priority": acceptance.get("priority", ""),
                "acceptance_status": acceptance.get("acceptance_status", ""),
                "target_casilla_02_delta": acceptance.get("target_casilla_02_delta", ""),
                "source_book_reconciliation_status": reconciliation_by_period.get(period, ""),
                "required_deliverables": "; ".join(_label(row) for row in required),
                "missing_deliverables": "; ".join(_label(row) for row in missing),
                "matched_deliverables": "; ".join(_label(row) for row in matched),
                "missing_count": str(len(missing)),
                "matched_count": str(len(matched)),
                "required_evidence": acceptance.get("required_evidence", ""),
                "next_action": _next_action(acceptance, missing, dropzone),
                "package_status": package_status,
                "dropzone": dropzone,
            }
        )
    return rows


def write_source_book_waitlist_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_WAITLIST_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_waitlist_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    open_rows = [row for row in rows if row.get("acceptance_status") != "accepted_from_source_books"]
    first_open = open_rows[0] if open_rows else {}
    priority_counts = Counter(row.get("priority", "") for row in open_rows)
    status_counts = Counter(row.get("acceptance_status", "") for row in open_rows)
    package_status = rows[0].get("package_status", "unknown") if rows else "unknown"
    dropzone = rows[0].get("dropzone", str(Path("evidence") / "xolo-source-books")) if rows else ""
    unique_missing = sorted(
        {
            item.strip()
            for row in open_rows
            for item in row.get("missing_deliverables", "").split(";")
            if item.strip()
        }
    )

    lines = [
        "# Xolo Source-Book Waitlist",
        "",
        "This report is the chronological stop list for the Modelo 130 audit.",
        "It keeps quarter closure blocked on Xolo's official IRPF registers instead of fitting unknown accounting treatment from targets.",
        "",
        "## Verdict",
        "",
        f"- Open quarters: `{len(open_rows)}` of `{len(rows)}`.",
        f"- First open quarter: `{first_open.get('period', 'none')}`.",
        f"- Request package status: `{package_status}`.",
        f"- Response dropzone: `{dropzone}`.",
        f"- Missing deliverable types/scopes: `{len(unique_missing)}`.",
        f"- Priority counts: `{_counts(priority_counts)}`.",
        f"- Acceptance status counts: `{_counts(status_counts)}`.",
        "",
        "## Next Action",
        "",
        "Send or paste `runs/xolo_source_book_request_package/message_to_xolo.md` to Xolo support.",
        f"When Xolo replies, place the received CSV/XLSX/PDF files under `{dropzone}` and run `audit-source-book-refresh`.",
        "Do not close an open quarter by guessing Apple amortization, VAT base treatment, or row inclusion from the filed target.",
        "",
        "## Quarters",
        "",
        "| Period | Priority | Acceptance | Target 02 delta | Source-book reconciliation | Missing deliverables | Next action |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["period"]),
                    _cell(row["priority"]),
                    _cell(row["acceptance_status"]),
                    _cell(row["target_casilla_02_delta"]),
                    _cell(row["source_book_reconciliation_status"]),
                    _cell(row["missing_deliverables"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    if unique_missing:
        lines.extend(["## Missing Deliverables", ""])
        for item in unique_missing:
            lines.append(f"- `{item}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _required_for_period(row: dict[str, str], period: str) -> bool:
    required_for = row.get("required_for", "")
    required_periods = [item.strip() for item in required_for.split(",") if item.strip()]
    if period in required_periods:
        return True
    if required_for in {"all quarters", "all quarters in quarter acceptance matrix"}:
        return True
    scope = row.get("scope", "")
    return scope == period or (len(scope) == 4 and period.startswith(f"{scope}-"))


def _label(row: dict[str, str]) -> str:
    check = row.get("check", "")
    scope = row.get("scope", "")
    return f"{check}:{scope}" if scope else check


def _next_action(acceptance: dict[str, str], missing: list[dict[str, str]], dropzone: str) -> str:
    if acceptance.get("acceptance_status") == "accepted_from_source_books":
        return "No action; this quarter is accepted from source-book evidence."
    if missing:
        labels = ", ".join(_label(row) for row in missing)
        return f"Request/add missing Xolo deliverables to {dropzone}: {labels}."
    return acceptance.get("next_action", "") or "Import and reconcile the available Xolo source-book deliverables."


def _package_status(path: Path | None) -> str:
    if path is None or not path.exists():
        return "not_checked"
    rows = _load_rows(path)
    package = next((row for row in rows if row.get("source") == "xolo_source_book_request_package"), None)
    if package:
        return package.get("status", "")
    request = next((row for row in rows if row.get("source") == "xolo_support_request"), None)
    return request.get("status", "not_found") if request else "not_found"


def _counts(counts: Counter[str]) -> str:
    if not counts:
        return "none"
    return "; ".join(f"{key or 'blank'}={value}" for key, value in sorted(counts.items()))


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
