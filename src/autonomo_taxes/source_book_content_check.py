from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

from .source_book_xlsx import read_xlsx_tables


SOURCE_BOOK_CONTENT_CHECK_FIELDS = [
    "check",
    "scope",
    "status",
    "matched_count",
    "content_ready_count",
    "matched_files",
    "content_ready_files",
    "required_for",
    "missing_required_groups",
    "observed_headers",
    "evidence",
    "next_action",
]

REQUIRED_GROUPS = {
    "ingresos_book": {
        "date": ["date", "fecha", "fecha factura", "invoice date", "fecha expedicion", "fecha operacion"],
        "counterparty": ["nombre destinatario", "customer", "cliente", "recipient", "destinatario", "party", "counterparty", "nombre"],
        "document_number": [
            "identificacion de la factura numero",
            "identificacion de la factura serie numero",
            "serie numero",
            "number",
            "invoice number",
            "invoice_number",
            "document number",
            "numero",
            "serie",
            "factura",
        ],
        "concept": ["concept", "concepto", "description", "descripcion", "detalle", "operation", "operacion"],
        "income_amount_eur": ["ingreso computable", "income eur", "revenue eur", "sales eur", "ingresos", "importe", "base imponible", "total eur", "amount"],
    },
    "gastos_book": {
        "date": ["fecha expedicion", "fecha operacion", "date", "fecha", "invoice date", "booking date", "posting date", "fecha factura", "fecha registro"],
        "counterparty": ["nombre expedidor", "supplier", "proveedor", "recipient", "vendor", "party", "counterparty", "emisor", "nombre"],
        "document_number": [
            "identificacion factura del expedidor serie numero",
            "identificacion factura del expedidor",
            "serie numero",
            "number",
            "invoice number",
            "invoice_number",
            "document number",
            "numero",
            "serie",
            "factura",
        ],
        "concept": ["concept", "concepto", "description", "descripcion", "detalle", "operation", "operacion"],
        "irpf_deductible_eur": [
            "gasto deducible",
            "irpf deductible eur",
            "deductible eur",
            "deductible base eur",
            "tax deductible eur",
            "importe deducible",
            "gasto irpf",
            "gasto a efectos del irpf",
            "importe gasto irpf",
            "importe que es considerado gasto",
            "casilla 02",
        ],
    },
    "bienes_inversion_book": {
        "acquisition_date": ["acquisition date", "purchase date", "fecha adquisicion", "fecha inicio utilizacion", "date"],
        "description_or_document": [
            "descripcion del bien literal",
            "descripcion del bien",
            "description",
            "descripcion",
            "bien inversion",
            "bien de inversion",
            "investment good",
            "document number",
            "factura",
            "numero",
        ],
        "supplier_or_counterparty": ["supplier", "proveedor", "vendor", "counterparty", "emisor", "nombre"],
        "acquisition_or_amortizable_value": [
            "valor amortizable",
            "acquisition value",
            "valor adquisicion",
            "amortizable base eur",
            "base amortizable",
            "cost basis",
            "acquisition cost",
        ],
        "amortization_method": ["method", "metodo", "metodo amortizacion", "depreciation method", "amortization method"],
        "amortization_rate_or_quota": [
            "amortizacion cuota resultante",
            "rate",
            "coefficient",
            "coeficiente",
            "percent",
            "%",
            "quota",
            "cuota",
            "amortization amount eur",
            "amortizacion eur",
            "depreciation amount",
            "deducted amortization",
            "amortizacion anual",
            "annual amortization",
        ],
        "accumulated_amortization": [
            "amortizacion acumulada al final",
            "amortizacion acumulada al inicio",
            "accumulated amortization",
            "amortizacion acumulada",
            "depreciation accumulated",
            "accumulated depreciation",
        ],
    },
    "provisiones_suplidos_book": {
        "date": ["date", "fecha", "movement date", "fecha movimiento", "fecha registro"],
        "counterparty": ["customer", "cliente", "supplier", "proveedor", "counterparty", "party", "nombre"],
        "movement_type_or_concept": ["type", "tipo", "concept", "concepto", "description", "descripcion", "suplido", "provision"],
        "amount_eur": ["amount", "importe", "total eur", "amount eur", "importe eur"],
    },
}

