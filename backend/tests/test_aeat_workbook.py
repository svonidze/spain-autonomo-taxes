from __future__ import annotations

import base64
from datetime import date
import hashlib
import io
import json
from pathlib import Path
from urllib import error, parse
import xml.etree.ElementTree as ET
import zipfile

import pytest

from autonomo_taxes import aeat_workbook
from autonomo_taxes import aeat_books
from autonomo_taxes.aeat_workbook import (
    AeatValidationConsentError,
    INPUT_SHEET_BY_CONTRACT,
    SHEET_CONTRACTS,
    TEMPLATE_DATA_CAPACITY,
    TYPE_ROW_CONTRACTS,
    build_aeat_workbook_payload,
    inspect_aeat_template,
    validate_aeat_workbook,
    write_aeat_workbook_xlsx,
)
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize


def test_template_contract_checks_hash_sheets_and_exact_type_rows(tmp_path: Path) -> None:
    template = tmp_path / "official-template.xlsx"
    _write_template_fixture(template)
    digest = hashlib.sha256(template.read_bytes()).hexdigest()

    check = inspect_aeat_template(template, expected_sha256=digest)
    wrong_hash = inspect_aeat_template(template, expected_sha256="0" * 64)

    assert check["valid"] is True
    assert set(check["sheets"]) == set(SHEET_CONTRACTS)
    assert check["sheets"]["EXPEDIDAS_INGRESOS"]["column_count"] == 36
    assert check["sheets"]["RECIBIDAS_GASTOS"]["column_count"] == 42
    assert check["sheets"]["BIENES-INVERSIÓN"]["column_count"] == 42
    assert check["writer_strategy"]["status"] == "input_sheet_writer_ready"
    assert check["writer_strategy"]["xlsx_generation_supported"] is True
    assert check["writer_strategy"]["contract_sheets_are_formula_mirrors"] is True
    assert check["writer_strategy"]["data_capacity"] == TEMPLATE_DATA_CAPACITY
    assert all(check["writer_strategy"]["calc_chain"].values())
    assert all(
        row["formula_mirror"] and row["input_sheet"]["data_validation_count"]
        for row in check["sheets"].values()
    )
    assert all(
        row["missing_formula_cell_count"] == 0
        and row["unexpected_formula_cell_count"] == 0
        for row in check["sheets"].values()
    )
    assert wrong_hash["valid"] is False
    assert wrong_hash["writer_strategy"]["status"] == "template_architecture_invalid"
    assert "SHA-256" in wrong_hash["errors"][0]


