from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping

from .ledger_db import LedgerDB


AEAT_BOOK_CONTRACT = {
    "version": "V.18.02.2026",
    "format": "unified_iva_irpf_xlsx",
    "design_url": (
        "https://sede.agenciatributaria.gob.es/static_files/Sede/Tema/IVA/"
        "Fact_registro/Libros_registro/Formato_Electronico_Comun_Libros_Registro_IVA_IRPF.pdf"
    ),
    "template_url": (
        "https://sede.agenciatributaria.gob.es/static_files/Sede/Tema/IVA/"
        "Fact_registro/Libros/PLANTILLA_LIBROS_UNIFICADOS.xlsx"
    ),
    "template_sha256_observed_2026_07_16": (
        "0dd42d6b40d9388365624ae2ed7f301d8479ec92271b4eac06d2440096f49ebc"
    ),
    "required_sheets": (
        "EXPEDIDAS_INGRESOS",
        "RECIBIDAS_GASTOS",
        "BIENES-INVERSIÓN",
    ),
    "validation_url": "https://prewww2.aeat.es/wlpl/PACM-SERV/validarLLRs.html",
    "filename_rule_status": "unverified_public_sources_conflict",
}


class AeatBookProjectionError(ValueError):
    """Raised when the requested AEAT book scope cannot be projected safely."""


def failed_aeat_book_projection(
    *,
    period_key: str,
    message: str,
    allow_authoritative_history: bool = False,
) -> dict[str, Any]:
    blocker = _blocker("projection_prerequisite_missing", period_key, message)
    return {
        "schema_version": 1,
        "projection_type": "aeat_unified_books_review",
        "period": period_key,
        "scope": None,
        "taxpayer": None,
        "contract": AEAT_BOOK_CONTRACT,
        "provisional_filename": None,
        "xlsx_generation_supported": False,
        "xlsx_ready": False,
        "data_projection_ready": False,
        "allow_authoritative_history": allow_authoritative_history,
        "counts": {"income": 0, "expense": 0, "assets": 0, "blockers": 1},
        "income_rows": [],
        "expense_rows": [],
        "asset_rows": [],
        "blockers": [blocker],
        "warnings": ["Projection prerequisites were not satisfied."],
    }


