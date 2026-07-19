from __future__ import annotations

import base64
import copy
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
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
TEMPLATE_DATA_START_ROW = 4
TEMPLATE_DATA_END_ROW = 103
TEMPLATE_DATA_CAPACITY = TEMPLATE_DATA_END_ROW - TEMPLATE_DATA_START_ROW + 1


class AeatValidationConsentError(ValueError):
    """Raised when an AEAT upload was not explicitly confirmed."""


class AeatWorkbookWriteError(ValueError):
    """Raised when an official AEAT workbook cannot be emitted safely."""


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

INPUT_SHEET_BY_CONTRACT = {
    "EXPEDIDAS_INGRESOS": "Registrar expedidas_ingresos",
    "RECIBIDAS_GASTOS": "Registrar recibidas_gastos",
    "BIENES-INVERSIÓN": "Registrar bienes de inversión",
}

_ACTIVITY_SUBTYPE_CATEGORY = "__activity_subtype__"
INPUT_LITERAL_CATEGORY_BY_CONTRACT = {
    "EXPEDIDAS_INGRESOS": {
        "C": "ACTIVIDAD",
        "D": _ACTIVITY_SUBTYPE_CATEGORY,
        "F": "TIPO FACTURA",
        "G": "CONCEPTO INGRESO",
        "N": "TIPO NIF",
        "R": "CLAVE OPERACION",
        "S": "CALIFICACION OPERACION",
        "T": "OPERACION EXENTA",
        "AC": "MEDIO UTILIZADO",
        "AH": "SITUACION",
    },
    "RECIBIDAS_GASTOS": {
        "C": "ACTIVIDAD",
        "D": _ACTIVITY_SUBTYPE_CATEGORY,
        "F": "TIPO FACTURA GASTO",
        "G": "CONCEPTO GASTO",
        "P": "TIPO NIF",
        "T": "CLAVE OPERACION GASTO",
        "AI": "MEDIO UTILIZADO",
        "AN": "SITUACION",
    },
    "BIENES-INVERSIÓN": {
        "C": "ACTIVIDAD",
        "D": _ACTIVITY_SUBTYPE_CATEGORY,
        "F": "TIPO BIEN",
        "L": "METODO AMORTIZACION",
        "W": "TIPO NIF",
        "AI": "CAUSA BAJA BIEN",
        "AN": "SITUACION",
    },
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
            "writer_strategy": _writer_strategy_unavailable("template_missing"),
            "errors": ["Template file does not exist"],
        }
    actual_hash = _sha256(source)
    if actual_hash != expected_sha256:
        errors.append("Template SHA-256 does not match the reviewed AEAT 2026 template")
    sheet_checks: dict[str, Any] = {}
    architecture_errors: list[str] = []
    input_sheet_checks: dict[str, Any] = {}
    calc_chain_check = {
        "part_present": False,
        "content_type_registered": False,
        "relationship_present": False,
    }
    try:
        with zipfile.ZipFile(source) as archive:
            workbook_sheets = _workbook_sheets(archive)
            for package_part in {"xl/workbook.xml", *workbook_sheets.values()}:
                missing_prefixes = _undeclared_ignorable_prefixes(
                    archive.read(package_part)
                )
                if missing_prefixes:
                    architecture_errors.append(
                        f"{package_part} has undeclared mc:Ignorable prefixes: "
                        + ", ".join(sorted(missing_prefixes))
                    )
            shared_strings = _shared_strings(archive)
            try:
                literal_lookup = _code_literal_lookup(archive)
            except AeatWorkbookWriteError as exc:
                architecture_errors.append(str(exc))
                literal_lookup = {}
            if not literal_lookup:
                architecture_errors.append(
                    "AEAT template CODIGO-LITERAL lookup is empty"
                )
            calc_chain_check = _calc_chain_check(archive)
            if not all(calc_chain_check.values()):
                architecture_errors.append(
                    "Reviewed template calcChain package links are incomplete"
                )
            for contract_name, input_name in INPUT_SHEET_BY_CONTRACT.items():
                target = workbook_sheets.get(input_name)
                if target is None:
                    architecture_errors.append(f"Missing input sheet: {input_name}")
                    continue
                root = ET.fromstring(archive.read(target))
                validation_count = len(root.findall(".//{*}dataValidation"))
                if validation_count == 0:
                    architecture_errors.append(
                        f"{input_name} has no data-validation rules"
                    )
                input_sheet_checks[contract_name] = {
                    "name": input_name,
                    "target": target,
                    "data_validation_count": validation_count,
                    "data_formula_count": _formula_cell_count(
                        root,
                        start_row=TEMPLATE_DATA_START_ROW,
                        end_row=TEMPLATE_DATA_END_ROW,
                    ),
                }
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
                root = ET.fromstring(archive.read(target))
                formula_references = _formula_cell_references(
                    root,
                    start_row=TEMPLATE_DATA_START_ROW,
                    end_row=TEMPLATE_DATA_END_ROW,
                )
                expected_formula_references = {
                    f"{_column_letter(column_index)}{row_number}"
                    for row_number in range(
                        TEMPLATE_DATA_START_ROW, TEMPLATE_DATA_END_ROW + 1
                    )
                    for column_index in range(1, len(columns) + 1)
                }
                missing_formula_references = (
                    expected_formula_references - formula_references
                )
                unexpected_formula_references = (
                    formula_references - expected_formula_references
                )
                formula_mirror = not (
                    missing_formula_references or unexpected_formula_references
                )
                if not formula_mirror:
                    architecture_errors.append(
                        f"{sheet_name} is not the reviewed 100-row formula mirror"
                    )
                sheet_checks[sheet_name] = {
                    "target": target,
                    "column_count": len(type_row),
                    "expected_column_count": len(columns),
                    "type_row_matches": type_row_ok,
                    "data_start_row": TEMPLATE_DATA_START_ROW,
                    "data_end_row": TEMPLATE_DATA_END_ROW,
                    "data_capacity": TEMPLATE_DATA_CAPACITY,
                    "formula_cell_count": len(formula_references),
                    "expected_formula_cell_count": len(expected_formula_references),
                    "missing_formula_cell_count": len(missing_formula_references),
                    "unexpected_formula_cell_count": len(
                        unexpected_formula_references
                    ),
                    "formula_mirror": formula_mirror,
                    "input_sheet": input_sheet_checks.get(sheet_name),
                }
    except (zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        errors.append(f"Invalid XLSX package: {exc}")
    errors.extend(architecture_errors)
    formula_mirrors = bool(sheet_checks) and all(
        bool(check.get("formula_mirror")) for check in sheet_checks.values()
    )
    architecture_valid = not errors and formula_mirrors
    writer_strategy = {
        "status": (
            "input_sheet_writer_ready"
            if architecture_valid
            else "template_architecture_invalid"
        ),
        "xlsx_generation_supported": architecture_valid,
        "contract_sheets_are_formula_mirrors": formula_mirrors,
        "data_start_row": TEMPLATE_DATA_START_ROW,
        "data_end_row": TEMPLATE_DATA_END_ROW,
        "data_capacity": TEMPLATE_DATA_CAPACITY,
        "calc_chain": calc_chain_check,
        "input_sheets": input_sheet_checks,
        "reason": (
            "The reviewed ALL-CAPS sheets are formula mirrors of Registrar input sheets. "
            "The deterministic writer populates only the unlocked Registrar cells and marks "
            "the workbook for recalculation. Official validator acceptance remains a separate "
            "manual gate."
        ),
    }
    return {
        "valid": not errors,
        "path": str(source.resolve()),
        "expected_sha256": expected_sha256,
        "actual_sha256": actual_hash,
        "sheets": sheet_checks,
        "writer_strategy": writer_strategy,
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
    generation_blockers: list[dict[str, Any]] = []
    for sheet_name, sheet in sheets.items():
        if int(sheet["row_count"]) <= TEMPLATE_DATA_CAPACITY:
            continue
        generation_blockers.append(
            {
                "code": "aeat_template_capacity_exceeded",
                "subject": sheet_name,
                "message": (
                    f"{sheet_name} has {sheet['row_count']} rows; the reviewed template "
                    f"supports at most {TEMPLATE_DATA_CAPACITY}."
                ),
            }
        )
    payload: dict[str, Any] = {
        "schema_version": 3,
        "payload_type": "aeat_unified_books_xlsx_write_plan",
        "period": projection.get("period"),
        "scope": projection.get("scope"),
        "taxpayer": projection.get("taxpayer"),
        "provisional_filename": projection.get("provisional_filename"),
        "template": {
            "actual_sha256": template_check.get("actual_sha256"),
            "expected_sha256": template_check.get("expected_sha256"),
            "valid": bool(template_check.get("valid")),
            "writer_strategy": dict(template_check.get("writer_strategy", {})),
        },
        "data_start_row": TEMPLATE_DATA_START_ROW,
        "data_end_row": TEMPLATE_DATA_END_ROW,
        "data_capacity": TEMPLATE_DATA_CAPACITY,
        "sheets": sheets,
        "blockers": blockers,
        "payload_ready": bool(projection.get("data_projection_ready")) and not blockers,
        "xlsx_generation_supported": True,
        "writer_strategy_status": (
            template_check.get("writer_strategy", {}).get("status")
            or (
                "input_sheet_writer_ready"
                if template_check.get("valid")
                else "template_architecture_invalid"
            )
        ),
        "xlsx_generation_blockers": generation_blockers,
        "instructions": (
            "Write rows only into the unlocked Registrar input sheets. Translate controlled "
            "codes through the template CODIGO-LITERAL lookup, replace Registrar helper "
            "formulas with literals, preserve the ALL-CAPS formula mirrors, request full "
            "recalculation on open, and treat official validator acceptance as a separate "
            "manual gate."
        ),
    }
    payload["xlsx_write_ready"] = bool(payload["payload_ready"]) and not generation_blockers
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


def write_aeat_workbook_xlsx(
    template_path: str | Path,
    payload: Mapping[str, Any],
    out_path: str | Path,
) -> Path:
    source = Path(template_path)
    target = Path(out_path)
    if source.suffix.lower() != ".xlsx" or not source.is_file():
        raise AeatWorkbookWriteError("AEAT source template must be an existing .xlsx file")
    if target.suffix.lower() != ".xlsx":
        raise AeatWorkbookWriteError("AEAT workbook output must use the .xlsx suffix")
    if source.resolve() == target.resolve():
        raise AeatWorkbookWriteError("Refusing to overwrite the source template")
    if not payload.get("payload_ready"):
        raise AeatWorkbookWriteError("AEAT workbook payload is not ready")
    generation_blockers = list(payload.get("xlsx_generation_blockers", []))
    if generation_blockers:
        codes = ", ".join(str(row.get("code") or "unknown") for row in generation_blockers)
        if "aeat_template_capacity_exceeded" in codes:
            raise AeatWorkbookWriteError(f"AEAT workbook capacity blocker: {codes}")
        raise AeatWorkbookWriteError(f"AEAT workbook generation is blocked: {codes}")
    if not payload.get("xlsx_write_ready"):
        raise AeatWorkbookWriteError("AEAT workbook payload is not ready for XLSX output")
    if payload.get("payload_type") != "aeat_unified_books_xlsx_write_plan":
        raise AeatWorkbookWriteError("Unsupported AEAT workbook payload type")
    if payload.get("payload_sha256") != _payload_sha256(payload):
        raise AeatWorkbookWriteError("AEAT workbook payload hash does not match its content")

    template_meta = payload.get("template")
    if not isinstance(template_meta, Mapping):
        raise AeatWorkbookWriteError("AEAT payload does not identify its source template")
    expected_hash = str(template_meta.get("expected_sha256") or "")
    payload_template_hash = str(template_meta.get("actual_sha256") or "")
    source_hash = _sha256(source)
    if not expected_hash or source_hash != expected_hash or source_hash != payload_template_hash:
        raise AeatWorkbookWriteError("AEAT source template hash does not match the payload")
    template_check = inspect_aeat_template(source, expected_sha256=expected_hash)
    if not template_check.get("valid"):
        raise AeatWorkbookWriteError(
            "AEAT source template contract is invalid: "
            + "; ".join(str(row) for row in template_check.get("errors", []))
        )

    sheet_payloads = payload.get("sheets")
    if not isinstance(sheet_payloads, Mapping):
        raise AeatWorkbookWriteError("AEAT payload has no sheet data")

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}-",
        suffix=".xlsx.tmp",
        dir=target.parent,
        delete=False,
    )
    temporary = Path(temporary_handle.name)
    temporary_handle.close()
    try:
        with zipfile.ZipFile(source, "r") as source_archive:
            workbook_sheets = _workbook_sheets(source_archive)
            workbook_sheet_ids = _workbook_sheet_ids(source_archive)
            literal_lookup = _code_literal_lookup(source_archive)
            replacements = {
                "xl/workbook.xml": _workbook_recalculation_xml(
                    source_archive.read("xl/workbook.xml")
                )
            }
            removed_formula_cells: dict[str, set[str]] = {}
            for contract_name, columns in SHEET_CONTRACTS.items():
                input_name = INPUT_SHEET_BY_CONTRACT[contract_name]
                input_target = workbook_sheets.get(input_name)
                if input_target is None:
                    raise AeatWorkbookWriteError(
                        f"AEAT source template is missing input sheet {input_name}"
                    )
                sheet_payload = sheet_payloads.get(contract_name)
                if not isinstance(sheet_payload, Mapping):
                    raise AeatWorkbookWriteError(
                        f"AEAT payload is missing sheet {contract_name}"
                    )
                rewritten_sheet, removed_references = _populated_input_sheet_xml(
                    source_archive.read(input_target),
                    columns=columns,
                    sheet_payload=sheet_payload,
                    sheet_name=input_name,
                    contract_name=contract_name,
                    literal_lookup=literal_lookup,
                )
                replacements[input_target] = rewritten_sheet
                sheet_id = workbook_sheet_ids.get(input_name)
                if sheet_id is None:
                    raise AeatWorkbookWriteError(
                        f"AEAT source template has no sheet id for {input_name}"
                    )
                if removed_references:
                    removed_formula_cells[sheet_id] = removed_references
            replacements["xl/calcChain.xml"] = _calc_chain_without_cells(
                source_archive.read("xl/calcChain.xml"),
                removed_formula_cells=removed_formula_cells,
            )

            with zipfile.ZipFile(temporary, "w") as output_archive:
                output_archive.comment = source_archive.comment
                for member in source_archive.infolist():
                    member_copy = copy.copy(member)
                    content = replacements.get(member.filename)
                    if content is None:
                        content = source_archive.read(member.filename)
                    output_archive.writestr(member_copy, content)

        if temporary.stat().st_size > MAX_AEAT_WORKBOOK_BYTES:
            raise AeatWorkbookWriteError("Generated AEAT workbook exceeds the official 4 MB limit")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _workbook_recalculation_xml(content: bytes) -> bytes:
    pattern = re.compile(
        rb"<(?P<qname>(?:[A-Za-z_][A-Za-z0-9_.-]*:)?calcPr)\b"
        rb"(?P<attrs>[^>]*?)(?P<ending>/?>)",
        re.DOTALL,
    )

    def replace(match: re.Match[bytes]) -> bytes:
        attrs = _set_xml_attribute(
            match.group("attrs"),
            b"fullCalcOnLoad",
            b"1",
        )
        return b"<" + match.group("qname") + attrs + match.group("ending")

    output, count = pattern.subn(replace, content, count=1)
    if count != 1:
        raise AeatWorkbookWriteError("AEAT template workbook.xml has no calcPr element")
    _validate_rewritten_xml(output, package_part="xl/workbook.xml")
    return output


