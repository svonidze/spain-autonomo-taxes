from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib import error, parse, request
import xml.etree.ElementTree as ET
import zipfile


OFFICIAL_TEMPLATE_SHA256 = (
    "0dd42d6b40d9388365624ae2ed7f301d8479ec92271b4eac06d2440096f49ebc"
)
AEAT_VALIDATOR_POST_URL = (
    "https://www2.agenciatributaria.gob.es/wlpl/PACM-SERV/ServletValidarLLRSI"
)
MAX_AEAT_WORKBOOK_BYTES = 4 * 1024 * 1024


class AeatValidationConsentError(ValueError):
    """Raised when an AEAT upload was not explicitly confirmed."""


def _column_letter(index: int) -> str:
    output = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


def _column_specs(*items: tuple[str, str]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "index": index,
            "letter": _column_letter(index),
            "projection_key": key,
            "value_type": value_type,
        }
        for index, (key, value_type) in enumerate(items, start=1)
    )


INCOME_COLUMNS = _column_specs(
    ("autoliquidacion_ejercicio", "integer"),
    ("autoliquidacion_periodo", "text"),
    ("actividad_codigo", "text"),
    ("actividad_tipo", "text"),
    ("iae_grupo_epigrafe", "text"),
    ("tipo_factura", "text"),
    ("concepto_ingreso", "text"),
    ("ingreso_computable_eur", "decimal"),
    ("fecha_expedicion", "date"),
    ("fecha_operacion", "date"),
    ("factura_serie", "text"),
    ("factura_numero", "text"),
    ("factura_numero_final", "text"),
    ("destinatario_id_tipo", "text"),
    ("destinatario_pais", "text"),
    ("destinatario_identificacion", "text"),
    ("destinatario_nombre", "text"),
    ("clave_operacion", "text"),
    ("calificacion_operacion", "text"),
    ("operacion_exenta", "text"),
    ("total_factura_eur", "decimal"),
    ("base_imponible_eur", "decimal"),
    ("tipo_iva_percent", "decimal"),
    ("cuota_iva_repercutida_eur", "decimal"),
    ("tipo_recargo_equivalencia_percent", "decimal"),
    ("cuota_recargo_equivalencia_eur", "decimal"),
    ("cobro_fecha", "date"),
    ("cobro_importe_eur", "decimal"),
    ("cobro_medio", "text"),
    ("cobro_identificacion_medio", "text"),
    ("tipo_retencion_irpf_percent", "decimal"),
    ("importe_retenido_irpf_eur", "decimal"),
    ("registro_acuerdo_facturacion", "text"),
    ("inmueble_situacion", "text"),
    ("inmueble_referencia_catastral", "text"),
    ("referencia_externa", "text"),
)

EXPENSE_COLUMNS = _column_specs(
    ("autoliquidacion_ejercicio", "integer"),
    ("autoliquidacion_periodo", "text"),
    ("actividad_codigo", "text"),
    ("actividad_tipo", "text"),
    ("iae_grupo_epigrafe", "text"),
    ("tipo_factura", "text"),
    ("concepto_gasto", "text"),
    ("gasto_deducible_eur", "decimal"),
    ("fecha_expedicion", "date"),
    ("fecha_operacion", "date"),
    ("factura_expedidor_serie_numero", "text"),
    ("factura_expedidor_numero_final", "text"),
    ("fecha_recepcion", "date"),
    ("numero_recepcion", "text"),
    ("numero_recepcion_final", "text"),
    ("expedidor_id_tipo", "text"),
    ("expedidor_pais", "text"),
    ("expedidor_identificacion", "text"),
    ("expedidor_nombre", "text"),
    ("clave_operacion", "text"),
    ("bien_inversion", "text"),
    ("inversion_sujeto_pasivo", "text"),
    ("deducible_periodo_posterior", "text"),
    ("deduccion_ejercicio", "integer"),
    ("deduccion_periodo", "text"),
    ("total_factura_eur", "decimal"),
    ("base_imponible_eur", "decimal"),
    ("tipo_iva_percent", "decimal"),
    ("cuota_iva_soportado_eur", "decimal"),
    ("cuota_deducible_eur", "decimal"),
    ("tipo_recargo_equivalencia_percent", "decimal"),
    ("cuota_recargo_equivalencia_eur", "decimal"),
    ("pago_fecha", "date"),
    ("pago_importe_eur", "decimal"),
    ("pago_medio", "text"),
    ("pago_identificacion_medio", "text"),
    ("tipo_retencion_irpf_percent", "decimal"),
    ("importe_retenido_irpf_eur", "decimal"),
    ("registro_acuerdo_facturacion", "text"),
    ("inmueble_situacion", "text"),
    ("inmueble_referencia_catastral", "text"),
    ("referencia_externa", "text"),
)

