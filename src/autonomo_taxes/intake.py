from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess

from .pdf_text import readable_text


DOCUMENT_KINDS = {
    "income_invoice",
    "expense_invoice",
    "bank_statement",
    "tax_report",
    "other",
}
IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
INVOICE_KINDS = {"income_invoice", "expense_invoice"}


@dataclass(frozen=True)
class IntakeResult:
    source_path: str
    sha256: str
    kind: str
    mime_type: str
    extraction_method: str
    extracted_text: str
    status: str
    structural_errors: tuple[str, ...]
    posting_eligible: bool = False

    @property
    def needs_review(self) -> bool:
        return self.status == "needs_review"


@dataclass(frozen=True)
class ExpenseInboxCleanupCandidate:
    source_path: Path
    archive_path: Path
    inbox_root: Path
    archive_root: Path
    period_key: str
    sha256: str


class InboxCleanupError(ValueError):
    pass


def inspect_document(
    path: Path,
    kind: str,
    *,
    tesseract_command: str = "tesseract",
) -> IntakeResult:
    """Extract document text without making a tax or posting decision."""
    if kind not in DOCUMENT_KINDS:
        raise ValueError(f"Unsupported document kind: {kind}")
    if not path.is_file():
        raise FileNotFoundError(path)

    digest = _sha256(path)
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    suffix = path.suffix.lower()
    text = ""
    method = "unsupported"
    extraction_error: str | None = None

    if suffix == ".pdf":
        text, extraction_error = readable_text(path)
        method = "pdf_text"
    elif suffix in IMAGE_SUFFIXES:
        text, extraction_error = _ocr_image(path, tesseract_command)
        method = "tesseract"
    elif suffix in {".csv", ".txt", ".tsv"}:
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="latin-1")
        method = "plain_text"
    else:
        extraction_error = f"Unsupported file type: {suffix or '<none>'}"

    errors = list(structural_errors(kind, text))
    if extraction_error:
        errors.insert(0, extraction_error)
    status = "extracted" if not errors else "needs_review"
    return IntakeResult(
        source_path=str(path.resolve()),
        sha256=digest,
        kind=kind,
        mime_type=mime_type,
        extraction_method=method,
        extracted_text=text,
        status=status,
        structural_errors=tuple(errors),
        # Extraction can only prepare a review candidate. Posting requires an
        # explicit tax treatment and approval in the canonical ledger.
        posting_eligible=False,
    )


def archive_evidence(
    path: Path,
    archive_root: Path,
    *,
    period_key: str,
    evidence_kind: str,
    digest: str | None = None,
) -> Path:
    """Copy evidence into a content-addressed archive without changing the source."""
    if not path.is_file():
        raise FileNotFoundError(path)
    source_digest = (digest or _sha256(path)).lower()
    target = evidence_archive_path(
        path,
        archive_root,
        period_key=period_key,
        evidence_kind=evidence_kind,
        digest=source_digest,
    )
    target_dir = target.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if _sha256(target).lower() != source_digest:
            raise ValueError(f"Archive collision for {target}")
        return target.resolve()
    shutil.copy2(path, target)
    if _sha256(target).lower() != source_digest:
        target.unlink(missing_ok=True)
        raise IOError(f"Archived evidence hash mismatch for {target}")
    return target.resolve()


def evidence_archive_path(
    path: Path,
    archive_root: Path,
    *,
    period_key: str,
    evidence_kind: str,
    digest: str,
) -> Path:
    """Return the deterministic archive target without creating or copying it."""
    source_digest = digest.strip().lower()
    if len(source_digest) != 64 or any(
        character not in "0123456789abcdef" for character in source_digest
    ):
        raise ValueError("Evidence digest must be a SHA-256 hexadecimal value")
    safe_name = re.sub(r"[^0-9A-Za-z._ -]+", "_", path.name).strip(" ._") or "evidence"
    return (archive_root / period_key / evidence_kind / f"{source_digest[:12]}-{safe_name}").resolve()


def validate_expense_inbox_cleanup(candidate: ExpenseInboxCleanupCandidate) -> str:
    """Validate one expense Inbox cleanup without changing the filesystem."""
    status, _, _ = _validated_expense_cleanup_paths(candidate)
    return status


def cleanup_expense_inbox_source(candidate: ExpenseInboxCleanupCandidate) -> str:
    """Delete one verified expense source while preserving its Evidence copy."""
    status, _, _ = _validated_expense_cleanup_paths(candidate)
    if status == "already_absent":
        return status
    candidate.source_path.unlink()
    return "deleted"


