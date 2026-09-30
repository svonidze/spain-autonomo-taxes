from __future__ import annotations

import csv
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from .source_book_content_check import required_group_header_map, required_groups_for_check
from .source_book_xlsx import read_xlsx_tables


SOURCE_BOOK_IMPORT_FIELDS = [
    "source_book_type",
    "source_scope",
    "source_file",
    "source_file_format",
    "source_row_number",
    "source_book_line_id",
    "period",
    "date",
    "booking_date",
    "supplier",
    "document_number",
    "counterparty_id_type",
    "counterparty_country_code",
    "counterparty_tax_id",
    "counterparty_vat_id",
    "invoice_type",
    "operation_key",
    "operation_qualification",
    "exempt_operation",
    "reverse_charge",
    "original_amount",
    "original_currency",
    "gross_eur",
    "invoice_total_eur",
    "taxable_base_eur",
    "vat_rate_percent",
    "vat_eur",
    "deductible_vat_eur",
    "withholding_rate_percent",
    "withholding_eur",
    "deductible_base_eur",
    "irpf_deductible_eur",
    "vat_treatment",
    "reason_code",
    "asset_id",
    "amortizable_base_eur",
    "amortization_period",
    "amortization_amount_eur",
    "rate_or_life",
    "casilla01_ytd",
    "casilla02_ytd",
    "casilla07",
    "casilla19",
    "import_status",
    "missing_required_groups",
    "notes",
]


OPTIONAL_ALIASES = {
    "period": ["autoliquidacion periodo", "periodo", "period", "quarter", "trimestre", "deducted in period", "modelo 130 period", "inclusion quarter"],
    "period_year": ["autoliquidacion ejercicio", "ejercicio", "year"],
    "amortization_period": ["autoliquidacion periodo", "periodo", "period", "quarter", "trimestre", "amortization period"],
    "booking_date": ["fecha recepcion", "booking date", "posting date", "fecha contabilizacion", "fecha registro"],
    "original_amount": ["original amount", "amount original", "importe original", "amount"],
    "original_currency": ["moneda original", "monedo original", "original currency", "currency", "moneda"],
    "gross_eur": ["total factura", "gross eur", "gross amount eur", "importe total eur", "total eur"],
    "invoice_total_eur": ["total factura", "invoice total eur", "invoice total", "total eur"],
    "taxable_base_eur": ["base imponible", "taxable base eur", "vat base eur"],
    "vat_rate_percent": ["tipo de iva", "vat rate", "vat rate percent"],
    "vat_eur": [
        "cuota iva soportado",
        "cuota iva repercutida",
        "vat eur",
        "input vat eur",
        "output vat eur",
    ],
    "deductible_vat_eur": ["cuota deducible", "deductible vat eur"],
    "withholding_rate_percent": ["tipo retencion del irpf", "withholding rate", "irpf withholding rate"],
    "withholding_eur": ["importe retenido del irpf", "withholding eur", "irpf withholding eur"],
    "counterparty_id_type": ["nif expedidor tipo", "nif destinatario tipo", "counterparty id type"],
    "counterparty_country_code": [
        "nif expedidor codigo pais",
        "nif destinatario codigo pais",
        "counterparty country code",
        "country code",
    ],
    "counterparty_tax_id": [
        "nif expedidor identificacion",
        "nif destinatario identification",
        "nif destinatario identificacion",
        "counterparty tax id",
        "tax id",
    ],
    "invoice_type": ["tipo de factura", "invoice type"],
    "operation_key": ["clave de operacion", "operation key"],
    "operation_qualification": ["calificacion de la operacion", "operation qualification"],
    "exempt_operation": ["operacion exenta", "exempt operation"],
    "reverse_charge": ["inversion del sujeto pasivo", "reverse charge"],
    "deductible_base_eur": ["deductible base eur", "base eur", "base imponible", "tax base eur"],
    "vat_treatment": ["vat treatment", "iva treatment", "tratamiento iva", "reverse charge"],
    "reason_code": ["reason code", "reason", "source book treatment", "accounting treatment", "tratamiento contable"],
    "source_book_line_id": ["source book line id", "line id", "folio", "asiento", "row id"],
    "income_amount_eur": ["ingreso computable", "income eur", "revenue eur", "sales eur", "ingresos", "importe", "base imponible", "total eur", "amount"],
    "amount_eur": ["amount", "importe", "total eur", "amount eur", "importe eur"],
    "concept": ["concept", "concepto", "description", "descripcion", "detalle", "operation", "operacion"],
    "irpf_deductible_eur": ["gasto deducible", "irpf deductible eur", "deductible eur", "tax deductible eur", "importe deducible"],
    "amortization_amount_eur": [
        "amortizacion cuota resultante",
        "amortization amount eur",
        "amortizacion eur",
        "depreciation amount",
        "deducted amortization",
        "amortizacion anual",
        "annual amortization",
        "cuota",
    ],
    "rate_or_life": ["porcentaje de amortizacion", "rate", "coefficient", "coeficiente", "useful life", "vida util", "percent", "%"],
    "casilla01_ytd": ["casilla 01", "casilla01", "sales ytd", "income ytd", "ingresos"],
    "casilla02_ytd": ["casilla 02", "casilla02", "expenses ytd", "gastos deducibles"],
    "casilla07": ["casilla 07", "casilla07", "payment due before reductions"],
    "casilla19": ["casilla 19", "casilla19", "payable", "amount due", "resultado"],
}