ASSET_COLUMNS = _column_specs(
    ("autoliquidacion_ejercicio", "integer"),
    ("autoliquidacion_periodo", "text"),
    ("actividad_codigo", "text"),
    ("actividad_tipo", "text"),
    ("iae_grupo_epigrafe", "text"),
    ("tipo_bien", "text"),
    ("descripcion_identificador", "text"),
    ("descripcion_literal", "text"),
    ("fecha_inicio_utilizacion", "date"),
    ("valor_adquisicion_eur", "decimal"),
    ("valor_amortizable_eur", "decimal"),
    ("metodo_amortizacion", "text"),
    ("porcentaje_amortizacion", "decimal"),
    ("amortizacion_acumulada_inicio_eur", "decimal"),
    ("amortizacion_cuota_resultante_eur", "decimal"),
    ("amortizacion_acumulada_final_eur", "decimal"),
    ("amortizacion_pendiente_eur", "decimal"),
    ("fecha_expedicion", "date"),
    ("factura_expedidor_serie_numero", "text"),
    ("factura_expedidor_numero_final", "text"),
    ("numero_recepcion", "text"),
    ("numero_recepcion_final", "text"),
    ("expedidor_id_tipo", "text"),
    ("expedidor_pais", "text"),
    ("expedidor_identificacion", "text"),
    ("expedidor_nombre", "text"),
    ("inicio_base_imponible_eur", "decimal"),
    ("inicio_tipo_iva_percent", "decimal"),
    ("inicio_prorrata_definitiva_percent", "decimal"),
    ("inicio_cuota_deducible_eur", "decimal"),
    ("regularizacion_prorrata_definitiva_percent", "decimal"),
    ("regularizacion_cuota_deducible_eur", "decimal"),
    ("regularizacion_cuota_eur", "decimal"),
    ("baja_fecha", "date"),
    ("baja_causa", "text"),
    ("transmision_factura_serie", "text"),
    ("transmision_factura_numero", "text"),
    ("transmision_factura_numero_final", "text"),
    ("registro_acuerdo_facturacion", "text"),
    ("inmueble_situacion", "text"),
    ("inmueble_referencia_catastral", "text"),
    ("referencia_externa", "text"),
)

SHEET_CONTRACTS = {
    "EXPEDIDAS_INGRESOS": INCOME_COLUMNS,
    "RECIBIDAS_GASTOS": EXPENSE_COLUMNS,
    "BIENES-INVERSIÓN": ASSET_COLUMNS,
}

