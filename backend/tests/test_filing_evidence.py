from decimal import Decimal
from pathlib import Path

import pytest

from autonomo_taxes.filing_evidence import extract_filing_evidence, parse_filing_metadata
from autonomo_taxes.modelo303 import Modelo303Values
from autonomo_taxes.modelo390 import Modelo390Values


def test_parse_filing_metadata_from_aeat_receipt_page() -> None:
    text = """
    INFORMACION DE LA PRESENTACION DE LA DECLARACION
    Modelo 303
    Presentacion realizada el: 08-07-2026 a las 12:49:25
    Expediente/Referencia (no registro asignado): 202630309400430F
    Codigo Seguro de Verificacion: QD558KKTDLCUTEG4
    Numero de justificante: 3037001157635
    Ejercicio Periodo
    2026 2T
    """

    metadata = parse_filing_metadata(text)

    assert metadata == {
        "form_code": "303",
        "filed_on": "2026-07-08T12:49:25",
        "period_key": "2026-Q2",
        "submission_reference": "202630309400430F",
        "verification_code": "QD558KKTDLCUTEG4",
        "justificante_number": "3037001157635",
    }


def test_malformed_recognized_pdf_is_kept_as_failed_evidence(tmp_path: Path) -> None:
    path = tmp_path / "MOD 303 2T 2026 broken.pdf"
    path.write_bytes(b"this is not a PDF")

    evidence = extract_filing_evidence(path)

    assert evidence.form_code == "303"
    assert evidence.period_key == "2026-Q2"
    assert evidence.payload["extraction_status"] == "pdf_unreadable"
    assert evidence.payload["extraction_error"]


def test_modelo303_filing_evidence_keeps_settlement_casillas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "MOD 303 4T 2025 Example Taxpayer.pdf"
    path.write_bytes(b"fixture")

    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        def __init__(self, _path: str):
            self.pages = [FakePage()]

    values = Modelo303Values(
        output_base=Decimal("0.00"),
        output_vat=Decimal("0.00"),
        deductible_base=Decimal("215.99"),
        deductible_vat=Decimal("45.36"),
        result=Decimal("-45.36"),
        extraction_status="deductible_only",
        monetary_sequence=(),
        structural_casillas=(
            ("12", Decimal("4285.59")),
            ("13", Decimal("899.98")),
            ("27", Decimal("899.98")),
            ("28", Decimal("4501.59")),
            ("29", Decimal("945.34")),
            ("30", Decimal("0.00")),
            ("31", Decimal("0.00")),
            ("45", Decimal("945.34")),
            ("46", Decimal("-45.36")),
        ),
        structural_extraction_status="casillas_extracted",
        settlement_casillas=(
            ("64", Decimal("-45.36")),
            ("110", Decimal("876.91")),
            ("78", Decimal("0.00")),
            ("87", Decimal("876.91")),
            ("69", Decimal("-45.36")),
            ("71", Decimal("-45.36")),
            ("72", Decimal("45.36")),
            ("73", Decimal("0.00")),
        ),
        settlement_extraction_status="casillas_extracted",
        blank_casillas=("30", "31", "78", "73"),
        value_sources=(
            ("12", "layout_line+positioned_row"),
            ("13", "layout_line"),
            ("27", "layout_line+positioned_row"),
            ("28", "layout_line+positioned_row"),
            ("29", "layout_line"),
            ("30", "box_present_no_value_captured"),
            ("31", "box_present_no_value_captured"),
            ("45", "layout_line+positioned_row"),
            ("46", "layout_line+positioned_row"),
            ("64", "positioned_row"),
            ("110", "positioned_row"),
            ("78", "box_present_no_value_captured"),
            ("87", "positioned_row"),
            ("69", "positioned_row"),
            ("71", "positioned_row"),
            ("72", "positioned_row"),
            ("73", "box_present_no_value_captured"),
        ),
    )
    monkeypatch.setattr("autonomo_taxes.filing_evidence.PdfReader", FakeReader)
    monkeypatch.setattr(
        "autonomo_taxes.filing_evidence.extract_modelo303_values",
        lambda _path: values,
    )

    evidence = extract_filing_evidence(path)

    assert evidence.payload["value_extraction_schema"] == "modelo303_v4"
    assert evidence.payload["structural_extraction_status"] == "casillas_extracted"
    assert evidence.payload["settlement_extraction_status"] == "casillas_extracted"
    assert evidence.payload["filed_values"]["13"] == "899.98"
    assert evidence.payload["filed_values"]["29"] == "945.34"
    assert evidence.payload["filed_values"]["110"] == "876.91"
    assert evidence.payload["filed_values"]["72"] == "45.36"
    assert evidence.payload["filed_values"]["73"] == "0.00"
    assert evidence.payload["filed_values"]["compensation_carryforward"] == "922.27"
    assert evidence.payload["blank_casillas"] == ["30", "31", "78", "73"]
    assert evidence.payload["value_sources"]["78"] == "box_present_no_value_captured"


def test_modelo390_filing_evidence_keeps_annual_casillas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "MOD 390 2025 Example Taxpayer.pdf"
    path.write_bytes(b"fixture")

    class FakePage:
        def extract_text(self):
            return ""

    class FakeReader:
        def __init__(self, _path: str):
            self.pages = [FakePage()]

    values = Modelo390Values(
        casillas=(
            ("47", Decimal("0.00")),
            ("64", Decimal("489.19")),
            ("65", Decimal("-489.19")),
            ("84", Decimal("-489.19")),
            ("85", Decimal("433.08")),
            ("86", Decimal("-922.27")),
            ("523", Decimal("6096.19")),
        ),
        blank_casillas=("47",),
    )
    monkeypatch.setattr("autonomo_taxes.filing_evidence.PdfReader", FakeReader)
    monkeypatch.setattr(
        "autonomo_taxes.filing_evidence.extract_modelo390_values",
        lambda _path: values,
    )

    evidence = extract_filing_evidence(path)

    assert evidence.form_code == "390"
    assert evidence.period_key == "2025"
    assert evidence.payload["value_extraction_schema"] == "modelo390_v2"
    assert evidence.payload["extraction_status"] == "casillas_extracted"
    assert evidence.payload["filed_values"]["86"] == "-922.27"
    assert evidence.payload["filed_values"]["523"] == "6096.19"
    assert evidence.payload["blank_casillas"] == ["47"]
    assert evidence.payload["value_sources"] == {}
