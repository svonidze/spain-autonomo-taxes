from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from autonomo_taxes.aeat_documents import (
    build_aeat_candidate,
    record_aeat_document,
    unified_aeat_documents,
)
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION, LedgerDB, LifecycleError


METADATA = {
    "form_code": "036",
    "filed_on": "2032-04-03T10:20:30",
    "submission_reference": "2032C3600000001A",
    "justificante_number": "0367000000001",
    "verification_code": "SYNTHETICCSV0001",
}


def _evidence(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic-modelo-036.pdf"
    path.write_bytes(b"%PDF-synthetic AEAT receipt")
    return path


def _record(db: LedgerDB, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    evidence = _evidence(tmp_path)
    monkeypatch.setattr(
        "autonomo_taxes.aeat_documents.inspect_aeat_pdf", lambda _path: dict(METADATA)
    )
    return record_aeat_document(
        db,
        evidence,
        tmp_path / "archive",
        actor="synthetic-operator",
        title="Synthetic ROI registration",
        procedure_kind="roi_registration",
        procedure_code="G322",
        form_code="036",
        document_kind="submission_receipt",
        status="submitted",
        occurred_at="2032-04-03T10:20:30+02:00",
        requested_effective_on="2032-04-10",
        primary_reference="2032C3600000001A",
        submission_reference="2032C3600000001A",
        justificante_number="0367000000001",
        verification_code="SYNTHETICCSV0001",
        notes="Synthetic fixture",
    )


def test_migrates_schema_24_to_25(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        for name in (
            "aeat_source_documents_no_update",
            "aeat_source_documents_no_delete",
        ):
            db.connection.execute(f"DROP TRIGGER {name}")
        for name in ("aeat_case_events", "aeat_documents", "aeat_cases"):
            db.connection.execute(f"DROP TABLE {name}")
        db.connection.execute("PRAGMA user_version = 24")
        db.connection.commit()

    with LedgerDB.open(database, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        tables = {
            row[0]
            for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'aeat_%'"
            )
        }
    assert tables == {"aeat_cases", "aeat_documents", "aeat_case_events"}


def test_records_immutable_document_and_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        first = _record(db, tmp_path, monkeypatch)
        second = _record(db, tmp_path, monkeypatch)
        assert first["idempotent"] is False
        assert second["idempotent"] is True
        assert db.connection.execute("SELECT COUNT(*) FROM aeat_cases").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM aeat_documents").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM aeat_case_events").fetchone()[0] == 1
        assert db.connection.execute("SELECT COUNT(*) FROM document_attachments").fetchone()[0] == 1
        stored = Path(
            db.connection.execute(
                "SELECT source_path FROM documents WHERE document_type='aeat_official'"
            ).fetchone()[0]
        )
        assert stored.is_file()
        assert stored.stat().st_mode & 0o777 == 0o600
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.connection.execute("UPDATE aeat_documents SET notes='changed'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.connection.execute("DELETE FROM aeat_case_events")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.connection.execute(
                "UPDATE documents SET source_path='changed' WHERE document_type='aeat_official'"
            )


def test_identical_pdf_with_different_metadata_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        _record(db, tmp_path, monkeypatch)
        with pytest.raises(ValueError, match="different metadata"):
            evidence = _evidence(tmp_path)
            record_aeat_document(
                db,
                evidence,
                tmp_path / "archive",
                actor="synthetic-operator",
                title="Conflicting title",
                procedure_kind="roi_registration",
                procedure_code="G322",
                form_code="036",
                document_kind="submission_receipt",
                status="submitted",
                occurred_at="2032-04-03T10:20:30+02:00",
                requested_effective_on="2032-04-10",
                primary_reference="2032C3600000001A",
                submission_reference="2032C3600000001A",
                justificante_number="0367000000001",
                verification_code="SYNTHETICCSV0001",
                notes="Synthetic fixture",
            )


def test_status_history_enforces_transitions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        saved = _record(db, tmp_path, monkeypatch)
        case_id = saved["aeat_case_id"]
        for version, status in enumerate(
            ("information_requested", "responded", "approved"), start=1
        ):
            result = db.record_aeat_case_status(
                case_id,
                status=status,
                occurred_at=f"2032-04-{3 + version:02d}T10:00:00Z",
                evidence_reference=f"synthetic-check-{version}",
                notes=None,
                actor="synthetic-operator",
                expected_row_version=version,
            )
            assert result["current_status"] == status
        assert _record(db, tmp_path, monkeypatch)["idempotent"] is True
        with pytest.raises(LifecycleError, match="approved -> information_requested"):
            db.record_aeat_case_status(
                case_id,
                status="information_requested",
                occurred_at="2032-04-10T10:00:00Z",
                evidence_reference="synthetic-reopen",
                notes=None,
                actor="synthetic-operator",
                expected_row_version=4,
            )


def test_rejects_metadata_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = _evidence(tmp_path)
    monkeypatch.setattr(
        "autonomo_taxes.aeat_documents.inspect_aeat_pdf", lambda _path: dict(METADATA)
    )
    with pytest.raises(ValueError, match="verification_code conflicts"):
        build_aeat_candidate(
            evidence,
            title=None,
            procedure_kind="roi_registration",
            procedure_code="G322",
            form_code="036",
            document_kind="submission_receipt",
            status="submitted",
            occurred_at=None,
            requested_effective_on=None,
            primary_reference=None,
            submission_reference=None,
            justificante_number=None,
            verification_code="DIFFERENTCSV0001",
            notes=None,
        )


def test_unified_registry_deduplicates_periodic_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "periodic.pdf"
    source.write_bytes(b"%PDF-periodic")
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        _record(db, tmp_path, monkeypatch)
        db.ensure_period("2032-Q1")
        for source_hash in ("a" * 64, "b" * 64):
            db.create_filing_snapshot(
                "2032-Q1",
                status="baseline",
                filed_on="2032-04-01T09:00:00",
                payload={"form": "303"},
                form_code="303",
                submission_reference="2032303000000001A",
                justificante_number="3037000000001",
                verification_code="SYNTHETICCSV0303",
                source_reference=str(source),
                source_hash=source_hash,
                snapshot_hash=source_hash,
            )
        rows = unified_aeat_documents(db.connection)
    assert len(rows) == 2
    assert {row["record_source"] for row in rows} == {"aeat_document", "filing_snapshot"}


def test_cli_dry_run_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database):
        pass
    evidence = _evidence(tmp_path)
    monkeypatch.setattr(
        "autonomo_taxes.aeat_documents.inspect_aeat_pdf", lambda _path: dict(METADATA)
    )
    assert main([
        "aeat-documents", "record", "--db", str(database), "--evidence", str(evidence),
        "--procedure-kind", "roi_registration", "--procedure-code", "G322",
        "--document-kind", "submission_receipt", "--status", "submitted",
        "--actor", "synthetic-operator", "--dry-run",
    ]) == 0
    assert '"dry_run": true' in capsys.readouterr().out
    with LedgerDB.open(database, read_only=True) as db:
        assert db.connection.execute("SELECT COUNT(*) FROM aeat_cases").fetchone()[0] == 0