TYPE_ROW_CONTRACTS = {
    "EXPEDIDAS_INGRESOS": (
        "Decimal (4,0)", "Alfanumérico (2)", "Alfanumérico (1)",
        "Alfanumérico (2)", "Alfanumérico (4)", "Alfanumérico (2)",
        "Alfanumérico (3)", "Decimal(12,2)", "Fecha(dd/mm/yyyy)",
        "Fecha(dd/mm/yyyy)", "Alfanumérico (20)", "Alfanumérico (20)",
        "Alfanumérico (20)", "Alfanumérico (2)", "Alfanumérico (2)",
        "Alfanumérico (20)", "Alfanumérico (40)", "Alfanumérico (2)",
        "Alfanumérico (2)", "Alfanumérico (2)", "Decimal(12,2)",
        "Decimal(12,2)", "Decimal(4,2)", "Decimal(12,2)",
        "Decimal(4,2)", "Decimal(12,2)", "Fecha(dd/mm/yyyy)",
        "Decimal(12,2)", "Alfanumérico (2)", "Alfanumérico (34)",
        "Decimal(4,2)", "Decimal(12,2)", "Alfanumérico (15)",
        "Alfanumérico (1)", "Alfanumérico (20)", "Alfanumérico (40)",
    ),
    "RECIBIDAS_GASTOS": (
        "Decimal (4,0)", "Alfanumérico (2)", "Alfanumérico (1)",
        "Alfanumérico (2)", "Alfanumérico (4)", "Alfanumérico (2)",
        "Alfanumérico (3)", "Decimal(12,2)", "Fecha(dd/mm/yyyy)",
        "Fecha(dd/mm/yyyy)", "Alfanumérico (40)", "Alfanumérico (20)",
        "Fecha(dd/mm/yyyy)", "Alfanumérico (20)", "Alfanumérico (20)",
        "Alfanumérico (2)", "Alfanumérico (2)", "Alfanumérico (20)",
        "Alfanumérico (40)", "Alfanumérico (2)", "Alfanumérico (1)",
        "Alfanumérico (1)", "Alfanumérico (1)", "Decimal (4,0)",
        "Alfanumérico (2)", "Decimal(12,2)", "Decimal(12,2)",
        "Decimal(4,2)", "Decimal(12,2)", "Decimal(12,2)",
        "Decimal(4,2)", "Decimal(12,2)", "Fecha(dd/mm/yyyy)",
        "Decimal(12,2)", "Alfanumérico (2)", "Alfanumérico (34)",
        "Decimal(4,2)", "Decimal(12,2)", "Alfanumérico (15)",
        "Alfanumérico (1)", "Alfanumérico (20)", "Alfanumérico (40)",
    ),
    "BIENES-INVERSIÓN": (
        "Decimal (4,0)", "Alfanumérico (2)", "Alfanumérico (1)",
        "Alfanumérico (2)", "Alfanumérico (4)", "Alfanumérico (2)",
        "Alfanumérico (40)", "Alfanumérico (160)", "Fecha(dd/mm/yyyy)",
        "Decimal(12,2)", "Decimal(12,2)", "Alfanumérico (2)",
        "Decimal (4,2)", "Decimal(12,2)", "Decimal(12,2)",
        "Decimal(12,2)", "Decimal(12,2)", "Fecha(dd/mm/yyyy)",
        "Alfanumérico (40)", "Alfanumérico (20)", "Alfanumérico (20)",
        "Alfanumérico (20)", "Alfanumérico (2)", "Alfanumérico (2)",
        "Alfanumérico (20)", "Alfanumérico (40)", "Decimal(12,2)",
        "Decimal(4,2)", "Decimal(5,2)", "Decimal(12,2)",
        "Decimal(5,2)", "Decimal(12,2)", "Decimal(12,2)",
        "Fecha(dd/mm/yyyy)", "Alfanumérico (2)", "Alfanumérico (20)",
        "Alfanumérico (20)", "Alfanumérico (20)", "Alfanumérico (15)",
        "Alfanumérico (1)", "Alfanumérico (20)", "Alfanumérico (40)",
    ),
}