LEGACY_REQUIRED_GROUP_ALIASES = {
    "compras_gastos_book": "gastos_book",
    "bienes_inversion_or_asset_schedule": "bienes_inversion_book",
}

EMPTY_OK_BOOK_TYPES = {"bienes_inversion_book", "provisiones_suplidos_book"}


@dataclass(frozen=True)
class _Inspection:
    path: str
    status: str
    file_format: str
    row_count: int
    headers: list[str]
    missing_groups: list[str]
    error: str = ""


def build_source_book_content_check(
    *,
    response_root: Path,
    source_book_response_check_csv: Path,
) -> list[dict[str, str]]:
    response_rows = _load_rows(source_book_response_check_csv)
    deliverables = [row for row in response_rows if row.get("check") != "package_verdict"]
    rows = [_deliverable_content_row(response_root, row) for row in deliverables]
    rows.append(_verdict_row(rows, response_rows))
    return rows


def write_source_book_content_check_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SOURCE_BOOK_CONTENT_CHECK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_source_book_content_check_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    verdict = next((row for row in rows if row["check"] == "content_verdict"), {})
    deliverables = [row for row in rows if row["check"] != "content_verdict"]
    ready = [row for row in deliverables if row["status"] == "content_ready"]
    attention = [row for row in deliverables if row["status"] != "content_ready"]
    lines = [
        "# Xolo Source-Book Content Check",
        "",
        "This report checks whether matched Xolo official register files have the columns needed for row-level Modelo 130 reconciliation.",
        "It does not interpret the accounting treatment; it only blocks import when required structure is missing.",
        "",
        "## Verdict",
        "",
        f"- Status: `{verdict.get('status', 'unknown')}`.",
        f"- Content-ready deliverables: `{len(ready)}`.",
        f"- Deliverables needing attention: `{len(attention)}`.",
        f"- Next action: {verdict.get('next_action', '')}",
        "",
        "## Deliverables",
        "",
        "| Check | Scope | Status | Ready files | Missing required groups | Required for | Next action |",
        "|---|---|---|---:|---|---|---|",
    ]
    for row in deliverables:
        lines.append(
            "| "
            + " | ".join(
                [
                    _cell(row["check"]),
                    _cell(row["scope"]),
                    _cell(row["status"]),
                    _cell(row["content_ready_count"]),
                    _cell(row["missing_required_groups"]),
                    _cell(row["required_for"]),
                    _cell(row["next_action"]),
                ]
            )
            + " |"
        )

    if ready:
        lines.extend(["", "## Content-Ready Files", "", "| Check | Scope | Files |", "|---|---|---|"])
        for row in ready:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _cell(row["check"]),
                        _cell(row["scope"]),
                        _cell(row["content_ready_files"]),
                    ]
                )
                + " |"
            )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _deliverable_content_row(response_root: Path, row: dict[str, str]) -> dict[str, str]:
    check = row.get("check", "")
    files = _matched_paths(response_root, row.get("matched_files", ""))
    inspections = [_inspect_file(path, check) for path in files]
    ready = [item for item in inspections if item.status == "content_ready"]
    if not files:
        status = "missing_response_file"
        missing = sorted(_required_groups(check))
        evidence = "No matched response file was listed by audit-source-book-response-check."
        next_action = row.get("next_action", "Add the missing Xolo source-book file.")
    elif ready:
        status = "content_ready"
        missing = []
        evidence = _inspection_evidence(inspections)
        next_action = "Import and reconcile this deliverable against answer-intake rows."
    elif inspections and all(item.status == "unsupported_format" for item in inspections):
        status = "unsupported_content_format"
        missing = sorted(_required_groups(check))
        evidence = _inspection_evidence(inspections)
        next_action = "Ask Xolo for CSV/XLSX source-book exports with machine-readable headers."
    else:
        status = "content_incomplete"
        missing = _least_missing_groups(inspections, check)
        evidence = _inspection_evidence(inspections)
        next_action = "Ask Xolo to include the missing required fields or map them explicitly before import."
    return {
        "check": check,
        "scope": row.get("scope", ""),
        "status": status,
        "matched_count": str(len(files)),
        "content_ready_count": str(len(ready)),
        "matched_files": row.get("matched_files", ""),
        "content_ready_files": "; ".join(item.path for item in ready),
        "required_for": row.get("required_for", ""),
        "missing_required_groups": "; ".join(missing),
        "observed_headers": " || ".join(_headers_summary(item) for item in inspections),
        "evidence": evidence,
        "next_action": next_action,
    }