def _populated_input_sheet_xml(
    content: bytes,
    *,
    columns: Sequence[Mapping[str, Any]],
    sheet_payload: Mapping[str, Any],
    sheet_name: str,
    contract_name: str,
    literal_lookup: Mapping[tuple[str, str], str],
) -> tuple[bytes, set[str]]:
    payload_columns = sheet_payload.get("columns")
    if not isinstance(payload_columns, list) or payload_columns != [
        dict(column) for column in columns
    ]:
        raise AeatWorkbookWriteError(
            f"AEAT payload column contract differs for {sheet_name}"
        )
    rows = sheet_payload.get("rows")
    if not isinstance(rows, list):
        raise AeatWorkbookWriteError(f"AEAT payload rows are invalid for {sheet_name}")
    if int(sheet_payload.get("row_count", -1)) != len(rows):
        raise AeatWorkbookWriteError(
            f"AEAT payload row count differs for {sheet_name}"
        )
    if len(rows) > TEMPLATE_DATA_CAPACITY:
        raise AeatWorkbookWriteError(
            f"AEAT workbook capacity exceeded for {sheet_name}"
        )

    root = ET.fromstring(content)
    cells = {
        cell.attrib.get("r", ""): cell
        for cell in root.findall(".//{*}sheetData/{*}row/{*}c")
    }
    writes: dict[str, tuple[Any, str, str]] = {}
    formula_references: set[str] = set()
    for row_offset, row_values in enumerate(rows):
        row_number = TEMPLATE_DATA_START_ROW + row_offset
        if not isinstance(row_values, list) or len(row_values) != len(columns):
            raise AeatWorkbookWriteError(
                f"AEAT payload row {row_offset + 1} has the wrong width for {sheet_name}"
            )
        for column_offset, column in enumerate(columns, start=1):
            reference = f"{_column_letter(column_offset)}{row_number}"
            cell = cells.get(reference)
            if cell is None or "s" not in cell.attrib:
                raise AeatWorkbookWriteError(
                    f"AEAT template lacks styled input cell {sheet_name}!{reference}"
                )
            writes[reference] = (
                _input_template_value(
                    contract_name=contract_name,
                    column_letter=str(column["letter"]),
                    row_values=row_values,
                    projected_value=row_values[column_offset - 1],
                    literal_lookup=literal_lookup,
                    reference=f"{sheet_name}!{reference}",
                ),
                str(column["value_type"]),
                f"{sheet_name}!{reference}",
            )
    for reference, cell in cells.items():
        formula = cell.find("{*}f")
        if formula is None:
            continue
        row_number = _row_index(reference)
        column_index = _column_index(reference)
        if not (
            TEMPLATE_DATA_START_ROW <= row_number <= TEMPLATE_DATA_END_ROW
            and 1 <= column_index <= len(columns)
        ):
            continue
        if any(_local_name(child.tag) not in {"f", "v"} for child in cell):
            raise AeatWorkbookWriteError(
                f"AEAT formula cell has unsupported children: {sheet_name}!{reference}"
            )
        formula_references.add(reference)
        writes.setdefault(
            reference,
            (
                "",
                str(columns[column_index - 1]["value_type"]),
                f"{sheet_name}!{reference}",
            ),
        )
    seen: set[str] = set()

    def replace_cell(match: re.Match[bytes]) -> bytes:
        reference_bytes = _xml_attribute_value(match.group("attrs"), b"r")
        if reference_bytes is None:
            return match.group(0)
        reference = reference_bytes.decode("ascii", errors="strict")
        write = writes.get(reference)
        if write is None:
            return match.group(0)
        if reference in seen:
            raise AeatWorkbookWriteError(
                f"AEAT template contains duplicate input cell {sheet_name}!{reference}"
            )
        seen.add(reference)
        value, value_type, qualified_reference = write
        return _rewritten_cell_fragment(
            match,
            value=value,
            value_type=value_type,
            reference=qualified_reference,
        )

    output = _CELL_FRAGMENT_PATTERN.sub(replace_cell, content)
    missing = set(writes) - seen
    if missing:
        raise AeatWorkbookWriteError(
            f"AEAT template XML lacks input cells in {sheet_name}: "
            + ", ".join(sorted(missing))
        )
    _validate_rewritten_xml(output, package_part=sheet_name)
    return output, formula_references