def test_template_contract_rejects_missing_sheet_and_changed_type_row(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing-sheet.xlsx"
    changed = tmp_path / "changed-type.xlsx"
    _write_template_fixture(missing, omit_sheet="BIENES-INVERSIÓN")
    _write_template_fixture(changed, change_first_type=True)

    missing_check = inspect_aeat_template(
        missing,
        expected_sha256=hashlib.sha256(missing.read_bytes()).hexdigest(),
    )
    changed_check = inspect_aeat_template(
        changed,
        expected_sha256=hashlib.sha256(changed.read_bytes()).hexdigest(),
    )

    assert missing_check["valid"] is False
    assert "Missing required sheet: BIENES-INVERSIÓN" in missing_check["errors"]
    assert changed_check["valid"] is False
    assert any("type row differs" in message for message in changed_check["errors"])


def test_template_contract_rejects_incomplete_formula_mirror_package(
    tmp_path: Path,
) -> None:
    no_calc_chain = tmp_path / "no-calc-chain.xlsx"
    formula_gap = tmp_path / "formula-gap.xlsx"
    _write_template_fixture(no_calc_chain, omit_calc_chain=True)
    _write_template_fixture(formula_gap, omit_formula_cell=True)

    no_calc_check = inspect_aeat_template(
        no_calc_chain,
        expected_sha256=hashlib.sha256(no_calc_chain.read_bytes()).hexdigest(),
    )
    formula_gap_check = inspect_aeat_template(
        formula_gap,
        expected_sha256=hashlib.sha256(formula_gap.read_bytes()).hexdigest(),
    )

    assert no_calc_check["valid"] is False
    assert any("calcChain" in message for message in no_calc_check["errors"])
    assert formula_gap_check["valid"] is False
    assert any("formula mirror" in message for message in formula_gap_check["errors"])
    assert (
        formula_gap_check["sheets"]["EXPEDIDAS_INGRESOS"][
            "missing_formula_cell_count"
        ]
        == 1
    )


def test_template_contract_rejects_undeclared_ignorable_namespace(
    tmp_path: Path,
) -> None:
    template = tmp_path / "broken-mce.xlsx"
    _write_template_fixture(template, break_mce_declaration=True)

    check = inspect_aeat_template(
        template,
        expected_sha256=hashlib.sha256(template.read_bytes()).hexdigest(),
    )

    assert check["valid"] is False
    assert any("mc:Ignorable" in message for message in check["errors"])
    assert any("xr3" in message for message in check["errors"])


def test_payload_maps_projection_to_official_36_42_42_column_contract() -> None:
    projection = {
        "period": "2026-Q2",
        "scope": {"year": 2026, "through_quarter": 2, "cumulative_ytd": True},
        "taxpayer": {"tax_id": "X0000000A", "full_name": "Example Taxpayer"},
        "provisional_filename": "2026X0000000ATExample_Taxpayer.xlsx",
        "data_projection_ready": True,
        "blockers": [],
        "income_rows": [
            {
                "autoliquidacion_ejercicio": 2026,
                "autoliquidacion_periodo": "2T",
                "actividad_codigo": "A",
                "actividad_tipo": "05",
                "iae_grupo_epigrafe": "763",
                "tipo_factura": "F1",
                "concepto_ingreso": "I01",
                "ingreso_computable_eur": "100.00",
                "fecha_expedicion": "01/04/2026",
                "fecha_operacion": "01/04/2026",
                "factura_serie": "F-2026",
                "factura_numero": "1",
                "destinatario_id_tipo": "04",
                "destinatario_pais": "US",
                "destinatario_identificacion": "12-3456789",
                "destinatario_nombre": "Example Customer",
                "clave_operacion": "01",
                "calificacion_operacion": "N2",
                "total_factura_eur": "100.00",
                "base_imponible_eur": "100.00",
                "tipo_iva_percent": "0.00",
                "cuota_iva_repercutida_eur": "0.00",
                "tipo_retencion_irpf_percent": "0.00",
                "importe_retenido_irpf_eur": "0.00",
                "referencia_externa": "income-1",
                "transaction_id": "income-transaction-1",
            }
        ],
        "expense_rows": [
            {
                "autoliquidacion_ejercicio": 2026,
                "autoliquidacion_periodo": "2T",
                "actividad_codigo": "A",
                "actividad_tipo": "05",
                "iae_grupo_epigrafe": "763",
                "tipo_factura": "F1",
                "concepto_gasto": "G19",
                "gasto_deducible_eur": "100.00",
                "fecha_expedicion": "02/04/2026",
                "fecha_operacion": "02/04/2026",
                "factura_expedidor_serie_numero": "SUP-1",
                "fecha_recepcion": "03/04/2026",
                "expedidor_id_tipo": "04",
                "expedidor_pais": "GE",
                "expedidor_identificacion": "123456789",
                "expedidor_nombre": "Example Supplier",
                "clave_operacion": "01",
                "bien_inversion": "N",
                "inversion_sujeto_pasivo": "S",
                "total_factura_eur": "100.00",
                "base_imponible_eur": "100.00",
                "tipo_iva_percent": "21.00",
                "cuota_iva_soportado_eur": "21.00",
                "cuota_deducible_eur": "21.00",
                "tipo_retencion_irpf_percent": "0.00",
                "importe_retenido_irpf_eur": "0.00",
                "referencia_externa": "expense-1",
                "transaction_id": "expense-transaction-1",
            }
        ],
        "asset_rows": [
            {
                "autoliquidacion_ejercicio": 2026,
                "autoliquidacion_periodo": "0A",
                "actividad_codigo": "A",
                "actividad_tipo": "05",
                "iae_grupo_epigrafe": "763",
                "tipo_bien": "23",
                "descripcion_identificador": "EPI",
                "descripcion_literal": "Business computer",
                "fecha_inicio_utilizacion": "08/04/2026",
                "valor_adquisicion_eur": "1000.00",
                "valor_amortizable_eur": "826.45",
                "metodo_amortizacion": "02",
                "porcentaje_amortizacion": "26.00",
                "amortizacion_acumulada_inicio_eur": "0.00",
                "amortizacion_cuota_resultante_eur": "214.88",
                "amortizacion_acumulada_final_eur": "214.88",
                "amortizacion_pendiente_eur": "611.57",
                "fecha_expedicion": "08/04/2026",
                "factura_expedidor_serie_numero": "ASSET-1",
                "expedidor_identificacion": "B00000000",
                "expedidor_nombre": "Example Asset Supplier",
                "inicio_base_imponible_eur": "826.45",
                "inicio_tipo_iva_percent": "21.00",
                "inicio_prorrata_definitiva_percent": "100.00",
                "inicio_cuota_deducible_eur": "173.55",
                "referencia_externa": "asset-1",
                "asset_id": "asset-uuid-1",
            }
        ],
    }
    payload = build_aeat_workbook_payload(
        projection,
        template_check={
            "valid": True,
            "actual_sha256": "a" * 64,
            "expected_sha256": "a" * 64,
            "errors": [],
        },
    )

    income = payload["sheets"]["EXPEDIDAS_INGRESOS"]["rows"][0]
    expense = payload["sheets"]["RECIBIDAS_GASTOS"]["rows"][0]
    asset = payload["sheets"]["BIENES-INVERSIÓN"]["rows"][0]
    assert payload["payload_ready"] is True
    assert payload["schema_version"] == 3
    assert payload["xlsx_generation_supported"] is True
    assert payload["xlsx_write_ready"] is True
    assert payload["writer_strategy_status"] == "input_sheet_writer_ready"
    assert payload["xlsx_generation_blockers"] == []
    assert "Registrar" in payload["instructions"]
    assert len(income) == 36
    assert len(expense) == 42
    assert len(asset) == 42
    assert income[17:20] == ["01", "N2", ""]
    assert expense[19:22] == ["01", "N", "S"]
    assert asset[26:30] == ["826.45", "21.00", "100.00", "173.55"]
    assert payload["sheets"]["RECIBIDAS_GASTOS"]["row_lineage"] == [
        "expense-transaction-1"
    ]
    overflow_projection = dict(projection)
    overflow_projection["income_rows"] = projection["income_rows"] * 101
    overflow = build_aeat_workbook_payload(
        overflow_projection,
        template_check={
            "valid": True,
            "actual_sha256": "a" * 64,
            "expected_sha256": "a" * 64,
            "errors": [],
        },
    )
    assert overflow["payload_ready"] is True
    assert [row["code"] for row in overflow["xlsx_generation_blockers"]] == [
        "aeat_template_capacity_exceeded"
    ]
    assert overflow["xlsx_write_ready"] is False
    blocked_projection = dict(projection)
    blocked_projection["data_projection_ready"] = False
    blocked_projection["blockers"] = [
        {"code": "row_mapping_incomplete", "reference": "expense-1", "message": "review"}
    ]
    blocked = build_aeat_workbook_payload(
        blocked_projection,
        template_check={
            "valid": False,
            "path": "template.xlsx",
            "actual_sha256": "b" * 64,
            "expected_sha256": "a" * 64,
            "errors": ["template mismatch"],
        },
    )
    assert blocked["payload_ready"] is False
    assert blocked["xlsx_write_ready"] is False
    assert [row["code"] for row in blocked["blockers"]] == [
        "row_mapping_incomplete",
        "aeat_template_contract_mismatch",
    ]


def test_writer_populates_only_input_sheets_and_is_deterministic(
    tmp_path: Path,
) -> None:
    template = tmp_path / "official-template.xlsx"
    first = tmp_path / "books-first.xlsx"
    second = tmp_path / "books-second.xlsx"
    _write_template_fixture(template)
    template_digest = hashlib.sha256(template.read_bytes()).hexdigest()
    template_check = inspect_aeat_template(
        template,
        expected_sha256=template_digest,
    )
    payload = build_aeat_workbook_payload(
        _writer_projection(),
        template_check=template_check,
    )

    with zipfile.ZipFile(template) as source:
        source_sheets = aeat_workbook._workbook_sheets(source)
        source_members = {
            member.filename: source.read(member.filename)
            for member in source.infolist()
        }
        source_formula_parts = {
            source_sheets[name]: source.read(source_sheets[name])
            for name in SHEET_CONTRACTS
        }
        source_input_roots = {
            name: ET.fromstring(source.read(source_sheets[input_name]))
            for name, input_name in INPUT_SHEET_BY_CONTRACT.items()
        }

    assert write_aeat_workbook_xlsx(template, payload, first) == first
    assert write_aeat_workbook_xlsx(template, payload, second) == second
    assert first.read_bytes() == second.read_bytes()
    assert first.stat().st_size < aeat_workbook.MAX_AEAT_WORKBOOK_BYTES

    with zipfile.ZipFile(first) as generated:
        generated_sheets = aeat_workbook._workbook_sheets(generated)
        changed_members = {
            member.filename
            for member in generated.infolist()
            if generated.read(member.filename) != source_members[member.filename]
        }
        assert changed_members == {
            "xl/workbook.xml",
            "xl/calcChain.xml",
            *(generated_sheets[input_name] for input_name in INPUT_SHEET_BY_CONTRACT.values()),
        }
        for part in changed_members:
            generated_part = generated.read(part)
            assert aeat_workbook._undeclared_ignorable_prefixes(generated_part) == set()
            assert _xml_declaration(generated_part) == _xml_declaration(
                source_members[part]
            )
        for formula_target, original in source_formula_parts.items():
            assert generated.read(formula_target) == original
        calc_chain = ET.fromstring(generated.read("xl/calcChain.xml"))
        calc_chain_refs = {
            (cell.attrib.get("i"), cell.attrib["r"])
            for cell in calc_chain.findall("{*}c")
        }
        assert ("1", "U4") not in calc_chain_refs
        assert ("3", "N4") not in calc_chain_refs
        assert ("1", "U5") not in calc_chain_refs
        assert ("3", "N5") not in calc_chain_refs
        assert ("2", "A4") in calc_chain_refs

        workbook = ET.fromstring(generated.read("xl/workbook.xml"))
        assert workbook.find("{*}calcPr").attrib["fullCalcOnLoad"] == "1"

        income_target = generated_sheets[
            INPUT_SHEET_BY_CONTRACT["EXPEDIDAS_INGRESOS"]
        ]
        income_root = ET.fromstring(generated.read(income_target))
        assert _cell_value(income_root, "A4") == "2026"
        assert _cell_value(income_root, "B4") == "2T"
        assert _cell_value(income_root, "C4") == "A-Activity"
        assert _cell_value(income_root, "D4") == "05-Professional"
        assert _cell_value(income_root, "F4") == "F1-Invoice"
        assert _cell_value(income_root, "G4") == "I01-Income"
        assert _cell_value(income_root, "N4") == "04-Official ID"
        assert _cell_value(income_root, "H4") == "123.45"
        assert _cell_value(income_root, "I4") == str(
            (date(2026, 4, 1) - date(1899, 12, 30)).days
        )
        assert _cell(income_root, "A4").attrib.get("t") is None
        assert _cell(income_root, "B4").attrib["t"] == "inlineStr"
        assert _cell(income_root, "I4").attrib.get("t") is None

        for contract_name, input_name in INPUT_SHEET_BY_CONTRACT.items():
            generated_root = ET.fromstring(generated.read(generated_sheets[input_name]))
            source_root = source_input_roots[contract_name]
            assert _cell(generated_root, "A4").attrib["s"] == _cell(
                source_root, "A4"
            ).attrib["s"]
            assert generated_root.find("{*}sheetProtection").attrib == source_root.find(
                "{*}sheetProtection"
            ).attrib
        generated_income = ET.fromstring(
            generated.read(
                generated_sheets[INPUT_SHEET_BY_CONTRACT["EXPEDIDAS_INGRESOS"]]
            )
        )
        source_income = source_input_roots["EXPEDIDAS_INGRESOS"]
        assert _cell(generated_income, "U4").find("{*}f") is None
        assert _cell_value(generated_income, "U4") == "123.45"
        assert _cell(generated_income, "U4").attrib.get("t") is None
        assert _cell(generated_income, "U5").find("{*}f") is None
        assert _cell_value(generated_income, "U5") == ""
        assert ET.tostring(_cell(generated_income, "A5")) == ET.tostring(
            _cell(source_income, "A5")
        )

        generated_expense = ET.fromstring(
            generated.read(
                generated_sheets[INPUT_SHEET_BY_CONTRACT["RECIBIDAS_GASTOS"]]
            )
        )
        assert _cell(generated_expense, "N4").find("{*}f") is None
        assert _cell_value(generated_expense, "N4") == "expense-N"
        assert _cell(generated_expense, "N4").attrib["t"] == "inlineStr"
        assert all(
            cell.find("{*}f") is None
            for sheet_root in (generated_income, generated_expense)
            for row in sheet_root.findall(".//{*}sheetData/{*}row")
            if 4 <= int(row.attrib["r"]) <= 103
            for cell in row.findall("{*}c")
        )

    generated_digest = hashlib.sha256(first.read_bytes()).hexdigest()
    generated_check = inspect_aeat_template(first, expected_sha256=generated_digest)
    assert generated_check["valid"] is True


def test_writer_rejects_controlled_code_missing_from_template_lookup(
    tmp_path: Path,
) -> None:
    template = tmp_path / "official-template.xlsx"
    _write_template_fixture(template)
    template_digest = hashlib.sha256(template.read_bytes()).hexdigest()
    payload = build_aeat_workbook_payload(
        _writer_projection(),
        template_check=inspect_aeat_template(
            template,
            expected_sha256=template_digest,
        ),
    )
    payload["sheets"]["EXPEDIDAS_INGRESOS"]["rows"][0][2] = "Z"
    payload["payload_sha256"] = aeat_workbook._payload_sha256(payload)

    with pytest.raises(ValueError, match=r"CODIGO-LITERAL.*Registrar.*C4"):
        write_aeat_workbook_xlsx(template, payload, tmp_path / "invalid.xlsx")


def test_writer_refuses_unready_over_capacity_or_mismatched_template(
    tmp_path: Path,
) -> None:
    template = tmp_path / "official-template.xlsx"
    _write_template_fixture(template)
    template_digest = hashlib.sha256(template.read_bytes()).hexdigest()
    template_check = inspect_aeat_template(
        template,
        expected_sha256=template_digest,
    )
    projection = _writer_projection()
    payload = build_aeat_workbook_payload(projection, template_check=template_check)

    blocked_projection = dict(projection)
    blocked_projection["data_projection_ready"] = False
    blocked_projection["blockers"] = [
        {"code": "review_required", "subject": "expense-1", "message": "review"}
    ]
    blocked = build_aeat_workbook_payload(
        blocked_projection,
        template_check=template_check,
    )
    with pytest.raises(ValueError, match="not ready"):
        write_aeat_workbook_xlsx(template, blocked, tmp_path / "blocked.xlsx")

    overflow_projection = dict(projection)
    overflow_projection["income_rows"] = projection["income_rows"] * 101
    overflow = build_aeat_workbook_payload(
        overflow_projection,
        template_check=template_check,
    )
    with pytest.raises(ValueError, match="capacity"):
        write_aeat_workbook_xlsx(template, overflow, tmp_path / "overflow.xlsx")

    changed_template = tmp_path / "changed-template.xlsx"
    changed_template.write_bytes(template.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="hash"):
        write_aeat_workbook_xlsx(
            changed_template,
            payload,
            tmp_path / "mismatched.xlsx",
        )

    with pytest.raises(ValueError, match="overwrite the source template"):
        write_aeat_workbook_xlsx(template, payload, template)


def test_aeat_write_cli_emits_workbook_hash_and_optional_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    template = tmp_path / "official-template.xlsx"
    workbook = tmp_path / "2026-Q2-books.xlsx"
    write_plan = tmp_path / "2026-Q2-write-plan.json"
    manifest = tmp_path / "2026-Q2-manifest.json"
    _write_template_fixture(template)
    template_digest = hashlib.sha256(template.read_bytes()).hexdigest()
    original_inspect = inspect_aeat_template

    def inspect_fixture(
        path: str | Path,
        *,
        expected_sha256: str = template_digest,
    ) -> dict[str, object]:
        return original_inspect(path, expected_sha256=expected_sha256)

    monkeypatch.setattr(aeat_workbook, "inspect_aeat_template", inspect_fixture)
    monkeypatch.setattr(
        aeat_books,
        "build_aeat_book_projection",
        lambda _db, **_kwargs: _writer_projection(),
    )
    with initialize(database):
        pass

    exit_code = main(
        [
            "books",
            "aeat-write",
            "--db",
            str(database),
            "--period",
            "2026-Q2",
            "--template",
            str(template),
            "--out",
            str(workbook),
            "--out-payload",
            str(write_plan),
            "--out-manifest",
            str(manifest),
        ]
    )
    result = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert result["ok"] is True
    assert result["workbook_written"] is True
    assert result["validator_status"] == "not_run"
    assert result["workbook_sha256"] == hashlib.sha256(workbook.read_bytes()).hexdigest()
    assert result["payload_sha256"] == json.loads(
        write_plan.read_text(encoding="utf-8")
    )["payload_sha256"]
    manifest_payload = json.loads(manifest.read_text(encoding="utf-8"))
    assert manifest_payload["workbook"]["sha256"] == result["workbook_sha256"]
    assert manifest_payload["source_template"]["sha256"] == template_digest
    assert manifest_payload["validator_status"] == "not_run"


def test_validator_requires_consent_and_preserves_request_hash_without_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook = tmp_path / "books.xlsx"
    workbook.write_bytes(b"PK\x03\x04reviewed-workbook")
    with pytest.raises(AeatValidationConsentError):
        validate_aeat_workbook(
            workbook,
            year=2026,
            confirm_upload_to_aeat=False,
        )

    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self) -> bytes:
            return '{"resultado":"correcto"}'.encode("iso-8859-15")

    def fake_urlopen(http_request, timeout):
        captured["url"] = http_request.full_url
        captured["body"] = http_request.data
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(aeat_workbook.request, "urlopen", fake_urlopen)
    receipt = validate_aeat_workbook(
        workbook,
        year=2026,
        confirm_upload_to_aeat=True,
    )
    posted = parse.parse_qs(captured["body"].decode("ascii"))

    assert captured["url"] == aeat_workbook.AEAT_VALIDATOR_POST_URL
    assert posted["EJER"] == ["2026"]
    assert base64.b64decode(posted["FIC"][0]) == workbook.read_bytes()
    assert receipt["transport"]["ok"] is True
    assert receipt["validator_response"] == {"resultado": "correcto"}
    assert receipt["request"]["workbook_sha256"] == hashlib.sha256(
        workbook.read_bytes()
    ).hexdigest()
    assert "FIC" not in str(receipt)
    assert "reviewed-workbook" not in str(receipt)


