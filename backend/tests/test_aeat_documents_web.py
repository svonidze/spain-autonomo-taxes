from __future__ import annotations

from pathlib import Path

import pytest

from autonomo_test_support.local_web import _config
from autonomo_taxes.aeat_documents import record_aeat_document
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalWebError


SYNTHETIC_METADATA = {
    "form_code": "036",
    "filed_on": "2032-05-06T11:12:13",
    "submission_reference": "2032C3600000002B",
    "justificante_number": "0367000000002",
    "verification_code": "SYNTHETICCSV0002",
}


def _saved_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LocalAccountingApp:
    config = _config(tmp_path)
    config.archive_root.mkdir(parents=True)
    evidence = tmp_path / "synthetic-receipt.pdf"
    evidence.write_bytes(b"%PDF-synthetic")
    monkeypatch.setattr(
        "autonomo_taxes.aeat_documents.inspect_aeat_pdf",
        lambda _path: dict(SYNTHETIC_METADATA),
    )
    with LedgerDB.initialize(config.database) as db:
        record_aeat_document(
            db,
            evidence,
            config.archive_root,
            actor="synthetic-user",
            title="Synthetic ROI",
            procedure_kind="roi_registration",
            procedure_code="G322",
            form_code="036",
            document_kind="submission_receipt",
            status="submitted",
            occurred_at="2032-05-06T11:12:13+02:00",
            requested_effective_on="2032-05-10",
            primary_reference=None,
            submission_reference=None,
            justificante_number=None,
            verification_code=None,
            notes=None,
        )
    return LocalAccountingApp(config)


def test_registry_api_projection_filters_without_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _saved_app(tmp_path, monkeypatch)
    result = app.aeat_documents(year="2032", form_code="036", query="synthetic roi")
    assert result["total"] == 1
    row = result["rows"][0]
    assert row["status"] == "submitted"
    assert row["content_url"].startswith("/api/document/")
    assert "source_path" not in row
    assert app.aeat_documents(year="2031")["rows"] == []


def test_registry_original_uses_verified_document_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _saved_app(tmp_path, monkeypatch)
    row = app.aeat_documents()["rows"][0]
    document_id = row["content_url"].split("/")[3]
    path, media_type = app.document_file(document_id)
    assert path.is_file()
    assert path.is_relative_to(app.config.archive_root)
    assert media_type == "application/pdf"


def test_aeat_upload_validates_pdf_and_delegates_to_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    with LedgerDB.initialize(config.database):
        pass
    app = LocalAccountingApp(config)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        app,
        "_run_cli",
        lambda command: calls.append(command) or {"ok": True, "dry_run": True},
    )
    fields = {
        "dry_run": "1",
        "procedure_kind": "roi_registration",
        "document_kind": "submission_receipt",
        "status": "submitted",
    }
    result = app.ingest_aeat_upload(
        fields=fields,
        filename="synthetic.pdf",
        content=b"%PDF-synthetic",
        actor="synthetic-user",
    )
    assert result["dry_run"] is True
    assert "--dry-run" in calls[0]
    assert "synthetic-user" in calls[0]
    assert not list((config.cache_root / "aeat-upload").glob("request-*"))
    with pytest.raises(LocalWebError, match="must be a PDF"):
        app.ingest_aeat_upload(
            fields=fields,
            filename="synthetic.txt",
            content=b"not a pdf",
            actor="synthetic-user",
        )


def test_manual_status_update_uses_optimistic_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app = _saved_app(tmp_path, monkeypatch)
    row = app.aeat_documents()["rows"][0]
    updated = app.update_aeat_case_status(
        row["aeat_case_id"],
        {
            "status": "approved",
            "occurred_at": "2032-05-09T09:00:00Z",
            "evidence_reference": "synthetic-vies-check",
            "notes": None,
            "expected_row_version": row["case_row_version"],
        },
        actor="synthetic-user",
    )
    assert updated["current_status"] == "approved"
    assert app.aeat_documents()["rows"][0]["status"] == "approved"


def test_periodic_filing_source_cannot_escape_configured_roots(tmp_path: Path) -> None:
    config = _config(tmp_path)
    outside = tmp_path / "outside" / "synthetic.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"%PDF-synthetic")
    with LedgerDB.initialize(config.database) as db:
        db.ensure_period("2032-Q1")
        snapshot = db.create_filing_snapshot(
            "2032-Q1",
            status="baseline",
            filed_on="2032-04-01T09:00:00",
            payload={"form": "303"},
            form_code="303",
            submission_reference="2032303000000003C",
            justificante_number="3037000000003",
            verification_code="SYNTHETICCSV0303",
            source_reference=str(outside),
        )
    app = LocalAccountingApp(config)
    row = app.aeat_documents()["rows"][0]
    assert row["source_available"] is False
    with pytest.raises(LocalWebError, match="outside configured evidence roots"):
        app.filing_file(snapshot["filing_snapshot_id"])
