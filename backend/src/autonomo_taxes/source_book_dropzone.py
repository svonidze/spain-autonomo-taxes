from __future__ import annotations

import csv
from pathlib import Path

from .private_paths import configured_private_root
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


def write_source_book_dropzone_markdown(
    path: Path,
    rows: list[dict[str, str]],
    *,
    response_root: Path,
    runs_root: Path | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    response_root_display = str(response_root.resolve())
    runs_root_display = str((runs_root or configured_private_root() / "runs").resolve())
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
            '$env:PYTHONPATH = "<project-root>\\src"',
            "python -m autonomo_taxes.cli audit-source-book-refresh `",
            f'  --response-root "{response_root_display}" `',
            f'  --runs-root "{runs_root_display}" `',
            f'  --out-csv "{runs_root_display}\\xolo_source_book_refresh.csv" `',
            f'  --out-md "{runs_root_display}\\xolo_source_book_refresh.md"',
            "```",
            "",
            "This refresh intentionally regenerates the canonical audit artifacts under `--runs-root`; use a scratch `--runs-root` for experiments.",
            "",
            "If any refresh step reports a non-complete status, rerun the expanded commands below to inspect that gate directly:",
            "",
            "```powershell",
            '$env:PYTHONPATH = "<project-root>\\src"',
            "python -m autonomo_taxes.cli audit-source-book-response-check `",
            f'  --response-root "{response_root_display}" `',
            f'  --quarter-acceptance "{runs_root_display}\\modelo130_quarter_acceptance.csv" `',
            f'  --out-csv "{runs_root_display}\\xolo_source_book_response_check.csv" `',
            f'  --out-md "{runs_root_display}\\xolo_source_book_response_check.md"',
            "",
            "python -m autonomo_taxes.cli audit-source-book-content-check `",
            f'  --response-root "{response_root_display}" `',
            f'  --source-book-response-check "{runs_root_display}\\xolo_source_book_response_check.csv" `',
            f'  --out-csv "{runs_root_display}\\xolo_source_book_content_check.csv" `',
            f'  --out-md "{runs_root_display}\\xolo_source_book_content_check.md"',
            "",
            "python -m autonomo_taxes.cli audit-source-book-import `",
            f'  --response-root "{response_root_display}" `',
            f'  --source-book-content-check "{runs_root_display}\\xolo_source_book_content_check.csv" `',
            f'  --out-csv "{runs_root_display}\\xolo_source_book_rows.csv" `',
            f'  --out-md "{runs_root_display}\\xolo_source_book_rows.md"',
            "",
            "python -m autonomo_taxes.cli audit-source-book-reconcile `",
            f'  --history-audit "{runs_root_display}\\modelo130_history_audit.csv" `',
            f'  --source-book-rows "{runs_root_display}\\xolo_source_book_rows.csv" `',
            f'  --out-csv "{runs_root_display}\\xolo_source_book_reconciliation.csv" `',
            f'  --out-md "{runs_root_display}\\xolo_source_book_reconciliation.md"',
            "",
            "python -m autonomo_taxes.cli audit-quarter-acceptance `",
            f'  --quarter-closure "{runs_root_display}\\modelo130_quarter_closure.csv" `',
            f'  --quarter-balance-bridge "{runs_root_display}\\modelo130_quarter_balance_bridge.csv" `',
            f'  --material-gap-drilldown "{runs_root_display}\\modelo130_material_gap_drilldown.csv" `',
            f'  --packets-dir "{runs_root_display}\\modelo130_quarter_packets" `',
            f'  --source-book-reconciliation "{runs_root_display}\\xolo_source_book_reconciliation.csv" `',
            f'  --out-csv "{runs_root_display}\\modelo130_quarter_acceptance.csv" `',
            f'  --out-md "{runs_root_display}\\modelo130_quarter_acceptance.md"',
            "",
            "python -m autonomo_taxes.cli audit-goal-status `",
            f'  --tax-report-sequence "{runs_root_display}\\modelo130_tax_report_sequence.csv" `',
            f'  --target-values-coverage "{runs_root_display}\\modelo130_target_values_coverage.csv" `',
            f'  --quarter-acceptance "{runs_root_display}\\modelo130_quarter_acceptance.csv" `',
            f'  --first-gate-answer-check "{runs_root_display}\\modelo130_first_gate_answer_check.csv" `',
            f'  --source-book-response-check "{runs_root_display}\\xolo_source_book_response_check.csv" `',
            f'  --source-book-content-check "{runs_root_display}\\xolo_source_book_content_check.csv" `',
            f'  --source-book-reconciliation "{runs_root_display}\\xolo_source_book_reconciliation.csv" `',
            f'  --out-csv "{runs_root_display}\\modelo130_goal_status.csv" `',
            f'  --out-md "{runs_root_display}\\modelo130_goal_status.md"',
            "```",
            "",
            "A quarter is not closed merely because a file exists. It closes only after imported official-register rows and any investment-goods amortization rows reconcile to the filed values and the refreshed acceptance matrix marks it `accepted_from_source_books`.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def write_source_book_dropzone_readme(
    path: Path,
    rows: list[dict[str, str]],
    *,
    response_root: Path,
    runs_root: Path | None = None,
) -> None:
    write_source_book_dropzone_markdown(
        path,
        rows,
        response_root=response_root,
        runs_root=runs_root,
    )


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
