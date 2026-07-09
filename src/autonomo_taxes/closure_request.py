from __future__ import annotations

import csv
from pathlib import Path

from .money import format_es, parse_amount


def build_xolo_closure_request(
    quarter_closure_csv: Path,
    source_findings_csv: Path | None = None,
    row_decisions_csv: Path | None = None,
) -> str:
    rows = _load_rows(quarter_closure_csv)
    material = [row for row in rows if row["closure_status"] == "blocked_material_unexplained_adjustment"]
    asset = [row for row in rows if row["has_asset_decision"] == "yes"]
    exclusions = [row for row in rows if row["has_nearest_exclusion"] == "yes"]
    source_findings = _source_finding_highlights(source_findings_csv) if source_findings_csv else []
    row_decisions = _row_decision_highlights(row_decisions_csv) if row_decisions_csv else []

    lines = [
        "# Xolo Modelo 130 Closure Request",
        "",
        "Draft-only artifact. Do not send automatically.",
        "",
        "## Request To Xolo",
        "",
        "Hello,",
        "",
        "I am reconstructing the submitted Modelo 130 declarations from 2023-Q2 through 2026-Q2 and need the row-level basis that Xolo used.",
        "The expense UI export is not sufficient because it does not show the submitted Modelo 130 register, row inclusion, amortization, or deductible basis per row.",
        "",
        "Please provide:",
        "",
        "1. The submitted Modelo 130 expense register for every quarter from 2023-Q2 through 2026-Q2, with date, supplier, invoice number, category, original amount, EUR deductible amount used in casilla 02, and whether the amount was gross, VAT-base, excluded, netted, reversed, or adjusted.",
        "2. The full asset amortization schedule used for Modelo 130 and annual Renta/Modelo 100: asset, acquisition date, acquisition basis, VAT treatment, start date, amortization rate, quarterly amortization amount, and accumulated amortization by quarter.",
        "3. The source rows or accounting adjustments for the material quarter gaps listed below.",
        "",
        "## Material Quarter Gaps",
        "",
        "| Period | Target 02 delta | Pre-plug residual | Balancing adjustment | Plug % | Required answer |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for row in material:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["target_casilla_02_delta"]),
                    _fmt(row["pre_plug_model_minus_target_eur"]),
                    _fmt(row["balancing_adjustment_eur"]),
                    row["balancing_adjustment_pct_of_target"] + "%",
                    _cell(row["next_question"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Asset Schedule Rows To Confirm",
            "",
            "| Period | Candidate amortization | Asset rows |",
            "|---|---:|---|",
        ]
    )
    for row in asset:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    _fmt(row["candidate_amortization_delta"]),
                    _cell(row["asset_rows"] or "no direct asset row this quarter; schedule carry-forward"),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Excluded Or Netted Candidate Rows",
            "",
            "| Period | Candidate rows |",
            "|---|---|",
        ]
    )
    for row in exclusions:
        lines.append("| " + row["period"] + " | " + _cell(row["nearest_exclusion_rows"]) + " |")

    if row_decisions:
        lines.extend(
            [
                "",
                "## Concrete Row-Level Decisions",
                "",
                "These are the shortest row-level checks needed to close the remaining residuals. Please answer with the submitted Modelo 130 deductible EUR amount and treatment for each row.",
                "",
                "| Priority | Period | Decision | Row | Amount | Question |",
                "|---|---|---|---|---:|---|",
            ]
        )
        for row in row_decisions:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["priority"],
                        row["period"],
                        _cell(row["decision_kind"]),
                        _cell(_row_label(row)),
                        _fmt(row["amount_eur"]),
                        _cell(row["question"]),
                    ]
                )
                + " |"
            )

    if source_findings:
        lines.extend(
            [
                "",
                "## Source-Finding Highlights To Preserve",
                "",
                "These rows are already checked locally or deliberately marked as unconfirmed. Please do not treat a target-fitting hypothesis as a confirmed Xolo filing decision.",
                "",
                "| Period | Row | Status | Finding | Impact |",
                "|---|---|---|---|---|",
            ]
        )
        for row in source_findings:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["period"],
                        _cell(row["row_ref"]),
                        _cell(row["source_status"]),
                        _cell(row["finding"]),
                        _cell(row["impact"]),
                    ]
                )
                + " |"
            )

    lines.extend(
        [
            "",
            "## Local Evidence Already Checked",
            "",
            "- Local Xolo export contains expense documents and submitted PDF declarations, but no submitted Modelo 130 expense register.",
            "- Local Xolo expense API snapshot contains UI expense rows only; it does not contain row-level Modelo 130 inclusion or asset amortization schedule.",
            "- Modelo 100 PDFs expose annual aggregate amortization, not the per-asset schedule needed to close quarterly Modelo 130.",
            "",
        ]
    )
    return "\n".join(lines)


def write_xolo_closure_request(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _source_finding_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    return [
        row
        for row in rows
        if row.get("source_status", "").startswith("unconfirmed")
        or row.get("row_ref", "").startswith("scenario_")
    ]


def _row_decision_highlights(path: Path) -> list[dict[str, str]]:
    rows = _load_rows(path)
    priority_order = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        rows,
        key=lambda row: (
            priority_order.get(row.get("priority", ""), 9),
            row.get("period", ""),
            row.get("decision_kind", ""),
            row.get("date", ""),
            row.get("number", ""),
        ),
    )


def _row_label(row: dict[str, str]) -> str:
    parts = [row.get("date", ""), row.get("number", ""), row.get("recipient", "")]
    value = " ".join(part for part in parts if part)
    return value or row.get("classification", "")


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|")