def _verdict_row(rows: list[dict[str, str]], response_rows: list[dict[str, str]]) -> dict[str, str]:
    deliverables = [row for row in rows if row.get("check") != "content_verdict"]
    ready = [row for row in deliverables if row.get("status") == "content_ready"]
    response_verdict = next((row for row in response_rows if row.get("check") == "package_verdict"), {})
    complete = bool(deliverables) and len(ready) == len(deliverables)
    status = "ready_for_import" if complete else "content_attention"
    if status == "ready_for_import":
        next_action = "Import source-book rows into answer-intake/register review and rerun quarter acceptance."
    else:
        next_action = f"Resolve {len(deliverables) - len(ready)} source-book content deliverable(s) before import."
    return {
        "check": "content_verdict",
        "scope": response_verdict.get("scope", ""),
        "status": status,
        "matched_count": str(len(deliverables)),
        "content_ready_count": str(len(ready)),
        "matched_files": "",
        "content_ready_files": "",
        "required_for": "all quarters in source-book response check",
        "missing_required_groups": "",
        "observed_headers": "",
        "evidence": f"{len(ready)}/{len(deliverables)} deliverables content-ready.",
        "next_action": next_action,
    }


def _inspect_file(path: Path, check: str) -> _Inspection:
    suffix = path.suffix.lower()
    try:
        if suffix in {".csv", ".tsv"}:
            headers, row_count = _read_delimited_headers(path, "\t" if suffix == ".tsv" else None)
            return _inspection(path, check, suffix.removeprefix("."), headers, row_count)
        if suffix == ".xlsx":
            return _best_xlsx_inspection(path, check)
        return _Inspection(str(path), "unsupported_format", suffix.removeprefix(".") or "unknown", 0, [], sorted(_required_groups(check)))
    except Exception as exc:  # pragma: no cover - exact parser failures are environment/file dependent.
        return _Inspection(str(path), "unreadable", suffix.removeprefix(".") or "unknown", 0, [], sorted(_required_groups(check)), str(exc))


def _inspection(path: Path, check: str, file_format: str, headers: list[str], row_count: int) -> _Inspection:
    missing = _missing_groups(check, headers)
    if not headers:
        status = "empty_or_no_header"
    elif row_count <= 0 and (check != "provisiones_suplidos_book" or missing):
        status = "empty_or_no_data_rows"
    elif missing:
        status = "content_incomplete"
    else:
        status = "content_ready"
    return _Inspection(str(path), status, file_format, row_count, headers, missing)


def _missing_groups(check: str, headers: list[str]) -> list[str]:
    mapping = required_group_header_map(check, headers)
    return [group for group in _required_groups(check) if group not in mapping]


def required_group_header_map(check: str, headers: list[str]) -> dict[str, str]:
    groups = required_groups_for_check(check)
    normalized_headers = [_normalize(header) for header in headers]
    compact_headers = [_compact(header) for header in normalized_headers]
    mapping: dict[str, str] = {}
    for group, aliases in groups.items():
        match = _matched_header(aliases, headers, normalized_headers, compact_headers)
        if match:
            mapping[group] = match
    return mapping


def required_groups_for_check(check: str) -> dict[str, list[str]]:
    return _required_groups(check)


def _matched_header(
    aliases: list[str],
    headers: list[str],
    normalized_headers: list[str],
    compact_headers: list[str],
) -> str:
    for alias in aliases:
        for header, normalized_header, compact_header in zip(headers, normalized_headers, compact_headers, strict=True):
            if _alias_matches(alias, [normalized_header], [compact_header]):
                return header
    return ""