def test_validator_rejects_bad_local_input_and_preserves_transport_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workbook = tmp_path / "books.xlsx"
    workbook.write_bytes(b"PK\x03\x04candidate")
    with pytest.raises(ValueError, match="supported range"):
        validate_aeat_workbook(
            workbook,
            year=1900,
            confirm_upload_to_aeat=True,
        )
    wrong_suffix = tmp_path / "books.csv"
    wrong_suffix.write_text("not xlsx", encoding="utf-8")
    with pytest.raises(ValueError, match="existing .xlsx"):
        validate_aeat_workbook(
            wrong_suffix,
            year=2026,
            confirm_upload_to_aeat=True,
        )
    oversized = tmp_path / "oversized.xlsx"
    oversized.write_bytes(b"0" * (aeat_workbook.MAX_AEAT_WORKBOOK_BYTES + 1))
    with pytest.raises(ValueError, match="4 MB"):
        validate_aeat_workbook(
            oversized,
            year=2026,
            confirm_upload_to_aeat=True,
        )

    def http_failure(http_request, timeout):
        raise error.HTTPError(
            http_request.full_url,
            503,
            "Service unavailable",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"busy"}'),
        )

    monkeypatch.setattr(aeat_workbook.request, "urlopen", http_failure)
    receipt = validate_aeat_workbook(
        workbook,
        year=2026,
        confirm_upload_to_aeat=True,
    )
    assert receipt["transport"]["ok"] is False
    assert receipt["transport"]["http_status"] == 503
    assert receipt["validator_response"] == {"error": "busy"}

    def timeout_failure(_http_request, timeout):
        del timeout
        raise TimeoutError("validator timed out")

    monkeypatch.setattr(aeat_workbook.request, "urlopen", timeout_failure)
    timeout_receipt = validate_aeat_workbook(
        workbook,
        year=2026,
        confirm_upload_to_aeat=True,
    )
    assert timeout_receipt["transport"] == {
        "http_status": None,
        "ok": False,
        "error": "validator timed out",
    }


