from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re

from .register_review import REGISTER_REVIEW_FIELDS


REGISTER_ANSWER_APPLY_FIELDS = [
    "queue_rank",
    "period",
    "classification",
    "xolo_id",
    "application_target",
    "apply_status",
    "matched_review_row",
    "notes",
]


@dataclass(frozen=True)
class RegisterAnswerApplyResult:
    review_rows: list[dict[str, str]]
    report_rows: list[dict[str, str]]


def apply_register_answers(
    register_review_csv: Path,
    answer_intake_csv: Path,
) -> RegisterAnswerApplyResult:
    review_rows = _load_rows(register_review_csv)
    answer_rows = _load_rows(answer_intake_csv)
    report_rows: list[dict[str, str]] = []

    for answer in answer_rows:
        if not _has_structured_confirmation(answer):
            report_rows.append(_report(answer, "skipped_open_or_unstructured", "", "No confirmed_* fields were filled."))
            continue

        target = answer.get("application_target", "")
        if target in {"register_review.new_adjustment_row", "register_review.residual_adjustment"}:
            review_rows.append(_new_adjustment_row(answer))
            report_rows.append(_report(answer, "added_adjustment_row", _adjustment_number(answer), "Created synthetic review row."))
            continue

        match_index = _match_review_row(review_rows, answer)
        if match_index is None:
            report_rows.append(_report(answer, "no_matching_review_row", "", "Could not match by period/xolo_id/url/row label."))
            continue

        _apply_to_review_row(review_rows[match_index], answer)
        report_rows.append(_report(answer, "applied_to_existing_row", _row_label(review_rows[match_index]), "Updated confirmed fields."))

    return RegisterAnswerApplyResult(review_rows=review_rows, report_rows=report_rows)


def write_applied_register_review_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_REVIEW_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in REGISTER_REVIEW_FIELDS})