def _alias_matches(alias: str, normalized_headers: list[str], compact_headers: list[str]) -> bool:
    normalized_alias = _normalize(alias)
    compact_alias = _compact(normalized_alias)
    for header, compact_header in zip(normalized_headers, compact_headers, strict=True):
        if normalized_alias == header or compact_alias == compact_header:
            return True
        alias_tokens = normalized_alias.split()
        header_tokens = header.split()
        if len(alias_tokens) == 1:
            token = alias_tokens[0]
            if token in header_tokens:
                return True
            if (any(char.isdigit() for char in token) or token == "%") and compact_alias and compact_alias in compact_header:
                return True
            continue
        if normalized_alias and normalized_alias in header:
            return True
        if compact_alias and compact_alias in compact_header:
            return True
    return False


def _read_delimited_headers(path: Path, delimiter: str | None) -> tuple[list[str], int]:
    text = _read_text(path)
    sample = text[:4096]
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            delimiter = ","
    reader = csv.reader(text.splitlines(), delimiter=delimiter)
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        return [], 0
    return [cell.strip() for cell in rows[0]], max(0, len(rows) - 1)


def _read_text(path: Path) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            return path.read_text(encoding=encoding)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _best_xlsx_inspection(path: Path, check: str) -> _Inspection:
    tables = read_xlsx_tables(path)
    if not tables:
        return _inspection(path, check, "xlsx", [], 0)
    if _canonical_check(check) in EMPTY_OK_BOOK_TYPES and _is_xolo_combined_accounting_book(path):
        has_matching_table = any(
            _table_type_rank(check, table.headers) == 0 and not _missing_groups(check, table.headers)
            for table in tables
        )
        if not has_matching_table:
            return _Inspection(
                str(path),
                "content_ready",
                f"xlsx:{_canonical_check(check)}:empty-not-applicable",
                0,
                _synthetic_required_headers(check),
                [],
            )
    best_table = min(
        tables,
        key=lambda table: (
            _table_type_rank(check, table.headers),
            _table_data_penalty(table.rows),
            len(_missing_groups(check, table.headers)),
            -len(table.rows),
        ),
    )
    return _inspection(path, check, best_table.file_format, best_table.headers, len(best_table.rows))


def _inspection_rank(item: _Inspection) -> tuple[int, int, int]:
    status_rank = {
        "content_ready": 0,
        "content_incomplete": 1,
        "empty_or_no_data_rows": 2,
        "empty_or_no_header": 3,
    }.get(item.status, 9)
    return (status_rank, len(item.missing_groups), -item.row_count)


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


def _matched_paths(root: Path, raw: str) -> list[Path]:
    paths: list[Path] = []
    for value in [part.strip() for part in raw.split(";") if part.strip()]:
        path = Path(value)
        if not path.is_absolute():
            path = root / value
        paths.append(path)
    return paths


def _least_missing_groups(inspections: list[_Inspection], check: str) -> list[str]:
    if not inspections:
        return sorted(_required_groups(check))
    return min((item.missing_groups for item in inspections), key=len, default=sorted(_required_groups(check)))


def _required_groups(check: str) -> dict[str, list[str]]:
    return REQUIRED_GROUPS.get(check) or REQUIRED_GROUPS.get(LEGACY_REQUIRED_GROUP_ALIASES.get(check, ""), {})


def _canonical_check(check: str) -> str:
    return LEGACY_REQUIRED_GROUP_ALIASES.get(check, check)


def _synthetic_required_headers(check: str) -> list[str]:
    return [aliases[0] for aliases in _required_groups(check).values()]


def _is_xolo_combined_accounting_book(path: Path) -> bool:
    name = _normalize(path.name)
    return "libros contables" in name or "libro contable" in name


def _inspection_evidence(inspections: list[_Inspection]) -> str:
    if not inspections:
        return "No files inspected."
    pieces = []
    for item in inspections:
        detail = f"{Path(item.path).name}: status={item.status}; rows={item.row_count}; format={item.file_format}"
        if item.error:
            detail += f"; error={item.error}"
        pieces.append(detail)
    return " | ".join(pieces)


def _headers_summary(item: _Inspection) -> str:
    if not item.headers:
        return f"{Path(item.path).name}:"
    return f"{Path(item.path).name}: " + ", ".join(item.headers[:20])


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
    return re.sub(r"[^a-z0-9%]+", "", value)


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