def build_aeat_book_projection(
    database: LedgerDB,
    *,
    period_key: str,
    taxpayer_tax_id: str | None = None,
    allow_authoritative_history: bool = False,
) -> dict[str, Any]:
    year, quarter = _parse_quarter(period_key)
    through_date = _quarter_end(year, quarter)
    profile = _taxpayer_profile(database, taxpayer_tax_id)
    activities = database.list_business_activities(
        taxpayer_profile_id=profile["taxpayer_profile_id"]
    )
    if not activities:
        raise AeatBookProjectionError(
            "AEAT book projection requires at least one reviewed business activity"
        )

    authoritative_ids = (
        {
            row["transaction_id"]
            for row in database.list_authoritative_history_transactions(year=year)
        }
        if allow_authoritative_history
        else set()
    )
    transactions = _transaction_rows(database, year=year, through_date=through_date)
    included: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for transaction in transactions:
        status = transaction["lifecycle_status"]
        if status in {"posted", "included_in_snapshot"}:
            included.append(transaction)
            continue
        if status == "approved" and transaction["transaction_id"] in authoritative_ids:
            included.append(transaction)
            continue
        if status == "approved":
            blockers.append(
                _blocker(
                    "approved_not_posted",
                    transaction["transaction_id"],
                    "Approved rows are excluded unless they are posted or explicitly accepted as immutable authoritative history.",
                )
            )

    income_rows: list[dict[str, Any]] = []
    expense_rows: list[dict[str, Any]] = []
    for transaction in included:
        try:
            activity = _activity_for_transaction(transaction, activities)
            treatment = _single_treatment(database, transaction)
            if transaction["entry_type"].startswith("income"):
                income_rows.append(_income_row(transaction, treatment, activity))
            elif transaction["entry_type"].startswith("expense"):
                expense_rows.append(_expense_row(transaction, treatment, activity))
        except AeatBookProjectionError as exc:
            blockers.append(
                _blocker(
                    "row_mapping_incomplete",
                    transaction["transaction_id"],
                    str(exc),
                )
            )

    asset_rows, asset_blockers = _asset_book_rows(
        database,
        activities=activities,
        year=year,
        through_date=through_date,
    )
    blockers.extend(asset_blockers)

    income_rows.sort(key=_book_sort_key)
    expense_rows.sort(key=_book_sort_key)
    return {
        "schema_version": 1,
        "projection_type": "aeat_unified_books_review",
        "period": period_key,
        "scope": {
            "year": year,
            "through_quarter": quarter,
            "through_date": through_date.isoformat(),
            "cumulative_ytd": True,
        },
        "taxpayer": {
            "taxpayer_profile_id": profile["taxpayer_profile_id"],
            "tax_id": profile["tax_id"],
            "full_name": profile["full_name"],
            "source_hash": profile["source_hash"],
        },
        "contract": AEAT_BOOK_CONTRACT,
        "provisional_filename": _provisional_filename(year, profile),
        "xlsx_generation_supported": False,
        "xlsx_ready": False,
        "data_projection_ready": not blockers,
        "allow_authoritative_history": allow_authoritative_history,
        "counts": {
            "income": len(income_rows),
            "expense": len(expense_rows),
            "assets": len(asset_rows),
            "blockers": len(blockers),
        },
        "income_rows": income_rows,
        "expense_rows": expense_rows,
        "asset_rows": asset_rows,
        "blockers": blockers,
        "warnings": [
            "This JSON is a reviewed row projection, not an AEAT-importable XLSX.",
            "Only posted rows and explicitly allowed authoritative historical rows are included.",
            "The exact 2026 filename grammar remains unverified because the current AEAT workbook and PDF are inconsistent.",
        ],
    }


