from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import subprocess

import pytest

import autonomo_taxes.intake as intake_module

from autonomo_taxes.intake import (
    ExpenseInboxCleanupCandidate,
    InboxCleanupError,
    archive_evidence,
    cleanup_expense_inbox_source,
    inspect_document,
    structural_errors,
    validate_expense_inbox_cleanup,
)


def test_plain_text_invoice_is_extracted_but_never_posting_eligible(tmp_path: Path) -> None:
    path = tmp_path / "invoice.txt"
    content = "Invoice FACT-2026-SYNTH-DOCUMENT-023 dated 2026-07-14 for consulting services. Total 100.00 EUR."
    path.write_text(content, encoding="utf-8")

    result = inspect_document(path, "income_invoice")

    assert result.status == "extracted"
    assert result.posting_eligible is False
    assert result.structural_errors == ()
    assert result.sha256 == sha256(content.encode()).hexdigest()


def test_image_without_tesseract_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "receipt.png"
    path.write_bytes(b"not-a-real-image")

    result = inspect_document(path, "expense_invoice", tesseract_command="definitely-missing-tesseract")

    assert result.status == "needs_review"
    assert result.posting_eligible is False
    assert "OCR unavailable" in result.structural_errors[0]


@pytest.mark.parametrize("failure", ["timeout", "exec"])
def test_image_ocr_runtime_failure_stays_in_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path = tmp_path / "receipt.png"
    path.write_bytes(b"synthetic-image")
    monkeypatch.setattr(intake_module.shutil, "which", lambda command: "/synthetic/tesseract")

    def fail(command: list[str], **kwargs: object) -> None:
        assert kwargs["timeout"] == 60
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 60)
        raise PermissionError("cannot execute")

    monkeypatch.setattr(intake_module.subprocess, "run", fail)
    result = inspect_document(path, "income_invoice")
    assert result.status == "needs_review"
    assert result.posting_eligible is False
    assert "OCR" in result.structural_errors[0]
    assert ("timed out" if failure == "timeout" else "could not start") in result.structural_errors[0]
    assert path.read_bytes() == b"synthetic-image"


def test_successful_image_ocr_remains_extraction_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "receipt.png"
    path.write_bytes(b"synthetic-image")
    monkeypatch.setattr(intake_module.shutil, "which", lambda command: "/synthetic/tesseract")
    text = "Synthetic invoice dated 2032-04-10 for consulting services. Total 100.00 EUR."

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["timeout"] == 60
        return subprocess.CompletedProcess(command, 0, text, "")

    monkeypatch.setattr(intake_module.subprocess, "run", run)
    result = inspect_document(path, "income_invoice")
    assert result.status == "extracted"
    assert result.extracted_text == text
    assert result.posting_eligible is False


def test_invoice_requires_date_amount_and_enough_text() -> None:
    errors = structural_errors("expense_invoice", "short receipt")
    assert "Invoice date not detected" in errors
    assert "Invoice total not detected" in errors
    assert "too short" in errors[-1]


def test_unsupported_kind_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "document.txt"
    path.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="Unsupported document kind"):
        inspect_document(path, "mystery")


def test_archive_evidence_is_content_addressed_and_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "incoming" / "invoice.pdf"
    source.parent.mkdir()
    source.write_bytes(b"immutable invoice")
    archive = tmp_path / "archive"

    first = archive_evidence(
        source,
        archive,
        period_key="2026-Q3",
        evidence_kind="expense_invoice",
    )
    second = archive_evidence(
        source,
        archive,
        period_key="2026-Q3",
        evidence_kind="expense_invoice",
    )

    assert first == second
    assert first.read_bytes() == source.read_bytes()
    assert first.parent == archive / "2026-Q3" / "expense_invoice"


def test_expense_inbox_cleanup_deletes_only_verified_source(tmp_path: Path) -> None:
    candidate = _cleanup_candidate(tmp_path)

    assert validate_expense_inbox_cleanup(candidate) == "ready"
    assert cleanup_expense_inbox_source(candidate) == "deleted"

    assert not candidate.source_path.exists()
    assert candidate.archive_path.read_bytes() == b"immutable invoice"