_CELL_FRAGMENT_PATTERN = re.compile(
    rb"<(?P<qname>(?:[A-Za-z_][A-Za-z0-9_.-]*:)?c)\b"
    rb"(?P<attrs>[^>]*?)(?:(?P<self_closing>/>)|>"
    rb"(?P<inner>.*?)</(?P=qname)>)",
    re.DOTALL,
)


def _rewritten_cell_fragment(
    match: re.Match[bytes],
    *,
    value: Any,
    value_type: str,
    reference: str,
) -> bytes:
    qname = match.group("qname")
    attrs = match.group("attrs")
    prefix = qname[:-1]
    if value is None or value == "":
        attrs = _set_xml_attribute(attrs, b"t", None)
        return b"<" + qname + attrs + b"/>"

    rendered, _numeric = _render_typed_value(
        value,
        value_type=value_type,
        reference=reference,
    )
    escaped = _escape_xml_text(rendered)
    if value_type == "text":
        attrs = _set_xml_attribute(attrs, b"t", b"inlineStr")
        xml_space = b' xml:space="preserve"' if rendered != rendered.strip() else b""
        return (
            b"<"
            + qname
            + attrs
            + b"><"
            + prefix
            + b"is><"
            + prefix
            + b"t"
            + xml_space
            + b">"
            + escaped
            + b"</"
            + prefix
            + b"t></"
            + prefix
            + b"is></"
            + qname
            + b">"
        )
    attrs = _set_xml_attribute(attrs, b"t", None)
    return (
        b"<"
        + qname
        + attrs
        + b"><"
        + prefix
        + b"v>"
        + escaped
        + b"</"
        + prefix
        + b"v></"
        + qname
        + b">"
    )


