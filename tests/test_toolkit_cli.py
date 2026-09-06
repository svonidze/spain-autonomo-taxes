"""Agent operations exercised directly, with synthetic data and no web service."""

from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import stat
import pytest
from autonomo_taxes.cli import main
from autonomo_taxes.fx_reference import ECBRateObservation, ECBRateResult
from autonomo_taxes.ledger_db import open as open_db
from autonomo_taxes.services import invoices
from test_review_packet import _invoice_fixture, _approve_income


def invoke(capsys, fixture, words, *, payload=None, output_dir=None):
    args = [*words, "--db", str(fixture["database"])]
    if payload is not None:
        path = Path(fixture["database"]).parent / "request.json"
        path.write_text(json.dumps(payload))
        args += ["--input", str(path)]
    if output_dir:
        args += ["--archive-root", str(output_dir)]
    code = main(args)
    capture = capsys.readouterr()
    return code, json.loads(capture.out if code == 0 else capture.err)


def test_income_agent_can_read_confirm_verified_fx_and_post_without_web(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(
        tmp_path,
        entry_type="income",
        currency="USD",
        country_code="US",
        transaction_date="2026-07-01",
    )
    raw = "Synthetic ECB observation"
    observation = ECBRateObservation(
        "USD",
        date(2026, 7, 1),
        Decimal("1.25"),
        Decimal("0.8"),
        "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        raw,
        hashlib.sha256(raw.encode()).hexdigest(),
    )
    calls = []

    def lookup(currency, when):
        calls.append((currency, when))
        return ECBRateResult("exact", observation)

    monkeypatch.setattr(invoices, "fetch_eur_rate", lookup)
    packet_path = tmp_path / "private" / "work-item.json"
    code, summary = invoke(
        capsys,
        fixture,
        [
            "review",
            "work-item",
            "transaction:" + fixture["transaction_id"],
            "--out",
            str(packet_path),
        ],
    )
    assert code == 0, summary
    assert "packet" not in summary
    item = json.loads(packet_path.read_text())
    assert item["fx_suggestion"]["status"] == "exact"
    packet = item["packet"]
    _approve_income(packet)
    packet["decision"]["tax_treatment"]["taxable_base_minor"] = 40000
    fx = {
        "rate_date": "2026-07-01",
        "rate": "0.8",
        "rate_source": "ecb",
        "source_reference": None,
        "raw_observation": None,
        "raw_observation_hash": None,
        "supersedes_rate_id": None,
    }
    code, result = invoke(
        capsys,
        fixture,
        ["review", "confirm-packet"],
        payload={"packet": packet, "fx": fx},
    )
    assert code == 0, result
    assert len(calls) >= 2  # Suggestion and fresh confirmation verification.
    with open_db(fixture["database"], read_only=True) as db:
        tx = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id=?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert tx["lifecycle_status"] == "approved" and tx["amount_eur_minor"] == 40000
        assert db.fx_verification_for_transaction(
            tx["transaction_id"], tx["fx_rate_id"]
        )
        version = tx["row_version"]
    code, preview = invoke(
        capsys, fixture, ["review", "posting-preview", "--period", fixture["period"]]
    )
    assert code == 0 and fixture["transaction_id"] in json.dumps(preview)
    assert (
        main(
            [
                "review",
                "post",
                "transaction:" + fixture["transaction_id"],
                "--db",
                str(fixture["database"]),
                "--expected-row-version",
                str(version),
            ]
        )
        == 0
    )
    capsys.readouterr()
    code, detail = invoke(
        capsys, fixture, ["transactions", "show", fixture["transaction_id"]]
    )
    assert code == 0 and detail["transaction"]["lifecycle_status"] == "posted"
    assert str(tmp_path) not in json.dumps(detail)
    code, stale = invoke(
        capsys,
        fixture,
        ["review", "confirm-packet"],
        payload={"packet": packet, "fx": fx},
    )
    assert code == 1 and stale["code"]
    with open_db(fixture["database"], read_only=True) as db:
        assert (
            db.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            == 1
        )


def test_agent_original_export_is_private_verified_and_never_overwrites(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    output = tmp_path / "private" / "export.pdf"
    code, result = invoke(
        capsys,
        fixture,
        ["documents", "original", fixture["document_id"], "--out", str(output)],
        output_dir=tmp_path / "private",
    )
    assert code == 0, result
    assert (
        result["source_hash_matched"]
        and output.read_bytes() == fixture["source"].read_bytes()
    )
    assert stat.S_IMODE(output.stat().st_mode) == 0o600 and str(
        tmp_path
    ) not in json.dumps(result)
    original = output.read_bytes()
    code, error = invoke(
        capsys,
        fixture,
        ["documents", "original", fixture["document_id"], "--out", str(output)],
        output_dir=tmp_path / "private",
    )
    assert (
        code == 1
        and output.read_bytes() == original
        and str(tmp_path) not in json.dumps(error)
    )


def test_new_commands_report_invalid_json_and_missing_database_without_paths(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    bad = tmp_path / "private" / "bad.json"
    bad.write_text("{")
    assert (
        main(
            [
                "review",
                "confirm-packet",
                "--db",
                str(fixture["database"]),
                "--input",
                str(bad),
            ]
        )
        == 1
    )
    result = capsys.readouterr()
    assert not result.out and json.loads(result.err)["code"] == "invalid_input"
    assert (
        main(
            [
                "transactions",
                "show",
                fixture["transaction_id"],
                "--db",
                str(tmp_path / "missing.sqlite"),
            ]
        )
        == 1
    )
    result = capsys.readouterr()
    assert str(tmp_path) not in result.err


@pytest.mark.parametrize("route", ["documents", "work-item"])
@pytest.mark.parametrize("symlink", [False, True])
def test_agent_outputs_cannot_escape_private_root(
    tmp_path, capsys, monkeypatch, route, symlink
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    outside = tmp_path / "shared"
    outside.mkdir()
    if symlink:
        link = tmp_path / "private" / "link"
        link.symlink_to(outside, target_is_directory=True)
        output = link / "export"
    else:
        output = outside / "export"
    command = (
        ["documents", "original", fixture["document_id"]]
        if route == "documents"
        else ["review", "work-item", "transaction:" + fixture["transaction_id"]]
    )
    code, result = invoke(
        capsys,
        fixture,
        [*command, "--out", str(output)],
        output_dir=tmp_path / "private",
    )
    assert code == 1 and result["code"] == "private_output_required"
    assert list(outside.iterdir()) == []


def test_agent_lists_cover_documents_transactions_and_counterparty_reads(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    for command in [
        ["documents", "list", "--period", fixture["period"]],
        [
            "documents",
            "list",
            "--period",
            fixture["period"],
            "--kind",
            "income_invoice",
            "--limit",
            "1",
        ],
        [
            "transactions",
            "list",
            "--period",
            fixture["period"],
            "--entry-type",
            "income",
            "--status",
            "needs_review",
        ],
        ["expense", "list", "--period", fixture["period"]],
        ["counterparties", "list"],
        ["counterparties", "show", fixture["counterparty_id"]],
        [
            "counterparties",
            "transactions",
            fixture["counterparty_id"],
            "--period",
            fixture["period"],
        ],
    ]:
        code, result = invoke(capsys, fixture, command)
        assert code == 0, (command, result)
        assert str(tmp_path) not in json.dumps(result)


def test_local_cli_intake_accepts_once_without_posting(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    database = tmp_path / "private" / "ledger.sqlite"
    database.parent.mkdir()
    assert main(["db", "init", "--db", str(database)]) == 0
    capsys.readouterr()
    source = tmp_path / "private" / "invoice.txt"
    source.write_text("Synthetic customer invoice SYN-CLI-1\n2026-07-01\nEUR 12.00")
    fixture = {"database": database}
    facts = {
        "kind": "income_invoice",
        "period": "2026-Q3",
        "issued_on": "2026-07-01",
        "document_number": "SYN-CLI-1",
        "gross": "12.00",
        "currency": "EUR",
        "counterparty_name": "Synthetic Customer",
    }
    first = None
    for _ in range(2):
        code, result = invoke(
            capsys, fixture, ["intake", "local", str(source)], payload=facts
        )
        assert code == 0, result
        if first:
            assert result["transaction_id"] == first
        first = result["transaction_id"]
    with open_db(database, read_only=True) as db:
        assert (
            db.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            == 1
        )
        assert (
            db.connection.execute(
                "SELECT lifecycle_status FROM transactions"
            ).fetchone()[0]
            != "posted"
        )


def test_private_packet_keeps_settlement_reference_and_its_snapshot(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(
        tmp_path,
        entry_type="income",
        currency="USD",
        country_code="US",
        transaction_date="2026-07-01",
    )
    reference = tmp_path / "private" / "settlement.txt"
    reference.write_text("Synthetic settlement evidence")
    with open_db(fixture["database"]) as db:
        tx = db.connection.execute(
            "SELECT row_version FROM transactions WHERE transaction_id=?",
            (fixture["transaction_id"],),
        ).fetchone()
        db.review_transaction_fx_rate(
            fixture["transaction_id"],
            expected_row_version=tx["row_version"],
            rate_date="2026-07-01",
            rate="0.8",
            rate_source="actual_settlement",
            source_reference=str(reference),
        )
    output = tmp_path / "private" / "packet.json"
    code, summary = invoke(
        capsys,
        fixture,
        [
            "review",
            "work-item",
            "transaction:" + fixture["transaction_id"],
            "--out",
            str(output),
        ],
    )
    assert code == 0 and str(tmp_path) not in json.dumps(summary)
    item = json.loads(output.read_text())
    packet = item["packet"]
    assert packet["state"]["fx"]["source_reference"] == str(reference)
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    _approve_income(packet)
    packet["decision"]["tax_treatment"]["taxable_base_minor"] = 40000
    code, result = invoke(
        capsys,
        fixture,
        ["review", "confirm-packet"],
        payload={"packet": packet, "fx": None},
    )
    assert code == 0, result


def test_drive_intake_uses_shared_provider_service_without_picker(
    tmp_path, capsys, monkeypatch
):
    from autonomo_taxes import storage_import

    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture = _invoice_fixture(tmp_path, entry_type="income")
    calls = []

    def accept(**values):
        calls.append(values)
        return {
            "document_id": fixture["document_id"],
            "transaction_id": fixture["transaction_id"],
            "status": "accepted_for_review",
        }

    monkeypatch.setattr(storage_import, "ingest_google_drive_url", accept)
    code, result = invoke(
        capsys,
        fixture,
        [
            "intake",
            "google-drive",
            "https://drive.google.com/file/d/synthetic-file-123/view",
        ],
        payload={"kind": "income_invoice", "period": fixture["period"]},
    )
    assert code == 0, result
    assert calls[0]["file_id"] == "synthetic-file-123"
    assert not hasattr(calls[0]["config"], "google_picker_developer_key")