def inspect_aeat_template(
    path: str | Path,
    *,
    expected_sha256: str = OFFICIAL_TEMPLATE_SHA256,
) -> dict[str, Any]:
    source = Path(path)
    errors: list[str] = []
    if not source.is_file():
        return {
            "valid": False,
            "path": str(source.resolve()),
            "expected_sha256": expected_sha256,
            "actual_sha256": None,
            "sheets": {},
            "errors": ["Template file does not exist"],
        }
    actual_hash = _sha256(source)
    if actual_hash != expected_sha256:
        errors.append("Template SHA-256 does not match the reviewed AEAT 2026 template")
    sheet_checks: dict[str, Any] = {}
    try:
        with zipfile.ZipFile(source) as archive:
            workbook_sheets = _workbook_sheets(archive)
            shared_strings = _shared_strings(archive)
            for sheet_name, columns in SHEET_CONTRACTS.items():
                target = workbook_sheets.get(sheet_name)
                if target is None:
                    errors.append(f"Missing required sheet: {sheet_name}")
                    continue
                rows = _sheet_rows(archive, target, shared_strings, through_row=3)
                type_row = tuple(rows.get(3, []))
                width_ok = len(type_row) == len(columns)
                type_row_ok = type_row == TYPE_ROW_CONTRACTS[sheet_name]
                if not width_ok:
                    errors.append(
                        f"{sheet_name} has {len(type_row)} columns; expected {len(columns)}"
                    )
                if width_ok and not type_row_ok:
                    errors.append(f"{sheet_name} type row differs from the reviewed contract")
                sheet_checks[sheet_name] = {
                    "target": target,
                    "column_count": len(type_row),
                    "expected_column_count": len(columns),
                    "type_row_matches": type_row_ok,
                    "data_start_row": 4,
                }
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        errors.append(f"Invalid XLSX package: {exc}")
    return {
        "valid": not errors,
        "path": str(source.resolve()),
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_hash,
        "sheets": sheet_checks,
        "errors": errors,
    }


def build_aeat_workbook_payload(
    projection: Mapping[str, Any],
    *,
    template_check: Mapping[str, Any],
) -> dict[str, Any]:
    blockers = [dict(row) for row in projection.get("blockers", [])]
    if not template_check.get("valid"):
        blockers.append(
            {
                "code": "aeat_template_contract_mismatch",
                "subject": str(template_check.get("path") or "template"),
                "message": "; ".join(template_check.get("errors", []))
                or "AEAT template contract is not valid",
            }
        )
    sheets = {
        "EXPEDIDAS_INGRESOS": _sheet_payload(
            INCOME_COLUMNS, projection.get("income_rows", [])
        ),
        "RECIBIDAS_GASTOS": _sheet_payload(
            EXPENSE_COLUMNS, projection.get("expense_rows", [])
        ),
        "BIENES-INVERSIÓN": _sheet_payload(
            ASSET_COLUMNS, projection.get("asset_rows", [])
        ),
    }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "payload_type": "aeat_unified_books_xlsx_write_plan",
        "period": projection.get("period"),
        "scope": projection.get("scope"),
        "taxpayer": projection.get("taxpayer"),
        "provisional_filename": projection.get("provisional_filename"),
        "template": {
            "actual_sha256": template_check.get("actual_sha256"),
            "expected_sha256": template_check.get("expected_sha256"),
            "valid": bool(template_check.get("valid")),
        },
        "data_start_row": 4,
        "sheets": sheets,
        "blockers": blockers,
        "payload_ready": bool(projection.get("data_projection_ready")) and not blockers,
        "xlsx_generation_supported": False,
        "instructions": (
            "Write the row arrays into the reviewed AEAT template with @oai/artifact-tool, "
            "preserving the first three rows and all workbook validations."
        ),
    }
    payload["payload_sha256"] = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return payload