def _xml_attribute_value(attrs: bytes, name: bytes) -> bytes | None:
    pattern = re.compile(
        rb"(?:^|\s)" + re.escape(name) + rb"=(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
        re.DOTALL,
    )
    match = pattern.search(attrs)
    return match.group("value") if match is not None else None


def _calc_chain_without_cells(
    content: bytes,
    *,
    removed_formula_cells: Mapping[str, set[str]],
) -> bytes:
    current_sheet_id: str | None = None
    last_output_sheet_id: str | None = None

    def replace_cell(match: re.Match[bytes]) -> bytes:
        nonlocal current_sheet_id, last_output_sheet_id
        attrs = match.group("attrs")
        explicit_sheet_id = _xml_attribute_value(attrs, b"i")
        if explicit_sheet_id is not None:
            current_sheet_id = explicit_sheet_id.decode("ascii", errors="strict")
        if current_sheet_id is None:
            raise AeatWorkbookWriteError("AEAT calcChain cell has no sheet id context")
        reference_bytes = _xml_attribute_value(attrs, b"r")
        if reference_bytes is None:
            raise AeatWorkbookWriteError("AEAT calcChain cell has no reference")
        reference = reference_bytes.decode("ascii", errors="strict")
        if reference in removed_formula_cells.get(current_sheet_id, set()):
            return b""
        if explicit_sheet_id is None and current_sheet_id != last_output_sheet_id:
            attrs = _set_xml_attribute(
                attrs,
                b"i",
                current_sheet_id.encode("ascii"),
            )
            rendered = _fragment_with_attrs(match, attrs)
        else:
            rendered = match.group(0)
        last_output_sheet_id = current_sheet_id
        return rendered

    output = _CELL_FRAGMENT_PATTERN.sub(replace_cell, content)
    _validate_rewritten_xml(output, package_part="xl/calcChain.xml")
    root = ET.fromstring(output)
    current_sheet_id = None
    for cell in root.findall("{*}c"):
        if "i" in cell.attrib:
            current_sheet_id = cell.attrib["i"]
        if current_sheet_id is None:
            raise AeatWorkbookWriteError("Rewritten AEAT calcChain lost sheet context")
        if cell.attrib.get("r") in removed_formula_cells.get(current_sheet_id, set()):
            raise AeatWorkbookWriteError(
                "Rewritten AEAT calcChain still references a literal input cell"
            )
    return output