def test_expense_inbox_cleanup_is_idempotent_when_source_is_absent(tmp_path: Path) -> None:
    candidate = _cleanup_candidate(tmp_path)
    candidate.source_path.unlink()

    assert validate_expense_inbox_cleanup(candidate) == "already_absent"
    assert cleanup_expense_inbox_source(candidate) == "already_absent"
    assert candidate.archive_path.is_file()


def test_expense_inbox_cleanup_refuses_missing_or_mismatched_evidence(tmp_path: Path) -> None:
    missing_archive = _cleanup_candidate(tmp_path / "missing")
    missing_archive.archive_path.unlink()

    with pytest.raises(InboxCleanupError, match="Evidence archive"):
        cleanup_expense_inbox_source(missing_archive)
    assert missing_archive.source_path.is_file()

    changed_source = _cleanup_candidate(tmp_path / "changed")
    changed_source.source_path.write_bytes(b"changed after intake")

    with pytest.raises(InboxCleanupError, match="Inbox source SHA-256"):
        cleanup_expense_inbox_source(changed_source)
    assert changed_source.source_path.read_bytes() == b"changed after intake"


def test_expense_inbox_cleanup_refuses_paths_outside_expected_directories(
    tmp_path: Path,
) -> None:
    candidate = _cleanup_candidate(tmp_path)
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(candidate.source_path.read_bytes())
    escaped = ExpenseInboxCleanupCandidate(
        source_path=outside,
        archive_path=candidate.archive_path,
        inbox_root=candidate.inbox_root,
        archive_root=candidate.archive_root,
        period_key=candidate.period_key,
        sha256=candidate.sha256,
    )

    with pytest.raises(InboxCleanupError, match="Inbox source must resolve inside"):
        cleanup_expense_inbox_source(escaped)
    assert outside.is_file()

    outside_archive = tmp_path / "outside-archive.pdf"
    outside_archive.write_bytes(candidate.archive_path.read_bytes())
    escaped_archive = ExpenseInboxCleanupCandidate(
        source_path=candidate.source_path,
        archive_path=outside_archive,
        inbox_root=candidate.inbox_root,
        archive_root=candidate.archive_root,
        period_key=candidate.period_key,
        sha256=candidate.sha256,
    )

    with pytest.raises(InboxCleanupError, match="Evidence archive must resolve inside"):
        cleanup_expense_inbox_source(escaped_archive)
    assert candidate.source_path.is_file()
    assert outside_archive.is_file()


def test_expense_inbox_cleanup_rejects_invalid_period_path(tmp_path: Path) -> None:
    candidate = _cleanup_candidate(tmp_path)
    invalid = ExpenseInboxCleanupCandidate(
        source_path=candidate.source_path,
        archive_path=candidate.archive_path,
        inbox_root=candidate.inbox_root,
        archive_root=candidate.archive_root,
        period_key="../outside",
        sha256=candidate.sha256,
    )

    with pytest.raises(InboxCleanupError, match="accounting period is invalid"):
        cleanup_expense_inbox_source(invalid)
    assert candidate.source_path.is_file()
    assert candidate.archive_path.is_file()


def test_expense_inbox_cleanup_refuses_symlink_outside_inbox(tmp_path: Path) -> None:
    candidate = _cleanup_candidate(tmp_path)
    candidate.source_path.unlink()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"immutable invoice")
    try:
        candidate.source_path.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable in this environment")

    with pytest.raises(InboxCleanupError, match="Inbox source must resolve inside"):
        cleanup_expense_inbox_source(candidate)
    assert outside.is_file()


def _cleanup_candidate(root: Path) -> ExpenseInboxCleanupCandidate:
    source = root / "Inbox" / "2026-Q3" / "expense_invoice" / "invoice.pdf"
    archive = (
        root
        / "Evidence"
        / "2026-Q3"
        / "expense_invoice"
        / "digest-invoice.pdf"
    )
    source.parent.mkdir(parents=True)
    archive.parent.mkdir(parents=True)
    content = b"immutable invoice"
    source.write_bytes(content)
    archive.write_bytes(content)
    return ExpenseInboxCleanupCandidate(
        source_path=source,
        archive_path=archive,
        inbox_root=root / "Inbox",
        archive_root=root / "Evidence",
        period_key="2026-Q3",
        sha256=sha256(content).hexdigest(),
    )
