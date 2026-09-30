from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.storage_cleanup_preflight import check_cleanup_preflight


class _LiveHashAdapter:
    def __init__(self, *, broken_locator: str | None = None) -> None:
        self.broken_locator = broken_locator
        self.calls: list[tuple[str, str]] = []

    def verify(self, locator: str, expected_sha256: str) -> None:
        self.calls.append((locator, expected_sha256))
        if locator == self.broken_locator:
            raise OSError("provider object did not match")


def _adapter_factory(adapters: dict[str, _LiveHashAdapter]):
    return lambda backend: adapters[str(backend["backend_key"])]


def _proof(path: Path, *, restored_at: datetime, valid: bool = True) -> None:
    backup_at = restored_at - timedelta(minutes=1)
    path.write_text(
        json.dumps(
            {
                "format": "autonomo-backup-restore-proof/v1",
                "backup_completed_at": backup_at.isoformat().replace("+00:00", "Z"),
                "restore_completed_at": restored_at.isoformat().replace("+00:00", "Z"),
                "backup_archive_sha256": "a" * 64,
                "restore": {
                    "sqlite_integrity_check": "ok" if valid else "failed",
                    "foreign_key_check": "ok",
                },
            }
        ),
        encoding="utf-8",
    )


def _catalogue(database: Path, *, managed_status: str = "retired", include_yandex: bool = True) -> None:
    payload = b"archive original"
    digest = hashlib.sha256(payload).hexdigest()
    with LedgerDB.initialize(database) as db:
        document = db.upsert_document(
            document_type="expense_invoice", issued_on="2026-08-18", source_hash="a" * 64
        )
        file_row = db.upsert_file(
            content_sha256=digest, byte_size=len(payload), media_type="application/pdf"
        )
        db.attach_file_to_document(
            document_id=str(document["document_id"]), file_id=str(file_row["file_id"]), attachment_role="source"
        )
        backends = {}
        for key, driver in (("google_archive_ro", "google_drive"), ("yandex_evidence", "s3"), ("google_managed_rw", "google_drive")):
            backends[key] = db.upsert_storage_backend(
                backend_key=key,
                display_name=key,
                driver_key=driver,
                provider_key="google" if driver == "google_drive" else "yandex_cloud",
                access_mode="read_only" if key == "google_archive_ro" else "read_write",
                config={},
            )
        verified_at = "2026-08-18T00:00:00Z"
        db.register_file_replica(
            file_id=str(file_row["file_id"]),
            storage_backend_id=str(backends["google_archive_ro"]["storage_backend_id"]),
            provider_locator="original-drive-file-id",
            is_primary=True,
            last_verified_at=verified_at,
        )
        if include_yandex:
            db.register_file_replica(
                file_id=str(file_row["file_id"]),
                storage_backend_id=str(backends["yandex_evidence"]["storage_backend_id"]),
                provider_locator="encrypted-object",
                last_verified_at=verified_at,
            )
        db.register_file_replica(
            file_id=str(file_row["file_id"]),
            storage_backend_id=str(backends["google_managed_rw"]["storage_backend_id"]),
            provider_locator="duplicate-object",
            replica_status=managed_status,
            last_verified_at=verified_at if managed_status == "available" else None,
        )


