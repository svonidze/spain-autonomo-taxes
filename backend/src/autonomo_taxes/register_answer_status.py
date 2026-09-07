from __future__ import annotations

from collections import Counter
import csv
from pathlib import Path


REGISTER_ANSWER_STATUS_FIELDS = [
    "queue_rank",
    "period",
    "audit_priority",
    "classification",
    "xolo_id",
    "row_label",
    "application_target",
    "answer_status",
    "readiness_status",
    "confirmed_field_count",
    "missing_fields",
    "notes",
]


CONFIRMATION_FIELDS = [
    "confirmed_included",
    "confirmed_irpf_deductible_eur",
    "confirmed_amortization_eur",
    "confirmed_basis",
    "confirmed_source",
]


def build_register_answer_status(answer_intake_csv: Path) -> list[dict[str, str]]:
    rows = _load_rows(answer_intake_csv)
    return [_status_row(row) for row in rows]


def write_register_answer_status_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_ANSWER_STATUS_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_register_answer_status_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    readiness_counts = Counter(row["readiness_status"] for row in rows)
    period_counts: dict[str, Counter[str]] = {}
    for row in rows:
        period_counts.setdefault(row["period"], Counter())[row["readiness_status"]] += 1

    lines = [
        "# Register Answer Status",
        "",
        "This report checks whether Xolo answer-intake rows are structured enough to apply to the register review.",
        "It does not apply answers and it does not close any Modelo 130 quarter.",
        "",
        "## Summary",
        "",
    ]
    for status in sorted(readiness_counts):
        lines.append(f"- `{status}`: `{readiness_counts[status]}`")
    lines.extend(["", "## Period Readiness", "", "| Period | Ready | Incomplete | Free text | Open |", "|---|---:|---:|---:|---:|"])
    for period in sorted(period_counts):
        counts = period_counts[period]
        lines.append(
            "| "
            + " | ".join(
                [
                    period,
                    str(counts["ready_to_apply"]),
                    str(counts["structured_incomplete"]),
                    str(counts["free_text_only_unstructured"]),
                    str(counts["open"]),
                ]
            )
            + " |"
        )

    blockers = [row for row in rows if row["readiness_status"] in {"structured_incomplete", "free_text_only_unstructured"}]
    if blockers:
        lines.extend(["", "## Rows Needing Cleanup", "", "| Rank | Period | Status | Missing fields | Row |", "|---:|---|---|---|---|"])
        for row in blockers:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["queue_rank"],
                        row["period"],
                        row["readiness_status"],
                        row["missing_fields"],
                        _cell(row["row_label"] or row["classification"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _status_row(row: dict[str, str]) -> dict[str, str]:
    filled = [field for field in CONFIRMATION_FIELDS if (row.get(field) or "").strip()]
    missing = _missing_fields(row, filled)
    readiness = _readiness(row, filled, missing)
    return {
        "queue_rank": row.get("queue_rank", ""),
        "period": row.get("period", ""),
        "audit_priority": row.get("audit_priority", ""),
        "classification": row.get("classification", ""),
        "xolo_id": row.get("xolo_id", ""),
        "row_label": row.get("row_label", ""),
        "application_target": row.get("application_target", ""),
        "answer_status": row.get("answer_status", ""),
        "readiness_status": readiness,
        "confirmed_field_count": str(len(filled)),
        "missing_fields": "; ".join(missing),
        "notes": _notes(row, readiness),
    }


def _readiness(row: dict[str, str], filled: list[str], missing: list[str]) -> str:
    if not filled:
        if (row.get("xolo_answer") or "").strip():
            return "free_text_only_unstructured"
        return "open"
    if missing:
        return "structured_incomplete"
    return "ready_to_apply"


def _missing_fields(row: dict[str, str], filled: list[str]) -> list[str]:
    if not filled:
        return []
    missing: list[str] = []
    if not row.get("confirmed_source", "").strip():
        missing.append("confirmed_source")
    if not row.get("confirmed_basis", "").strip():
        missing.append("confirmed_basis")

    included = _normalize_bool(row.get("confirmed_included", ""))
    has_amount = bool((row.get("confirmed_irpf_deductible_eur") or "").strip())
    has_amortization = bool((row.get("confirmed_amortization_eur") or "").strip())
    target = row.get("application_target", "")

    if included is True and not (has_amount or has_amortization):
        missing.append("confirmed_irpf_deductible_eur_or_confirmed_amortization_eur")
    if target in {"register_review.new_adjustment_row", "register_review.residual_adjustment"} and not (
        has_amount or has_amortization
    ):
        missing.append("confirmed_adjustment_amount")
    if target == "register_review.confirmed_amortization_eur" and included is not False and not (
        has_amount or has_amortization
    ):
        missing.append("confirmed_amortization_eur_or_direct_expense_amount")
    return missing


def _notes(row: dict[str, str], readiness: str) -> str:
    if readiness == "ready_to_apply":
        return "Structured confirmation can be applied to register review."
    if readiness == "free_text_only_unstructured":
        return "Move the Xolo answer into confirmed_* fields before applying."
    if readiness == "structured_incomplete":
        return "Some confirmed_* fields are present, but required basis/source/amount fields are missing."
    return "No Xolo answer has been recorded for this row."


def _normalize_bool(value: str) -> bool | None:
    text = value.strip().lower()
    if text in {"yes", "y", "true", "1", "included", "include", "si", "sí"}:
        return True
    if text in {"no", "n", "false", "0", "excluded", "exclude"}:
        return False
    return None


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
