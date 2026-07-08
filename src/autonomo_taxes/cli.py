from __future__ import annotations

import argparse
import csv
from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone

try:
    import yaml
except Exception:  # pragma: no cover - dependency guard for clearer CLI errors.
    yaml = None

from .modelo130 import (
    calculate_modelo130,
    extract_modelo130_values,
    previous_positive_payments_with_warnings,
)
from .money import cents, format_es, parse_amount, parse_rate
from .parsers import (
    LedgerEntry,
    apply_fx,
    derive_single_currency_rate,
    in_ytd,
    scan_expense_dir,
    scan_income_dir,
)
from .reports import write_compare, write_ledger, write_manifest, write_markdown_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autonomo-tax")
    parser.add_argument("--config", type=Path, help="Optional YAML config file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    modelo = subparsers.add_parser("modelo130", help="Build Modelo 130 ledger/report")
    _add_common_args(modelo)
    modelo.add_argument("--out-dir", type=Path, help="Output directory. Defaults to runs/YYYY-QN")
    modelo.add_argument("--fx-rate", action="append", default=[], help="Currency rate, e.g. USD=0.85679")
    modelo.add_argument(
        "--manual-ledger",
        type=Path,
        help="Optional reviewed CSV ledger with scanned/manual entries",
    )
    modelo.add_argument("--derive-target-fx", action="store_true", help="Derive missing income FX from the target Xolo report for historical reconciliation only")

    extract_income = subparsers.add_parser("extract-invoices", help="Extract income invoice ledger")
    _add_common_args(extract_income)
    extract_income.add_argument("--out", type=Path, required=True)

    extract_expenses = subparsers.add_parser("extract-expenses", help="Extract expense ledger")
    _add_common_args(extract_expenses)
    extract_expenses.add_argument("--out", type=Path, required=True)

    compare = subparsers.add_parser("compare-xolo", help="Read target Modelo 130 casillas")
    compare.add_argument("--target-report", type=Path, required=True)
    compare.add_argument("--year", type=int, required=True)
    compare.add_argument("--quarter", type=int, required=True, choices=[1, 2, 3, 4])

    args = parser.parse_args(argv)
    config = _load_config(args.config)
    _merge_config(args, config)

    if args.command == "modelo130":
        return _cmd_modelo130(args)
    if args.command == "extract-invoices":
        entries, manual = scan_income_dir(Path(args.xolo_root) / "INVOICE")
        write_ledger(args.out, entries + manual)
        print(f"Wrote {args.out}")
        return 0
    if args.command == "extract-expenses":
        entries, manual = scan_expense_dir(Path(args.xolo_root) / "EXPENSE")
        write_ledger(args.out, entries + manual)
        print(f"Wrote {args.out}")
        return 0
    if args.command == "compare-xolo":
        values = extract_modelo130_values(args.target_report, args.year, args.quarter)
        for key, value in values.items():
            print(f"{key}: {format_es(value)}")
        return 0
    raise AssertionError(args.command)


def _cmd_modelo130(args: argparse.Namespace) -> int:
    xolo_root = Path(args.xolo_root)
    year = int(args.year)
    quarter = int(args.quarter)
    out_dir = args.out_dir or Path.cwd() / "runs" / f"{year}-Q{quarter}"
    out_dir.mkdir(parents=True, exist_ok=True)

    target = None
    if args.target_report:
        target = extract_modelo130_values(Path(args.target_report), year, quarter)

    income_entries, income_manual = scan_income_dir(xolo_root / "INVOICE")
    expense_entries, expense_manual = scan_expense_dir(xolo_root / "EXPENSE")
    entries = income_entries + expense_entries
    manual = income_manual + expense_manual
    if args.manual_ledger:
        reviewed = _load_manual_ledger(Path(args.manual_ledger))
        entries.extend(e for e in reviewed if not e.review_required)
        manual.extend(e for e in reviewed if e.review_required)

    fx_rates = _parse_fx_rates(args.fx_rate)
    fx_notes: list[str] = []
    for currency, rate in sorted(fx_rates.items()):
        fx_notes.append(f"Configured FX rate: {currency}={rate}")
    if target and "USD" not in fx_rates and args.derive_target_fx:
        derived = derive_single_currency_rate(income_entries, target["01"], "USD", year, quarter)
        if derived is not None:
            fx_rates["USD"] = derived
            fx_notes.append(
                "WARNING: Derived USD rate from Xolo target casilla 01 for historical reconciliation only: "
                f"USD={derived}"
            )

    warnings = apply_fx(entries, fx_rates)
    manual.extend(e for e in entries if e.review_required)
    ytd_entries = [e for e in entries if in_ytd(e.date, year, quarter)]
    ytd_manual = [e for e in manual if _manual_relevant_to_run(e, year, quarter)]

    income_ytd = cents(sum((e.amount_eur or Decimal("0.00")) for e in ytd_entries if e.kind == "income"))
    deductible_before_difficult = cents(
        sum((e.deductible_eur or Decimal("0.00")) for e in ytd_entries if e.kind == "expense" and not e.review_required)
    )
    previous, previous_warnings = previous_positive_payments_with_warnings(xolo_root / "TAX_REPORT", year, quarter)
    warnings.extend(previous_warnings)
    result = calculate_modelo130(income_ytd, deductible_before_difficult, previous)

    write_ledger(out_dir / "ledger.csv", ytd_entries)
    write_ledger(out_dir / "manual_review.csv", ytd_manual)
    write_ledger(out_dir / "manual_review_all.csv", manual)
    write_compare(out_dir / "xolo_compare.json", result, target)
    write_manifest(out_dir / "run_manifest.json", _build_manifest(args, xolo_root, out_dir, target is not None))
    write_markdown_report(
        out_dir / "modelo130_report.md",
        year,
        quarter,
        result,
        target,
        ytd_entries,
        ytd_manual,
        warnings,
        fx_notes,
    )

    print(f"Wrote run artifacts to {out_dir}")
    print(f"casilla 01: {format_es(result.casilla_01)}")
    print(f"casilla 02: {format_es(result.casilla_02)}")
    print(f"casilla 19: {format_es(result.casilla_19)}")
    if target:
        print(f"xolo casilla 19: {format_es(target.get('19'))}")
        print(f"diff casilla 19: {format_es(result.casilla_19 - target['19'])}")
    if warnings:
        print(f"warnings: {len(warnings)}")
    if ytd_manual:
        print(f"manual review items: {len(ytd_manual)}")
    return 0


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--xolo-root", type=Path, required=False)
    parser.add_argument("--year", type=int, required=False)
    parser.add_argument("--quarter", type=int, choices=[1, 2, 3, 4], required=False)
    parser.add_argument("--target-report", type=Path, required=False)


def _load_config(path: Path | None) -> dict:
    if path is None:
        return {}
    if yaml is None:
        raise SystemExit("PyYAML is required to use --config")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _merge_config(args: argparse.Namespace, config: dict) -> None:
    for key in ("xolo_root", "year", "quarter", "target_report", "manual_ledger"):
        if hasattr(args, key) and getattr(args, key, None) in (None, "") and key in config:
            setattr(args, key, config[key])
    if hasattr(args, "fx_rate") and not args.fx_rate:
        fx = config.get("fx_rates") or {}
        args.fx_rate = [f"{currency}={rate}" for currency, rate in fx.items() if rate not in (None, "")]
    required = []
    if getattr(args, "command", None) in {"modelo130", "extract-invoices", "extract-expenses"}:
        required = ["xolo_root"]
    if getattr(args, "command", None) == "modelo130":
        required += ["year", "quarter"]
    missing = [key for key in required if getattr(args, key, None) in (None, "")]
    if missing:
        raise SystemExit(f"Missing required option(s): {', '.join('--' + m.replace('_', '-') for m in missing)}")


def _parse_fx_rates(values: list[str]) -> dict[str, Decimal]:
    rates: dict[str, Decimal] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"Invalid --fx-rate {value!r}; expected CURRENCY=RATE")
        currency, raw_rate = value.split("=", 1)
        rates[currency.upper()] = parse_rate(raw_rate)
    return rates


