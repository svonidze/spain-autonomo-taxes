from __future__ import annotations

import csv
from pathlib import Path

from .source_book_content_check import REQUIRED_GROUPS


SOURCE_BOOK_DROPZONE_FIELDS = [
    "check",
    "scope",
    "required_for",
    "suggested_filename",
    "accepted_filename_pattern",
    "required_column_groups",
    "destination",
    "status",
    "next_action",
]


def build_source_book_dropzone_rows(
    *,
    source_book_response_check_csv: Path,
    response_root: Path,
) -> list[dict[str, str]]:
    destination = str(response_root.resolve())
    rows = [row for row in _load_rows(source_book_response_check_csv) if row.get("check") != "package_verdict"]
    output: list[dict[str, str]] = []
    for row in rows:
        check = row.get("check", "")
        scope = row.get("scope", "")
        output.append(
            {
                "check": check,
                "scope": scope,
                "required_for": row.get("required_for", ""),
                "suggested_filename": _suggested_filename(check, scope),
                "accepted_filename_pattern": _accepted_pattern(check, scope),
                "required_column_groups": "; ".join(REQUIRED_GROUPS.get(check, {})),
                "destination": destination,
                "status": row.get("status", ""),
                "next_action": _next_action(check, scope, row.get("status", "")),
            }
        )
    return output


def write_source_book_dropzone_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_DROPZONE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_dropzone_markdown(path: Path, rows: list[dict[str, str]], *, response_root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    response_root_display = str(response_root.resolve())
    missing = [row for row in rows if row["status"] == "missing"]
    found = [row for row in rows if row["status"] == "found"]
    lines = [
        "# Xolo Source-Book Dropzone Guide",
        "",
        f"Drop Xolo's official-register response files into `{response_root_display}`.",
        "Do not edit the original Google Drive `Xolo export` archive; this folder is the controlled evidence intake area.",
        "",
        "## Current Status",
        "",
        f"- Expected deliverables: `{len(rows)}`.",
        f"- Already matched by filename: `{len(found)}`.",
        f"- Still missing by filename: `{len(missing)}`.",
        "",
        "## Required Files",
        "",
        "| Check | Scope | Status | Suggested filename | Required for | Required column groups | Next action |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["check"]),
                    _cell(row["scope"]),
                    _cell(row["status"]),
                    _cell(row["suggested_filename"]),
                    _cell(row["required_for"]),
                    _cell(row["required_column_groups"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## After Files Arrive",
            "",
            "Run the full source-book refresh first:",
            "",
            "```powershell",
            '$env:PYTHONPATH = "C:\\projects\\spain-autonomo-taxes\\src"',
            "python -m autonomo_taxes.cli audit-source-book-refresh `",
            f'  --response-root "{response_root_display}" `',
            '  --runs-root "C:\\projects\\spain-autonomo-taxes\\runs" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_refresh.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_refresh.md"',
            "```",
            "",
            "This refresh intentionally regenerates the canonical audit artifacts under `--runs-root`; use a scratch `--runs-root` for experiments.",
            "",
            "If any refresh step reports a non-complete status, rerun the expanded commands below to inspect that gate directly:",
            "",
            "```powershell",
            '$env:PYTHONPATH = "C:\\projects\\spain-autonomo-taxes\\src"',
            "python -m autonomo_taxes.cli audit-source-book-response-check `",
            f'  --response-root "{response_root_display}" `',
            '  --quarter-acceptance "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_acceptance.csv" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_response_check.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_response_check.md"',
            "",
            "python -m autonomo_taxes.cli audit-source-book-content-check `",
            f'  --response-root "{response_root_display}" `',
            '  --source-book-response-check "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_response_check.csv" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_content_check.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_content_check.md"',
            "",
            "python -m autonomo_taxes.cli audit-source-book-import `",
            f'  --response-root "{response_root_display}" `',
            '  --source-book-content-check "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_content_check.csv" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_rows.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_rows.md"',
            "",
            "python -m autonomo_taxes.cli audit-source-book-reconcile `",
            '  --history-audit "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_history_audit.csv" `',
            '  --source-book-rows "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_rows.csv" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_reconciliation.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_reconciliation.md"',
            "",
            "python -m autonomo_taxes.cli audit-quarter-acceptance `",
            '  --quarter-closure "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_closure.csv" `',
            '  --quarter-balance-bridge "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_balance_bridge.csv" `',
            '  --material-gap-drilldown "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_material_gap_drilldown.csv" `',
            '  --packets-dir "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_packets" `',
            '  --source-book-reconciliation "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_reconciliation.csv" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_acceptance.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_acceptance.md"',
            "",
            "python -m autonomo_taxes.cli audit-goal-status `",
            '  --tax-report-sequence "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_tax_report_sequence.csv" `',
            '  --target-values-coverage "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_target_values_coverage.csv" `',
            '  --quarter-acceptance "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_quarter_acceptance.csv" `',
            '  --first-gate-answer-check "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_first_gate_answer_check.csv" `',
            '  --source-book-response-check "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_response_check.csv" `',
            '  --source-book-content-check "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_content_check.csv" `',
            '  --source-book-reconciliation "C:\\projects\\spain-autonomo-taxes\\runs\\xolo_source_book_reconciliation.csv" `',
            '  --out-csv "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_goal_status.csv" `',
            '  --out-md "C:\\projects\\spain-autonomo-taxes\\runs\\modelo130_goal_status.md"',
            "```",
            "",
            "A quarter is not closed merely because a file exists. It closes only after imported official-register rows and any investment-goods amortization rows reconcile to the filed values and the refreshed acceptance matrix marks it `accepted_from_source_books`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_source_book_dropzone_readme(path: Path, rows: list[dict[str, str]], *, response_root: Path) -> None:
    write_source_book_dropzone_markdown(path, rows, response_root=response_root)


def _suggested_filename(check: str, scope: str) -> str:
    if check == "ingresos_book":
        return f"libro_registro_ingresos_{scope}.xlsx"
    if check == "gastos_book":
        return f"libro_registro_gastos_{scope}.xlsx"
    if check == "bienes_inversion_book":
        return f"libro_registro_bienes_inversion_{scope}.xlsx"
    if check == "provisiones_suplidos_book":
        return f"libro_registro_provisiones_suplidos_{scope}.xlsx"
    return f"{check}_{scope}.xlsx"


def _accepted_pattern(check: str, scope: str) -> str:
    if check == "ingresos_book":
        return f"filename contains libro/register/book + ingresos/ventas/income/sales + {scope}"
    if check == "gastos_book":
        return f"filename contains libro/register/book + gastos/compras/expense/purchases + {scope}"
    if check == "bienes_inversion_book":
        return f"filename contains libro/register/book + bienes inversion/asset/amortization/depreciation + {scope}"
    if check == "provisiones_suplidos_book":
        return f"filename contains libro/register/book + provisiones/suplidos/provisions/disbursements + {scope}"
    return f"filename identifies {check} for {scope}"


def _next_action(check: str, scope: str, status: str) -> str:
    if status == "found":
        return "Run content check; filename matched, but row-level columns still need validation."
    return f"Place a machine-readable CSV or XLSX for {check} {scope} in the dropzone; keep the file's real extension."


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
