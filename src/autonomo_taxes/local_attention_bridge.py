from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from .local_archive_audit import ATTENTION_STATUSES
from .money import cents, format_es, parse_amount


LOCAL_ATTENTION_BRIDGE_FIELDS = [
    "period",
    "quarter_balance_eur",
    "balance_direction",
    "attention_status",
    "date",
    "local_document",
    "known_local_amount_eur",
    "matched_xolo_numbers",
    "matched_xolo_amounts",
    "known_effect_eur",
    "gap_fit_signal",
    "priority",
    "xolo_question",
    "notes",
]


def build_local_attention_bridge(
    local_archive_audit_csv: Path,
    quarter_closure_csv: Path,
) -> list[dict[str, str]]:
    attention_rows = [
        row
        for row in _load_rows(local_archive_audit_csv)
        if row.get("status") in ATTENTION_STATUSES and _focus_period(row.get("period", ""))
    ]
    closure_by_period = {row["period"]: row for row in _load_rows(quarter_closure_csv)}
    output: list[dict[str, str]] = []
    for row in attention_rows:
        closure = closure_by_period.get(row["period"], {})
        balance = _amount(closure.get("balancing_adjustment_eur"))
        known_effect = _known_effect(row)
        output.append(
            {
                "period": row["period"],
                "quarter_balance_eur": _money(balance),
                "balance_direction": _balance_direction(balance),
                "attention_status": row["status"],
                "date": row["date"],
                "local_document": row["local_document"],
                "known_local_amount_eur": _known_local_amount(row),
                "matched_xolo_numbers": row["matched_xolo_numbers"],
                "matched_xolo_amounts": row["matched_xolo_amounts"],
                "known_effect_eur": "" if known_effect is None else _money(known_effect),
                "gap_fit_signal": _gap_fit_signal(row, balance, known_effect),
                "priority": _priority(row, balance, known_effect),
                "xolo_question": _question(row, balance, known_effect),
                "notes": row["notes"],
            }
        )
    return output


def write_local_attention_bridge_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=LOCAL_ATTENTION_BRIDGE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_local_attention_bridge_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_priority = _counts(rows, "priority")
    by_signal = _counts(rows, "gap_fit_signal")
    lines = [
        "# Modelo 130 Local Attention Bridge",
        "",
        "This report ties local archive attention candidates to the current quarter residuals.",
        "It does not infer Xolo's submitted accounting. Its purpose is to show which local documents can materially explain a residual and which only need evidence cleanup.",
        "",
        "## Summary",
        "",
        f"- Attention rows bridged: `{len(rows)}`.",
        "",
        "| Priority | Count |",
        "|---|---:|",
    ]
    for priority, count in by_priority:
        lines.append(f"| {priority} | {count} |")
    lines.extend(["", "| Gap fit signal | Count |", "|---|---:|"])
    for signal, count in by_signal:
        lines.append(f"| {signal} | {count} |")

    lines.extend(
        [
            "",
            "## Candidate Bridge",
            "",
            "| Period | Priority | Balance | Status | Amount/effect | Document | Gap signal | Xolo question |",
            "|---|---|---:|---|---:|---|---|---|",
        ]
    )
    for row in rows:
        amount = row["known_effect_eur"] or row["known_local_amount_eur"]
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["priority"],
                    _fmt(row["quarter_balance_eur"]),
                    row["attention_status"],
                    _fmt_optional(amount),
                    _cell(row["local_document"]),
                    row["gap_fit_signal"],
                    _cell(row["xolo_question"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _known_effect(row: dict[str, str]) -> Decimal | None:
    if row.get("status") != "amount_mismatch_potential_xolo_raw":
        return None
    local = _amount(row.get("deductible_eur") or row.get("amount_eur") or row.get("amount_original"))
    matched = _first_amount(row.get("matched_xolo_amounts", ""))
    if matched is None:
        return None
    return cents(local - matched)


def _known_local_amount(row: dict[str, str]) -> str:
    return row.get("deductible_eur") or row.get("amount_eur") or row.get("amount_original") or ""


def _gap_fit_signal(row: dict[str, str], balance: Decimal, known_effect: Decimal | None) -> str:
    if row.get("status") == "amount_mismatch_potential_xolo_raw":
        if known_effect is None:
            return "amount_mismatch_unknown_effect"
        if balance == Decimal("0.00"):
            return "amount_mismatch_without_quarter_balance"
        if _same_direction(balance, known_effect):
            return "known_effect_moves_toward_gap"
        return "known_effect_moves_away_from_gap"
    if row.get("status") == "ambiguous_xolo_raw_matches":
        return "duplicate_or_correction_treatment_can_change_gap"
    if not _known_local_amount(row):
        return "amount_unknown_needs_document_parse_or_visual_review"
    return "local_only_known_amount_can_be_tested"


def _priority(row: dict[str, str], balance: Decimal, known_effect: Decimal | None) -> str:
    if row["period"] == "2024-Q3":
        return "high"
    if row.get("status") == "ambiguous_xolo_raw_matches":
        return "high"
    if known_effect is not None and abs(known_effect) >= Decimal("20.00"):
        return "high"
    if abs(balance) >= Decimal("20.00"):
        return "medium"
    return "low"


def _question(row: dict[str, str], balance: Decimal, known_effect: Decimal | None) -> str:
    document = row["local_document"]
    period = row["period"]
    status = row["status"]
    if status == "amount_mismatch_potential_xolo_raw":
        effect = "" if known_effect is None else f" ({format_es(known_effect)} EUR local-minus-Xolo effect)"
        return (
            f"For {period}, should `{document}` be treated at the local parsed amount or the Xolo raw amount "
            f"{row['matched_xolo_amounts']} for Modelo 130?{effect}"
        )
    if status == "ambiguous_xolo_raw_matches":
        return (
            f"For {period}, which Xolo row(s) does `{document}` support, and were duplicate/corrected rows "
            "included, corrected, netted, or shifted to another quarter?"
        )
    if not _known_local_amount(row):
        return (
            f"For {period}, does `{document}` contain a deductible expense amount used in the source books for Modelo 130, "
            "or is it evidence-only/ignored?"
        )
    direction = "increase" if balance > 0 else "reduce"
    return f"For {period}, was `{document}` manually included in the source books to {direction} casilla 02?"


def _balance_direction(value: Decimal) -> str:
    if value > Decimal("0.00"):
        return "target_above_model"
    if value < Decimal("0.00"):
        return "model_above_target"
    return "balanced"


def _same_direction(left: Decimal, right: Decimal) -> bool:
    return (left > Decimal("0.00") and right > Decimal("0.00")) or (
        left < Decimal("0.00") and right < Decimal("0.00")
    )


def _focus_period(period: str) -> bool:
    return period.startswith("2023-") or period.startswith("2024-")


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _amount(value: str | None) -> Decimal:
    return parse_amount(value) if value else Decimal("0.00")


def _first_amount(value: str) -> Decimal | None:
    for part in value.split("; "):
        if part:
            return parse_amount(part)
    return None


def _money(value: Decimal) -> str:
    return f"{cents(value):.2f}"


def _fmt(value: str) -> str:
    return format_es(parse_amount(value))


def _fmt_optional(value: str) -> str:
    return "" if value == "" else _fmt(value)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


def _counts(rows: list[dict[str, str]], field: str) -> list[tuple[str, int]]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row[field]] = counts.get(row[field], 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
