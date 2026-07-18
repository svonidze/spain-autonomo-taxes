from __future__ import annotations

from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from typing import Any, Mapping

from .cash_check import build_cash_check
from .tax_calendar import build_period_ics


DATA_BLOCKER_KINDS = {
    "validation_issue",
    "document_review",
    "transaction_review",
    "target_derived_fx",
    "xolo_recorded_fx_after_cutover",
    "invalid_amount",
    "out_of_period",
    "supplier_invoice_missing",
    "amortization_asset_link_missing",
    "calendar_deadline_mismatch",
    "confirmed_deadline_missing",
}


def build_period_preparation(
    *,
    period: Mapping[str, Any],
    as_of: date,
    dashboard: Mapping[str, Any],
    calculations: Mapping[str, Mapping[str, Any]],
    obligations: list[dict[str, Any]],
    available_eur: Decimal | None,
    cash_buffer_eur: Decimal | None,
) -> dict[str, Any]:
    period_key = str(period["period_key"])
    period_ended = as_of > date.fromisoformat(str(period["ends_on"]))
    due_forms = {
        str(row["obligation_code"])
        for row in obligations
        if row.get("determination") == "due"
        and row.get("filing_status") not in {"filed", "waived"}
    }
    unknown_forms = sorted(
        str(row["obligation_code"])
        for row in obligations
        if row.get("determination") == "unknown"
    )
    dashboard_blockers = [dict(row) for row in dashboard.get("blocking_items", [])]
    data_blockers = [row for row in dashboard_blockers if row.get("kind") in DATA_BLOCKER_KINDS]
    workflow_items = [
        row
        for row in dashboard_blockers
        if row.get("kind") in {"approved_not_posted", "future_posted_after_as_of"}
    ]
    expected_items = [
        dict(row)
        for row in dashboard.get("expected_items", [])
        if row.get("kind") == "approved_forecast_pending"
    ]
    calculation_blockers = [
        {"form": form, "reason": calculation.get("reason", "calculation blocked")}
        for form, calculation in calculations.items()
        if calculation.get("blocked")
    ]
    cash = build_cash_check(
        calculations,
        due_forms=due_forms,
        available_eur=available_eur,
        buffer_eur=cash_buffer_eur,
    )
    filing_blockers: list[dict[str, Any]] = []
    if period_ended and workflow_items:
        filing_blockers.append(
            {
                "kind": "workflow_not_posted",
                "detail": f"{len(workflow_items)} reviewed row(s) still need posting",
            }
        )
    if period_ended and not cash["tax_payment_ready"]:
        filing_blockers.append(
            {
                "kind": "cash_not_ready",
                "detail": f"cash-check status is {cash['status']}",
            }
        )

    preparation_ready = not data_blockers and not unknown_forms and not calculation_blockers
    filing_ready = bool(
        period_ended
        and preparation_ready
        and not workflow_items
        and cash["tax_payment_ready"]
    )
    if not preparation_ready:
        status = "blocked"
    elif filing_ready:
        status = "filing_ready"
    elif period_ended:
        status = "blocked"
    else:
        status = "in_progress"

    return {
        "schema_version": 1,
        "report_type": "quarter_tax_preparation",
        "period": period_key,
        "as_of": as_of.isoformat(),
        "status": status,
        "preparation_ready": preparation_ready,
        "filing_ready": filing_ready,
        "submission_mode": "manual_aeat",
        "period_ended_as_of": period_ended,
        "period_ends_on": period["ends_on"],
        "due_forms": sorted(due_forms),
        "unknown_forms": unknown_forms,
        "obligations": obligations,
        "calculations": calculations,
        "cash_check": cash,
        "data_blockers": data_blockers,
        "workflow_items": workflow_items,
        "expected_items": expected_items,
        "calculation_blockers": calculation_blockers,
        "filing_blockers": filing_blockers,
        "manual_submission_steps": [
            "Compare each AEAT casilla with the generated form report.",
            "Enter or import the reviewed values in the AEAT form.",
            "Choose payment or compensation using the cash-check result.",
            "Submit with the taxpayer certificate and download the filed PDF/justificante.",
            "Record the immutable filed snapshot and archive its evidence.",
        ],
    }