def write_aeat_workbook_payload(path: str | Path, payload: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


def validate_aeat_workbook(
    path: str | Path,
    *,
    year: int,
    confirm_upload_to_aeat: bool,
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    if not confirm_upload_to_aeat:
        raise AeatValidationConsentError(
            "AEAT validation uploads the workbook; pass explicit confirmation"
        )
    source = Path(path)
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise ValueError("AEAT validator input must be an existing .xlsx file")
    content = source.read_bytes()
    if len(content) > MAX_AEAT_WORKBOOK_BYTES:
        raise ValueError("AEAT workbook exceeds the official 4 MB limit")
    if year < 2000 or year > 2100:
        raise ValueError("AEAT validation year is outside the supported range")
    encoded = parse.urlencode(
        {
            "EJER": str(year),
            "FIC": base64.b64encode(content).decode("ascii"),
        }
    ).encode("ascii")
    http_request = request.Request(
        AEAT_VALIDATOR_POST_URL,
        data=encoded,
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "application/json, text/plain, */*",
        },
        method="POST",
    )
    response_status: int | None = None
    response_text = ""
    transport_error: str | None = None
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            response_status = int(response.status)
            response_text = response.read().decode("iso-8859-15", errors="replace")
    except error.HTTPError as exc:
        response_status = int(exc.code)
        response_text = exc.read().decode("iso-8859-15", errors="replace")
        transport_error = str(exc)
    except error.URLError as exc:
        transport_error = str(exc.reason)
    except (TimeoutError, OSError) as exc:
        transport_error = str(exc)
    try:
        validator_response: Any = json.loads(response_text) if response_text else None
    except json.JSONDecodeError:
        validator_response = {"raw_text": response_text}
    return {
        "schema_version": 1,
        "receipt_type": "aeat_unified_books_validator_response",
        "submitted_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "request": {
            "year": year,
            "validator_url": AEAT_VALIDATOR_POST_URL,
            "workbook_sha256": hashlib.sha256(content).hexdigest(),
            "workbook_size_bytes": len(content),
            "workbook_name": source.name,
        },
        "transport": {
            "http_status": response_status,
            "ok": response_status is not None and 200 <= response_status < 300,
            "error": transport_error,
        },
        "validator_response": validator_response,
        "interpretation": (
            "The validator response is preserved verbatim; transport success alone does not "
            "mean that AEAT accepted the workbook."
        ),
    }


def write_aeat_validation_receipt(path: str | Path, receipt: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return target


def _sheet_payload(
    columns: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    return {
        "columns": [dict(column) for column in columns],
        "row_count": len(rows),
        "rows": [
            [row.get(str(column["projection_key"]), "") for column in columns]
            for row in rows
        ],
        "row_lineage": [
            row.get("transaction_id") or row.get("asset_id") or row.get("referencia_externa")
            for row in rows
        ],
    }


def _workbook_sheets(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {row.attrib["Id"]: row.attrib["Target"] for row in relationships}
    result: dict[str, str] = {}
    relationship_id = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    for sheet in workbook.findall(".//{*}sheet"):
        target = targets[sheet.attrib[relationship_id]].lstrip("/")
        if not target.startswith("xl/"):
            target = f"xl/{target}"
        result[sheet.attrib["name"]] = target
    return result


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.findall(".//{*}t"))
        for item in root.findall(".//{*}si")
    ]


def _sheet_rows(
    archive: zipfile.ZipFile,
    target: str,
    shared_strings: Sequence[str],
    *,
    through_row: int,
) -> dict[int, list[str]]:
    root = ET.fromstring(archive.read(target))
    output: dict[int, list[str]] = {}
    for row in root.findall(".//{*}sheetData/{*}row"):
        row_number = int(row.attrib["r"])
        if row_number > through_row:
            continue
        values: dict[int, str] = {}
        for cell in row.findall("{*}c"):
            column = _column_index(cell.attrib.get("r", ""))
            values[column] = _cell_value(cell, shared_strings)
        width = max(values, default=0)
        output[row_number] = [values.get(index, "") for index in range(1, width + 1)]
    return output


def _cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(".//{*}t"))
    value = cell.find("{*}v")
    raw = value.text if value is not None and value.text else ""
    if cell_type == "s" and raw.isdigit():
        return shared_strings[int(raw)]
    return raw


def _column_index(reference: str) -> int:
    index = 0
    for char in reference:
        if not char.isalpha():
            break
        index = index * 26 + ord(char.upper()) - ord("A") + 1
    return index


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