def _write_template_fixture(
    path: Path,
    *,
    omit_sheet: str | None = None,
    change_first_type: bool = False,
    omit_calc_chain: bool = False,
    omit_formula_cell: bool = False,
    break_mce_declaration: bool = False,
) -> None:
    main_namespace = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    relationship_namespace = (
        "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    )
    package_relationship_namespace = (
        "http://schemas.openxmlformats.org/package/2006/relationships"
    )
    workbook = ET.Element(f"{{{main_namespace}}}workbook")
    sheets = ET.SubElement(workbook, f"{{{main_namespace}}}sheets")
    relationships = ET.Element(f"{{{package_relationship_namespace}}}Relationships")
    sheet_documents: list[tuple[str, bytes]] = []

    for contract_index, (name, type_row) in enumerate(
        TYPE_ROW_CONTRACTS.items(), start=1
    ):
        input_worksheet = ET.Element(f"{{{main_namespace}}}worksheet")
        input_sheet_data = ET.SubElement(
            input_worksheet, f"{{{main_namespace}}}sheetData"
        )
        for row_number in range(4, 104):
            input_row = ET.SubElement(
                input_sheet_data,
                f"{{{main_namespace}}}row",
                {"r": str(row_number)},
            )
            for column_index in range(1, len(type_row) + 1):
                input_cell = ET.SubElement(
                    input_row,
                    f"{{{main_namespace}}}c",
                    {
                        "r": f"{_column_letter(column_index)}{row_number}",
                        "s": str(column_index),
                    },
                )
                formula_text = None
                if name == "EXPEDIDAS_INGRESOS" and column_index == 21:
                    formula_text = f"IF(V{row_number}=\"\",\"\",V{row_number})"
                elif name == "RECIBIDAS_GASTOS" and column_index == 14:
                    formula_text = f"IF(F{row_number}=\"\",\"\",F{row_number})"
                if formula_text is not None:
                    input_cell.set("t", "str")
                    formula = ET.SubElement(input_cell, f"{{{main_namespace}}}f")
                    formula.text = formula_text
                    ET.SubElement(input_cell, f"{{{main_namespace}}}v")
        ET.SubElement(
            input_worksheet,
            f"{{{main_namespace}}}sheetProtection",
            {"sheet": "1", "algorithmName": "SHA-512", "hashValue": "fixture"},
        )
        validations = ET.SubElement(
            input_worksheet,
            f"{{{main_namespace}}}dataValidations",
            {"count": "1"},
        )
        ET.SubElement(
            validations,
            f"{{{main_namespace}}}dataValidation",
            {"type": "list", "sqref": "A4:A103"},
        )
        input_document = ET.tostring(
            input_worksheet,
            encoding="utf-8",
            xml_declaration=True,
        )
        input_prefixes = ("x14ac", "xr", "xr2", "xr3")
        declared_prefixes = (
            input_prefixes[:-1]
            if break_mce_declaration and contract_index == 1
            else input_prefixes
        )
        sheet_documents.append(
            (
                INPUT_SHEET_BY_CONTRACT[name],
                _with_markup_compatibility(
                    input_document,
                    declared_prefixes=declared_prefixes,
                    ignorable_prefixes=input_prefixes,
                ),
            )
        )

        if name == omit_sheet:
            continue
        worksheet = ET.Element(f"{{{main_namespace}}}worksheet")
        sheet_data = ET.SubElement(worksheet, f"{{{main_namespace}}}sheetData")
        type_row_element = ET.SubElement(
            sheet_data, f"{{{main_namespace}}}row", {"r": "3"}
        )
        for column_index, value in enumerate(type_row, start=1):
            if change_first_type and contract_index == 1 and column_index == 1:
                value = "Alfanumérico (4)"
            cell = ET.SubElement(
                type_row_element,
                f"{{{main_namespace}}}c",
                {"r": f"{_column_letter(column_index)}3", "t": "inlineStr"},
            )
            inline = ET.SubElement(cell, f"{{{main_namespace}}}is")
            text = ET.SubElement(inline, f"{{{main_namespace}}}t")
            text.text = value
        for row_number in range(4, 104):
            row = ET.SubElement(
                sheet_data,
                f"{{{main_namespace}}}row",
                {"r": str(row_number)},
            )
            for column_index in range(1, len(type_row) + 1):
                if (
                    omit_formula_cell
                    and contract_index == 1
                    and row_number == 4
                    and column_index == 1
                ):
                    continue
                reference = f"{_column_letter(column_index)}{row_number}"
                cell = ET.SubElement(
                    row,
                    f"{{{main_namespace}}}c",
                    {"r": reference, "t": "str"},
                )
                formula = ET.SubElement(cell, f"{{{main_namespace}}}f")
                formula.text = (
                    f"IF('{INPUT_SHEET_BY_CONTRACT[name]}'!{reference}=\"\",\"\","
                    f"'{INPUT_SHEET_BY_CONTRACT[name]}'!{reference})"
                )
                ET.SubElement(cell, f"{{{main_namespace}}}v")
        sheet_documents.append((name, ET.tostring(worksheet, encoding="utf-8")))

    literal_worksheet = ET.Element(f"{{{main_namespace}}}worksheet")
    literal_sheet_data = ET.SubElement(
        literal_worksheet, f"{{{main_namespace}}}sheetData"
    )
    literal_rows = (
        ("ACTIVIDAD", "A-Activity", "A"),
        ("A", "05-Professional", "05"),
        ("TIPO FACTURA", "F1-Invoice", "F1"),
        ("CONCEPTO INGRESO", "I01-Income", "I01"),
        ("TIPO NIF", "04-Official ID", "04"),
        ("CLAVE OPERACION", "01-General", "01"),
        ("CALIFICACION OPERACION", "N2-Location rules", "N2"),
        ("TIPO FACTURA GASTO", "F1-Expense invoice", "F1"),
        ("CONCEPTO GASTO", "G19-Other expense", "G19"),
        ("CLAVE OPERACION GASTO", "01-General expense", "01"),
        ("TIPO BIEN", "23-Computer equipment", "23"),
        ("METODO AMORTIZACION", "02-Linear", "02"),
    )
    for row_number, (category, literal, code) in enumerate(literal_rows, start=1):
        row = ET.SubElement(
            literal_sheet_data,
            f"{{{main_namespace}}}row",
            {"r": str(row_number)},
        )
        for column_index, value in (
            (1, category),
            (2, literal),
            (3, category + literal),
            (4, code),
        ):
            cell = ET.SubElement(
                row,
                f"{{{main_namespace}}}c",
                {
                    "r": f"{_column_letter(column_index)}{row_number}",
                    "t": "inlineStr",
                },
            )
            inline = ET.SubElement(cell, f"{{{main_namespace}}}is")
            text = ET.SubElement(inline, f"{{{main_namespace}}}t")
            text.text = value
    sheet_documents.append(
        (
            "CODIGO-LITERAL",
            ET.tostring(literal_worksheet, encoding="utf-8"),
        )
    )

    for index, (name, document) in enumerate(sheet_documents, start=1):
        ET.SubElement(
            sheets,
            f"{{{main_namespace}}}sheet",
            {
                "name": name,
                "sheetId": str(index),
                f"{{{relationship_namespace}}}id": f"rId{index}",
            },
        )
        ET.SubElement(
            relationships,
            f"{{{package_relationship_namespace}}}Relationship",
            {
                "Id": f"rId{index}",
                "Type": (
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
                ),
                "Target": f"worksheets/sheet{index}.xml",
            },
        )
    if not omit_calc_chain:
        ET.SubElement(
            relationships,
            f"{{{package_relationship_namespace}}}Relationship",
            {
                "Id": f"rId{len(sheet_documents) + 1}",
                "Type": (
                    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
                    "calcChain"
                ),
                "Target": "calcChain.xml",
            },
        )
    ET.SubElement(workbook, f"{{{main_namespace}}}calcPr", {"calcId": "191029"})

    content_types_namespace = (
        "http://schemas.openxmlformats.org/package/2006/content-types"
    )
    content_types = ET.Element(f"{{{content_types_namespace}}}Types")
    if not omit_calc_chain:
        ET.SubElement(
            content_types,
            f"{{{content_types_namespace}}}Override",
            {
                "PartName": "/xl/calcChain.xml",
                "ContentType": (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml."
                    "calcChain+xml"
                ),
            },
        )

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml", ET.tostring(content_types, encoding="utf-8")
        )
        archive.writestr(
            "xl/workbook.xml",
            _with_markup_compatibility(
                ET.tostring(
                    workbook,
                    encoding="utf-8",
                    xml_declaration=True,
                ),
                declared_prefixes=("x15", "xr", "xr6", "xr10", "xr2"),
                ignorable_prefixes=("x15", "xr", "xr6", "xr10", "xr2"),
            ),
        )
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            ET.tostring(relationships, encoding="utf-8"),
        )
        for index, (_name, document) in enumerate(sheet_documents, start=1):
            archive.writestr(f"xl/worksheets/sheet{index}.xml", document)
        if not omit_calc_chain:
            calc_chain = ET.Element(f"{{{main_namespace}}}calcChain")
            for reference, sheet_id in (
                ("U4", "1"),
                ("U5", "1"),
                ("A4", "2"),
                ("N4", "3"),
                ("N5", "3"),
            ):
                ET.SubElement(
                    calc_chain,
                    f"{{{main_namespace}}}c",
                    {"r": reference, "i": sheet_id},
                )
            archive.writestr(
                "xl/calcChain.xml", ET.tostring(calc_chain, encoding="utf-8")
            )


