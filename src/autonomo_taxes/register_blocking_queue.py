from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path

from .money import format_es, parse_amount


REGISTER_BLOCKING_QUEUE_FIELDS = [
    "queue_rank",
    "period",
    "audit_priority",
    "acceptance_status",
    "register_status",
    "open_high",
    "open_total",
    "amortization_chain_signal",
    "decision_priority",
    "decision_kind",
    "classification",
    "row_label",
    "amount_eur",
    "question",
    "required_evidence",
    "xolo_url",
]


def build_register_blocking_queue(
    quarter_acceptance_csv: Path,
    register_reconciliation_csv: Path,
    row_decisions_csv: Path,
    amortization_chain_csv: Path,
) -> list[dict[str, str]]:
    acceptance_by_period = {row["period"]: row for row in _load_rows(quarter_acceptance_csv)}
    reconciliation_by_period = {row["period"]: row for row in _load_rows(register_reconciliation_csv)}
    amortization_by_period = {row["period"]: row for row in _load_rows(amortization_chain_csv)}
    decision_rows = _load_rows(row_decisions_csv)

    rows: list[dict[str, str]] = []
    for decision in decision_rows:
        period = decision["period"]
        acceptance = acceptance_by_period[period]
        reconciliation = reconciliation_by_period.get(period, {})
        amortization = amortization_by_period.get(period, {})
        rows.append(
            {
                "queue_rank": "",
                "period": period,
                "audit_priority": acceptance["priority"],
                "acceptance_status": acceptance["acceptance_status"],
                "register_status": reconciliation.get("status", "not_reconciled"),
                "open_high": reconciliation.get("open_high", ""),
                "open_total": reconciliation.get("open_total", ""),
                "amortization_chain_signal": amortization.get("amortization_chain_signal", ""),
                "decision_priority": decision["priority"],
                "decision_kind": decision["decision_kind"],
                "classification": decision["classification"],
                "row_label": _row_label(decision),
                "amount_eur": decision["amount_eur"],
                "question": _question(decision, amortization),
                "required_evidence": acceptance["required_evidence"],
                "xolo_url": decision["xolo_url"],
            }
        )

    rows.sort(key=_sort_key)
    for index, row in enumerate(rows, start=1):
        row["queue_rank"] = str(index)
    return rows


def write_register_blocking_queue_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=REGISTER_BLOCKING_QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_register_blocking_queue_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    priority_counts = Counter(row["audit_priority"] for row in rows)
    signal_counts = Counter(row["amortization_chain_signal"] or "none" for row in rows)
    lines = [
        "# Modelo 130 Register Blocking Queue",
        "",
        "This is the concise external-confirmation queue for the sequential Modelo 130 audit.",
        "It is built from the acceptance matrix, open register reconciliation, row decisions, and amortization chain; it does not confirm any Xolo filing treatment.",
        "",
        "## Summary",
        "",
        f"- Blocking decision rows: `{len(rows)}`.",
        "- Audit priorities: " + "; ".join(f"`{key}`={priority_counts[key]}" for key in sorted(priority_counts)) + ".",
        "- Amortization signals: " + "; ".join(f"`{key}`={signal_counts[key]}" for key in sorted(signal_counts)) + ".",
        "",
        "## First Questions",
        "",
        "| Rank | Period | Audit | Decision | Row | Amount | Question |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for row in rows[:20]:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["queue_rank"],
                    row["period"],
                    row["audit_priority"],
                    _cell(row["decision_kind"]),
                    _cell(row["row_label"] or "synthetic adjustment"),
                    _fmt(row["amount_eur"]),
                    _cell(row["question"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Queue By Quarter", ""])
    for period, period_rows in _group_by_period(rows).items():
        first = period_rows[0]
        lines.extend(
            [
                f"### {period}",
                "",
                f"- Audit priority: `{first['audit_priority']}`.",
                f"- Acceptance: `{first['acceptance_status']}`.",
                f"- Register status: `{first['register_status']}`; open high `{first['open_high']}`, open total `{first['open_total']}`.",
                f"- Amortization signal: `{first['amortization_chain_signal'] or 'none'}`.",
                f"- Required evidence: {first['required_evidence']}.",
                "",
                "| Rank | Decision priority | Decision | Row | Amount | Question |",
                "|---:|---|---|---|---:|---|",
            ]
        )
        for row in period_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        row["queue_rank"],
                        row["decision_priority"],
                        _cell(row["decision_kind"]),
                        _cell(row["row_label"] or "synthetic adjustment"),
                        _fmt(row["amount_eur"]),
                        _cell(row["question"]),
                    ]
                )
                + " |"
            )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _question(decision: dict[str, str], amortization: dict[str, str]) -> str:
    question = decision["question"]
    signal = amortization.get("amortization_chain_signal", "")
    if signal == "current_year_unconstrained_asset_schedule_required" and "asset" in decision["decision_kind"]:
        return question + " Also provide the current-year asset schedule because annual Modelo 100 is not available yet."
    if signal == "m100_zero_asset_like_row_reclassification_question" and decision["classification"] == "asset_amortization_candidate":
        return question + " Annual Modelo 100 line 0208 is zero for this year, so confirm direct expense, exclusion, or reclassification explicitly."
    return question


def _row_label(row: dict[str, str]) -> str:
    parts = [row.get("date", ""), row.get("xolo_id", ""), row.get("number", ""), row.get("recipient", "")]
    return " ".join(part for part in parts if part)


def _group_by_period(rows: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["period"], []).append(row)
    return grouped


def _sort_key(row: dict[str, str]) -> tuple[int, str, int, str, str]:
    return (
        {"P0": 0, "P1": 1, "P2": 2}.get(row["audit_priority"], 9),
        row["period"],
        {"high": 0, "medium": 1, "low": 2}.get(row["decision_priority"], 9),
        row["decision_kind"],
        row["row_label"],
    )


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _fmt(value: str) -> str:
    return "" if value == "" else format_es(parse_amount(value))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