EMPTY_OK_BOOK_TYPES = {"bienes_inversion_book", "provisiones_suplidos_book"}


def build_source_book_import(
    *,
    response_root: Path,
    source_book_content_check_csv: Path,
) -> list[dict[str, str]]:
    content_rows = _load_rows(source_book_content_check_csv)
    ready_rows = [row for row in content_rows if row.get("status") == "content_ready"]
    output: list[dict[str, str]] = []
    for task in _unique_import_tasks(response_root, ready_rows):
        try:
            output.extend(_import_file(task.path, task.check, task.scope))
        except Exception as exc:
            output.append(_unreadable_row(task.path, task.check, task.scope, exc))
    return output


def write_source_book_import_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_IMPORT_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_import_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    by_type = Counter(row["source_book_type"] for row in rows)
    by_status = Counter(row["import_status"] for row in rows)
    lines = [
        "# Xolo Source-Book Imported Rows",
        "",
        "This report normalizes machine-readable Xolo source-book files that passed the content-ready gate.",
        "It does not decide tax treatment; it creates row-level evidence for later reconciliation against Modelo 130 quarters.",
        "",
        "## Summary",
        "",
        f"- Imported rows: `{len(rows)}`.",
    ]
    for source_type in sorted(by_type):
        lines.append(f"- `{source_type}`: `{by_type[source_type]}`.")
    for status in sorted(by_status):
        lines.append(f"- `{status}`: `{by_status[status]}`.")
    lines.extend(
        [
            "",
            "## Sample Rows",
            "",
            "| Type | Scope | Period | Date | Document | Amount | File row | Status |",
            "|---|---|---|---|---|---:|---:|---|",
        ]
    )
    for row in rows[:50]:
        amount = row["irpf_deductible_eur"] or row["amortization_amount_eur"] or row["casilla02_ytd"] or row["gross_eur"]
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["source_book_type"]),
                    _cell(row["source_scope"]),
                    _cell(row["period"] or row["amortization_period"]),
                    _cell(row["date"]),
                    _cell(row["document_number"] or row["asset_id"] or row["source_book_line_id"]),
                    _cell(amount),
                    _cell(row["source_row_number"]),
                    _cell(row["import_status"]),
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


@dataclass(frozen=True)
class _ImportTask:
    check: str
    path: Path
    scope: str


@dataclass
class _PendingImportTask:
    check: str
    path: Path
    scopes: list[str]