def _column_letter(index: int) -> str:
    output = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        output = chr(ord("A") + remainder) + output
    return output


def _with_markup_compatibility(
    document: bytes,
    *,
    declared_prefixes: tuple[str, ...],
    ignorable_prefixes: tuple[str, ...],
) -> bytes:
    declaration_end = document.find(b"?>")
    root_start = document.find(b"<", declaration_end + 2 if declaration_end >= 0 else 0)
    root_end = document.find(b">", root_start)
    assert root_end > 0
    declarations = b"".join(
        f' xmlns:{prefix}="urn:test:{prefix}"'.encode("ascii")
        for prefix in declared_prefixes
    )
    markup = (
        b' xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006"'
        + declarations
        + f' mc:Ignorable="{" ".join(ignorable_prefixes)}"'.encode("ascii")
    )
    return document[:root_end] + markup + document[root_end:]


def _xml_declaration(document: bytes) -> bytes:
    declaration_end = document.find(b"?>")
    return document[: declaration_end + 2] if declaration_end >= 0 else b""


def _writer_projection() -> dict[str, object]:
    def row(columns, prefix: str) -> dict[str, object]:
        values: dict[str, object] = {}
        for column in columns:
            value_type = column["value_type"]
            if value_type == "integer":
                value: object = 2026
            elif value_type == "decimal":
                value = "123.45"
            elif value_type == "date":
                value = "01/04/2026"
            else:
                value = f"{prefix}-{column['letter']}"
            values[str(column["projection_key"])] = value
        return values

    income = row(aeat_workbook.INCOME_COLUMNS, "income")
    income.update(
        {
            "autoliquidacion_periodo": "2T",
            "actividad_codigo": "A",
            "actividad_tipo": "05",
            "tipo_factura": "F1",
            "concepto_ingreso": "I01",
            "destinatario_id_tipo": "04",
            "clave_operacion": "01",
            "calificacion_operacion": "N2",
            "operacion_exenta": "",
            "cobro_medio": "",
            "inmueble_situacion": "",
        }
    )
    expense = row(aeat_workbook.EXPENSE_COLUMNS, "expense")
    expense.update(
        {
            "autoliquidacion_periodo": "2T",
            "actividad_codigo": "A",
            "actividad_tipo": "05",
            "tipo_factura": "F1",
            "concepto_gasto": "G19",
            "expedidor_id_tipo": "04",
            "clave_operacion": "01",
            "pago_medio": "",
            "inmueble_situacion": "",
        }
    )
    asset = row(aeat_workbook.ASSET_COLUMNS, "asset")
    asset.update(
        {
            "autoliquidacion_periodo": "2T",
            "actividad_codigo": "A",
            "actividad_tipo": "05",
            "tipo_bien": "23",
            "metodo_amortizacion": "02",
            "expedidor_id_tipo": "04",
            "baja_causa": "",
            "inmueble_situacion": "",
        }
    )

    return {
        "period": "2026-Q2",
        "scope": {"year": 2026, "through_quarter": 2, "cumulative_ytd": True},
        "taxpayer": {"tax_id": "X0000000A", "full_name": "Example Taxpayer"},
        "provisional_filename": "2026X0000000ATExample_Taxpayer.xlsx",
        "data_projection_ready": True,
        "blockers": [],
        "counts": {"income": 1, "expense": 1, "assets": 1, "blockers": 0},
        "income_rows": [income],
        "expense_rows": [expense],
        "asset_rows": [asset],
    }


def _cell(root: ET.Element, reference: str) -> ET.Element:
    cell = root.find(f".//{{*}}c[@r='{reference}']")
    assert cell is not None
    return cell


def _cell_value(root: ET.Element, reference: str) -> str:
    cell = _cell(root, reference)
    if cell.attrib.get("t") == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//{*}t"))
    value = cell.find("{*}v")
    return value.text if value is not None and value.text is not None else ""
