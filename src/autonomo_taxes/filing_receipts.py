from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
import json
from typing import Any, Mapping

from .filing_evidence import FilingEvidence
from .money import cents


SUPPORTED_FORMS = {"130", "303"}
REQUIRED_FILED_KEYS = {
    "130": {
        "01",
        "02",
        "03",
        "04",
        "05",
        "06",
        "07",
        "12",
        "13",
        "14",
        "15",
        "16",
        "17",
        "18",
        "19",
    },
    "303": {"output_base", "output_vat", "deductible_base", "deductible_vat", "result"},
}
ACCEPTED_EXTRACTION_STATUSES = {
    "130": {"casillas_extracted"},
    "303": {
        "no_activity_or_no_page2_vat_values",
        "deductible_only",
        "output_and_deductible",
    },
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def load_calculation_file(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read calculation JSON from {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return payload, hashlib.sha256(raw).hexdigest().upper()


def verify_filing_receipt(
    evidence: FilingEvidence,
    calculation: Mapping[str, Any],
    *,
    expected_form: str,
    expected_period: str,
    calculation_sha256: str,
    tolerance_eur: Decimal = Decimal("0.01"),
) -> dict[str, Any]:
    form = _normalize_form(expected_form)
    if form not in SUPPORTED_FORMS:
        raise ValueError(f"Receipt verification is not implemented for Modelo {form}")
    if evidence.form_code != form:
        raise ValueError(
            f"Filed PDF is Modelo {evidence.form_code}, not requested Modelo {form}"
        )
    if evidence.period_key != expected_period:
        raise ValueError(
            f"Filed PDF belongs to {evidence.period_key}, not requested {expected_period}"
        )
    _validate_typed_metadata(evidence)
    _validate_calculation_identity(calculation, form=form, period=expected_period)
    if calculation.get("blocked"):
        raise ValueError("Cannot record a receipt against a blocked calculation")

    extraction_status = str(evidence.payload.get("extraction_status") or "")
    if extraction_status not in ACCEPTED_EXTRACTION_STATUSES[form]:
        detail = evidence.payload.get("extraction_error") or extraction_status or "unknown"
        raise ValueError(f"Filed values could not be verified: {detail}")

    filed_values = _money_mapping(evidence.payload.get("filed_values"), label="filed values")
    calculated_values = _calculated_money_mapping(calculation.get("values"), form=form)
    comparison_expected = _comparison_values(form, calculated_values)
    missing = sorted(REQUIRED_FILED_KEYS[form] - set(filed_values))
    if missing:
        raise ValueError(
            f"Filed Modelo {form} is missing required extracted values: {', '.join(missing)}"
        )
    unexpected = sorted(set(filed_values) - set(comparison_expected))
    if unexpected:
        raise ValueError(
            f"Filed Modelo {form} contains values that the calculation cannot map: "
            + ", ".join(unexpected)
        )

    tolerance = cents(tolerance_eur)
    if tolerance < Decimal("0.00"):
        raise ValueError("Receipt comparison tolerance cannot be negative")
    differences = {
        key: cents(comparison_expected[key] - filed_values[key])
        for key in sorted(filed_values, key=_value_key)
    }
    mismatches = {
        key: difference
        for key, difference in differences.items()
        if abs(difference) > tolerance
    }
    comparison = {
        key: {
            "calculated": f"{comparison_expected[key]:.2f}",
            "filed": f"{filed_values[key]:.2f}",
            "difference": f"{differences[key]:.2f}",
        }
        for key in differences
    }
    result = {
        "status": "matched" if not mismatches else "mismatch",
        "form": form,
        "period": expected_period,
        "tolerance_eur": f"{tolerance:.2f}",
        "extraction_status": extraction_status,
        "source_sha256": evidence.source_sha256,
        "calculation_sha256": calculation_sha256,
        "compared_values": comparison,
        "mismatches": {
            key: f"{difference:.2f}" for key, difference in mismatches.items()
        },
    }
    return result


def build_filing_receipt_payload(
    evidence: FilingEvidence,
    calculation: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> dict[str, Any]:
    if verification.get("status") != "matched":
        raise ValueError("A mismatched receipt cannot become a final filing snapshot")
    return {
        "schema_version": 1,
        "form": verification["form"],
        "period": verification["period"],
        "evidence_kind": "filed_return_pdf",
        "filed_values": dict(evidence.payload["filed_values"]),
        "values": _stringify_money_values(calculation["values"]),
        "lineage": dict(calculation.get("lineage") or {}),
        "warnings": list(calculation.get("warnings") or []),
        "receipt_verification": dict(verification),
    }


def _validate_typed_metadata(evidence: FilingEvidence) -> None:
    missing = [
        name
        for name, value in (
            ("filed_on", evidence.filed_on),
            ("submission_reference", evidence.submission_reference),
            ("justificante_number", evidence.justificante_number),
            ("verification_code", evidence.verification_code),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            "Filed PDF is missing required submission metadata: " + ", ".join(missing)
        )


def _validate_calculation_identity(
    calculation: Mapping[str, Any],
    *,
    form: str,
    period: str,
) -> None:
    calculation_form = calculation.get("form")
    if calculation_form is not None and _normalize_form(str(calculation_form)) != form:
        raise ValueError(
            f"Calculation is for Modelo {calculation_form}, not requested Modelo {form}"
        )
    calculation_period = calculation.get("period")
    if calculation_period is not None and str(calculation_period) != period:
        raise ValueError(
            f"Calculation belongs to {calculation_period}, not requested {period}"
        )


def _comparison_values(
    form: str,
    calculated: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    if form == "130":
        return {key: value for key, value in calculated.items() if key.isdigit()}
    return {
        "output_base": _sum_keys(calculated, "150", "01", "04", "07", "10", "12"),
        "output_vat": _required_value(calculated, "27"),
        "deductible_base": _sum_keys(calculated, "28", "30", "32", "34", "36", "38"),
        "deductible_vat": _required_value(calculated, "45"),
        "result": _required_value(calculated, "71"),
    }


def _money_mapping(value: Any, *, label: str) -> dict[str, Decimal]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(f"{label.capitalize()} are missing")
    result: dict[str, Decimal] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        try:
            result[key] = cents(Decimal(str(raw_value)))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{label.capitalize()} contain a non-monetary value for {key}") from exc
    return result


def _calculated_money_mapping(value: Any, *, form: str) -> dict[str, Decimal]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("Calculated values are missing")
    required_303 = {
        "150",
        "01",
        "04",
        "07",
        "10",
        "12",
        "27",
        "28",
        "30",
        "32",
        "34",
        "36",
        "38",
        "45",
        "71",
    }
    selected = {
        str(key): raw_value
        for key, raw_value in value.items()
        if (form == "130" and str(key).isdigit())
        or (form == "303" and str(key) in required_303)
    }
    return _money_mapping(selected, label="calculated values")


def _required_value(values: Mapping[str, Decimal], key: str) -> Decimal:
    if key not in values:
        raise ValueError(f"Calculated Modelo 303 is missing casilla {key}")
    return values[key]


def _sum_keys(values: Mapping[str, Decimal], *keys: str) -> Decimal:
    missing = [key for key in keys if key not in values]
    if missing:
        raise ValueError(
            "Calculated Modelo 303 is missing casillas: " + ", ".join(missing)
        )
    return cents(sum((values[key] for key in keys), Decimal("0.00")))


def _stringify_money_values(values: Any) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError("Calculated values are missing")
    return {
        str(key): f"{value:.2f}" if isinstance(value, Decimal) else value
        for key, value in values.items()
    }


def _normalize_form(value: str) -> str:
    return value.lower().replace("modelo", "").strip()


def _value_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)