def _validated_expense_cleanup_paths(
    candidate: ExpenseInboxCleanupCandidate,
) -> tuple[str, Path, Path]:
    expected_digest = candidate.sha256.strip().lower()
    if len(expected_digest) != 64 or any(
        character not in "0123456789abcdef" for character in expected_digest
    ):
        raise InboxCleanupError("Stored evidence SHA-256 is invalid")
    if re.fullmatch(r"\d{4}-Q[1-4]", candidate.period_key) is None:
        raise InboxCleanupError("Stored accounting period is invalid for Inbox cleanup")

    expected_source_dir = (
        candidate.inbox_root / candidate.period_key / "expense_invoice"
    )
    expected_archive_dir = (
        candidate.archive_root / candidate.period_key / "expense_invoice"
    )
    source_path = _resolve_cleanup_path(
        candidate.source_path,
        expected_source_dir,
        label="Inbox source",
        require_exists=False,
    )
    archive_path = _resolve_cleanup_path(
        candidate.archive_path,
        expected_archive_dir,
        label="Evidence archive",
        require_exists=True,
    )
    if source_path == archive_path:
        raise InboxCleanupError("Inbox source and Evidence archive resolve to the same file")
    if not archive_path.is_file():
        raise InboxCleanupError("Evidence archive is not a regular file")
    if _cleanup_sha256(archive_path, label="Evidence archive") != expected_digest:
        raise InboxCleanupError("Evidence archive SHA-256 does not match SQLite")

    try:
        source_path.stat()
    except FileNotFoundError:
        return "already_absent", source_path, archive_path
    except OSError as exc:
        raise InboxCleanupError("Inbox source could not be inspected") from exc
    source_path = _resolve_cleanup_path(
        candidate.source_path,
        expected_source_dir,
        label="Inbox source",
        require_exists=True,
    )
    if source_path == archive_path:
        raise InboxCleanupError("Inbox source and Evidence archive resolve to the same file")
    if not source_path.is_file():
        raise InboxCleanupError("Inbox source is not a regular file")
    if _cleanup_sha256(source_path, label="Inbox source") != expected_digest:
        raise InboxCleanupError("Inbox source SHA-256 does not match SQLite")
    return "ready", source_path, archive_path


def _resolve_cleanup_path(
    path: Path,
    expected_dir: Path,
    *,
    label: str,
    require_exists: bool,
) -> Path:
    try:
        resolved_dir = expected_dir.resolve(strict=False)
        resolved_path = path.resolve(strict=require_exists)
        resolved_path.relative_to(resolved_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        raise InboxCleanupError(
            f"{label} must resolve inside its configured period directory"
        ) from exc
    return resolved_path


def _cleanup_sha256(path: Path, *, label: str) -> str:
    try:
        return _sha256(path).lower()
    except OSError as exc:
        raise InboxCleanupError(f"{label} could not be read for SHA-256 verification") from exc


def structural_errors(kind: str, text: str) -> tuple[str, ...]:
    normalized = " ".join(text.split())
    if not normalized:
        return ("No readable text extracted",)
    if kind not in INVOICE_KINDS:
        return ()

    errors: list[str] = []
    if not _contains_date(normalized):
        errors.append("Invoice date not detected")
    if not _contains_amount(normalized):
        errors.append("Invoice total not detected")
    if len(normalized) < 40:
        errors.append("Extracted invoice text is too short for structural review")
    return tuple(errors)


def _contains_date(text: str) -> bool:
    patterns = (
        r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b",
        r"\b\d{1,2}[-/.]\d{1,2}[-/.]20\d{2}\b",
        r"\b(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
        r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|"
        r"Dec(?:ember)?)\s+\d{1,2},?\s+20\d{2}\b",
    )
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def _contains_amount(text: str) -> bool:
    return bool(
        re.search(
            r"(?:EUR|USD|GBP|€|\$|£)\s*\d[\d\s.,]*|\d[\d\s.,]*\s*(?:EUR|USD|GBP|€|\$|£)",
            text,
            re.IGNORECASE,
        )
    )


def _ocr_image(path: Path, command: str) -> tuple[str, str | None]:
    executable = shutil.which(command)
    if not executable:
        return "", f"OCR unavailable: {command} was not found"
    run = subprocess.run(
        [executable, str(path), "stdout", "--psm", "6"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if run.returncode != 0:
        message = run.stderr.strip() or f"exit code {run.returncode}"
        return "", f"OCR failed: {message}"
    return run.stdout, None


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
