from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import hashlib
import re
from typing import Any

from pypdf import PdfReader

from .modelo130 import extract_modelo130_values
from .modelo303 import extract_modelo303_values
from .modelo390 import extract_modelo390_values
from .tax_rules import recognize_tax_form_filename


@dataclass(frozen=True)
class FilingEvidence:
    form_code: str
    period_key: str
    filed_on: str | None
    submission_reference: str | None
    justificante_number: str | None
    verification_code: str | None
    source_sha256: str
    source_reference: str
    payload: dict[str, Any]


def extract_filing_evidence(path: Path) -> FilingEvidence:
    recognized = recognize_tax_form_filename(path.name)
    source_sha256 = _sha256(path)
    pdf_error: str | None = None
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except Exception as exc:
        if recognized is None:
            raise ValueError(f"Cannot read or identify filed tax form from {path}: {exc}") from exc
        text = ""
        pdf_error = f"{type(exc).__name__}: {exc}"
    metadata = parse_filing_metadata(text)
    form_code = metadata.get("form_code") or (recognized.code if recognized else "")
    period_key = metadata.get("period_key") or (recognized.period if recognized else "")
    if not form_code or not period_key:
        raise ValueError(f"Cannot identify filed tax form and period from {path}")

    payload: dict[str, Any] = {
        "form": form_code,
        "period": period_key,
        "evidence_kind": "filed_return_pdf",
    }
    if pdf_error is not None:
        payload["extraction_status"] = "pdf_unreadable"
        payload["extraction_error"] = pdf_error
    year = int(period_key[:4])
    quarter = int(period_key[-1]) if "-Q" in period_key else None
    if pdf_error is None and form_code == "130" and quarter is not None:
        try:
            values = extract_modelo130_values(path, year, quarter)
        except ValueError as exc:
            payload["extraction_status"] = "values_unavailable"
            payload["extraction_error"] = str(exc)
        else:
            payload["filed_values"] = {key: f"{value:.2f}" for key, value in values.items()}
            payload["extraction_status"] = "casillas_extracted"
    elif pdf_error is None and form_code == "303" and quarter is not None:
        try:
            values = extract_modelo303_values(path)
        except ValueError as exc:
            payload["extraction_status"] = "values_unavailable"
            payload["extraction_error"] = str(exc)
        else:
            filed_values = {
                "output_base": f"{values.output_base:.2f}",
                "output_vat": f"{values.output_vat:.2f}",
                "deductible_base": f"{values.deductible_base:.2f}",
                "deductible_vat": f"{values.deductible_vat:.2f}",
                "result": f"{values.result:.2f}",
            }
            filed_values.update(
                {key: f"{value:.2f}" for key, value in values.settlement_casillas}
            )
            if values.compensation_carryforward is not None:
                filed_values["compensation_carryforward"] = (
                    f"{values.compensation_carryforward:.2f}"
                )
            payload["filed_values"] = filed_values
            payload["extraction_status"] = values.extraction_status
            payload["settlement_extraction_status"] = values.settlement_extraction_status
            payload["value_extraction_schema"] = "modelo303_v3"
    elif pdf_error is None and form_code == "390" and quarter is None:
        try:
            values = extract_modelo390_values(path)
        except ValueError as exc:
            payload["extraction_status"] = "values_unavailable"
            payload["extraction_error"] = str(exc)
        else:
            payload["filed_values"] = {
                key: f"{value:.2f}" for key, value in values.casillas
            }
            payload["blank_casillas"] = list(values.blank_casillas)
            payload["value_sources"] = dict(values.value_sources)
            payload["extraction_status"] = "casillas_extracted"
            payload["value_extraction_schema"] = "modelo390_v2"

    return FilingEvidence(
        form_code=form_code,
        period_key=period_key,
        filed_on=metadata.get("filed_on"),
        submission_reference=metadata.get("submission_reference"),
        justificante_number=metadata.get("justificante_number"),
        verification_code=metadata.get("verification_code"),
        source_sha256=source_sha256,
        source_reference=str(path),
        payload=payload,
    )


def parse_filing_metadata(text: str) -> dict[str, str]:
    normalized = " ".join((text or "").split())
    form = _match(normalized, r"\bModelo\s+(\d{3})\b")
    filed_raw = _match(
        normalized,
        r"Presentaci[oó]n realizada el:\s*(\d{2}-\d{2}-\d{4}\s+a las\s+\d{2}:\d{2}:\d{2})",
    )
    year_period = re.search(r"\b(20\d{2})\s+([1-4])T\b", normalized)
    result: dict[str, str] = {}
    if form:
        result["form_code"] = form
    if filed_raw:
        result["filed_on"] = datetime.strptime(
            filed_raw,
            "%d-%m-%Y a las %H:%M:%S",
        ).isoformat(timespec="seconds")
    if year_period:
        result["period_key"] = f"{year_period.group(1)}-Q{year_period.group(2)}"

    fields = {
        "submission_reference": (
            r"Expediente/Referencia\s*\(n[ºo]\s*registro asignado\):\s*([A-Z0-9]+)"
        ),
        "verification_code": r"C[oó]digo Seguro de Verificaci[oó]n:\s*([A-Z0-9]+)",
        "justificante_number": r"N[uú]mero de justificante:\s*([0-9]+)",
    }
    for key, pattern in fields.items():
        value = _match(normalized, pattern)
        if value:
            result[key] = value
    return result


def _match(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return match.group(1) if match else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()