def _unique_import_tasks(response_root: Path, ready_rows: list[dict[str, str]]) -> list[_ImportTask]:
    """Collapse one full-history file matched to multiple deliverables.

    Filename-level intake intentionally lets a single Xolo source-book export
    satisfy several years or quarters. Importing that same file once per
    deliverable would multiply every row during reconciliation.
    """
    tasks: dict[tuple[str, str], _PendingImportTask] = {}
    order: list[tuple[str, str]] = []
    for content_row in ready_rows:
        check = content_row.get("check", "")
        scope = content_row.get("scope", "")
        for source_file in _content_ready_files(response_root, content_row.get("content_ready_files", "")):
            key = (check, _canonical_source_file(source_file))
            if key not in tasks:
                tasks[key] = _PendingImportTask(check=check, path=source_file, scopes=[])
                order.append(key)
            if scope and scope not in tasks[key].scopes:
                tasks[key].scopes.append(scope)
    output: list[_ImportTask] = []
    for key in order:
        task = tasks[key]
        output.append(_ImportTask(task.check, task.path, "; ".join(sorted(task.scopes))))
    return output


def _canonical_source_file(path: Path) -> str:
    try:
        return str(path.resolve()).casefold()
    except OSError:
        return str(path.absolute()).casefold()


def _import_file(path: Path, check: str, scope: str) -> list[dict[str, str]]:
    table = _read_table(path, check)
    mapping = _column_mapping(check, table.headers)
    required = required_group_header_map(check, table.headers)
    missing = [group for group in required_groups_for_check(check) if group not in required]
    if not table.rows:
        if _canonical_check(check) in EMPTY_OK_BOOK_TYPES and not missing:
            return []
        return [_empty_row(path, check, scope, table.file_format, missing)]
    rows: list[dict[str, str]] = []
    for index, values in enumerate(table.rows, start=2):
        value_by_header = _value_by_header(table.headers, values)
        source_row_number = str(index)
        rows.append(_normalized_row(check, scope, path, table.file_format, source_row_number, value_by_header, mapping, missing))
    return rows


def _unreadable_row(path: Path, check: str, scope: str, exc: Exception) -> dict[str, str]:
    row = {field: "" for field in SOURCE_BOOK_IMPORT_FIELDS}
    row.update(
        {
            "source_book_type": check,
            "source_scope": scope,
            "source_file": str(path),
            "source_file_format": path.suffix.lower().removeprefix(".") or "unknown",
            "source_book_line_id": f"{Path(path).name}:unreadable",
            "import_status": "skipped_unreadable_source_file",
            "notes": f"{type(exc).__name__}: {exc}",
        }
    )
    return row


def _empty_row(path: Path, check: str, scope: str, file_format: str, missing: list[str]) -> dict[str, str]:
    row = {field: "" for field in SOURCE_BOOK_IMPORT_FIELDS}
    row.update(
        {
            "source_book_type": check,
            "source_scope": scope,
            "source_file": str(path),
            "source_file_format": file_format,
            "source_book_line_id": f"{Path(path).name}:empty",
            "import_status": "skipped_empty_source_file",
            "missing_required_groups": "; ".join(missing),
            "notes": "No data rows were available at import time.",
        }
    )
    return row