def write_period_preparation(
    report: Mapping[str, Any],
    output_dir: str | Path,
) -> dict[str, Path]:
    root = Path(output_dir)
    calculations_dir = root / "calculations"
    calculations_dir.mkdir(parents=True, exist_ok=True)

    outputs: dict[str, Path] = {}
    for form, calculation in sorted(report["calculations"].items()):
        path = calculations_dir / f"modelo{form}.json"
        path.write_text(_json(calculation), encoding="utf-8")
        outputs[f"modelo{form}"] = path
        markdown_path = calculations_dir / f"modelo{form}.md"
        markdown_path.write_text(
            _calculation_markdown(form, calculation),
            encoding="utf-8",
        )
        outputs[f"modelo{form}_markdown"] = markdown_path

    cash_path = root / "cash-check.json"
    cash_path.write_text(_json(report["cash_check"]), encoding="utf-8")
    outputs["cash_check"] = cash_path

    period_end = date.fromisoformat(str(report["period_ends_on"]))
    ics_path = root / "tax-actions.ics"
    ics_path.write_text(
        build_period_ics(
            period_key=str(report["period"]),
            period_ends_on=period_end,
            obligations=list(report["obligations"]),
        ),
        encoding="utf-8",
        newline="",
    )
    outputs["calendar_ics"] = ics_path

    json_path = root / "preparation.json"
    markdown_path = root / "preparation.md"
    json_path.write_text(_json(report), encoding="utf-8")
    markdown_path.write_text(_markdown(report), encoding="utf-8")
    outputs["json"] = json_path
    outputs["markdown"] = markdown_path
    return outputs


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n"


def _calculation_markdown(form: str, calculation: Mapping[str, Any]) -> str:
    lines = [f"# Modelo {form}", ""]
    if calculation.get("blocked"):
        lines.extend([f"Blocked: {calculation.get('reason', 'unknown reason')}.", ""])
        return "\n".join(lines)
    values = calculation.get("values") or {}
    casillas = sorted(
        ((key, value) for key, value in values.items() if str(key).isdigit()),
        key=lambda item: int(str(item[0])),
    )
    lines.extend(["| Casilla | Value (EUR unless noted) |", "| --- | ---: |"])
    lines.extend(f"| {key} | {value} |" for key, value in casillas)
    entries = sorted(
        (str(key), value)
        for key, value in values.items()
        if not str(key).isdigit()
        and key not in {"result", "compensation_carryforward", "difficult_expenses"}
    )
    if entries:
        lines.extend(["", "## Declared entries", "", "| Key | Value |", "| --- | ---: |"])
        lines.extend(f"| {key} | {value} |" for key, value in entries)
    elif form == "349":
        lines.extend(["", "No intra-community operations declared."])
    summary_keys = [
        key
        for key in ("result", "compensation_carryforward", "difficult_expenses")
        if key in values
    ]
    if summary_keys:
        lines.extend(["", "## Summary", ""])
        lines.extend(f"- {key}: {values[key]}" for key in summary_keys)
    lines.extend(["", "## Lineage", ""])
    lineage = calculation.get("lineage") or {}
    if lineage:
        for key in sorted(lineage):
            lines.append(f"- Casilla {key}: {len(lineage[key])} ledger row(s).")
    else:
        lines.append("No row lineage recorded.")
    lines.append("")
    return "\n".join(lines)


def _markdown(report: Mapping[str, Any]) -> str:
    cash = report["cash_check"]
    lines = [
        f"# {report['period']} tax preparation",
        "",
        f"Status: **{report['status']}**. Submission mode: manual AEAT.",
        "",
        "## Forms",
        "",
    ]
    for form in report["due_forms"]:
        calculation = report["calculations"].get(form, {})
        result = calculation.get("values", {}).get("result")
        lines.append(f"- Modelo {form}: result {result if result is not None else 'review required'} EUR.")
    lines.extend(
        [
            "",
            "## Cash",
            "",
            f"- Tax required: {cash['required_tax_eur']} EUR.",
            f"- Recommended reserve: {cash['recommended_reserve_eur']} EUR.",
            f"- Available: {cash['available_eur'] if cash['available_eur'] is not None else 'not checked'} EUR.",
            f"- Status: {cash['status']}.",
            "",
            "## Blockers",
            "",
        ]
    )
    blockers = (
        list(report["data_blockers"])
        + list(report["calculation_blockers"])
        + list(report["filing_blockers"])
    )
    blockers.extend(
        {
            "kind": "unknown_obligation",
            "reference": form,
            "detail": "applicability is unresolved",
        }
        for form in report["unknown_forms"]
    )
    if blockers:
        for blocker in blockers:
            kind = blocker.get("kind") or "calculation"
            reference = blocker.get("reference") or blocker.get("form") or ""
            detail = blocker.get("detail") or blocker.get("reason") or ""
            lines.append(f"- {kind}: {reference} {detail}".rstrip())
    else:
        lines.append("None.")
    lines.extend(["", "## Manual filing", ""])
    lines.extend(
        f"{index}. {step}"
        for index, step in enumerate(report["manual_submission_steps"], start=1)
    )
    lines.append("")
    return "\n".join(lines)