def _fragment_with_attrs(match: re.Match[bytes], attrs: bytes) -> bytes:
    qname = match.group("qname")
    if match.group("self_closing") is not None:
        return b"<" + qname + attrs + b"/>"
    return (
        b"<"
        + qname
        + attrs
        + b">"
        + (match.group("inner") or b"")
        + b"</"
        + qname
        + b">"
    )


def _set_xml_attribute(attrs: bytes, name: bytes, value: bytes | None) -> bytes:
    pattern = re.compile(
        rb"(?P<leading>\s+)"
        + re.escape(name)
        + rb"=(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
        re.DOTALL,
    )
    match = pattern.search(attrs)
    if match is not None:
        replacement = b"" if value is None else match.group("leading") + name + b'="' + value + b'"'
        return attrs[: match.start()] + replacement + attrs[match.end() :]
    if value is None:
        return attrs
    return attrs + b" " + name + b'="' + value + b'"'


def _escape_xml_text(value: str) -> bytes:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .encode("utf-8")
    )


def _render_typed_value(
    value: Any,
    *,
    value_type: str,
    reference: str,
) -> tuple[str, bool]:
    if value_type == "text":
        return str(value), False
    if value_type == "integer":
        rendered = str(value).strip()
        if not re.fullmatch(r"[+-]?\d+", rendered):
            raise AeatWorkbookWriteError(f"Invalid integer value at {reference}: {value!r}")
        return str(int(rendered)), True
    elif value_type == "decimal":
        try:
            decimal_value = Decimal(str(value).strip())
        except (InvalidOperation, ValueError) as exc:
            raise AeatWorkbookWriteError(
                f"Invalid decimal value at {reference}: {value!r}"
            ) from exc
        if not decimal_value.is_finite():
            raise AeatWorkbookWriteError(
                f"Invalid decimal value at {reference}: {value!r}"
            )
        return format(decimal_value, "f"), True
    elif value_type == "date":
        try:
            if isinstance(value, datetime):
                parsed_date = value.date()
            elif isinstance(value, date):
                parsed_date = value
            else:
                parsed_date = datetime.strptime(str(value).strip(), "%d/%m/%Y").date()
        except ValueError as exc:
            raise AeatWorkbookWriteError(
                f"Invalid date value at {reference}: {value!r}"
            ) from exc
        return str((parsed_date - date(1899, 12, 30)).days), True
    else:
        raise AeatWorkbookWriteError(
            f"Unsupported AEAT value type at {reference}: {value_type}"
        )


