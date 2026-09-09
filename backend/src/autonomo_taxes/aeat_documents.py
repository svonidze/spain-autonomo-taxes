from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
import re
from typing import Any

from pypdf import PdfReader

from .filing_evidence import parse_filing_metadata
from .ledger_db import (
    AEAT_CASE_STATUSES,
    AEAT_DOCUMENT_KINDS,
    AEAT_PROCEDURE_KINDS,
    LedgerDB,
)
from .storage_service import register_local_source_replica


MAX_AEAT_PDF_BYTES = 30 * 1024 * 1024


@dataclass(frozen=True)
class AeatDocumentCandidate:
    title: str
    procedure_kind: str
    procedure_code: str | None
    form_code: str | None
    document_kind: str
    status: str
    occurred_at: str
    requested_effective_on: str | None
    primary_reference: str | None
    submission_reference: str | None
    justificante_number: str | None
    verification_code: str | None
    notes: str | None
    source_sha256: str
    source_name: str
    media_type: str


def inspect_aeat_pdf(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.stat().st_size > MAX_AEAT_PDF_BYTES:
        raise ValueError("AEAT PDF exceeds the 30 MB limit")
    with path.open("rb") as source:
        if source.read(5) != b"%PDF-":
            raise ValueError("AEAT evidence must be a PDF file")
    try:
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    except Exception as exc:
        raise ValueError("AEAT PDF could not be read") from exc
    if not text.strip():
        raise ValueError("AEAT PDF does not contain readable text")
    return parse_filing_metadata(text)


def build_aeat_candidate(
    path: Path,
    *,
    title: str | None,
    procedure_kind: str,
    procedure_code: str | None,
    form_code: str | None,
    document_kind: str,
    status: str,
    occurred_at: str | None,
    requested_effective_on: str | None,
    primary_reference: str | None,
    submission_reference: str | None,
    justificante_number: str | None,
    verification_code: str | None,
    notes: str | None,
) -> AeatDocumentCandidate:
    extracted = inspect_aeat_pdf(path)
    form = _confirmed_value("form_code", form_code, extracted.get("form_code"))
    occurred = _confirmed_datetime(occurred_at, extracted.get("filed_on"))
    reference = _confirmed_value(
        "submission_reference", submission_reference, extracted.get("submission_reference")
    )
    justificante = _confirmed_value(
        "justificante_number", justificante_number, extracted.get("justificante_number")
    )
    verification = _confirmed_value(
        "verification_code", verification_code, extracted.get("verification_code"), upper=True
    )
    if document_kind == "submission_receipt":
        missing = [
            name
            for name, value in (
                ("form_code", form),
                ("occurred_at", occurred),
                ("submission_reference", reference),
                ("justificante_number", justificante),
                ("verification_code", verification),
            )
            if not value
        ]
        if missing:
            raise ValueError(
                "AEAT submission receipt is missing required metadata: " + ", ".join(missing)
            )
    if not occurred:
        raise ValueError("AEAT document occurred_at is required")
    if procedure_kind not in AEAT_PROCEDURE_KINDS:
        raise ValueError(f"Unsupported AEAT procedure kind: {procedure_kind}")
    if document_kind not in AEAT_DOCUMENT_KINDS:
        raise ValueError(f"Unsupported AEAT document kind: {document_kind}")
    if status not in AEAT_CASE_STATUSES:
        raise ValueError(f"Unsupported AEAT case status: {status}")
    try:
        datetime.fromisoformat(occurred.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("AEAT document occurred_at must use ISO 8601") from exc
    if form is not None and re.fullmatch(r"\d{3}", form) is None:
        raise ValueError("AEAT form_code must contain exactly three digits")
    if procedure_code and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", procedure_code) is None:
        raise ValueError("AEAT procedure_code contains unsupported characters")
    if requested_effective_on:
        try:
            datetime.strptime(requested_effective_on, "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("AEAT requested_effective_on must use YYYY-MM-DD") from exc
    effective_title = (title or "").strip() or (
        f"Modelo {form} · {procedure_kind}" if form else procedure_kind.replace("_", " ")
    )
    digest = _sha256(path)
    return AeatDocumentCandidate(
        title=effective_title,
        procedure_kind=procedure_kind,
        procedure_code=_optional(procedure_code),
        form_code=form,
        document_kind=document_kind,
        status=status,
        occurred_at=occurred,
        requested_effective_on=_optional(requested_effective_on),
        primary_reference=_optional(primary_reference) or reference,
        submission_reference=reference,
        justificante_number=justificante,
        verification_code=verification,
        notes=_optional(notes),
        source_sha256=digest,
        source_name=path.name,
        media_type="application/pdf",
    )


def record_aeat_document(
    db: LedgerDB,
    path: Path,
    archive_root: Path,
    *,
    actor: str,
    dry_run: bool = False,
    **metadata: Any,
) -> dict[str, Any]:
    candidate = build_aeat_candidate(path, **metadata)
    payload = candidate.__dict__.copy()
    existing = db.connection.execute(
        """
        SELECT ad.*, ac.title, ac.procedure_kind, ac.procedure_code, ac.form_code,
               ac.current_status, ac.requested_effective_on, ac.primary_reference,
               ac.row_version AS case_row_version, initial.status AS document_status
        FROM aeat_documents ad
        JOIN aeat_cases ac ON ac.aeat_case_id = ad.aeat_case_id
        JOIN aeat_case_events initial ON initial.evidence_document_id = ad.aeat_document_id
        WHERE ad.source_hash = ?
        """,
        (candidate.source_sha256,),
    ).fetchone()
    if existing is not None:
        expected = {
            "title": candidate.title,
            "procedure_kind": candidate.procedure_kind,
            "procedure_code": candidate.procedure_code,
            "form_code": candidate.form_code,
            "document_kind": candidate.document_kind,
            "document_status": candidate.status,
            "occurred_at": candidate.occurred_at,
            "requested_effective_on": candidate.requested_effective_on,
            "primary_reference": candidate.primary_reference,
            "submission_reference": candidate.submission_reference,
            "justificante_number": candidate.justificante_number,
            "verification_code": candidate.verification_code,
            "notes": candidate.notes,
        }
        if any(existing[key] != value for key, value in expected.items()):
            raise ValueError("AEAT document already exists with different metadata")
        return {"ok": True, "recorded": True, "idempotent": True, **dict(existing)}
    if dry_run:
        return {"ok": True, "recorded": False, "dry_run": True, "candidate": payload}

    archived_path = archive_aeat_pdf(
        path,
        archive_root,
        occurred_at=candidate.occurred_at,
        document_kind=candidate.document_kind,
        digest=candidate.source_sha256,
    )
    existing_document = db.connection.execute(
        "SELECT * FROM documents WHERE source_hash = ?", (candidate.source_sha256,)
    ).fetchone()
    if existing_document is not None and str(existing_document["document_type"]) != "aeat_official":
        raise ValueError("The same PDF is already registered as a different document type")

    with db.transaction():
        if existing_document is None:
            document = db.upsert_document(
                document_type="aeat_official",
                document_number=candidate.justificante_number or candidate.submission_reference,
                issued_on=candidate.occurred_at[:10],
                currency="EUR",
                lifecycle_status="approved",
                source_hash=candidate.source_sha256,
            )
        else:
            document = dict(existing_document)
        stored = db.set_document_storage(
            str(document["document_id"]),
            source_path=str(archived_path),
            mime_type=candidate.media_type,
            expected_row_version=int(document["row_version"]),
        )
        register_local_source_replica(
            db,
            document_id=str(document["document_id"]),
            source_path=archived_path,
            media_type=candidate.media_type,
            storage_root=archive_root,
        )
        result = db.create_aeat_case_with_document(
            document_id=str(stored["document_id"]),
            source_hash=candidate.source_sha256,
            title=candidate.title,
            procedure_kind=candidate.procedure_kind,
            procedure_code=candidate.procedure_code,
            form_code=candidate.form_code,
            document_kind=candidate.document_kind,
            status=candidate.status,
            occurred_at=candidate.occurred_at,
            requested_effective_on=candidate.requested_effective_on,
            primary_reference=candidate.primary_reference,
            submission_reference=candidate.submission_reference,
            justificante_number=candidate.justificante_number,
            verification_code=candidate.verification_code,
            notes=candidate.notes,
            actor=actor,
        )
    return {
        "ok": True,
        "recorded": True,
        "idempotent": False,
        "source_sha256": candidate.source_sha256,
        "archived_file": archived_path.name,
        **result,
    }


def archive_aeat_pdf(
    path: Path,
    archive_root: Path,
    *,
    occurred_at: str,
    document_kind: str,
    digest: str,
) -> Path:
    year = occurred_at[:4]
    if not year.isdigit():
        raise ValueError("AEAT document year is invalid")
    safe_name = re.sub(r"[^0-9A-Za-z._ -]+", "_", path.name).strip(" ._") or "aeat.pdf"
    target = (
        archive_root.resolve()
        / "aeat"
        / year
        / document_kind
        / f"{digest[:12]}-{safe_name}"
    )
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    if target.exists():
        if _sha256(target) != digest:
            raise ValueError("AEAT archive collision")
        return target.resolve()
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_bytes(path.read_bytes())
    temporary.chmod(0o600)
    if _sha256(temporary) != digest:
        temporary.unlink(missing_ok=True)
        raise IOError("Archived AEAT PDF hash mismatch")
    temporary.replace(target)
    target.chmod(0o600)
    return target.resolve()


def unified_aeat_documents(connection: Any) -> list[dict[str, Any]]:
    events_by_case: dict[str, list[dict[str, Any]]] = {}
    for event in connection.execute(
        """
        SELECT aeat_case_id, status, occurred_at, evidence_reference, notes, actor
        FROM aeat_case_events
        ORDER BY occurred_at, created_at, aeat_case_event_id
        """
    ).fetchall():
        events_by_case.setdefault(str(event["aeat_case_id"]), []).append(dict(event))
    records = [
        {
            **dict(row),
            "record_source": "aeat_document",
            "record_id": row["aeat_document_id"],
            "period_key": None,
            "content_url": f"/api/document/{row['document_id']}/content",
            "status_history": events_by_case.get(str(row["aeat_case_id"]), []),
        }
        for row in connection.execute(
            """
            SELECT ad.aeat_document_id, ad.document_id, ad.document_kind,
                   ad.occurred_at, ad.submission_reference, ad.justificante_number,
                   ad.verification_code, ad.notes, ac.aeat_case_id, ac.title,
                   ac.procedure_kind, ac.procedure_code, ac.form_code,
                   ac.current_status AS status, ac.requested_effective_on,
                   ac.primary_reference, ac.row_version AS case_row_version,
                   CASE WHEN COALESCE(trim(d.source_path), '') <> '' OR EXISTS (
                       SELECT 1
                       FROM document_attachments da
                       JOIN file_replicas fr ON fr.file_id = da.file_id
                       JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
                       WHERE da.document_id = d.document_id
                         AND da.attachment_role = 'source'
                         AND fr.replica_status = 'available'
                         AND sb.enabled = 1
                   ) THEN 1 ELSE 0 END AS source_available
            FROM aeat_documents ad
            JOIN aeat_cases ac ON ac.aeat_case_id = ad.aeat_case_id
            JOIN documents d ON d.document_id = ad.document_id
            ORDER BY ad.occurred_at DESC, ad.created_at DESC
            """
        ).fetchall()
    ]
    filing_rows = connection.execute(
        """
        SELECT fs.filing_snapshot_id, fs.form_code, fs.filed_on,
               fs.submission_reference, fs.justificante_number, fs.verification_code,
               fs.source_reference, fs.created_at, p.period_key
        FROM filing_snapshots fs
        JOIN periods p ON p.period_id = fs.period_id
        WHERE COALESCE(trim(fs.source_reference), '') <> ''
          AND (
              COALESCE(trim(fs.submission_reference), '') <> ''
              OR COALESCE(trim(fs.justificante_number), '') <> ''
              OR COALESCE(trim(fs.verification_code), '') <> ''
          )
        ORDER BY fs.filed_on DESC, fs.created_at DESC
        """
    ).fetchall()
    seen: set[str] = set()
    for row in filing_rows:
        identity = str(row["submission_reference"] or row["justificante_number"] or "").strip()
        if not identity or identity in seen:
            continue
        seen.add(identity)
        records.append(
            {
                "record_source": "filing_snapshot",
                "record_id": row["filing_snapshot_id"],
                "aeat_case_id": None,
                "document_id": None,
                "title": f"Modelo {row['form_code']} · {row['period_key']}",
                "procedure_kind": "periodic_filing",
                "procedure_code": None,
                "form_code": row["form_code"],
                "document_kind": "submission_receipt",
                "status": "submitted",
                "occurred_at": row["filed_on"],
                "requested_effective_on": None,
                "primary_reference": row["submission_reference"],
                "submission_reference": row["submission_reference"],
                "justificante_number": row["justificante_number"],
                "verification_code": row["verification_code"],
                "notes": None,
                "period_key": row["period_key"],
                "case_row_version": None,
                "content_url": f"/api/aeat-filings/{row['filing_snapshot_id']}/content",
                "status_history": [],
                "source_available": True,
            }
        )
    return sorted(records, key=lambda row: str(row.get("occurred_at") or ""), reverse=True)


def _confirmed_value(
    name: str,
    supplied: str | None,
    extracted: str | None,
    *,
    upper: bool = False,
) -> str | None:
    left = _optional(supplied)
    right = _optional(extracted)
    if upper:
        left = left.upper() if left else None
        right = right.upper() if right else None
    if left and right and left != right:
        raise ValueError(f"AEAT PDF {name} conflicts with the supplied value")
    return left or right


def _confirmed_datetime(supplied: str | None, extracted: str | None) -> str | None:
    left = _optional(supplied)
    right = _optional(extracted)
    if left and right:
        try:
            left_value = datetime.fromisoformat(left.replace("Z", "+00:00"))
            right_value = datetime.fromisoformat(right.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("AEAT document occurred_at must use ISO 8601") from exc
        if left_value.replace(tzinfo=None) != right_value.replace(tzinfo=None):
            raise ValueError("AEAT PDF occurred_at conflicts with the supplied value")
    return left or right


def _optional(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