def _load_manual_ledger(path: Path) -> list[LedgerEntry]:
    if not path.exists():
        raise SystemExit(f"Manual ledger not found: {path}")
    entries: list[LedgerEntry] = []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            entry_date = date.fromisoformat(row["date"]) if row.get("date") else None
            amount_original = _optional_amount(row.get("amount_original"))
            amount_eur = _optional_amount(row.get("amount_eur"))
            deductible_eur = _optional_amount(row.get("deductible_eur"))
            currency = (row.get("currency") or "EUR").upper()
            if amount_eur is None and currency == "EUR":
                amount_eur = amount_original
            if deductible_eur is None and (row.get("kind") or "expense") == "expense":
                deductible_eur = amount_eur
            reviewed = (row.get("review_required") or "no").lower() in {"yes", "true", "1"}
            if not reviewed and (amount_eur is None or amount_eur == 0):
                raise SystemExit(f"Manual ledger row must have non-zero amount_eur when review_required=no: {row}")
            if not reviewed and (row.get("kind") or "expense") == "expense" and (deductible_eur is None or deductible_eur == 0):
                raise SystemExit(f"Manual ledger expense row must have non-zero deductible_eur when review_required=no: {row}")
            entries.append(
                LedgerEntry(
                    kind=row.get("kind") or "expense",
                    date=entry_date,
                    document=row.get("document") or str(path),
                    counterparty=row.get("counterparty") or "Manual",
                    description=row.get("description") or "",
                    amount_original=amount_original,
                    currency=currency,
                    amount_eur=amount_eur,
                    deductible_eur=deductible_eur,
                    category=row.get("category") or "manual",
                    confidence=row.get("confidence") or "manual",
                    review_required=reviewed,
                    notes=row.get("notes") or f"Manual ledger: {path}",
                )
            )
    return entries


def _optional_amount(raw: str | None) -> Decimal | None:
    if raw is None or raw == "":
        return None
    return parse_amount(raw)


def _build_manifest(args: argparse.Namespace, xolo_root: Path, out_dir: Path, target_present: bool) -> dict[str, object]:
    files: list[dict[str, object]] = []
    for subdir in ("INVOICE", "EXPENSE", "TAX_REPORT"):
        root = xolo_root / subdir
        if not root.exists():
            continue
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            files.append(
                {
                    "path": str(path),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "tool_git_head_at_run": _git_commit(),
        "year": args.year,
        "quarter": args.quarter,
        "xolo_root": str(xolo_root),
        "target_report": str(args.target_report) if args.target_report else None,
        "target_present": target_present,
        "manual_ledger": str(args.manual_ledger) if args.manual_ledger else None,
        "out_dir": str(out_dir),
        "input_files": files,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            text=True,
            capture_output=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


def _manual_relevant_to_run(entry: LedgerEntry, year: int, quarter: int) -> bool:
    if in_ytd(entry.date, year, quarter):
        return True
    name = Path(entry.document).name
    return str(year) in name


if __name__ == "__main__":
    sys.exit(main())