def write_register_answer_apply_report_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_ANSWER_APPLY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_register_answer_apply_report_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["apply_status"]] = counts.get(row["apply_status"], 0) + 1
    lines = [
        "# Register Answer Apply Report",
        "",
        "This report shows how filled Xolo answer-intake rows were applied to the source-book review template.",
        "Rows with only free-text answers and no structured `confirmed_*` fields are skipped on purpose.",
        "",
        "## Summary",
        "",
    ]
    for status in sorted(counts):
        lines.append(f"- `{status}`: `{counts[status]}`")
    lines.extend(["", "## Applied Rows", "", "| Rank | Period | Status | Matched row | Notes |", "|---:|---|---|---|---|"])
    for row in rows:
        if row["apply_status"].startswith("skipped"):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    row["queue_rank"],
                    row["period"],
                    row["apply_status"],
                    _cell(row["matched_review_row"]),
                    _cell(row["notes"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _apply_to_review_row(review: dict[str, str], answer: dict[str, str]) -> None:
    review["review_status"] = _review_status(answer)
    for field in (
        "confirmed_included",
        "confirmed_irpf_deductible_eur",
        "confirmed_amortization_eur",
        "confirmed_basis",
        "confirmed_source",
    ):
        value = answer.get(field, "")
        if value != "":
            review[field] = value
    notes = _answer_note(answer)
    if notes:
        review["notes"] = _append_note(review.get("notes", ""), notes)


def _new_adjustment_row(answer: dict[str, str]) -> dict[str, str]:
    amount = answer.get("amount_eur", "")
    return {
        "period": answer.get("period", ""),
        "priority": answer.get("decision_priority", "") or _priority_from_audit(answer.get("audit_priority", "")),
        "review_status": _review_status(answer),
        "row_kind": "xolo_submitted_adjustment",
        "source_classification": answer.get("classification", ""),
        "xolo_id": answer.get("xolo_id", ""),
        "date": "",
        "recipient": "Xolo source books",
        "type": "submitted_register_adjustment",
        "number": _adjustment_number(answer),
        "currency": "EUR",
        "amount_original": amount,
        "gross_eur": amount,
        "base_eur": amount,
        "gross_minus_base_eur": "0.00" if amount else "",
        "candidate_model_amount_eur": amount,
        "hypothesis": answer.get("application_target", ""),
        "confirmation_question": answer.get("question", ""),
        "confirmed_included": answer.get("confirmed_included", ""),
        "confirmed_irpf_deductible_eur": answer.get("confirmed_irpf_deductible_eur", ""),
        "confirmed_amortization_eur": answer.get("confirmed_amortization_eur", ""),
        "confirmed_basis": answer.get("confirmed_basis", ""),
        "confirmed_source": answer.get("confirmed_source", ""),
        "notes": _answer_note(answer),
        "xolo_url": answer.get("xolo_url", ""),
    }


def _match_review_row(review_rows: list[dict[str, str]], answer: dict[str, str]) -> int | None:
    period = answer.get("period", "")
    xolo_id = answer.get("xolo_id", "")
    if xolo_id:
        matches = [
            index
            for index, row in enumerate(review_rows)
            if row.get("period") == period and row.get("xolo_id") == xolo_id
        ]
        if len(matches) == 1:
            return matches[0]
    url = answer.get("xolo_url", "")
    if url:
        matches = [
            index
            for index, row in enumerate(review_rows)
            if row.get("period") == period and row.get("xolo_url") == url
        ]
        if len(matches) == 1:
            return matches[0]
    label = _normalize(answer.get("row_label", ""))
    if label:
        matches = [
            index
            for index, row in enumerate(review_rows)
            if row.get("period") == period and _label_matches(label, row)
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def _has_structured_confirmation(row: dict[str, str]) -> bool:
    return any(
        (row.get(field) or "").strip()
        for field in (
            "confirmed_included",
            "confirmed_irpf_deductible_eur",
            "confirmed_amortization_eur",
            "confirmed_basis",
            "confirmed_source",
        )
    )


def _review_status(answer: dict[str, str]) -> str:
    status = (answer.get("answer_status") or "").strip().lower()
    if status in {"closed", "confirmed", "done", "resolved"}:
        return "confirmed"
    return "answered"


def _answer_note(answer: dict[str, str]) -> str:
    pieces = []
    if answer.get("queue_rank"):
        pieces.append(f"answer_intake_rank={answer['queue_rank']}")
    if answer.get("xolo_answer"):
        pieces.append("xolo_answer=" + answer["xolo_answer"])
    if answer.get("application_notes"):
        pieces.append("application_notes=" + answer["application_notes"])
    return "; ".join(pieces)


def _append_note(existing: str, addition: str) -> str:
    if not existing:
        return addition
    if not addition:
        return existing
    return existing + " | " + addition


def _adjustment_number(answer: dict[str, str]) -> str:
    return f"answer-intake-{answer.get('queue_rank', '').strip() or 'unranked'}"


def _priority_from_audit(value: str) -> str:
    if value in {"P0", "P1"}:
        return "high"
    return "medium" if value == "P2" else "low"


def _report(answer: dict[str, str], status: str, matched: str, notes: str) -> dict[str, str]:
    return {
        "queue_rank": answer.get("queue_rank", ""),
        "period": answer.get("period", ""),
        "classification": answer.get("classification", ""),
        "xolo_id": answer.get("xolo_id", ""),
        "application_target": answer.get("application_target", ""),
        "apply_status": status,
        "matched_review_row": matched,
        "notes": notes,
    }


def _row_label(row: dict[str, str]) -> str:
    return " ".join(
        part
        for part in (
            row.get("date", ""),
            row.get("xolo_id", ""),
            row.get("number", ""),
            row.get("recipient", ""),
        )
        if part
    )


def _label_matches(normalized_label: str, row: dict[str, str]) -> bool:
    haystack = _normalize(_row_label(row))
    return bool(normalized_label and (normalized_label in haystack or haystack in normalized_label))


def _normalize(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