def _normalized_row(
    check: str,
    scope: str,
    path: Path,
    file_format: str,
    source_row_number: str,
    values: dict[str, str],
    mapping: dict[str, str],
    missing: list[str],
) -> dict[str, str]:
    line_id = _value(values, mapping, "source_book_line_id")
    if not line_id:
        line_id = f"{Path(path).name}:{source_row_number}"
    row = {field: "" for field in SOURCE_BOOK_IMPORT_FIELDS}
    row.update(
        {
            "source_book_type": check,
            "source_scope": scope,
            "source_file": str(path),
            "source_file_format": file_format,
            "source_row_number": source_row_number,
            "source_book_line_id": line_id,
            "import_status": "imported" if not missing else "imported_with_missing_required_groups",
            "missing_required_groups": "; ".join(missing),
        }
    )
    kind = _canonical_check(check)
    if kind == "gastos_book":
        date = _value(values, mapping, "date")
        period = _source_book_period(
            _value(values, mapping, "period"),
            _value(values, mapping, "period_year"),
            date,
        )
        row.update(
            {
                "period": period,
                "date": date,
                "booking_date": _value(values, mapping, "booking_date"),
                "supplier": _value(values, mapping, "counterparty"),
                "document_number": _value(values, mapping, "document_number"),
                **_tax_evidence(values, mapping),
                "original_amount": _value(values, mapping, "original_amount"),
                "original_currency": _value(values, mapping, "original_currency"),
                "gross_eur": _value(values, mapping, "gross_eur"),
                "deductible_base_eur": _value(values, mapping, "deductible_base_eur"),
                "irpf_deductible_eur": _value(values, mapping, "irpf_deductible_eur"),
                "vat_treatment": _value(values, mapping, "vat_treatment"),
                "reason_code": _value(values, mapping, "reason_code") or _value(values, mapping, "concept"),
            }
        )
    elif kind == "ingresos_book":
        date = _value(values, mapping, "date")
        period = _source_book_period(
            _value(values, mapping, "period"),
            _value(values, mapping, "period_year"),
            date,
        )
        row.update(
            {
                "period": period,
                "date": date,
                "supplier": _value(values, mapping, "counterparty"),
                "document_number": _value(values, mapping, "document_number"),
                **_tax_evidence(values, mapping),
                "gross_eur": _value(values, mapping, "income_amount_eur") or _value(values, mapping, "gross_eur"),
                "reason_code": _value(values, mapping, "concept"),
            }
        )
    elif kind == "bienes_inversion_book":
        acquisition_date = _value(values, mapping, "acquisition_date")
        row.update(
            {
                **_tax_evidence(values, mapping),
                "asset_id": _value(values, mapping, "description_or_document"),
                "date": acquisition_date,
                "supplier": _value(values, mapping, "supplier_or_counterparty"),
                "amortizable_base_eur": _value(values, mapping, "acquisition_or_amortizable_value"),
                "amortization_period": _value(values, mapping, "amortization_period"),
                "amortization_amount_eur": _value(values, mapping, "amortization_amount_eur"),
                "rate_or_life": _value(values, mapping, "rate_or_life") or _value(values, mapping, "amortization_rate_or_quota"),
            }
        )
    elif kind == "provisiones_suplidos_book":
        date = _value(values, mapping, "date")
        period = _source_book_period(
            _value(values, mapping, "period"),
            _value(values, mapping, "period_year"),
            date,
        )
        row.update(
            {
                "period": period,
                "date": date,
                "supplier": _value(values, mapping, "counterparty"),
                "gross_eur": _value(values, mapping, "amount_eur"),
                "reason_code": _value(values, mapping, "movement_type_or_concept"),
            }
        )
    elif check == "modelo130_source_book_tieout":
        row.update(
            {
                "period": _value(values, mapping, "period"),
                "casilla01_ytd": _value(values, mapping, "casilla01_ytd"),
                "casilla02_ytd": _value(values, mapping, "casilla02_ytd"),
                "casilla07": _value(values, mapping, "casilla07"),
                "casilla19": _value(values, mapping, "casilla19"),
            }
        )
    return row


def _tax_evidence(values: dict[str, str], mapping: dict[str, str]) -> dict[str, str]:
    country_code = _value(values, mapping, "counterparty_country_code").upper()
    tax_id = _value(values, mapping, "counterparty_tax_id").replace(" ", "")
    id_type = _value(values, mapping, "counterparty_id_type")
    vat_id = ""
    if id_type == "02" and country_code and tax_id:
        vat_id = tax_id if tax_id.upper().startswith(country_code) else f"{country_code}{tax_id}"
    return {
        "counterparty_id_type": id_type,
        "counterparty_country_code": country_code,
        "counterparty_tax_id": tax_id,
        "counterparty_vat_id": vat_id,
        "invoice_type": _value(values, mapping, "invoice_type"),
        "operation_key": _value(values, mapping, "operation_key"),
        "operation_qualification": _value(values, mapping, "operation_qualification"),
        "exempt_operation": _value(values, mapping, "exempt_operation"),
        "reverse_charge": _value(values, mapping, "reverse_charge"),
        "invoice_total_eur": _value(values, mapping, "invoice_total_eur"),
        "taxable_base_eur": _value(values, mapping, "taxable_base_eur"),
        "vat_rate_percent": _value(values, mapping, "vat_rate_percent"),
        "vat_eur": _value(values, mapping, "vat_eur"),
        "deductible_vat_eur": _value(values, mapping, "deductible_vat_eur"),
        "withholding_rate_percent": _value(values, mapping, "withholding_rate_percent"),
        "withholding_eur": _value(values, mapping, "withholding_eur"),
    }