def _validate_rewritten_xml(content: bytes, *, package_part: str) -> None:
    try:
        ET.fromstring(content)
    except ET.ParseError as exc:
        raise AeatWorkbookWriteError(
            f"Rewritten AEAT XML is invalid in {package_part}: {exc}"
        ) from exc
    missing_prefixes = _undeclared_ignorable_prefixes(content)
    if missing_prefixes:
        raise AeatWorkbookWriteError(
            f"Rewritten AEAT XML has undeclared mc:Ignorable prefixes in {package_part}: "
            + ", ".join(sorted(missing_prefixes))
        )


def _undeclared_ignorable_prefixes(content: bytes) -> set[str]:
    root_match = re.search(
        rb"<(?:[A-Za-z_][A-Za-z0-9_.-]*:)?(?:workbook|worksheet)\b"
        rb"(?P<attrs>[^>]*)>",
        content,
        re.DOTALL,
    )
    if root_match is None:
        return set()
    attrs = root_match.group("attrs")
    declared = {
        match.group("prefix").decode("ascii")
        for match in re.finditer(
            rb"\sxmlns:(?P<prefix>[A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*"
            rb"(?P<quote>['\"])(?P<uri>.*?)(?P=quote)",
            attrs,
            re.DOTALL,
        )
    }
    referenced: set[str] = set()
    for match in re.finditer(
        rb"\s(?:[A-Za-z_][A-Za-z0-9_.-]*:)?Ignorable\s*=\s*"
        rb"(?P<quote>['\"])(?P<value>.*?)(?P=quote)",
        attrs,
        re.DOTALL,
    ):
        referenced.update(match.group("value").decode("ascii").split())
    return referenced - declared