def write_aeat_book_projection(path: str | Path, projection: Mapping[str, Any]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(projection, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return target


def _taxpayer_profile(database: LedgerDB, tax_id: str | None) -> dict[str, Any]:
    if tax_id is not None:
        row = database.connection.execute(
            "SELECT * FROM taxpayer_profile WHERE tax_id = ?",
            (tax_id.strip().upper(),),
        ).fetchone()
        if row is None:
            raise AeatBookProjectionError(f"Unknown taxpayer profile: {tax_id}")
        return dict(row)
    rows = database.connection.execute(
        "SELECT * FROM taxpayer_profile ORDER BY taxpayer_profile_id"
    ).fetchall()
    if len(rows) != 1:
        raise AeatBookProjectionError(
            "AEAT book projection requires exactly one taxpayer profile or an explicit taxpayer tax_id"
        )
    return dict(rows[0])


def _transaction_rows(
    database: LedgerDB,
    *,
    year: int,
    through_date: date,
) -> list[dict[str, Any]]:
    rows = database.connection.execute(
        """
        SELECT t.*, p.period_key,
               d.document_number, d.issued_on, d.document_type,
               d.lifecycle_status AS document_lifecycle,
               c.display_name AS counterparty_name, c.country_code,
               c.tax_id AS counterparty_tax_id, c.vat_id AS counterparty_vat_id,
               ci.aeat_id_type AS counterparty_aeat_id_type,
               ci.country_code AS counterparty_identity_country,
               ci.identifier AS counterparty_identity_identifier,
               oi.external_series, oi.external_number
        FROM transactions t
        JOIN periods p ON p.period_id = t.period_id
        LEFT JOIN documents d ON d.document_id = t.document_id
        LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
        LEFT JOIN counterparty_identities ci
          ON ci.counterparty_id = c.counterparty_id AND ci.is_primary = 1
        LEFT JOIN outgoing_invoice_drafts oi
          ON oi.transaction_id = t.transaction_id AND oi.lifecycle_status = 'issued'
        WHERE t.transaction_date BETWEEN ? AND ?
          AND (t.entry_type LIKE 'income%' OR t.entry_type LIKE 'expense%')
          AND t.lifecycle_status NOT IN ('duplicate', 'rejected', 'void')
        ORDER BY t.transaction_date, t.transaction_id
        """,
        (f"{year:04d}-01-01", through_date.isoformat()),
    ).fetchall()
    return [dict(row) for row in rows]


def _single_treatment(database: LedgerDB, transaction: Mapping[str, Any]) -> dict[str, Any]:
    rows = database.connection.execute(
        """
        SELECT * FROM tax_treatments
        WHERE transaction_id = ? AND treatment_type <> 'invoice_review'
        ORDER BY treatment_id
        """,
        (transaction["transaction_id"],),
    ).fetchall()
    if not rows:
        rows = database.connection.execute(
            "SELECT * FROM tax_treatments WHERE transaction_id = ? ORDER BY treatment_id",
            (transaction["transaction_id"],),
        ).fetchall()
    if len(rows) != 1:
        raise AeatBookProjectionError(
            f"Transaction requires exactly one reviewed book treatment; found {len(rows)}"
        )
    treatment = dict(rows[0])
    if not treatment.get("tax_code") or treatment["tax_code"] == "unknown":
        raise AeatBookProjectionError("Transaction tax_code is not reviewed")
    return treatment


def _activity_for_transaction(
    transaction: Mapping[str, Any],
    activities: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    activity_id = transaction.get("business_activity_id")
    if not activity_id:
        raise AeatBookProjectionError("Transaction is not linked to a business activity")
    for activity in activities:
        if activity["business_activity_id"] == activity_id:
            return activity
    raise AeatBookProjectionError("Transaction business activity belongs to another taxpayer profile")


def _income_row(
    transaction: Mapping[str, Any],
    treatment: Mapping[str, Any],
    activity: Mapping[str, Any],
) -> dict[str, Any]:
    tax_code = str(treatment["tax_code"])
    if tax_code not in {"outside_scope", "not_subject_place_of_supply"}:
        raise AeatBookProjectionError(
            f"Income tax_code {tax_code!r} has no reviewed AEAT book mapping"
        )
    _require_document_and_counterparty(transaction)
    amount_minor = _eur_amount_minor(transaction)
    taxable_base_minor = _required_minor(treatment, "taxable_base_minor")
    vat_minor = _optional_minor(treatment, "vat_minor")
    withholding_minor = _optional_minor(treatment, "withholding_minor")
    series, number = _invoice_identity(transaction)
    id_type, country_code, counterparty_id = _counterparty_identity(transaction)
    row_year, row_quarter = _transaction_period(transaction)
    return {
        "autoliquidacion_ejercicio": row_year,
        "autoliquidacion_periodo": f"{row_quarter}T",
        **_activity_columns(activity),
        "tipo_factura": "F1",
        "concepto_ingreso": "I01",
        "ingreso_computable_eur": _money(taxable_base_minor),
        "fecha_expedicion": _date_es(transaction["issued_on"]),
        "fecha_operacion": _date_es(transaction["transaction_date"]),
        "factura_serie": series,
        "factura_numero": number,
        "destinatario_id_tipo": id_type,
        "destinatario_pais": country_code,
        "destinatario_identificacion": counterparty_id,
        "destinatario_nombre": transaction["counterparty_name"],
        "clave_operacion": "01",
        "calificacion_operacion": "N2",
        "operacion_exenta": "",
        "total_factura_eur": _money(amount_minor),
        "base_imponible_eur": _money(taxable_base_minor),
        "tipo_iva_percent": "0.00",
        "cuota_iva_repercutida_eur": _money(vat_minor),
        "tipo_retencion_irpf_percent": _withholding_rate(
            taxable_base_minor, withholding_minor
        ),
        "importe_retenido_irpf_eur": _money(withholding_minor),
        "referencia_externa": transaction.get("external_key") or transaction["transaction_id"],
        "transaction_id": transaction["transaction_id"],
        "source_hash": transaction["source_hash"],
    }


def _expense_row(
    transaction: Mapping[str, Any],
    treatment: Mapping[str, Any],
    activity: Mapping[str, Any],
) -> dict[str, Any]:
    _require_document_and_counterparty(transaction)
    concept = _expense_concept(treatment)
    amount_minor = _eur_amount_minor(transaction)
    taxable_base_minor = _optional_minor(treatment, "taxable_base_minor")
    vat_minor = _optional_minor(treatment, "vat_minor")
    deductible_vat_minor = _optional_minor(treatment, "deductible_vat_minor")
    deductible_irpf_minor = _required_minor(treatment, "deductible_irpf_minor")
    withholding_minor = _optional_minor(treatment, "withholding_minor")
    id_type, country_code, counterparty_id = _counterparty_identity(transaction)
    reverse_charge = str(treatment["tax_code"]) in {
        "non_eu_service_expense",
        "intra_eu_service_expense",
        "reverse_charge_service",
    }
    rate = treatment.get("rate_basis_points")
    row_year, row_quarter = _transaction_period(transaction)
    return {
        "autoliquidacion_ejercicio": row_year,
        "autoliquidacion_periodo": f"{row_quarter}T",
        **_activity_columns(activity),
        "tipo_factura": "F1",
        "concepto_gasto": concept,
        "gasto_deducible_eur": _money(deductible_irpf_minor),
        "fecha_expedicion": _date_es(transaction["issued_on"]),
        "fecha_operacion": _date_es(transaction["transaction_date"]),
        "factura_expedidor_serie_numero": transaction["document_number"],
        "fecha_recepcion": _date_es(transaction["booking_date"]),
        "expedidor_id_tipo": id_type,
        "expedidor_pais": country_code,
        "expedidor_identificacion": counterparty_id,
        "expedidor_nombre": transaction["counterparty_name"],
        "clave_operacion": "01",
        "bien_inversion": "",
        "inversion_sujeto_pasivo": "S" if reverse_charge else "",
        "total_factura_eur": _money(amount_minor),
        "base_imponible_eur": _money(taxable_base_minor),
        "tipo_iva_percent": _rate_percent(rate),
        "cuota_iva_soportado_eur": _money(vat_minor),
        "cuota_deducible_eur": _money(deductible_vat_minor),
        "tipo_retencion_irpf_percent": _withholding_rate(
            taxable_base_minor, withholding_minor
        ),
        "importe_retenido_irpf_eur": _money(withholding_minor),
        "referencia_externa": transaction.get("external_key") or transaction["transaction_id"],
        "transaction_id": transaction["transaction_id"],
        "source_hash": transaction["source_hash"],
    }


def _expense_concept(treatment: Mapping[str, Any]) -> str:
    tax_code = str(treatment["tax_code"])
    match = re.fullmatch(r"(?:historical_|aeat_)?(g(?:y)?\d{1,2})", tax_code, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    notes = str(treatment.get("notes") or "")
    match = re.search(r"(?:^|;)\s*reason_code=(G(?:Y)?\d{1,2})(?:;|$)", notes, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    if tax_code == "social_security_owner":
        return "G45"
    raise AeatBookProjectionError(
        f"Expense tax_code {tax_code!r} is missing an explicit AEAT expense concept"
    )


def _require_document_and_counterparty(transaction: Mapping[str, Any]) -> None:
    if not transaction.get("document_id") or not transaction.get("document_number"):
        raise AeatBookProjectionError("Transaction requires a linked numbered source document")
    if not transaction.get("counterparty_id") or not transaction.get("counterparty_name"):
        raise AeatBookProjectionError("Transaction requires a reviewed counterparty")
    if transaction.get("document_lifecycle") not in {
        "approved",
        "posted",
        "included_in_snapshot",
    }:
        raise AeatBookProjectionError("Source document is not approved")


def _activity_columns(activity: Mapping[str, Any]) -> dict[str, str]:
    return {
        "actividad_codigo": str(activity["aeat_activity_code"]),
        "actividad_tipo": str(activity["aeat_activity_type"]),
        "iae_grupo_epigrafe": str(activity["iae_group_epigraph"]),
        "business_activity_id": str(activity["business_activity_id"]),
    }


def _counterparty_identity(transaction: Mapping[str, Any]) -> tuple[str, str, str]:
    country = str(transaction.get("country_code") or "").upper()
    if not country:
        raise AeatBookProjectionError("Counterparty country is required")
    if country == "ES":
        tax_id = str(
            transaction.get("counterparty_tax_id")
            or transaction.get("counterparty_vat_id")
            or ""
        ).strip()
        if not tax_id:
            raise AeatBookProjectionError("Domestic counterparty tax identity is required")
        return "", "", tax_id.removeprefix("ES")
    reviewed_id_type = str(transaction.get("counterparty_aeat_id_type") or "").strip()
    reviewed_country = str(
        transaction.get("counterparty_identity_country") or ""
    ).strip().upper()
    reviewed_identifier = str(
        transaction.get("counterparty_identity_identifier") or ""
    ).strip()
    if reviewed_id_type or reviewed_country or reviewed_identifier:
        if not all((reviewed_id_type, reviewed_country, reviewed_identifier)):
            raise AeatBookProjectionError(
                "Source-backed counterparty identity is incomplete"
            )
        return reviewed_id_type, reviewed_country, reviewed_identifier
    raise AeatBookProjectionError(
        "Foreign counterparty requires a primary source-backed AEAT identity"
    )


def _invoice_identity(transaction: Mapping[str, Any]) -> tuple[str, str]:
    document_number = str(transaction.get("document_number") or "").strip()
    series = str(transaction.get("external_series") or "").strip()
    if series:
        suffix = document_number[len(series) :].lstrip("-_/ ") if document_number.startswith(series) else ""
        if suffix:
            return series, suffix
    match = re.fullmatch(r"(.+?)[-_](\d+)", document_number)
    if match:
        return match.group(1), match.group(2)
    return "", document_number


def _eur_amount_minor(transaction: Mapping[str, Any]) -> int:
    original_currency = str(
        transaction.get("original_currency") or transaction.get("currency") or ""
    ).upper()
    value = transaction.get("amount_eur_minor")
    if value is None:
        if original_currency != "EUR":
            raise AeatBookProjectionError(
                f"Foreign-currency transaction {original_currency} lacks sourced EUR value"
            )
        value = transaction.get("amount_minor")
    if original_currency != "EUR" and not transaction.get("fx_rate_id"):
        raise AeatBookProjectionError("Foreign-currency transaction lacks a sourced FX rate")
    return int(value)


def _required_minor(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    if value is None:
        raise AeatBookProjectionError(f"Reviewed treatment is missing {field}")
    return int(value)


def _optional_minor(row: Mapping[str, Any], field: str) -> int:
    value = row.get(field)
    return int(value) if value is not None else 0


def _asset_book_rows(
    database: LedgerDB,
    *,
    activities: list[Mapping[str, Any]],
    year: int,
    through_date: date,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    rows = database.connection.execute(
        """
        SELECT a.*, d.issued_on, d.lifecycle_status AS document_lifecycle,
               c.display_name AS counterparty_name, c.country_code,
               c.tax_id AS counterparty_tax_id, c.vat_id AS counterparty_vat_id,
               ci.aeat_id_type AS counterparty_aeat_id_type,
               ci.country_code AS counterparty_identity_country,
               ci.identifier AS counterparty_identity_identifier
        FROM assets a
        LEFT JOIN documents d ON d.document_id = a.document_id
        LEFT JOIN counterparties c ON c.counterparty_id = d.counterparty_id
        LEFT JOIN counterparty_identities ci
          ON ci.counterparty_id = c.counterparty_id AND ci.is_primary = 1
        WHERE a.placed_in_service_on IS NOT NULL
          AND a.placed_in_service_on <= ?
          AND COALESCE(a.amortizable_base_minor, 0) <> 0
        ORDER BY a.placed_in_service_on, a.asset_code
        """,
        (through_date.isoformat(),),
    ).fetchall()
    output: list[dict[str, Any]] = []
    blockers: list[dict[str, str]] = []
    for raw in rows:
        asset = dict(raw)
        try:
            activity = _activity_for_asset(asset, activities)
            output.append(_asset_row(database, asset, activity, year))
        except AeatBookProjectionError as exc:
            blockers.append(
                _blocker("asset_mapping_incomplete", asset["asset_id"], str(exc))
            )
    return output, blockers


def _activity_for_asset(
    asset: Mapping[str, Any],
    activities: Iterable[Mapping[str, Any]],
) -> Mapping[str, Any]:
    activity_id = asset.get("business_activity_id")
    if not activity_id:
        raise AeatBookProjectionError("Asset is not linked to a business activity")
    for activity in activities:
        if activity["business_activity_id"] == activity_id:
            return activity
    raise AeatBookProjectionError("Asset business activity belongs to another taxpayer profile")


def _asset_row(
    database: LedgerDB,
    asset: Mapping[str, Any],
    activity: Mapping[str, Any],
    year: int,
) -> dict[str, Any]:
    required_fields = (
        "aeat_asset_type",
        "description",
        "aeat_asset_identifier",
        "aeat_amortization_method",
        "source_invoice_number",
        "acquisition_taxable_base_minor",
        "acquisition_vat_rate_basis_points",
        "acquisition_deductible_vat_minor",
        "book_profile_source_reference",
        "book_profile_source_hash",
    )
    missing = [field for field in required_fields if asset.get(field) in {None, ""}]
    if missing:
        raise AeatBookProjectionError(
            "Asset book profile is missing: " + ", ".join(missing)
        )
    if not asset.get("counterparty_name"):
        raise AeatBookProjectionError("Asset source document is missing its supplier")
    if asset.get("document_lifecycle") not in {
        "approved",
        "posted",
        "included_in_snapshot",
    }:
        raise AeatBookProjectionError("Asset source document is not approved")
    evidence = database.connection.execute(
        """
        SELECT ae.amount_minor, ae.source_book_line_id, ae.source_hash
        FROM amortization_entries ae
        JOIN periods p ON p.period_id = ae.period_id
        WHERE ae.asset_id = ? AND ae.entry_kind = 'annual_evidence'
          AND ae.tax_year = ? AND p.period_key = ?
        """,
        (asset["asset_id"], year, str(year)),
    ).fetchall()
    if len(evidence) != 1:
        raise AeatBookProjectionError(
            f"Asset requires exactly one annual evidence row for {year}; found {len(evidence)}"
        )
    annual = dict(evidence[0])
    prior = database.connection.execute(
        """
        SELECT COALESCE(SUM(ae.amount_minor), 0)
        FROM amortization_entries ae
        WHERE ae.asset_id = ? AND ae.entry_kind = 'annual_evidence'
          AND ae.tax_year < ?
        """,
        (asset["asset_id"], year),
    ).fetchone()[0]
    accumulated_start = int(prior)
    current_amount = int(annual["amount_minor"])
    accumulated_end = accumulated_start + current_amount
    amortizable_base = int(asset["amortizable_base_minor"])
    pending = amortizable_base - accumulated_end
    if pending < 0:
        raise AeatBookProjectionError("Asset amortization exceeds its amortizable base")
    id_type, country_code, counterparty_id = _counterparty_identity(asset)
    rate_basis_points = asset.get("annual_rate_basis_points")
    if rate_basis_points is None:
        raise AeatBookProjectionError("Asset annual amortization rate is missing")
    return {
        "autoliquidacion_ejercicio": year,
        "autoliquidacion_periodo": "0A",
        **_activity_columns(activity),
        "tipo_bien": asset["aeat_asset_type"],
        "descripcion_identificador": asset["aeat_asset_identifier"],
        "descripcion_literal": asset["description"],
        "fecha_inicio_utilizacion": _date_es(asset["placed_in_service_on"]),
        "valor_adquisicion_eur": _money(int(asset["cost_minor"])),
        "valor_amortizable_eur": _money(amortizable_base),
        "metodo_amortizacion": asset["aeat_amortization_method"],
        "porcentaje_amortizacion": _rate_percent(rate_basis_points),
        "amortizacion_acumulada_inicio_eur": _money(accumulated_start),
        "amortizacion_cuota_resultante_eur": _money(current_amount),
        "amortizacion_acumulada_final_eur": _money(accumulated_end),
        "amortizacion_pendiente_eur": _money(pending),
        "fecha_expedicion": _date_es(asset.get("issued_on") or asset["placed_in_service_on"]),
        "factura_expedidor_numero_final": asset["source_invoice_number"],
        "expedidor_id_tipo": id_type,
        "expedidor_pais": country_code,
        "expedidor_identificacion": counterparty_id,
        "expedidor_nombre": asset["counterparty_name"],
        "inicio_base_imponible_eur": _money(
            int(asset["acquisition_taxable_base_minor"])
        ),
        "inicio_tipo_iva_percent": _rate_percent(
            asset["acquisition_vat_rate_basis_points"]
        ),
        "inicio_cuota_deducible_eur": _money(
            int(asset["acquisition_deductible_vat_minor"])
        ),
        "referencia_externa": asset["asset_code"],
        "asset_id": asset["asset_id"],
        "annual_evidence_source_book_line_id": annual["source_book_line_id"],
        "source_hash": asset["book_profile_source_hash"],
    }


def _parse_quarter(period_key: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-Q([1-4])", period_key)
    if not match:
        raise AeatBookProjectionError("AEAT book preview period must use YYYY-QN")
    return int(match.group(1)), int(match.group(2))


def _quarter_end(year: int, quarter: int) -> date:
    return (
        date(year, 3, 31)
        if quarter == 1
        else date(year, 6, 30)
        if quarter == 2
        else date(year, 9, 30)
        if quarter == 3
        else date(year, 12, 31)
    )


def _provisional_filename(year: int, profile: Mapping[str, Any]) -> str:
    name = re.sub(r"[^A-Za-z0-9]+", "_", str(profile["full_name"]).strip()).strip("_")
    return f"{year}{profile['tax_id']}T{name}.xlsx"


def _book_sort_key(row: Mapping[str, Any]) -> tuple[str, str, str]:
    raw_date = str(row.get("fecha_operacion") or row.get("fecha_expedicion") or "")
    day, month, row_year = (
        raw_date.split("/") if raw_date.count("/") == 2 else ("", "", "")
    )
    sort_date = f"{row_year}-{month}-{day}" if row_year else raw_date
    return (
        sort_date,
        str(row.get("factura_serie") or row.get("factura_expedidor_serie_numero") or ""),
        str(row.get("factura_numero") or ""),
    )


def _transaction_period(transaction: Mapping[str, Any]) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4})-Q([1-4])", str(transaction.get("period_key") or ""))
    if not match:
        raise AeatBookProjectionError("Transaction period is not a quarterly YYYY-QN period")
    return int(match.group(1)), int(match.group(2))


def _date_es(value: str) -> str:
    parsed = date.fromisoformat(value)
    return parsed.strftime("%d/%m/%Y")


def _money(minor: int) -> str:
    return f"{Decimal(minor) / Decimal(100):.2f}"


def _rate_percent(rate_basis_points: Any) -> str:
    if rate_basis_points is None:
        return ""
    return f"{Decimal(int(rate_basis_points)) / Decimal(100):.2f}"


def _withholding_rate(base_minor: int, withholding_minor: int) -> str:
    if withholding_minor == 0:
        return ""
    if base_minor == 0:
        raise AeatBookProjectionError("Withholding amount requires a non-zero taxable base")
    percent = (
        Decimal(withholding_minor) * Decimal(100) / Decimal(base_minor)
    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{percent:.2f}"


def _blocker(code: str, reference: str, message: str) -> dict[str, str]:
    return {"code": code, "reference": reference, "message": message}
