from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .money import format_es, parse_amount


SOURCE_BOOK_AVAILABILITY_FIELDS = [
    "source",
    "status",
    "evidence_count",
    "evidence",
    "conclusion",
    "next_action",
]


def build_source_book_availability(
    *,
    first_gate_csv: Path,
    evidence_inventory_csv: Path,
    dataexport_inventory_csv: Path,
    dataexport_archives_csv: Path,
    storage_probe_json: Path,
    support_request_md: Path,
    request_package_manifest_csv: Path | None = None,
) -> list[dict[str, str]]:
    rows = [
        _first_gate_row(first_gate_csv),
        _local_archive_row(evidence_inventory_csv),
        _latest_dataexport_row(dataexport_inventory_csv),
        _historical_dataexports_row(dataexport_archives_csv),
        _authenticated_probe_row(storage_probe_json),
        _support_request_row(support_request_md),
    ]
    if request_package_manifest_csv is not None:
        rows.append(_request_package_row(request_package_manifest_csv))
    return rows


def write_source_book_availability_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_AVAILABILITY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_availability_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    missing_sources = [row for row in rows if row["status"] in {"missing_required_evidence", "available_but_insufficient"}]
    request_rows = [row for row in rows if row["status"] == "ready_to_send"]
    package_rows = [row for row in rows if row["status"] == "ready_to_send_package"]
    candidate_rows = [row for row in rows if row["status"] == "candidate_found"]
    lines = [
        "# Xolo Source-Book Availability",
        "",
        "This report consolidates the local archive, Xolo data-export archives, authenticated UI probe, and first-gate status.",
        "It is an evidence availability report, not tax advice and not official-register evidence.",
        "",
        "## Conclusion",
        "",
        f"- Evidence surfaces checked: `{len(rows)}`.",
        f"- Surfaces missing or insufficient for source-book closure: `{len(missing_sources)}`.",
    ]
    if package_rows:
        lines.append(f"- Source-book request package is ready: `{package_rows[0]['evidence']}`.")
    elif request_rows:
        lines.append("- Xolo support request is ready: `runs/xolo_support_request_short.md`.")
    if candidate_rows:
        lines.append(
            "- Current local conclusion: at least one candidate source-book or asset/amortization file was found; inspect it before treating the evidence as unavailable."
        )
    else:
        lines.append(
            "- Current local conclusion: official-register rows and the investment-goods amortization book are not available in the discovered local/export/UI surfaces."
        )
    lines.extend(
        [
            "- Do not tune downstream amortization as confirmed accounting until Xolo provides the official registers.",
            "",
            "## Evidence Matrix",
            "",
            "| Source | Status | Count | Evidence | Conclusion | Next action |",
            "|---|---|---:|---|---|---|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["source"]),
                    _cell(row["status"]),
                    _cell(row["evidence_count"]),
                    _cell(row["evidence"]),
                    _cell(row["conclusion"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _first_gate_row(path: Path) -> dict[str, str]:
    rows = _load_rows(path)
    gate = next((row for row in rows if row.get("section") == "gate"), {})
    if not gate:
        return _row(
            "first_chronological_gate",
            "no_first_gate_row",
            "0",
            "No gate row was found in the first-gate report.",
            "The sequential gate status cannot be derived from this input.",
            "Regenerate runs/modelo130_first_gate.csv before relying on this availability report.",
        )
    context = next((row for row in rows if row.get("section") == "raw_context"), {})
    period = gate.get("period", "")
    gate_status = gate.get("status", "") or "gate_present"
    submitted_delta = _fmt(gate.get("amount_eur", ""))
    local_non_asset = _fmt(context.get("amount_eur", ""))
    residual = _fmt(context.get("fit_signal", ""))
    evidence = (
        f"{period}: submitted casilla 02 delta {submitted_delta}; "
        f"confirmed local non-asset rows {local_non_asset}; routing residual {residual}"
    ).strip("; ")
    return _row(
        "first_chronological_gate",
        gate_status,
        "1",
        evidence,
        _first_gate_conclusion(gate_status, period),
        _first_gate_next_action(gate_status),
    )


def _local_archive_row(path: Path) -> dict[str, str]:
    rows = {row.get("category", ""): row for row in _load_rows(path)}
    source_book_count = _count(rows, "candidate_source_book_row_evidence")
    asset_count = _count(rows, "candidate_asset_schedule")
    local_files = _count(rows, "scan_local_files")
    zip_members = _count(rows, "scan_zip_members")
    status = "missing_required_evidence" if source_book_count == 0 and asset_count == 0 else "candidate_found"
    if status == "candidate_found":
        conclusion = "Local Google Drive archive has candidate source-book or asset/amortization files by filename inventory."
        next_action = "Inspect candidate paths before treating the local archive as missing source-book evidence."
    else:
        conclusion = "Local Google Drive archive does not contain the source-book row export or asset schedule by filename inventory."
        next_action = "Use as negative local-archive evidence in the Xolo request."
    return _row(
        "google_drive_xolo_export_archive",
        status,
        str(source_book_count + asset_count),
        f"Scanned {local_files} local files and {zip_members} ZIP members; source-book candidates={source_book_count}; asset/amortization candidates={asset_count}.",
        conclusion,
        next_action,
    )


def _latest_dataexport_row(path: Path) -> dict[str, str]:
    rows = _load_rows(path)
    by_key = {(row.get("kind", ""), row.get("key", "")): row for row in rows}
    candidate_count = _count(by_key, ("conclusion", "candidate_source_book_or_asset_schedule"))
    expense_count = _count(by_key, ("top_level", "EXPENSE"))
    invoice_count = _count(by_key, ("top_level", "INVOICE"))
    tax_count = _count(by_key, ("top_level", "TAX_REPORT"))
    status = "missing_required_evidence" if candidate_count == 0 else "candidate_found"
    if status == "candidate_found":
        conclusion = "Latest downloaded Xolo data export contains source-book or asset/amortization filename candidates."
        next_action = "Inspect the candidate paths before requesting a separate Xolo source-book export."
    else:
        conclusion = "Latest downloaded Xolo data export appears to be an evidence-document archive, not bookkeeping source books."
        next_action = "Ask Xolo for source-book exports separately from the normal data export."
    return _row(
        "latest_xolo_dataexport_zip",
        status,
        str(candidate_count),
        f"Latest ZIP contains EXPENSE={expense_count}, INVOICE={invoice_count}, TAX_REPORT={tax_count}, but source-book/asset filename candidates={candidate_count}.",
        conclusion,
        next_action,
    )


def _historical_dataexports_row(path: Path) -> dict[str, str]:
    rows = _load_rows(path)
    archive_count = len(rows)
    candidate_count = sum(_int(row.get("candidate_source_book_or_asset_files", "")) for row in rows)
    status = "missing_required_evidence" if candidate_count == 0 else "candidate_found"
    if status == "candidate_found":
        conclusion = "At least one historical Xolo data export contains source-book or asset/amortization filename candidates."
        next_action = "Inspect historical candidate paths and rerun the quarter audit if they contain source-book rows or an asset schedule."
    else:
        conclusion = "Historical data exports do not expose source-book row evidence or asset schedule by filename inventory."
        next_action = "Keep the support request focused on books/schedule, not another normal data export."
    return _row(
        "historical_xolo_dataexport_archives",
        status,
        str(candidate_count),
        f"Compared {archive_count} historical ZIP exports; filename-level source-book/asset candidates={candidate_count}.",
        conclusion,
        next_action,
    )


def _authenticated_probe_row(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pages = payload.get("pages", [])
    login_like = [
        page
        for page in pages
        if "log in" in str(page.get("title", "")).lower() or "/hub/login" in str(page.get("final_url", ""))
    ]
    forbidden_asset_routes = [
        page
        for page in pages
        if int(page.get("status") or 0) == 403
        and any(term in str(page.get("source_url", "")).lower() for term in ["asset", "fixed", "depreci", "amort"])
    ]
    calculation_pages = [
        page
        for page in pages
        if "/tax-report/calculation/130/" in str(page.get("source_url", ""))
        and int(page.get("status") or 0) == 200
    ]
    if login_like:
        status = "auth_probe_not_authenticated"
        conclusion = "The probe hit login-like pages, so it cannot prove the current authenticated UI surface."
        next_action = "Refresh Xolo login and rerun the authenticated probe before using UI availability evidence."
    elif not calculation_pages:
        status = "probe_inconclusive"
        conclusion = "The probe did not reach Modelo 130 calculation pages, so it is insufficient as source-book availability evidence."
        next_action = "Rerun the authenticated probe or inspect the tax-report UI before relying on this row."
    else:
        status = "available_but_insufficient"
        conclusion = "Discovered UI/API surface exposes expense rows/details and quarter-level calculations, but not source-book rows or an asset schedule."
        next_action = "Treat UI data as supporting evidence only; ask Xolo for internal books/schedule."
    evidence = (
        f"Authenticated probe pages={len(pages)}; login-like pages={len(login_like)}; "
        f"Modelo 130 calculation pages={len(calculation_pages)}; forbidden asset/amortization routes={len(forbidden_asset_routes)}."
    )
    return _row(
        "authenticated_xolo_ui_probe",
        status,
        str(len(pages)),
        evidence,
        conclusion,
        next_action,
    )


def _support_request_row(path: Path) -> dict[str, str]:
    exists = path.exists()
    return _row(
        "xolo_support_request",
        "ready_to_send" if exists else "missing",
        "1" if exists else "0",
        str(path),
        "Prepared first-contact request for official IRPF registers.",
        "Send or paste the request to Xolo support; do not infer accounting treatment from local fits.",
    )


def _request_package_row(path: Path) -> dict[str, str]:
    if not path.exists():
        return _row(
            "xolo_source_book_request_package",
            "missing",
            "0",
            str(path),
            "The safe official-register request package manifest was not found.",
            "Build the package before sending a Xolo official-register request.",
        )
    rows = _load_rows(path)
    included = [row for row in rows if row.get("status") == "included"]
    missing_or_broken = [row for row in rows if row.get("status", "").startswith("missing_")]
    safety_excluded = [row for row in rows if row.get("status", "").startswith("excluded_")]
    message_rows = [row for row in included if row.get("role") == "message"]
    status = "ready_to_send_package" if message_rows and not missing_or_broken else "package_attention"
    evidence = (
        f"{path}; included={len(included)}; safety_excluded={len(safety_excluded)}; "
        f"missing_or_broken={len(missing_or_broken)}"
    )
    if status == "ready_to_send_package":
        conclusion = "A safe request package exists with the official-register message manifest."
        next_action = "Send or paste message_to_xolo.md; attach markdown support context only if Xolo asks for it."
    else:
        conclusion = "The request package manifest is present but not ready to send."
        next_action = "Regenerate the request package and resolve missing or broken inputs before sending."
    return _row(
        "xolo_source_book_request_package",
        status,
        str(len(rows)),
        evidence,
        conclusion,
        next_action,
    )


def _row(
    source: str,
    status: str,
    evidence_count: str,
    evidence: str,
    conclusion: str,
    next_action: str,
) -> dict[str, str]:
    return {
        "source": source,
        "status": status,
        "evidence_count": evidence_count,
        "evidence": evidence,
        "conclusion": conclusion,
        "next_action": next_action,
    }


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _count(rows: dict[Any, dict[str, str]], key: Any) -> int:
    return _int(rows.get(key, {}).get("count", ""))


def _int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _fmt(value: str) -> str:
    return format_es(parse_amount(value)) if value else ""


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _first_gate_conclusion(gate_status: str, period: str) -> str:
    if "blocked" in gate_status:
        return f"The sequential audit cannot honestly close {period} or downstream amortization without source-book row treatment."
    return f"First-gate report status is {gate_status}; source-book availability should be interpreted against that current gate status."


def _first_gate_next_action(gate_status: str) -> str:
    if "blocked" in gate_status:
        return "Start the external request with this gate."
    return "Review whether the next chronological gate should replace this request focus."