def _local_name(tag: Any) -> str:
    rendered = str(tag)
    return rendered.split("}", 1)[-1] if "}" in rendered else rendered


def _payload_sha256(payload: Mapping[str, Any]) -> str:
    content = dict(payload)
    content.pop("payload_sha256", None)
    return hashlib.sha256(
        json.dumps(
            content,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


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


def _writer_strategy_unavailable(status: str) -> dict[str, Any]:
    return {
        "status": status,
        "xlsx_generation_supported": False,
        "contract_sheets_are_formula_mirrors": False,
        "data_start_row": TEMPLATE_DATA_START_ROW,
        "data_end_row": TEMPLATE_DATA_END_ROW,
        "data_capacity": TEMPLATE_DATA_CAPACITY,
        "calc_chain": {
            "part_present": False,
            "content_type_registered": False,
            "relationship_present": False,
        },
        "input_sheets": {},
        "reason": "The reviewed template architecture is unavailable.",
    }


def _calc_chain_check(archive: zipfile.ZipFile) -> dict[str, bool]:
    content_types = ET.fromstring(archive.read("[Content_Types].xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    return {
        "part_present": "xl/calcChain.xml" in archive.namelist(),
        "content_type_registered": any(
            row.attrib.get("PartName") == "/xl/calcChain.xml"
            for row in content_types.findall("{*}Override")
        ),
        "relationship_present": any(
            row.attrib.get("Type", "").endswith("/calcChain")
            and row.attrib.get("Target", "").lstrip("/") in {"calcChain.xml", "xl/calcChain.xml"}
            for row in relationships
        ),
    }


def _formula_cell_count(
    root: ET.Element,
    *,
    start_row: int,
    end_row: int,
) -> int:
    return len(
        _formula_cell_references(root, start_row=start_row, end_row=end_row)
    )


def _formula_cell_references(
    root: ET.Element,
    *,
    start_row: int,
    end_row: int,
) -> set[str]:
    references: set[str] = set()
    for row in root.findall(".//{*}sheetData/{*}row"):
        row_number = int(row.attrib["r"])
        if not start_row <= row_number <= end_row:
            continue
        references.update(
            cell.attrib.get("r", "")
            for cell in row.findall("{*}c")
            if cell.find("{*}f") is not None
        )
    references.discard("")
    return references


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


def _workbook_sheet_ids(archive: zipfile.ZipFile) -> dict[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    return {
        sheet.attrib["name"]: sheet.attrib["sheetId"]
        for sheet in workbook.findall(".//{*}sheet")
    }


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return [
        "".join(text.text or "" for text in item.findall(".//{*}t"))
        for item in root.findall(".//{*}si")
    ]


def _code_literal_lookup(
    archive: zipfile.ZipFile,
) -> dict[tuple[str, str], str]:
    workbook_sheets = _workbook_sheets(archive)
    target = workbook_sheets.get("CODIGO-LITERAL")
    if target is None:
        raise AeatWorkbookWriteError(
            "AEAT source template is missing CODIGO-LITERAL sheet"
        )
    rows = _sheet_rows(
        archive,
        target,
        _shared_strings(archive),
        through_row=1000,
    )
    lookup: dict[tuple[str, str], str] = {}
    for values in rows.values():
        if len(values) < 4:
            continue
        category = values[0].strip()
        literal = values[1]
        code = values[3].strip()
        if not category or not literal or not code:
            continue
        key = (category, code)
        previous = lookup.get(key)
        if previous is not None and previous != literal:
            raise AeatWorkbookWriteError(
                "AEAT CODIGO-LITERAL has conflicting literals for "
                f"category {category!r}, code {code!r}"
            )
        lookup[key] = literal
    return lookup


def _input_template_value(
    *,
    contract_name: str,
    column_letter: str,
    row_values: Sequence[Any],
    projected_value: Any,
    literal_lookup: Mapping[tuple[str, str], str],
    reference: str,
) -> Any:
    if projected_value is None or projected_value == "":
        return ""
    category = INPUT_LITERAL_CATEGORY_BY_CONTRACT.get(contract_name, {}).get(
        column_letter
    )
    if category is None:
        return projected_value
    if category == _ACTIVITY_SUBTYPE_CATEGORY:
        if len(row_values) < 3:
            raise AeatWorkbookWriteError(
                f"AEAT activity context is missing at {reference}"
            )
        category = str(row_values[2]).strip()
    code = str(projected_value).strip()
    literal = literal_lookup.get((category, code))
    if literal is None:
        raise AeatWorkbookWriteError(
            f"AEAT CODIGO-LITERAL has no literal for {reference}: "
            f"category {category!r}, code {code!r}"
        )
    return literal


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


def _row_index(reference: str) -> int:
    digits = "".join(char for char in reference if char.isdigit())
    return int(digits) if digits else 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