def test_cleanup_preflight_passes_only_with_verified_archive_yandex_retired_managed_and_fresh_proof(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    proof = tmp_path / "restore-proof.json"
    now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    _catalogue(database)
    _proof(proof, restored_at=now)
    adapters = {
        "google_archive_ro": _LiveHashAdapter(),
        "yandex_evidence": _LiveHashAdapter(),
    }

    with LedgerDB.open(database, read_only=True) as db:
        result = check_cleanup_preflight(
            db,
            archive_backend_key="google_archive_ro",
            yandex_backend_key="yandex_evidence",
            managed_backend_key="google_managed_rw",
            backup_restore_proof=proof,
            now=now,
            adapter_factory=_adapter_factory(adapters),
        )

    assert result.passed is True
    assert len(adapters["google_archive_ro"].calls) == 1
    assert len(adapters["yandex_evidence"].calls) == 1
    assert result.as_dict() == {
        "passed": True,
        "source_files": 1,
        "archive_verified": 1,
        "yandex_verified": 1,
        "managed_active": 0,
        "backup_restore_proof": {"present": True, "valid": True, "fresh": True},
        "failure_codes": [],
    }


def test_cleanup_preflight_reports_failures_without_provider_locators_or_proof_contents(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    proof = tmp_path / "restore-proof.json"
    now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    _catalogue(database, managed_status="available", include_yandex=False)
    _proof(proof, restored_at=now - timedelta(hours=37), valid=False)
    adapters = {
        "google_archive_ro": _LiveHashAdapter(),
        "yandex_evidence": _LiveHashAdapter(),
    }

    with LedgerDB.open(database, read_only=True) as db:
        result = check_cleanup_preflight(
            db,
            archive_backend_key="google_archive_ro",
            yandex_backend_key="yandex_evidence",
            managed_backend_key="google_managed_rw",
            backup_restore_proof=proof,
            now=now,
            adapter_factory=_adapter_factory(adapters),
        )

    payload = result.as_dict()
    assert payload["passed"] is False
    assert set(payload["failure_codes"]) == {
        "yandex_coverage_incomplete",
        "managed_backend_still_active",
        "backup_restore_proof_invalid",
    }
    serialized = json.dumps(payload)
    assert "duplicate-object" not in serialized
    assert "encrypted-object" not in serialized


def test_cleanup_preflight_fails_closed_when_live_hash_verification_fails(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    proof = tmp_path / "restore-proof.json"
    now = datetime(2026, 8, 18, 12, tzinfo=timezone.utc)
    _catalogue(database)
    _proof(proof, restored_at=now)
    adapters = {
        "google_archive_ro": _LiveHashAdapter(broken_locator="original-drive-file-id"),
        "yandex_evidence": _LiveHashAdapter(),
    }

    with LedgerDB.open(database, read_only=True) as db:
        result = check_cleanup_preflight(
            db,
            archive_backend_key="google_archive_ro",
            yandex_backend_key="yandex_evidence",
            managed_backend_key="google_managed_rw",
            backup_restore_proof=proof,
            now=now,
            adapter_factory=_adapter_factory(adapters),
        )

    assert result.passed is False
    assert result.archive_verified == 0
    assert result.failure_codes == ("archive_coverage_incomplete",)


def test_cleanup_preflight_cli_returns_two_for_failed_gate_and_only_safe_summary(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    proof = tmp_path / "missing-proof.json"
    _catalogue(database)

    exit_code = main(
        [
            "storage", "cleanup-preflight", "--db", str(database),
            "--archive-backend", "google_archive_ro",
            "--yandex-backend", "yandex_evidence",
            "--managed-backend", "google_managed_rw",
            "--backup-restore-proof", str(proof),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["passed"] is False
    assert payload["backup_restore_proof"] == {"present": False, "valid": False, "fresh": False}
    assert "backup_restore_proof_missing" in payload["failure_codes"]
    assert "archive_coverage_incomplete" in payload["failure_codes"]
    assert "yandex_coverage_incomplete" in payload["failure_codes"]


def test_cleanup_preflight_rejects_duplicate_or_wrong_backend_roles(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    _catalogue(database)
    with LedgerDB.open(database) as db:
        result = check_cleanup_preflight(
            db,
            archive_backend_key="google_archive_ro",
            yandex_backend_key="google_archive_ro",
            managed_backend_key="google_archive_ro",
            backup_restore_proof=tmp_path / "missing.json",
        )

    assert result.passed is False
    assert "cleanup_backends_not_distinct" in result.failure_codes
    assert "yandex_backend_role_invalid" in result.failure_codes