def _canonical_check(check: str) -> str:
    return {
        "compras_gastos_book": "gastos_book",
        "bienes_inversion_or_asset_schedule": "bienes_inversion_book",
    }.get(check, check)


def _period_from_date(value: str) -> str:
    match = re.match(r"\s*(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})", value or "")
    if match:
        year = match.group(1)
        month = int(match.group(2))
        quarter = ((month - 1) // 3) + 1
        return f"{year}-Q{quarter}"
    match = re.match(r"\s*(\d{1,2})[-/.](\d{1,2})[-/.](20\d{2})", value or "")
    if not match:
        return ""
    year = match.group(3)
    month = int(match.group(2))
    quarter = ((month - 1) // 3) + 1
    return f"{year}-Q{quarter}"


def _source_book_period(period: str, year: str, fallback_date: str) -> str:
    period = (period or "").strip()
    year = (year or "").strip()
    match = re.match(r"^(20\d{2})[-/ ]?Q([1-4])$", period, flags=re.IGNORECASE)
    if match:
        return f"{match.group(1)}-Q{match.group(2)}"
    match = re.match(r"^([1-4])\s*T$", period, flags=re.IGNORECASE)
    if match and re.match(r"^20\d{2}$", year):
        return f"{year}-Q{match.group(1)}"
    return _period_from_date(fallback_date)


def _column_mapping(check: str, headers: list[str]) -> dict[str, str]:
    mapping = required_group_header_map(check, headers)
    for field, aliases in OPTIONAL_ALIASES.items():
        header = _first_header(headers, aliases)
        if header:
            mapping.setdefault(field, header)
    return mapping


def _first_header(headers: list[str], aliases: list[str]) -> str:
    normalized_pairs = [(header, _normalize(header), _compact(header)) for header in headers]
    for alias in aliases:
        alias = _normalize(alias)
        compact_alias = _compact(alias)
        alias_tokens = alias.split()
        if not alias_tokens:
            continue
        for header, normalized_header, compact_header in normalized_pairs:
            if alias == normalized_header or compact_alias == compact_header:
                return header
            if len(alias_tokens) == 1:
                if alias_tokens[0] in normalized_header.split():
                    return header
                continue
            if alias in normalized_header or compact_alias in compact_header:
                return header
    return ""


def _value(values: dict[str, str], mapping: dict[str, str], field: str) -> str:
    header = mapping.get(field, "")
    return values.get(header, "").strip() if header else ""


class _Table:
    def __init__(self, file_format: str, headers: list[str], rows: list[list[str]]) -> None:
        self.file_format = file_format
        self.headers = headers
        self.rows = rows


def _read_table(path: Path, check: str) -> _Table:
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv"}:
        headers, rows = _read_delimited(path, "\t" if suffix == ".tsv" else None)
        return _Table(suffix.removeprefix("."), headers, rows)
    if suffix == ".xlsx":
        return _best_xlsx_table(path, check)
    return _Table(suffix.removeprefix(".") or "unknown", [], [])


def _read_delimited(path: Path, delimiter: str | None) -> tuple[list[str], list[list[str]]]:
    text = _read_text(path)
    sample = text[:4096]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = [[cell.strip() for cell in row] for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _best_xlsx_table(path: Path, check: str) -> _Table:
    tables = [
        _Table(table.file_format, table.headers, table.rows)
        for table in read_xlsx_tables(path)
    ]
    if not tables:
        return _Table("xlsx", [], [])
    if _canonical_check(check) in EMPTY_OK_BOOK_TYPES and _is_xolo_combined_accounting_book(path):
        has_matching_table = any(
            _table_type_rank(check, table.headers) == 0 and not _missing_required(check, table.headers)
            for table in tables
        )
        if not has_matching_table:
            return _Table(f"xlsx:{_canonical_check(check)}:empty-not-applicable", _synthetic_required_headers(check), [])
    best = min(
        tables,
        key=lambda table: (
            _table_type_rank(check, table.headers),
            _table_data_penalty(table.rows),
            len(_missing_required(check, table.headers)),
            -len(table.rows),
        ),
    )
    if _missing_required(check, best.headers) and _canonical_check(check) in EMPTY_OK_BOOK_TYPES and _is_xolo_combined_accounting_book(path):
        return _Table(f"xlsx:{_canonical_check(check)}:empty-not-applicable", _synthetic_required_headers(check), [])
    return best


def _missing_required(check: str, headers: list[str]) -> list[str]:
    mapping = required_group_header_map(check, headers)
    return [group for group in required_groups_for_check(check) if group not in mapping]


def _content_ready_files(response_root: Path, raw: str) -> list[Path]:
    paths: list[Path] = []
    for value in [part.strip() for part in raw.split(";") if part.strip()]:
        path = Path(value)
        if path.is_absolute():
            paths.append(path)
            continue
        rooted = response_root / path
        if rooted.exists():
            paths.append(rooted)
            continue
        if path.exists():
            paths.append(path)
            continue
        paths.append(rooted)
    return paths


def _value_by_header(headers: list[str], values: list[str]) -> dict[str, str]:
    output: dict[str, str] = {}
    for index, header in enumerate(headers):
        output[header] = values[index].strip() if index < len(values) else ""
    return output


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _normalize(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_text = "".join(char for char in decomposed if not unicodedata.combining(char))
    lowered = ascii_text.lower()
    replaced = re.sub(r"[^a-z0-9%]+", " ", lowered)
    return " ".join(replaced.split())


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9%]+", "", value.lower())


def _synthetic_required_headers(check: str) -> list[str]:
    return [aliases[0] for aliases in required_groups_for_check(check).values()]


def _is_xolo_combined_accounting_book(path: Path) -> bool:
    name = _normalize(path.name)
    return "libros contables" in name or "libro contable" in name


def _table_type_rank(check: str, headers: list[str]) -> int:
    kind = _canonical_check(check)
    joined = " ".join(_normalize(header) for header in headers)
    if kind == "ingresos_book":
        if _contains_any(joined, ["ingreso computable", "concepto de ingreso", "nombre destinatario"]):
            return 0
        if _contains_any(joined, ["gasto deducible", "concepto de gasto", "nombre expedidor", "valor amortizable"]):
            return 3
    elif kind == "gastos_book":
        if _contains_any(joined, ["gasto deducible", "concepto de gasto", "nombre expedidor", "irpf deductible"]):
            return 0
        if _contains_any(joined, ["ingreso computable", "concepto de ingreso", "nombre destinatario", "valor amortizable"]):
            return 3
    elif kind == "bienes_inversion_book":
        if _contains_any(joined, ["descripcion del bien", "valor amortizable", "amortizacion cuota resultante", "acquisition value"]):
            return 0
        if _contains_any(joined, ["ingreso computable", "gasto deducible"]):
            return 3
    elif kind == "provisiones_suplidos_book":
        if _contains_any(joined, ["provisiones", "suplidos", "disbursements", "advances"]):
            return 0
        if _contains_any(joined, ["ingreso computable", "gasto deducible", "valor amortizable"]):
            return 3
    return 1


def _contains_any(value: str, needles: list[str]) -> bool:
    return any(needle in value for needle in needles)


def _table_data_penalty(rows: list[list[str]]) -> int:
    if not rows:
        return 0
    first = " ".join(_normalize(cell) for cell in rows[0])
    if _contains_any(first, ["ejercicio periodo", "periodo grupo", "serie numero", "codigo pais identificacion"]):
        return 5
    return 0


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
