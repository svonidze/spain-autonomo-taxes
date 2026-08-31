from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterable, Mapping
from types import EllipsisType
from uuid import uuid4

from .fx_policy import ALLOWED_PRODUCTION_SOURCES, XOLO_RECORDED_PRODUCTION_THROUGH
from .counterparty_names import normalize_counterparty_name, validate_counterparty_name
from .outgoing_invoices import (
    calculate_invoice_totals,
    canonical_lines_json,
    invoice_line_hash,
    normalize_invoice_lines,
    parse_template_lines,
    validate_currency,
    validate_withholding_rate,
)
from .tax_rules import ALL_FORM_CODES
from .vat_classification import is_vat_investment_good


LATEST_SCHEMA_VERSION = 23

# Migrations that rebuild a table referenced by a foreign key. They must
# run with foreign-key enforcement temporarily disabled, and that pragma
# only takes effect outside a transaction.
FK_REBUILD_MIGRATIONS = frozenset({19})

# Immutable provenance kinds for fx_rates rows (schema v19).
FX_PROVENANCE_KINDS = {
    "ecb": "official",
    "banco_de_espana": "official",
    "xolo_recorded": "recorded",
    "actual_settlement": "documented_settlement",
    "target_derived": "derived",
}

ACTIVE_LIFECYCLE_STATUSES = (
    "received",
    "extracted",
    "needs_review",
    "approved",
    "posted",
    "included_in_snapshot",
)
TERMINAL_LIFECYCLE_STATUSES = ("duplicate", "rejected", "void")
ALL_LIFECYCLE_STATUSES = ACTIVE_LIFECYCLE_STATUSES + TERMINAL_LIFECYCLE_STATUSES

VALID_LIFECYCLE_TRANSITIONS = {
    "received": {"extracted", "duplicate", "rejected", "void"},
    "extracted": {"needs_review", "approved", "duplicate", "rejected", "void"},
    "needs_review": {"approved", "duplicate", "rejected", "void"},
    "approved": {"posted", "duplicate", "rejected", "void"},
    "posted": {"included_in_snapshot", "void"},
    "included_in_snapshot": set(),
    "duplicate": set(),
    "rejected": set(),
    "void": set(),
}

PERIOD_STATUSES = {"open", "closed", "amended"}
OBLIGATION_STATUSES = {"unknown", "due", "filed", "waived"}
OBLIGATION_DETERMINATIONS = {"unknown", "due", "not_due"}
OBLIGATION_EVIDENCE_KINDS = {
    "aeat_account_check",
    "independent_calculation",
}
IRNR_REVIEW_ISSUE_CODES = {
    "nonresident_payee_legal_form_review",
    "nonresident_professional_irnr_review",
}
ISSUE_STATUSES = {"open", "resolved", "ignored"}
CORRECTION_KINDS = {"reversing", "correcting"}
COUNTERPARTY_LEGAL_FORMS = {
    "unknown",
    "individual",
    "legal_entity",
    "public_body",
}
FINAL_SNAPSHOT_STATUSES = {"filed", "submitted", "final"}


class LedgerDbError(Exception):
    """Base exception for ledger database failures."""


class SchemaVersionError(LedgerDbError):
    """Raised when the database schema version is unsupported."""


class LifecycleError(LedgerDbError):
    """Raised when a lifecycle transition is invalid."""


class StaleRowVersionError(LedgerDbError):
    """Raised when optimistic concurrency checks fail."""


class TaxpayerIdentityLockedError(LedgerDbError):
    """A filed or closed history requires an explicit identity migration."""


class FxRateConflictError(LedgerDbError):
    """Raised when an FX key is reused with conflicting sourced data."""


class BlockingIssueError(LedgerDbError):
    """Raised when validation issues block a period close."""


class ObligationStateError(LedgerDbError):
    """Raised when obligations prevent period closure."""


class ClosedPeriodError(LedgerDbError):
    """Raised when attempting to mutate immutable closed periods."""


class PeriodStateError(LedgerDbError):
    """Raised when a period operation is invalid for the current state."""


@dataclass(frozen=True)
class LedgerPaths:
    database: Path


def initialize(path: str | Path) -> "LedgerDB":
    return LedgerDB.initialize(path)


def open(
    path: str | Path,
    *,
    read_only: bool = False,
    apply_migrations: bool = False,
) -> "LedgerDB":
    return LedgerDB.open(
        path,
        read_only=read_only,
        apply_migrations=apply_migrations,
    )


def open_database(
    path: str | Path,
    *,
    read_only: bool = False,
    apply_migrations: bool = False,
) -> "LedgerDB":
    return LedgerDB.open(
        path,
        read_only=read_only,
        apply_migrations=apply_migrations,
    )


class LedgerDB:
    def __init__(self, connection: sqlite3.Connection, *, path: Path | None = None) -> None:
        self.connection = connection
        self.path = path
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA busy_timeout = 5000")

    @contextmanager
    def transaction(self):
        """Own a write transaction, or isolate this operation inside its caller."""
        nested = self.connection.in_transaction
        savepoint = f"ledger_{uuid4().hex}"
        self.connection.execute(f"SAVEPOINT {savepoint}" if nested else "BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            if nested:
                self.connection.execute(f"ROLLBACK TO {savepoint}")
                self.connection.execute(f"RELEASE {savepoint}")
            else:
                self.connection.rollback()
            raise
        else:
            if nested:
                self.connection.execute(f"RELEASE {savepoint}")
            else:
                self.connection.commit()

    @classmethod
    def initialize(cls, path: str | Path) -> "LedgerDB":
        db_path = Path(path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(db_path)
        database = cls(connection, path=db_path)
        try:
            database._apply_migrations()
        except Exception:
            connection.close()
            raise
        return database

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        read_only: bool = False,
        apply_migrations: bool = False,
    ) -> "LedgerDB":
        db_path = Path(path)
        if read_only:
            uri = f"file:{db_path.as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
        else:
            connection = sqlite3.connect(db_path)
        database = cls(connection, path=db_path)
        current_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version != LATEST_SCHEMA_VERSION:
            if read_only:
                connection.close()
                raise SchemaVersionError(
                    f"Read-only database schema is {current_version}; "
                    f"expected {LATEST_SCHEMA_VERSION}"
                )
            if not apply_migrations:
                connection.close()
                raise SchemaVersionError(
                    f"Database schema is {current_version}; explicit migration to "
                    f"{LATEST_SCHEMA_VERSION} is required (open with apply_migrations=True)"
                )
            try:
                database._apply_migrations()
            except Exception:
                connection.close()
                raise
        return database

    @classmethod
    def migrate(cls, path: str | Path) -> "LedgerDB":
        """Open a database through the sole opt-in schema migration path."""
        return cls.open(path, apply_migrations=True)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "LedgerDB":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def backup_to(self, target_path: str | Path) -> Path:
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        destination = sqlite3.connect(target)
        try:
            self.connection.backup(destination)
        finally:
            destination.close()
        return target

    def restore_from(self, source_path: str | Path) -> None:
        source_file = Path(source_path)
        if not source_file.is_file():
            raise FileNotFoundError(f"SQLite backup does not exist: {source_file}")
        uri = f"file:{source_file.resolve().as_posix()}?mode=ro"
        source = sqlite3.connect(uri, uri=True)
        try:
            integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise LedgerDbError(f"SQLite backup failed integrity_check: {integrity}")
            source_version = int(source.execute("PRAGMA user_version").fetchone()[0])
            if source_version < 1 or source_version > LATEST_SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"Unsupported backup schema version {source_version}; "
                    f"supported range is 1..{LATEST_SCHEMA_VERSION}"
                )
            rollback = sqlite3.connect(":memory:")
            try:
                self.connection.backup(rollback)
                source.backup(self.connection)
                self.connection.execute("PRAGMA foreign_keys = ON")
                self._apply_migrations()
                restored_integrity = self.connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0]
                if restored_integrity != "ok":
                    raise LedgerDbError(
                        f"Restored database failed integrity_check: {restored_integrity}"
                    )
            except Exception:
                self.connection.rollback()
                rollback.backup(self.connection)
                self.connection.execute("PRAGMA foreign_keys = ON")
                raise
            finally:
                rollback.close()
        finally:
            source.close()

    def upsert_taxpayer_profile(
        self,
        *,
        taxpayer_profile_id: str | None = None,
        tax_id: str,
        full_name: str,
        residency_country: str = "ES",
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        tax_id = tax_id.strip().upper()
        full_name = full_name.strip()
        residency_country = residency_country.strip().upper()
        if not tax_id or not full_name:
            raise ValueError("Taxpayer tax_id and full_name are required")
        if len(residency_country) != 2 or not residency_country.isalpha():
            raise ValueError("Taxpayer residency_country must be an ISO alpha-2 code")
        existing = self._fetch_optional("SELECT * FROM taxpayer_profile WHERE tax_id = ?", (tax_id,))
        timestamp = _utc_now()
        payload = {
            "tax_id": tax_id,
            "full_name": full_name,
            "residency_country": residency_country,
        }
        effective_hash = source_hash or _stable_hash(payload)
        if existing is not None:
            desired = {
                "full_name": full_name,
                "residency_country": residency_country,
                "source_hash": effective_hash,
            }
            if all(existing[key] == value for key, value in desired.items()):
                return existing
            if expected_row_version is None:
                raise StaleRowVersionError(
                    "Updating a taxpayer profile requires expected_row_version"
                )
            self._check_row_version(existing, expected_row_version)
        with self.connection:
            if existing is None:
                profile_id = taxpayer_profile_id or _new_id()
                self.connection.execute(
                    """
                    INSERT INTO taxpayer_profile (
                        taxpayer_profile_id, tax_id, full_name, residency_country, source_hash,
                        row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        profile_id,
                        tax_id,
                        full_name,
                        residency_country,
                        effective_hash,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                profile_id = existing["taxpayer_profile_id"]
                self.connection.execute(
                    """
                    UPDATE taxpayer_profile
                    SET full_name = ?, residency_country = ?, source_hash = ?,
                        row_version = ?, updated_at = ?
                    WHERE taxpayer_profile_id = ?
                    """,
                    (
                        full_name,
                        residency_country,
                        effective_hash,
                        existing["row_version"] + 1,
                        timestamp,
                        profile_id,
                    ),
                )
        return self._fetch_one(
            "SELECT * FROM taxpayer_profile WHERE taxpayer_profile_id = ?",
            (profile_id,),
        )

    def taxpayer_identity_locked(self) -> bool:
        return bool(self.connection.execute(
            "SELECT EXISTS(SELECT 1 FROM periods WHERE status IN ('closed', 'amended'))"
            " OR EXISTS(SELECT 1 FROM filing_snapshots)"
        ).fetchone()[0])

    def edit_taxpayer_profile(
        self,
        *,
        taxpayer_profile_id: str | None,
        expected_row_version: int,
        tax_id: str,
        full_name: str,
        residency_country: str,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Edit stable profile identity and record its audit in one transaction."""
        if type(expected_row_version) is not int or expected_row_version < 0:
            raise ValueError("Invalid expected profile version")
        if not all(isinstance(value, str) for value in (tax_id, full_name, residency_country)):
            raise ValueError("Profile fields must be strings")
        if "/" in tax_id or "\\" in tax_id:
            raise ValueError("Tax identifier must not contain path separators")
        payload = {
            "tax_id": validate_counterparty_name(tax_id).upper(),
            "full_name": validate_counterparty_name(full_name),
            "residency_country": residency_country.strip().upper(),
        }
        if not payload["tax_id"] or len(payload["tax_id"]) > 64 or not payload["full_name"] or len(payload["full_name"]) > 200:
            raise ValueError("Invalid profile fields")
        country = payload["residency_country"]
        if len(country) != 2 or not country.isascii() or not country.isalpha():
            raise ValueError("Country must be an alpha-2 code")
        with self.transaction():
            existing = self._fetch_optional(
                "SELECT * FROM taxpayer_profile WHERE taxpayer_profile_id = ?",
                (taxpayer_profile_id,),
            ) if taxpayer_profile_id is not None else None
            if taxpayer_profile_id is None:
                if expected_row_version != 0 or self.connection.execute("SELECT 1 FROM taxpayer_profile LIMIT 1").fetchone():
                    raise StaleRowVersionError("Profile changed; reload before saving")
            elif existing is None or existing["row_version"] != expected_row_version:
                raise StaleRowVersionError("Profile changed; reload before saving")
            if existing and existing["tax_id"] != payload["tax_id"] and self.taxpayer_identity_locked():
                raise TaxpayerIdentityLockedError("Tax identity is locked by accounting history")
            if existing and all(existing[key] == value for key, value in payload.items()):
                return existing
            timestamp = _utc_now()
            profile_id = taxpayer_profile_id or _new_id()
            old_version = existing["row_version"] if existing else 0
            if existing:
                cursor = self.connection.execute(
                    "UPDATE taxpayer_profile SET tax_id=?, full_name=?, residency_country=?,"
                    " source_hash=?, row_version=?, updated_at=?"
                    " WHERE taxpayer_profile_id=? AND row_version=?",
                    (payload["tax_id"], payload["full_name"], country, _stable_hash(payload),
                     old_version + 1, timestamp, profile_id, old_version),
                )
                if cursor.rowcount != 1:
                    raise StaleRowVersionError("Profile changed; reload before saving")
            else:
                self.connection.execute(
                    "INSERT INTO taxpayer_profile (taxpayer_profile_id,tax_id,full_name,"
                    "residency_country,source_hash,row_version,created_at,updated_at)"
                    " VALUES (?,?,?,?,?,1,?,?)",
                    (profile_id, payload["tax_id"], payload["full_name"], country,
                     _stable_hash(payload), timestamp, timestamp),
                )
            old = {key: existing[key] for key in payload} if existing else None
            self.connection.execute(
                "INSERT INTO taxpayer_profile_changes (change_id,taxpayer_profile_id,"
                "old_values_json,new_values_json,actor,changed_at,from_row_version,to_row_version)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (_new_id(), profile_id, json.dumps(old, sort_keys=True),
                 json.dumps(payload, sort_keys=True), actor, timestamp, old_version, old_version + 1),
            )
            return self._fetch_one("SELECT * FROM taxpayer_profile WHERE taxpayer_profile_id=?", (profile_id,))

    def upsert_business_activity(
        self,
        *,
        taxpayer_profile_id: str,
        activity_key: str,
        aeat_activity_code: str,
        aeat_activity_type: str,
        iae_group_epigraph: str,
        description: str,
        starts_on: str,
        iae_section: str | None = None,
        ends_on: str | None = None,
        irpf_method: str = "estimacion_directa_simplificada",
        iva_regime: str = "general",
        source_reference: str,
        business_activity_id: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        activity_key = activity_key.strip()
        aeat_activity_code = aeat_activity_code.strip().upper()
        aeat_activity_type = aeat_activity_type.strip().upper()
        iae_group_epigraph = iae_group_epigraph.strip().upper()
        iae_section = (iae_section or "").strip() or None
        description = description.strip()
        irpf_method = irpf_method.strip()
        iva_regime = iva_regime.strip()
        source_reference = source_reference.strip()
        if not all(
            (
                activity_key,
                aeat_activity_code,
                aeat_activity_type,
                iae_group_epigraph,
                description,
                starts_on,
                irpf_method,
                iva_regime,
                source_reference,
            )
        ):
            raise ValueError("Business activity fields and source_reference are required")
        if aeat_activity_code not in {"A", "B", "C"}:
            raise ValueError("AEAT activity code must be A, B, or C")
        if len(aeat_activity_type) != 2:
            raise ValueError("AEAT activity type must contain two characters")
        if iae_section is not None and iae_section not in {"1", "2", "3"}:
            raise ValueError("IAE section must be 1, 2, or 3")
        start = date.fromisoformat(starts_on)
        end = date.fromisoformat(ends_on) if ends_on else None
        if end is not None and end < start:
            raise ValueError("Business activity ends_on cannot precede starts_on")
        self._fetch_one(
            "SELECT taxpayer_profile_id FROM taxpayer_profile WHERE taxpayer_profile_id = ?",
            (taxpayer_profile_id,),
        )
        existing = self._fetch_optional(
            """
            SELECT * FROM business_activities
            WHERE taxpayer_profile_id = ? AND activity_key = ?
            """,
            (taxpayer_profile_id, activity_key),
        )
        payload = {
            "taxpayer_profile_id": taxpayer_profile_id,
            "activity_key": activity_key,
            "aeat_activity_code": aeat_activity_code,
            "aeat_activity_type": aeat_activity_type,
            "iae_section": iae_section,
            "iae_group_epigraph": iae_group_epigraph,
            "description": description,
            "starts_on": starts_on,
            "ends_on": ends_on,
            "irpf_method": irpf_method,
            "iva_regime": iva_regime,
            "source_reference": source_reference,
        }
        effective_hash = source_hash or _stable_hash(payload)
        desired = {**payload, "source_hash": effective_hash}
        if existing is not None and all(existing[key] == value for key, value in desired.items()):
            return existing
        if existing is not None:
            if expected_row_version is None:
                raise StaleRowVersionError(
                    "Updating a business activity requires expected_row_version"
                )
            self._check_row_version(existing, expected_row_version)
        timestamp = _utc_now()
        with self.connection:
            if existing is None:
                activity_id = business_activity_id or _new_id()
                self.connection.execute(
                    """
                    INSERT INTO business_activities (
                        business_activity_id, taxpayer_profile_id, activity_key,
                        aeat_activity_code, aeat_activity_type, iae_section,
                        iae_group_epigraph, description, starts_on, ends_on,
                        irpf_method, iva_regime, source_reference, source_hash,
                        row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        activity_id,
                        taxpayer_profile_id,
                        activity_key,
                        aeat_activity_code,
                        aeat_activity_type,
                        iae_section,
                        iae_group_epigraph,
                        description,
                        starts_on,
                        ends_on,
                        irpf_method,
                        iva_regime,
                        source_reference,
                        effective_hash,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                activity_id = existing["business_activity_id"]
                self.connection.execute(
                    """
                    UPDATE business_activities
                    SET aeat_activity_code = ?, aeat_activity_type = ?, iae_section = ?,
                        iae_group_epigraph = ?, description = ?, starts_on = ?, ends_on = ?,
                        irpf_method = ?, iva_regime = ?, source_reference = ?, source_hash = ?,
                        row_version = ?, updated_at = ?
                    WHERE business_activity_id = ?
                    """,
                    (
                        aeat_activity_code,
                        aeat_activity_type,
                        iae_section,
                        iae_group_epigraph,
                        description,
                        starts_on,
                        ends_on,
                        irpf_method,
                        iva_regime,
                        source_reference,
                        effective_hash,
                        existing["row_version"] + 1,
                        timestamp,
                        activity_id,
                    ),
                )
        return self._fetch_one(
            "SELECT * FROM business_activities WHERE business_activity_id = ?",
            (activity_id,),
        )

    def list_business_activities(
        self,
        *,
        taxpayer_profile_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM business_activities"
        params: tuple[Any, ...] = ()
        if taxpayer_profile_id is not None:
            sql += " WHERE taxpayer_profile_id = ?"
            params = (taxpayer_profile_id,)
        sql += " ORDER BY starts_on, activity_key"
        return self._fetch_all(sql, params)

    def add_import_batch(
        self,
        *,
        source_name: str,
        source_hash: str,
        batch_key: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        existing = self._fetch_optional("SELECT * FROM import_batches WHERE source_hash = ?", (source_hash,))
        if existing is not None:
            return existing
        timestamp = _utc_now()
        batch_id = _new_id()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO import_batches (
                    import_batch_id, source_name, batch_key, imported_at, notes,
                    source_hash, row_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (batch_id, source_name, batch_key, timestamp, notes, source_hash, timestamp, timestamp),
            )
        return self._fetch_one("SELECT * FROM import_batches WHERE import_batch_id = ?", (batch_id,))

    def ensure_period(
        self,
        period_key: str,
        *,
        starts_on: str | None = None,
        ends_on: str | None = None,
        period_type: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        existing = self._fetch_period(period_key)
        if existing is not None:
            return existing

        with _write_scope(self.connection):
            return self._ensure_period_uncommitted(
                period_key,
                starts_on=starts_on,
                ends_on=ends_on,
                period_type=period_type,
                source_hash=source_hash,
            )

    def _ensure_period_uncommitted(
        self,
        period_key: str,
        *,
        starts_on: str | None = None,
        ends_on: str | None = None,
        period_type: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        existing = self._fetch_period(period_key)
        if existing is not None:
            return existing

        if starts_on is None or ends_on is None:
            starts_on, ends_on = _default_period_bounds(period_key)
        effective_period_type = period_type or (
            "annual" if len(period_key) == 4 and period_key.isdigit() else "quarter"
        )

        timestamp = _utc_now()
        payload = {
            "period_key": period_key,
            "period_type": effective_period_type,
            "starts_on": starts_on,
            "ends_on": ends_on,
            "status": "open",
        }
        self.connection.execute(
            """
            INSERT INTO periods (
                period_id, period_key, period_type, starts_on, ends_on, status,
                source_hash, row_version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """,
            (
                _new_id(),
                period_key,
                effective_period_type,
                starts_on,
                ends_on,
                "open",
                source_hash or _stable_hash(payload),
                timestamp,
                timestamp,
            ),
        )
        return self._require_period(period_key)

    def upsert_counterparty(
        self,
        *,
        counterparty_id: str | None = None,
        external_key: str | None = None,
        tax_id: str | None = None,
        display_name: str,
        country_code: str = "ES",
        email: str | None = None,
        phone: str | None = None,
        legal_form: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        existing = self._find_counterparty(counterparty_id, external_key, tax_id)
        effective_legal_form = (
            legal_form
            if legal_form is not None
            else str(existing["legal_form"]) if existing is not None else "unknown"
        )
        _validate_counterparty_legal_form(effective_legal_form)
        timestamp = _utc_now()
        payload = {
            "external_key": external_key,
            "tax_id": tax_id,
            "display_name": display_name,
            "country_code": country_code,
            "email": email,
            "phone": phone,
            "legal_form": effective_legal_form,
        }
        effective_source_hash = source_hash or _stable_hash(payload)
        with _write_scope(self.connection):
            if existing is None:
                new_id = counterparty_id or _new_id()
                self.connection.execute(
                    """
                    INSERT INTO counterparties (
                        counterparty_id, external_key, tax_id, display_name, country_code, email, phone,
                        legal_form, source_hash, row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        new_id,
                        external_key,
                        tax_id,
                        display_name,
                        country_code,
                        email,
                        phone,
                        effective_legal_form,
                        effective_source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                return self._fetch_one("SELECT * FROM counterparties WHERE counterparty_id = ?", (new_id,))

            self._check_row_version(existing, expected_row_version)
            self.connection.execute(
                """
                UPDATE counterparties
                SET external_key = COALESCE(:external_key, external_key),
                    tax_id = COALESCE(:tax_id, tax_id),
                    display_name = CASE WHEN name_is_manual = 1 THEN display_name ELSE :display_name END,
                    country_code = :country_code, email = :email, phone = :phone,
                    legal_form = :legal_form, source_hash = :source_hash,
                    row_version = row_version + 1, updated_at = :timestamp
                WHERE counterparty_id = :counterparty_id
                  AND (:expected_version IS NULL OR row_version = :expected_version)
                  AND (
                    external_key IS NOT COALESCE(:external_key, external_key)
                    OR tax_id IS NOT COALESCE(:tax_id, tax_id)
                    OR (name_is_manual = 0 AND display_name IS NOT :display_name)
                    OR country_code IS NOT :country_code OR email IS NOT :email
                    OR phone IS NOT :phone OR legal_form IS NOT :legal_form
                    OR source_hash IS NOT :source_hash
                  )
                """,
                {**payload, "source_hash": effective_source_hash, "timestamp": timestamp,
                 "counterparty_id": existing["counterparty_id"],
                 "expected_version": expected_row_version},
            )
            if expected_row_version is not None and self.connection.execute(
                "SELECT changes()"
            ).fetchone()[0] == 0:
                self._check_row_version(self._fetch_one(
                    "SELECT * FROM counterparties WHERE counterparty_id = ?",
                    (existing["counterparty_id"],),
                ), expected_row_version)
            return self._fetch_one(
                "SELECT * FROM counterparties WHERE counterparty_id = ?",
                (existing["counterparty_id"],),
            )

    def rename_counterparty(
        self, counterparty_id: str, *, display_name: str,
        expected_row_version: int, change_source: str, actor: str | None,
    ) -> dict[str, Any]:
        name = validate_counterparty_name(display_name)
        if type(expected_row_version) is not int or expected_row_version < 1:
            raise ValueError("expected_row_version must be a positive integer")
        with self.transaction():
            existing = self._fetch_one(
                "SELECT * FROM counterparties WHERE counterparty_id = ?", (counterparty_id,),
            )
            self._check_row_version(existing, expected_row_version)
            if name == existing["display_name"]:
                return existing
            self.connection.execute(
                """UPDATE counterparties
                   SET display_name = ?, name_is_manual = 1,
                       row_version = row_version + 1, updated_at = ?
                   WHERE counterparty_id = ? AND row_version = ?""",
                (name, _utc_now(), counterparty_id, expected_row_version),
            )
            updated = self._fetch_one(
                "SELECT * FROM counterparties WHERE counterparty_id = ?", (counterparty_id,),
            )
            self._record_counterparty_name_change(existing, updated, change_source, actor)
            return updated

    def counterparty_name_history(self, counterparty_id: str) -> list[dict[str, Any]]:
        self._fetch_one(
            "SELECT counterparty_id FROM counterparties WHERE counterparty_id = ?", (counterparty_id,),
        )
        return self._fetch_all(
            """SELECT change_id, old_name, new_name, changed_at, change_source, actor,
                      from_row_version, to_row_version
               FROM counterparty_name_changes WHERE counterparty_id = ?
               ORDER BY to_row_version DESC""", (counterparty_id,),
        )

    def _record_counterparty_name_change(
        self, before: Mapping[str, Any], after: Mapping[str, Any],
        change_source: str, actor: str | None,
    ) -> None:
        self.connection.execute(
            """INSERT INTO counterparty_name_changes (
                change_id, counterparty_id, old_name, new_name,
                old_name_normalized, new_name_normalized, changed_at, change_source,
                actor, from_row_version, to_row_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_new_id(), before["counterparty_id"], before["display_name"], after["display_name"],
             normalize_counterparty_name(before["display_name"]),
             normalize_counterparty_name(after["display_name"]),
             after["updated_at"], change_source, actor, before["row_version"], after["row_version"]),
        )

    def set_counterparty_tax_profile(
        self, counterparty_id: str, *, vat_id: str | None = None,
        roi_status: str = "unknown", professional_supplier: bool | None = None,
        retention_expected: bool | None = None, legal_form: str | None = None,
        expected_row_version: int | None = None, source_hash: str | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            return self._set_counterparty_tax_profile(
                counterparty_id, vat_id=vat_id, roi_status=roi_status,
                professional_supplier=professional_supplier, retention_expected=retention_expected,
                legal_form=legal_form, expected_row_version=expected_row_version, source_hash=source_hash,
            )

    def _set_counterparty_tax_profile(
        self,
        counterparty_id: str,
        *,
        vat_id: str | None = None,
        roi_status: str = "unknown",
        professional_supplier: bool | None = None,
        retention_expected: bool | None = None,
        legal_form: str | None = None,
        expected_row_version: int | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        if roi_status not in {"unknown", "registered", "not_registered"}:
            raise ValueError(f"Unsupported ROI status: {roi_status}")
        existing = self._fetch_one(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (counterparty_id,),
        )
        effective_legal_form = (
            legal_form if legal_form is not None else str(existing["legal_form"])
        )
        _validate_counterparty_legal_form(effective_legal_form)
        self._check_row_version(existing, expected_row_version)
        timestamp = _utc_now()
        payload = {
            "vat_id": vat_id,
            "roi_status": roi_status,
            "professional_supplier": professional_supplier,
            "retention_expected": retention_expected,
            "legal_form": effective_legal_form,
        }
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE counterparties
                SET vat_id = ?, roi_status = ?, professional_supplier = ?, retention_expected = ?,
                    legal_form = ?, source_hash = ?, row_version = row_version + 1, updated_at = ?
                WHERE counterparty_id = ? AND row_version = ?
                """,
                (
                    vat_id,
                    roi_status,
                    _optional_bool_int(professional_supplier),
                    _optional_bool_int(retention_expected),
                    effective_legal_form,
                    source_hash or _stable_hash(payload),
                    timestamp,
                    counterparty_id,
                    existing["row_version"],
                ),
            )
            updated = self._fetch_one(
                "SELECT * FROM counterparties WHERE counterparty_id = ?",
                (counterparty_id,),
            )
            classification_changed = any(
                (
                    _optional_bool_int(professional_supplier)
                    != existing["professional_supplier"],
                    effective_legal_form != existing["legal_form"],
                )
            )
            if classification_changed:
                self._refresh_future_irnr_reviews_for_counterparty(counterparty_id)
        return updated

    def upsert_counterparty_identity(
        self,
        *,
        counterparty_id: str,
        identity_kind: str,
        country_code: str,
        identifier: str,
        source_reference: str,
        source_hash: str,
        is_primary: bool = True,
        counterparty_identity_id: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        aeat_types = {
            "vat_id": "02",
            "passport": "03",
            "official_id": "04",
            "residence_certificate": "05",
            "other_proof": "06",
        }
        identity_kind = identity_kind.strip().lower()
        country_code = country_code.strip().upper()
        identifier = identifier.strip()
        source_reference = source_reference.strip()
        source_hash = source_hash.strip().lower()
        if identity_kind not in aeat_types:
            raise ValueError(f"Unsupported counterparty identity kind: {identity_kind}")
        if len(country_code) != 2 or not country_code.isalpha():
            raise ValueError("Counterparty identity country_code must be an ISO alpha-2 code")
        if not identifier or len(identifier) > 20:
            raise ValueError("Counterparty identity identifier must contain 1 to 20 characters")
        if not source_reference or not source_hash:
            raise ValueError("Counterparty identity requires source_reference and source_hash")
        self._fetch_one(
            "SELECT counterparty_id FROM counterparties WHERE counterparty_id = ?",
            (counterparty_id,),
        )
        aeat_id_type = aeat_types[identity_kind]
        existing = self._fetch_optional(
            """
            SELECT * FROM counterparty_identities
            WHERE counterparty_identity_id = ?
               OR (counterparty_id = ? AND aeat_id_type = ? AND country_code = ? AND identifier = ?)
            ORDER BY counterparty_identity_id = ? DESC
            LIMIT 1
            """,
            (
                counterparty_identity_id,
                counterparty_id,
                aeat_id_type,
                country_code,
                identifier,
                counterparty_identity_id,
            ),
        )
        desired = {
            "counterparty_id": counterparty_id,
            "identity_kind": identity_kind,
            "aeat_id_type": aeat_id_type,
            "country_code": country_code,
            "identifier": identifier,
            "is_primary": int(is_primary),
            "source_reference": source_reference,
            "source_hash": source_hash,
        }
        if existing is not None and all(existing[key] == value for key, value in desired.items()):
            return existing
        if existing is not None:
            if expected_row_version is None:
                raise StaleRowVersionError(
                    "Updating a counterparty identity requires expected_row_version"
                )
            self._check_row_version(existing, expected_row_version)
            conflict = self._fetch_optional(
                """
                SELECT counterparty_identity_id
                FROM counterparty_identities
                WHERE counterparty_id = ? AND aeat_id_type = ?
                  AND country_code = ? AND identifier = ?
                  AND counterparty_identity_id <> ?
                """,
                (
                    counterparty_id,
                    aeat_id_type,
                    country_code,
                    identifier,
                    existing["counterparty_identity_id"],
                ),
            )
            if conflict is not None:
                raise ValueError(
                    "Counterparty identity duplicates existing source-backed identity"
                )
        primary = self._fetch_optional(
            """
            SELECT counterparty_identity_id
            FROM counterparty_identities
            WHERE counterparty_id = ? AND is_primary = 1
            """,
            (counterparty_id,),
        )
        if is_primary and primary is not None and (
            existing is None
            or primary["counterparty_identity_id"] != existing["counterparty_identity_id"]
        ):
            if existing is None:
                raced = self._fetch_optional(
                    """
                    SELECT * FROM counterparty_identities
                    WHERE counterparty_id = ? AND aeat_id_type = ?
                      AND country_code = ? AND identifier = ?
                    """,
                    (counterparty_id, aeat_id_type, country_code, identifier),
                )
                if raced is not None and all(
                    raced[key] == value for key, value in desired.items()
                ):
                    return raced
            raise ValueError("Counterparty already has a primary source-backed identity")
        if not is_primary and primary is None:
            raise ValueError(
                "A secondary counterparty identity requires an existing primary identity"
            )
        if (
            not is_primary
            and existing is not None
            and existing["is_primary"] == 1
            and primary is not None
            and primary["counterparty_identity_id"] == existing["counterparty_identity_id"]
        ):
            raise ValueError("The only primary counterparty identity cannot be demoted")
        timestamp = _utc_now()
        with self.connection:
            if existing is None:
                identity_id = counterparty_identity_id or _new_id()
                cursor = self.connection.execute(
                    """
                    INSERT OR IGNORE INTO counterparty_identities (
                        counterparty_identity_id, counterparty_id, identity_kind,
                        aeat_id_type, country_code, identifier, is_primary,
                        source_reference, source_hash, row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        identity_id,
                        counterparty_id,
                        identity_kind,
                        aeat_id_type,
                        country_code,
                        identifier,
                        int(is_primary),
                        source_reference,
                        source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                if cursor.rowcount == 0:
                    raced = self._fetch_optional(
                        """
                        SELECT * FROM counterparty_identities
                        WHERE counterparty_identity_id = ?
                           OR (counterparty_id = ? AND aeat_id_type = ?
                               AND country_code = ? AND identifier = ?)
                        ORDER BY counterparty_identity_id = ? DESC
                        LIMIT 1
                        """,
                        (
                            counterparty_identity_id,
                            counterparty_id,
                            aeat_id_type,
                            country_code,
                            identifier,
                            counterparty_identity_id,
                        ),
                    )
                    if raced is not None and all(
                        raced[key] == value for key, value in desired.items()
                    ):
                        return raced
                    raise ValueError(
                        "Concurrent counterparty identity insert conflicts with existing evidence"
                    )
            else:
                identity_id = existing["counterparty_identity_id"]
                cursor = self.connection.execute(
                    """
                    UPDATE counterparty_identities
                    SET counterparty_id = ?, identity_kind = ?, aeat_id_type = ?,
                        country_code = ?, identifier = ?, is_primary = ?,
                        source_reference = ?, source_hash = ?, row_version = ?, updated_at = ?
                    WHERE counterparty_identity_id = ? AND row_version = ?
                    """,
                    (
                        counterparty_id,
                        identity_kind,
                        aeat_id_type,
                        country_code,
                        identifier,
                        int(is_primary),
                        source_reference,
                        source_hash,
                        existing["row_version"] + 1,
                        timestamp,
                        identity_id,
                        existing["row_version"],
                    ),
                )
                if cursor.rowcount != 1:
                    raise StaleRowVersionError(
                        "Counterparty identity changed during update"
                    )
        return self._fetch_one(
            "SELECT * FROM counterparty_identities WHERE counterparty_identity_id = ?",
            (identity_id,),
        )

    def list_counterparty_identities(
        self,
        *,
        counterparty_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM counterparty_identities"
        params: tuple[Any, ...] = ()
        if counterparty_id is not None:
            sql += " WHERE counterparty_id = ?"
            params = (counterparty_id,)
        sql += " ORDER BY counterparty_id, is_primary DESC, identity_kind, identifier"
        return self._fetch_all(sql, params)

    def update_counterparty_review(
        self, counterparty_id: str, *, display_name: str, tax_id: str | None,
        country_code: str, vat_id: str | None, roi_status: str,
        professional_supplier: bool | None, retention_expected: bool | None,
        email: str | None, phone: str | None, legal_form: str | None = None,
        reviewed_from: str = "sheet", expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        with self.transaction():
            return self._update_counterparty_review(
                counterparty_id, display_name=display_name, tax_id=tax_id,
                country_code=country_code, vat_id=vat_id, roi_status=roi_status,
                professional_supplier=professional_supplier, retention_expected=retention_expected,
                email=email, phone=phone, legal_form=legal_form, reviewed_from=reviewed_from,
                expected_row_version=expected_row_version,
            )

    def _update_counterparty_review(
        self,
        counterparty_id: str,
        *,
        display_name: str,
        tax_id: str | None,
        country_code: str,
        vat_id: str | None,
        roi_status: str,
        professional_supplier: bool | None,
        retention_expected: bool | None,
        email: str | None,
        phone: str | None,
        legal_form: str | None = None,
        reviewed_from: str = "sheet",
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        display_name = validate_counterparty_name(display_name)
        country_code = country_code.strip().upper()
        if not display_name:
            raise ValueError("Counterparty display_name is required")
        if len(country_code) != 2:
            raise ValueError("Counterparty country_code must be a two-letter code")
        if roi_status not in {"unknown", "registered", "not_registered"}:
            raise ValueError(f"Unsupported ROI status: {roi_status}")
        existing = self._fetch_one(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (counterparty_id,),
        )
        effective_legal_form = (
            legal_form if legal_form is not None else str(existing["legal_form"])
        )
        _validate_counterparty_legal_form(effective_legal_form)
        self._check_row_version(existing, expected_row_version)
        payload = {
            "display_name": display_name,
            "tax_id": tax_id,
            "country_code": country_code,
            "vat_id": vat_id,
            "roi_status": roi_status,
            "professional_supplier": professional_supplier,
            "retention_expected": retention_expected,
            "email": email,
            "phone": phone,
            "legal_form": effective_legal_form,
            "reviewed_from": reviewed_from,
        }
        name_changed = display_name != existing["display_name"]
        if all(existing[key] == value for key, value in payload.items()
               if key not in {"display_name", "reviewed_from"}):
            return self.rename_counterparty(
                counterparty_id, display_name=display_name,
                expected_row_version=existing["row_version"],
                change_source=reviewed_from, actor=None,
            )
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE counterparties
                SET display_name = ?, tax_id = ?, country_code = ?, vat_id = ?, roi_status = ?,
                    professional_supplier = ?, retention_expected = ?, email = ?, phone = ?,
                    legal_form = ?, source_hash = ?, row_version = row_version + 1, updated_at = ?,
                    name_is_manual = CASE WHEN ? THEN 1 ELSE name_is_manual END
                WHERE counterparty_id = ? AND row_version = ?
                """,
                (
                    display_name,
                    tax_id,
                    country_code,
                    vat_id,
                    roi_status,
                    _optional_bool_int(professional_supplier),
                    _optional_bool_int(retention_expected),
                    email,
                    phone,
                    effective_legal_form,
                    _stable_hash(payload),
                    _utc_now(),
                    name_changed,
                    counterparty_id,
                    existing["row_version"],
                ),
            )
            updated = self._fetch_one(
                "SELECT * FROM counterparties WHERE counterparty_id = ?",
                (counterparty_id,),
            )
            if name_changed:
                self._record_counterparty_name_change(existing, updated, reviewed_from, None)
            classification_changed = any(
                (
                    country_code != existing["country_code"],
                    _optional_bool_int(professional_supplier)
                    != existing["professional_supplier"],
                    effective_legal_form != existing["legal_form"],
                )
            )
            if classification_changed:
                self._refresh_future_irnr_reviews_for_counterparty(counterparty_id)
        return updated

    def upsert_document(
        self,
        *,
        document_id: str | None = None,
        external_key: str | None = None,
        counterparty_id: str | None = None,
        import_batch_id: str | None = None,
        document_type: str,
        document_number: str | None = None,
        issued_on: str,
        period_key: str | None = None,
        currency: str = "EUR",
        total_minor: int | None = None,
        lifecycle_status: str = "received",
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        _validate_lifecycle_status(lifecycle_status)
        period_id = self.ensure_period(period_key)["period_id"] if period_key else None
        existing = self._find_document(document_id, external_key, source_hash)
        timestamp = _utc_now()
        payload = {
            "external_key": external_key,
            "counterparty_id": counterparty_id,
            "import_batch_id": import_batch_id,
            "document_type": document_type,
            "document_number": document_number,
            "issued_on": issued_on,
            "period_id": period_id,
            "currency": currency,
            "total_minor": total_minor,
            "lifecycle_status": lifecycle_status,
        }
        desired_source_hash = source_hash or _stable_hash(payload)

        if existing is not None:
            self._check_row_version(existing, expected_row_version)
            desired = {
                "external_key": external_key if external_key is not None else existing["external_key"],
                "counterparty_id": counterparty_id,
                "import_batch_id": import_batch_id,
                "document_type": document_type,
                "document_number": document_number,
                "issued_on": issued_on,
                "period_id": period_id,
                "currency": currency,
                "total_minor": total_minor,
                "lifecycle_status": lifecycle_status,
                "source_hash": desired_source_hash,
            }
            if _terminal_update_is_noop(existing, desired):
                return existing

        with _write_scope(self.connection):
            if existing is None:
                if period_id:
                    self._assert_period_mutable(period_id)
                new_id = document_id or _new_id()
                self.connection.execute(
                    """
                    INSERT INTO documents (
                        document_id, external_key, counterparty_id, import_batch_id, document_type,
                        document_number, issued_on, period_id, currency, total_minor, lifecycle_status,
                        source_hash, row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        new_id,
                        external_key,
                        counterparty_id,
                        import_batch_id,
                        document_type,
                        document_number,
                        issued_on,
                        period_id,
                        currency,
                        total_minor,
                        lifecycle_status,
                        desired_source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                return self._fetch_one("SELECT * FROM documents WHERE document_id = ?", (new_id,))

            if existing["period_id"]:
                self._assert_period_mutable(existing["period_id"])
            if period_id:
                self._assert_period_mutable(period_id)
            _validate_lifecycle_transition(existing["lifecycle_status"], lifecycle_status)
            next_version = existing["row_version"] + 1
            self.connection.execute(
                """
                UPDATE documents
                SET external_key = ?, counterparty_id = ?, import_batch_id = ?, document_type = ?,
                    document_number = ?, issued_on = ?, period_id = ?, currency = ?, total_minor = ?,
                    lifecycle_status = ?, source_hash = ?, row_version = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (
                    external_key if external_key is not None else existing["external_key"],
                    counterparty_id,
                    import_batch_id,
                    document_type,
                    document_number,
                    issued_on,
                    period_id,
                    currency,
                    total_minor,
                    lifecycle_status,
                    desired_source_hash,
                    next_version,
                    timestamp,
                    existing["document_id"],
                ),
            )
        return self._fetch_one("SELECT * FROM documents WHERE document_id = ?", (existing["document_id"],))

    def set_document_storage(
        self,
        document_id: str,
        *,
        source_path: str | None = None,
        drive_file_id: str | None = None,
        mime_type: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        existing = self._fetch_one("SELECT * FROM documents WHERE document_id = ?", (document_id,))
        self._check_row_version(existing, expected_row_version)
        if _terminal_update_is_noop(
            existing,
            {
                "source_path": source_path,
                "drive_file_id": drive_file_id,
                "mime_type": mime_type,
            },
        ):
            return existing
        if existing["period_id"]:
            self._assert_period_mutable(existing["period_id"])
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE documents
                SET source_path = ?, drive_file_id = ?, mime_type = ?, row_version = ?, updated_at = ?
                WHERE document_id = ?
                """,
                (
                    source_path,
                    drive_file_id,
                    mime_type,
                    existing["row_version"] + 1,
                    timestamp,
                    document_id,
                ),
            )
        return self._fetch_one("SELECT * FROM documents WHERE document_id = ?", (document_id,))

    def upsert_file(
        self,
        *,
        content_sha256: str,
        byte_size: int,
        media_type: str,
        file_id: str | None = None,
    ) -> dict[str, Any]:
        """Register immutable content, deduplicating only identical byte streams."""
        digest = _normalize_sha256(content_sha256)
        if byte_size < 0:
            raise ValueError("byte_size must be non-negative")
        if not media_type.strip():
            raise ValueError("media_type must not be empty")
        existing = self._fetch_optional(
            "SELECT * FROM files WHERE content_sha256 = ?", (digest,)
        )
        if existing is not None:
            if existing["byte_size"] != byte_size:
                raise LedgerDbError(
                    "Existing file content identity has different byte_size"
                )
            return existing
        timestamp = _utc_now()
        new_id = file_id or _new_id()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                INSERT INTO files (file_id, content_sha256, byte_size, media_type, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (new_id, digest, byte_size, media_type, timestamp),
            )
        return self._fetch_one("SELECT * FROM files WHERE file_id = ?", (new_id,))

    def upsert_storage_backend(
        self,
        *,
        backend_key: str,
        display_name: str,
        driver_key: str,
        provider_key: str,
        access_mode: str,
        config: Mapping[str, Any] | None = None,
        credential_ref: str | None = None,
        read_priority: int = 100,
        enabled: bool = True,
        storage_backend_id: str | None = None,
    ) -> dict[str, Any]:
        """Create or update a configured storage endpoint without storing credentials."""
        if not backend_key.strip() or not display_name.strip():
            raise ValueError("backend_key and display_name must not be empty")
        if not driver_key.strip() or not provider_key.strip():
            raise ValueError("driver_key and provider_key must not be empty")
        if access_mode not in {"read_only", "read_write"}:
            raise ValueError("access_mode must be read_only or read_write")
        config_json = _storage_config_json(config)
        existing = self._fetch_optional(
            "SELECT * FROM storage_backends WHERE backend_key = ?", (backend_key,)
        )
        timestamp = _utc_now()
        if existing is None:
            new_id = storage_backend_id or _new_id()
            with _write_scope(self.connection):
                self.connection.execute(
                    """
                    INSERT INTO storage_backends (
                        storage_backend_id, backend_key, display_name, driver_key, provider_key,
                        access_mode, config_json, credential_ref, read_priority, enabled,
                        row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        new_id, backend_key, display_name, driver_key, provider_key, access_mode,
                        config_json, credential_ref, read_priority, int(enabled), timestamp, timestamp,
                    ),
                )
            return self._fetch_one(
                "SELECT * FROM storage_backends WHERE storage_backend_id = ?", (new_id,)
            )

        if (
            existing["display_name"] == display_name
            and existing["driver_key"] == driver_key
            and existing["provider_key"] == provider_key
            and existing["access_mode"] == access_mode
            and existing["config_json"] == config_json
            and existing["credential_ref"] == credential_ref
            and existing["read_priority"] == read_priority
            and existing["enabled"] == int(enabled)
        ):
            return existing

        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE storage_backends
                SET display_name = ?, driver_key = ?, provider_key = ?, access_mode = ?,
                    config_json = ?, credential_ref = ?, read_priority = ?, enabled = ?,
                    row_version = ?, updated_at = ?
                WHERE storage_backend_id = ?
                """,
                (
                    display_name, driver_key, provider_key, access_mode, config_json, credential_ref,
                    read_priority, int(enabled), existing["row_version"] + 1, timestamp,
                    existing["storage_backend_id"],
                ),
            )
        return self._fetch_one(
            "SELECT * FROM storage_backends WHERE storage_backend_id = ?",
            (existing["storage_backend_id"],),
        )

    def attach_file_to_document(
        self,
        *,
        document_id: str,
        file_id: str,
        attachment_role: str,
        display_name: str | None = None,
        document_attachment_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach content metadata without changing the business document aggregate."""
        if attachment_role not in {"source", "supporting", "generated"}:
            raise ValueError("attachment_role must be source, supporting, or generated")
        self._fetch_one("SELECT document_id FROM documents WHERE document_id = ?", (document_id,))
        self._fetch_one("SELECT file_id FROM files WHERE file_id = ?", (file_id,))
        existing = self._fetch_optional(
            """
            SELECT * FROM document_attachments
            WHERE document_id = ? AND file_id = ? AND attachment_role = ?
            """,
            (document_id, file_id, attachment_role),
        )
        if existing is not None:
            return existing
        new_id = document_attachment_id or _new_id()
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                INSERT INTO document_attachments (
                    document_attachment_id, document_id, file_id, attachment_role, display_name,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (new_id, document_id, file_id, attachment_role, display_name, timestamp),
            )
        return self._fetch_one(
            "SELECT * FROM document_attachments WHERE document_attachment_id = ?", (new_id,)
        )

    def register_file_replica(
        self,
        *,
        file_id: str,
        storage_backend_id: str,
        provider_locator: str,
        provider_version: str | None = None,
        replica_status: str = "available",
        is_primary: bool = False,
        web_url: str | None = None,
        provider_metadata: Mapping[str, Any] | None = None,
        last_verified_at: str | None = None,
        file_replica_id: str | None = None,
    ) -> dict[str, Any]:
        """Register a physical copy; callers may mark it available only after verification."""
        if replica_status not in {"available", "missing", "corrupt", "retired"}:
            raise ValueError("Unknown replica_status")
        if not provider_locator.strip():
            raise ValueError("provider_locator must not be empty")
        if replica_status == "available" and not last_verified_at:
            raise ValueError("available replicas require last_verified_at")
        if is_primary and replica_status != "available":
            raise ValueError("Only available replicas may be primary")
        self._fetch_one("SELECT file_id FROM files WHERE file_id = ?", (file_id,))
        self._fetch_one(
            "SELECT storage_backend_id FROM storage_backends WHERE storage_backend_id = ?",
            (storage_backend_id,),
        )
        metadata_json = _storage_config_json(provider_metadata)
        existing = self._fetch_optional(
            "SELECT * FROM file_replicas WHERE file_id = ? AND storage_backend_id = ?",
            (file_id, storage_backend_id),
        )
        timestamp = _utc_now()
        with _write_scope(self.connection):
            if is_primary:
                self.connection.execute(
                    "UPDATE file_replicas SET is_primary = 0, row_version = row_version + 1, updated_at = ? "
                    "WHERE file_id = ? AND is_primary = 1",
                    (timestamp, file_id),
                )
            if existing is None:
                new_id = file_replica_id or _new_id()
                self.connection.execute(
                    """
                    INSERT INTO file_replicas (
                        file_replica_id, file_id, storage_backend_id, provider_locator,
                        provider_version, replica_status, is_primary, web_url,
                        provider_metadata_json, last_verified_at, row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        new_id, file_id, storage_backend_id, provider_locator, provider_version,
                        replica_status, int(is_primary), web_url, metadata_json, last_verified_at,
                        timestamp, timestamp,
                    ),
                )
            else:
                new_id = existing["file_replica_id"]
                self.connection.execute(
                    """
                    UPDATE file_replicas
                    SET provider_locator = ?, provider_version = ?, replica_status = ?,
                        is_primary = ?, web_url = ?, provider_metadata_json = ?,
                        last_verified_at = ?, row_version = ?, updated_at = ?
                    WHERE file_replica_id = ?
                    """,
                    (
                        provider_locator, provider_version, replica_status, int(is_primary), web_url,
                        metadata_json, last_verified_at, existing["row_version"] + 1,
                        timestamp, new_id,
                    ),
                )
        return self._fetch_one("SELECT * FROM file_replicas WHERE file_replica_id = ?", (new_id,))

    def promote_file_replica(
        self,
        file_replica_id: str,
        *,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        """Atomically make one already-verified physical copy the file's primary copy.

        Primary selection belongs to a concrete replica, not to a storage backend name.
        The selected replica must still be available and carry a verification timestamp.
        """
        replica = self._fetch_one(
            "SELECT * FROM file_replicas WHERE file_replica_id = ?", (file_replica_id,)
        )
        self._check_row_version(replica, expected_row_version)
        if replica["replica_status"] != "available" or not replica["last_verified_at"]:
            raise LedgerDbError("Only an available, verified replica may be promoted")

        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE file_replicas
                SET is_primary = 0, row_version = row_version + 1, updated_at = ?
                WHERE file_id = ? AND is_primary = 1 AND file_replica_id != ?
                """,
                (timestamp, replica["file_id"], file_replica_id),
            )
            self.connection.execute(
                """
                UPDATE file_replicas
                SET is_primary = 1, row_version = row_version + 1, updated_at = ?
                WHERE file_replica_id = ?
                """,
                (timestamp, file_replica_id),
            )
        return self._fetch_one(
            "SELECT * FROM file_replicas WHERE file_replica_id = ?", (file_replica_id,)
        )

    def retire_file_replica(
        self,
        file_replica_id: str,
        *,
        retirement_reason: str,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        """Retire a non-primary copy while preserving its provenance for audit.

        A caller must promote another verified copy before retiring a primary one.  This
        prevents a cleanup operation from silently removing the file's selected copy.
        """
        reason = retirement_reason.strip()
        if not reason:
            raise ValueError("retirement_reason must not be empty")
        replica = self._fetch_one(
            "SELECT * FROM file_replicas WHERE file_replica_id = ?", (file_replica_id,)
        )
        self._check_row_version(replica, expected_row_version)
        if replica["is_primary"]:
            raise LedgerDbError("Promote another verified replica before retiring a primary replica")

        try:
            metadata = json.loads(replica["provider_metadata_json"])
        except (TypeError, ValueError) as exc:
            raise LedgerDbError("Replica metadata is not valid JSON") from exc
        if not isinstance(metadata, dict):
            raise LedgerDbError("Replica metadata must be a JSON object")
        metadata["retirement_reason"] = reason
        metadata["retired_at"] = _utc_now()
        metadata_json = _storage_config_json(metadata)
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE file_replicas
                SET replica_status = 'retired', is_primary = 0,
                    provider_metadata_json = ?, row_version = row_version + 1, updated_at = ?
                WHERE file_replica_id = ?
                """,
                (metadata_json, timestamp, file_replica_id),
            )
        return self._fetch_one(
            "SELECT * FROM file_replicas WHERE file_replica_id = ?", (file_replica_id,)
        )

    def transition_document(
        self,
        document_id: str,
        *,
        lifecycle_status: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one(
            """
            SELECT d.*, p.period_key
            FROM documents d
            LEFT JOIN periods p ON p.period_id = d.period_id
            WHERE d.document_id = ?
            """,
            (document_id,),
        )
        return self.upsert_document(
            document_id=document_id,
            external_key=existing["external_key"],
            counterparty_id=existing["counterparty_id"],
            import_batch_id=existing["import_batch_id"],
            document_type=existing["document_type"],
            document_number=existing["document_number"],
            issued_on=existing["issued_on"],
            period_key=existing["period_key"],
            currency=existing["currency"],
            total_minor=existing["total_minor"],
            lifecycle_status=lifecycle_status,
            source_hash=existing["source_hash"],
            expected_row_version=expected_row_version,
        )

    def add_document_source(
        self,
        *,
        document_id: str,
        source_book_line_id: str,
        source_hash: str,
        import_batch_id: str | None = None,
        source_file: str | None = None,
        source_row_number: str | None = None,
    ) -> dict[str, Any]:
        self._fetch_one("SELECT document_id FROM documents WHERE document_id = ?", (document_id,))
        existing = self._fetch_optional(
            "SELECT * FROM document_sources WHERE document_id = ? AND source_book_line_id = ?",
            (document_id, source_book_line_id),
        )
        if existing is not None:
            hash_owner = self._fetch_optional(
                "SELECT * FROM document_sources WHERE source_hash = ?",
                (source_hash,),
            )
            if hash_owner is not None and hash_owner["document_source_id"] != existing["document_source_id"]:
                raise LedgerDbError("A source-book hash cannot identify two source lines")
            desired = {
                "import_batch_id": import_batch_id,
                "source_file": source_file,
                "source_row_number": source_row_number,
                "source_hash": source_hash,
            }
            if all(existing[key] == value for key, value in desired.items()):
                return existing
            with self.connection:
                self.connection.execute(
                    """
                    UPDATE document_sources
                    SET import_batch_id = ?, source_file = ?, source_row_number = ?, source_hash = ?
                    WHERE document_source_id = ?
                    """,
                    (
                        import_batch_id,
                        source_file,
                        source_row_number,
                        source_hash,
                        existing["document_source_id"],
                    ),
                )
            return self._fetch_one(
                "SELECT * FROM document_sources WHERE document_source_id = ?",
                (existing["document_source_id"],),
            )
        hash_owner = self._fetch_optional(
            "SELECT * FROM document_sources WHERE source_hash = ?",
            (source_hash,),
        )
        if hash_owner is not None:
            if hash_owner["document_id"] != document_id:
                raise LedgerDbError("A source-book line cannot be linked to two documents")
            return hash_owner
        timestamp = _utc_now()
        source_id = _new_id()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO document_sources (
                    document_source_id, document_id, import_batch_id, source_book_line_id,
                    source_file, source_row_number, source_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    document_id,
                    import_batch_id,
                    source_book_line_id,
                    source_file,
                    source_row_number,
                    source_hash,
                    timestamp,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM document_sources WHERE document_source_id = ?",
            (source_id,),
        )

    def get_intake_receipt(self, intake_receipt_id: str) -> dict[str, Any] | None:
        return self._fetch_optional(
            "SELECT * FROM intake_receipts WHERE intake_receipt_id = ?",
            (intake_receipt_id,),
        )

    def find_intake_receipt_by_fingerprint(
        self,
        row_fingerprint: str,
    ) -> dict[str, Any] | None:
        return self._fetch_optional(
            "SELECT * FROM intake_receipts WHERE row_fingerprint = ?",
            (row_fingerprint,),
        )

    def find_intake_receipt_by_evidence(
        self,
        evidence_sha256: str,
    ) -> dict[str, Any] | None:
        return self._fetch_optional(
            "SELECT * FROM intake_receipts WHERE evidence_sha256 = ?",
            (evidence_sha256,),
        )

    def add_intake_receipt(
        self,
        *,
        intake_tab: str,
        source_row_number: int,
        row_fingerprint: str,
        evidence_sha256: str,
        document_id: str,
        transaction_id: str,
        treatment_id: str,
        input_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        if intake_tab not in {"expense_intake", "income_intake"}:
            raise ValueError(f"Unsupported intake tab: {intake_tab}")
        if source_row_number < 2:
            raise ValueError("Intake source_row_number must be at least 2")
        document = self._fetch_one(
            "SELECT document_id FROM documents WHERE document_id = ?",
            (document_id,),
        )
        transaction = self._fetch_one(
            "SELECT transaction_id, document_id FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        treatment = self._fetch_one(
            "SELECT treatment_id, transaction_id FROM tax_treatments WHERE treatment_id = ?",
            (treatment_id,),
        )
        if transaction["document_id"] != document["document_id"]:
            raise LedgerDbError("Intake transaction is not linked to the intake document")
        if treatment["transaction_id"] != transaction["transaction_id"]:
            raise LedgerDbError("Intake treatment is not linked to the intake transaction")

        input_json = json.dumps(
            input_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        desired = {
            "intake_tab": intake_tab,
            "row_fingerprint": row_fingerprint,
            "evidence_sha256": evidence_sha256,
            "document_id": document_id,
            "transaction_id": transaction_id,
            "treatment_id": treatment_id,
            "input_json": input_json,
        }
        candidates = [
            self.find_intake_receipt_by_fingerprint(row_fingerprint),
            self.find_intake_receipt_by_evidence(evidence_sha256),
            self._fetch_optional(
                "SELECT * FROM intake_receipts WHERE document_id = ?",
                (document_id,),
            ),
        ]
        existing = next((row for row in candidates if row is not None), None)
        if existing is not None:
            changed = [key for key, value in desired.items() if existing[key] != value]
            if changed:
                raise LedgerDbError(
                    "Existing intake receipt conflicts with the submitted row: "
                    + ", ".join(changed)
                )
            return existing

        receipt_id = _new_id()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO intake_receipts (
                    intake_receipt_id, intake_tab, source_row_number, row_fingerprint,
                    evidence_sha256, document_id, transaction_id, treatment_id,
                    input_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    receipt_id,
                    intake_tab,
                    source_row_number,
                    row_fingerprint,
                    evidence_sha256,
                    document_id,
                    transaction_id,
                    treatment_id,
                    input_json,
                    _utc_now(),
                ),
            )
        return self._fetch_one(
            "SELECT * FROM intake_receipts WHERE intake_receipt_id = ?",
            (receipt_id,),
        )

    def add_transaction(
        self,
        *,
        transaction_id: str | None = None,
        external_key: str | None = None,
        period_key: str,
        transaction_date: str,
        booking_date: str,
        entry_type: str,
        description: str,
        amount_minor: int,
        currency: str = "EUR",
        amount_original_minor: int | None = None,
        original_currency: str | None = None,
        amount_eur_minor: int | None = None,
        fx_rate_id: str | None = None,
        direction: str = "debit",
        lifecycle_status: str = "received",
        document_id: str | None = None,
        counterparty_id: str | None = None,
        business_activity_id: str | None = None,
        correction_of_transaction_id: str | None = None,
        correction_kind: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        _validate_lifecycle_status(lifecycle_status)
        if direction not in {"debit", "credit"}:
            raise ValueError(f"Unsupported direction: {direction}")
        if correction_of_transaction_id is None and correction_kind is not None:
            raise ValueError("correction_kind requires correction_of_transaction_id")
        if correction_of_transaction_id is not None and correction_kind not in CORRECTION_KINDS:
            raise ValueError("correction_of_transaction_id requires correction_kind reversing or correcting")

        period = self.ensure_period(period_key)
        existing = self._find_transaction(transaction_id, external_key)
        if business_activity_id is None and existing is not None:
            effective_activity_id = existing["business_activity_id"]
        else:
            effective_activity_id = self._resolve_business_activity_id(
                business_activity_id,
                transaction_date=transaction_date,
            )
        correction_target = (
            self._fetch_one(
                "SELECT transaction_id, period_id, amount_minor FROM transactions WHERE transaction_id = ?",
                (correction_of_transaction_id,),
            )
            if correction_of_transaction_id
            else None
        )
        if correction_target is not None and correction_target["period_id"] == period["period_id"]:
            source_period = self._fetch_one("SELECT status FROM periods WHERE period_id = ?", (period["period_id"],))
            if source_period["status"] != "open":
                raise ClosedPeriodError("Corrections for closed periods must be new transactions in an open amendment period")

        timestamp = _utc_now()
        payload = {
            "external_key": external_key,
            "period_id": period["period_id"],
            "transaction_date": transaction_date,
            "booking_date": booking_date,
            "entry_type": entry_type,
            "description": description,
            "amount_minor": amount_minor,
            "currency": currency,
            "amount_original_minor": amount_original_minor,
            "original_currency": original_currency,
            "amount_eur_minor": amount_eur_minor,
            "fx_rate_id": fx_rate_id,
            "direction": direction,
            "lifecycle_status": lifecycle_status,
            "document_id": document_id,
            "counterparty_id": counterparty_id,
            "business_activity_id": effective_activity_id,
            "correction_of_transaction_id": correction_of_transaction_id,
            "correction_kind": correction_kind,
        }
        desired_source_hash = source_hash or _stable_hash(payload)
        desired_original_minor = amount_original_minor if amount_original_minor is not None else amount_minor
        desired_original_currency = original_currency or currency
        desired_eur_minor = amount_eur_minor if amount_eur_minor is not None else (amount_minor if currency == "EUR" else None)

        if existing is not None:
            self._check_row_version(existing, expected_row_version)
            desired = {
                "external_key": external_key if external_key is not None else existing["external_key"],
                "period_id": period["period_id"],
                "transaction_date": transaction_date,
                "booking_date": booking_date,
                "entry_type": entry_type,
                "description": description,
                "amount_minor": amount_minor,
                "currency": currency,
                "amount_original_minor": desired_original_minor,
                "original_currency": desired_original_currency,
                "amount_eur_minor": desired_eur_minor,
                "fx_rate_id": fx_rate_id,
                "direction": direction,
                "lifecycle_status": lifecycle_status,
                "document_id": document_id,
                "counterparty_id": counterparty_id,
                "business_activity_id": effective_activity_id,
                "correction_of_transaction_id": correction_of_transaction_id,
                "correction_kind": correction_kind,
                "source_hash": desired_source_hash,
            }
            if _terminal_update_is_noop(existing, desired):
                return existing

        with _write_scope(self.connection):
            if existing is None:
                self._assert_period_mutable(period["period_id"])
                new_id = transaction_id or _new_id()
                self.connection.execute(
                    """
                    INSERT INTO transactions (
                        transaction_id, external_key, period_id, transaction_date, booking_date, entry_type,
                        description, amount_minor, currency, direction, lifecycle_status, document_id,
                        counterparty_id, correction_of_transaction_id, correction_kind, included_snapshot_id,
                        source_hash, row_version, created_at, updated_at, amount_original_minor,
                        original_currency, amount_eur_minor, fx_rate_id, business_activity_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id,
                        external_key,
                        period["period_id"],
                        transaction_date,
                        booking_date,
                        entry_type,
                        description,
                        amount_minor,
                        currency,
                        direction,
                        lifecycle_status,
                        document_id,
                        counterparty_id,
                        correction_of_transaction_id,
                        correction_kind,
                        desired_source_hash,
                        timestamp,
                        timestamp,
                        desired_original_minor,
                        desired_original_currency,
                        desired_eur_minor,
                        fx_rate_id,
                        effective_activity_id,
                    ),
                )
                return self._fetch_one("SELECT * FROM transactions WHERE transaction_id = ?", (new_id,))

            self._assert_period_mutable(existing["period_id"])
            _validate_lifecycle_transition(existing["lifecycle_status"], lifecycle_status)
            self._assert_period_mutable(period["period_id"])
            next_version = existing["row_version"] + 1
            self.connection.execute(
                """
                UPDATE transactions
                SET external_key = ?, period_id = ?, transaction_date = ?, booking_date = ?, entry_type = ?,
                    description = ?, amount_minor = ?, currency = ?, direction = ?, lifecycle_status = ?,
                    document_id = ?, counterparty_id = ?, correction_of_transaction_id = ?, correction_kind = ?,
                    source_hash = ?, amount_original_minor = ?, original_currency = ?, amount_eur_minor = ?,
                    fx_rate_id = ?, business_activity_id = ?, row_version = ?, updated_at = ?
                WHERE transaction_id = ?
                """,
                (
                    external_key if external_key is not None else existing["external_key"],
                    period["period_id"],
                    transaction_date,
                    booking_date,
                    entry_type,
                    description,
                    amount_minor,
                    currency,
                    direction,
                    lifecycle_status,
                    document_id,
                    counterparty_id,
                    correction_of_transaction_id,
                    correction_kind,
                    desired_source_hash,
                    desired_original_minor,
                    desired_original_currency,
                    desired_eur_minor,
                    fx_rate_id,
                    effective_activity_id,
                    next_version,
                    timestamp,
                    existing["transaction_id"],
                ),
            )
        return self._fetch_one("SELECT * FROM transactions WHERE transaction_id = ?", (existing["transaction_id"],))

    def transition_transaction(
        self,
        transaction_id: str,
        *,
        lifecycle_status: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one(
            """
            SELECT t.*, p.period_key
            FROM transactions t
            JOIN periods p ON p.period_id = t.period_id
            WHERE t.transaction_id = ?
            """,
            (transaction_id,),
        )
        self._check_row_version(existing, expected_row_version)
        _validate_lifecycle_transition(existing["lifecycle_status"], lifecycle_status)
        if lifecycle_status == "posted" and existing["lifecycle_status"] != "posted":
            self._assert_transaction_postable(existing)
        transitioned = self.add_transaction(
            transaction_id=transaction_id,
            external_key=existing["external_key"],
            period_key=existing["period_key"],
            transaction_date=existing["transaction_date"],
            booking_date=existing["booking_date"],
            entry_type=existing["entry_type"],
            description=existing["description"],
            amount_minor=existing["amount_minor"],
            currency=existing["currency"],
            amount_original_minor=existing["amount_original_minor"],
            original_currency=existing["original_currency"],
            amount_eur_minor=existing["amount_eur_minor"],
            fx_rate_id=existing["fx_rate_id"],
            direction=existing["direction"],
            lifecycle_status=lifecycle_status,
            document_id=existing["document_id"],
            counterparty_id=existing["counterparty_id"],
            business_activity_id=existing["business_activity_id"],
            correction_of_transaction_id=existing["correction_of_transaction_id"],
            correction_kind=existing["correction_kind"],
            source_hash=existing["source_hash"],
            expected_row_version=expected_row_version,
        )
        return transitioned

    def _ensure_future_irnr_payment_review(
        self,
        *,
        transaction_id: str,
        payment_date: date,
    ) -> None:
        transaction = self._fetch_one(
            """
            SELECT t.entry_type, t.counterparty_id
            FROM transactions t
            WHERE t.transaction_id = ?
            """,
            (transaction_id,),
        )
        if (
            transaction["entry_type"] != "expense"
            or payment_date <= XOLO_RECORDED_PRODUCTION_THROUGH
            or transaction.get("counterparty_id") is None
        ):
            return
        counterparty = self._fetch_one(
            """
            SELECT country_code, professional_supplier, legal_form
            FROM counterparties
            WHERE counterparty_id = ?
            """,
            (transaction["counterparty_id"],),
        )
        if (
            str(counterparty["country_code"]).upper() == "ES"
            or counterparty["professional_supplier"] != 1
        ):
            return
        legal_form = str(counterparty["legal_form"] or "unknown")
        if legal_form in {"legal_entity", "public_body"}:
            return
        period_key = (
            f"{payment_date.year}-Q{((payment_date.month - 1) // 3) + 1}"
        )
        period = (
            self._ensure_period_uncommitted(period_key)
            if self.connection.in_transaction
            else self.ensure_period(period_key)
        )
        reviewed_obligation = self._fetch_optional(
            """
            SELECT o.determination
            FROM obligations o
            WHERE o.period_id = ? AND o.obligation_code = '216'
            """,
            (period["period_id"],),
        )
        if (
            reviewed_obligation is not None
            and reviewed_obligation["determination"] in {"due", "not_due"}
        ):
            return
        if legal_form == "individual":
            issue_code = "nonresident_professional_irnr_review"
            message = (
                "A current-period payment to a non-resident individual professional "
                "requires a reviewed IRNR reportability decision for Modelo 216. "
                "Expense deductibility is independent; treaty evidence is required "
                "only if treaty relief is used."
            )
        else:
            issue_code = "nonresident_payee_legal_form_review"
            message = (
                "A current-period payment to a foreign professional supplier cannot "
                "be classified for Modelo 216 until the payee legal form is reviewed. "
                "Expense deductibility is independent."
            )
        self.add_validation_issue(
            period_key=period_key,
            issue_code=issue_code,
            severity="error",
            message=message,
            subject_table="transactions",
            subject_id=transaction_id,
            blocking=True,
            source_hash=hashlib.sha256(
                f"{issue_code}:{transaction_id}:{period_key}".encode("utf-8")
            ).hexdigest(),
            dedupe_key=f"{issue_code}:{transaction_id}:{period_key}",
        )

    def _refresh_future_irnr_reviews_for_counterparty(
        self,
        counterparty_id: str,
    ) -> None:
        payments = self._fetch_all(
            """
            SELECT DISTINCT t.transaction_id, p.paid_on
            FROM transactions t
            JOIN payments p ON p.transaction_id = t.transaction_id
            WHERE t.counterparty_id = ? AND p.paid_on > ?
            ORDER BY p.paid_on, t.transaction_id
            """,
            (counterparty_id, XOLO_RECORDED_PRODUCTION_THROUGH.isoformat()),
        )
        if not payments:
            return
        transaction_ids = sorted(
            {str(row["transaction_id"]) for row in payments}
        )
        dedupe_keys = sorted(
            {
                f"{issue_code}:{row['transaction_id']}:"
                f"{date.fromisoformat(str(row['paid_on'])[:10]).year}-"
                f"Q{((date.fromisoformat(str(row['paid_on'])[:10]).month - 1) // 3) + 1}"
                for row in payments
                for issue_code in {
                    "nonresident_payee_legal_form_review",
                    "nonresident_professional_irnr_review",
                }
            }
        )
        issue_placeholders = ", ".join("?" for _ in IRNR_REVIEW_ISSUE_CODES)
        transaction_placeholders = ", ".join("?" for _ in transaction_ids)
        dedupe_placeholders = ", ".join("?" for _ in dedupe_keys)
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                f"""
                UPDATE validation_issues
                SET issue_status = 'resolved',
                    resolution_reason = ?,
                    resolved_at = ?,
                    row_version = row_version + 1,
                    updated_at = ?
                WHERE issue_status = 'open'
                  AND issue_code IN ({issue_placeholders})
                  AND (
                    (subject_table = 'transactions'
                     AND subject_id IN ({transaction_placeholders}))
                    OR dedupe_key IN ({dedupe_placeholders})
                  )
                """,
                (
                    "Counterparty IRNR classification inputs were reviewed.",
                    timestamp,
                    timestamp,
                    *sorted(IRNR_REVIEW_ISSUE_CODES),
                    *transaction_ids,
                    *dedupe_keys,
                ),
            )
            for payment in payments:
                self._ensure_future_irnr_payment_review(
                    transaction_id=str(payment["transaction_id"]),
                    payment_date=date.fromisoformat(str(payment["paid_on"])[:10]),
                )

    def _resolve_period_irnr_review_issues(
        self,
        *,
        period_id: str,
    ) -> None:
        issue_placeholders = ", ".join("?" for _ in IRNR_REVIEW_ISSUE_CODES)
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                f"""
                UPDATE validation_issues
                SET issue_status = 'resolved',
                    resolution_reason = ?,
                    resolved_at = ?,
                    row_version = row_version + 1,
                    updated_at = ?
                WHERE period_id = ?
                  AND issue_status = 'open'
                  AND issue_code IN ({issue_placeholders})
                """,
                (
                    "Modelo 216 applicability was reviewed for the period.",
                    timestamp,
                    timestamp,
                    period_id,
                    *sorted(IRNR_REVIEW_ISSUE_CODES),
                ),
            )

    def _assert_transaction_postable(self, transaction: Mapping[str, Any]) -> None:
        from .posting import assert_transaction_postable

        assert_transaction_postable(self, transaction)

    def update_transaction_counterparty(
        self,
        transaction_id: str,
        *,
        counterparty_id: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        self._check_row_version(existing, expected_row_version)
        if existing["lifecycle_status"] not in {
            "received",
            "extracted",
            "needs_review",
            "approved",
        }:
            raise LifecycleError(
                "Counterparty cannot change transaction in "
                f"lifecycle_status={existing['lifecycle_status']}"
            )
        self._assert_period_mutable(existing["period_id"])
        self._fetch_one(
            "SELECT counterparty_id FROM counterparties WHERE counterparty_id = ?",
            (counterparty_id,),
        )
        if existing["counterparty_id"] == counterparty_id:
            return existing
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE transactions
                SET counterparty_id = ?, row_version = ?, updated_at = ?
                WHERE transaction_id = ?
                """,
                (
                    counterparty_id,
                    existing["row_version"] + 1,
                    timestamp,
                    transaction_id,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )

    def set_transaction_business_activity(
        self,
        transaction_id: str,
        *,
        business_activity_id: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        self._check_row_version(existing, expected_row_version)
        if existing["lifecycle_status"] not in {
            "received",
            "extracted",
            "needs_review",
            "approved",
        }:
            raise LifecycleError(
                "Business activity cannot change transaction in "
                f"lifecycle_status={existing['lifecycle_status']}"
            )
        self._assert_period_mutable(existing["period_id"])
        effective_activity_id = self._resolve_business_activity_id(
            business_activity_id,
            transaction_date=existing["transaction_date"],
        )
        if existing["business_activity_id"] == effective_activity_id:
            return existing
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE transactions
                SET business_activity_id = ?, row_version = ?, updated_at = ?
                WHERE transaction_id = ?
                """,
                (
                    effective_activity_id,
                    existing["row_version"] + 1,
                    timestamp,
                    transaction_id,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )

    def apply_transaction_fx(
        self,
        transaction_id: str,
        *,
        fx_rate_id: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        self._check_row_version(existing, expected_row_version)
        if existing["lifecycle_status"] not in {"received", "extracted", "needs_review", "approved"}:
            raise LifecycleError(
                f"FX cannot change transaction in lifecycle_status={existing['lifecycle_status']}"
            )
        self._assert_period_mutable(existing["period_id"])
        rate = self._fetch_one(
            "SELECT * FROM fx_rates WHERE fx_rate_id = ?",
            (fx_rate_id,),
        )
        original_currency = str(existing["original_currency"] or existing["currency"]).upper()
        if str(rate["base_currency"]).upper() != original_currency:
            raise ValueError(
                f"FX base currency {rate['base_currency']} does not match transaction {original_currency}"
            )
        if str(rate["quote_currency"]).upper() != "EUR":
            raise ValueError("Transaction FX must quote EUR")
        original_minor = existing["amount_original_minor"]
        if original_minor is None:
            raise ValueError("Transaction is missing amount_original_minor")
        eur_minor = int(
            (Decimal(original_minor) * Decimal(str(rate["rate"]))).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
        if existing["fx_rate_id"] == fx_rate_id and existing["amount_eur_minor"] == eur_minor:
            return existing
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE transactions
                SET amount_eur_minor = ?, fx_rate_id = ?, row_version = ?, updated_at = ?
                WHERE transaction_id = ?
                """,
                (
                    eur_minor,
                    fx_rate_id,
                    existing["row_version"] + 1,
                    timestamp,
                    transaction_id,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )

    def add_tax_treatment(
        self,
        *,
        transaction_id: str,
        treatment_type: str,
        jurisdiction: str = "ES",
        rate_basis_points: int | None = None,
        deductible_ratio: float | None = None,
        notes: str | None = None,
        source_hash: str | None = None,
    ) -> dict[str, Any]:
        transaction = self._fetch_one("SELECT transaction_id, period_id FROM transactions WHERE transaction_id = ?", (transaction_id,))
        self._assert_period_mutable(transaction["period_id"])
        timestamp = _utc_now()
        payload = {
            "transaction_id": transaction_id,
            "treatment_type": treatment_type,
            "jurisdiction": jurisdiction,
            "rate_basis_points": rate_basis_points,
            "deductible_ratio": deductible_ratio,
            "notes": notes,
        }
        treatment_id = _new_id()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO tax_treatments (
                    treatment_id, transaction_id, treatment_type, jurisdiction, rate_basis_points,
                    deductible_ratio, notes, source_hash, row_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    treatment_id,
                    transaction_id,
                    treatment_type,
                    jurisdiction,
                    rate_basis_points,
                    deductible_ratio,
                    notes,
                    source_hash or _stable_hash(payload),
                    timestamp,
                    timestamp,
                ),
            )
        return self._fetch_one("SELECT * FROM tax_treatments WHERE treatment_id = ?", (treatment_id,))

    def add_detailed_tax_treatment(
        self,
        *,
        transaction_id: str,
        treatment_type: str,
        tax_code: str,
        aeat_invoice_type: str | None = None,
        aeat_operation_key: str | None = None,
        aeat_operation_qualification: str | None = None,
        aeat_exemption_code: str | None = None,
        aeat_reverse_charge: bool | None = None,
        vat_investment_good: bool | None | EllipsisType = ...,
        aeat_expense_concept: str | None = None,
        jurisdiction: str = "ES",
        rate_basis_points: int | None = None,
        deductible_ratio: float | None = None,
        taxable_base_minor: int | None = None,
        vat_minor: int | None = None,
        deductible_irpf_minor: int | None = None,
        deductible_vat_minor: int | None = None,
        withholding_minor: int | None = None,
        include_modelo130: bool = False,
        include_modelo303: bool = False,
        include_modelo347: bool = False,
        rule_version_id: str | None = None,
        notes: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        aeat_invoice_type = _optional_upper(aeat_invoice_type)
        aeat_operation_key = _optional_upper(aeat_operation_key)
        aeat_operation_qualification = _optional_upper(aeat_operation_qualification)
        aeat_exemption_code = _optional_upper(aeat_exemption_code)
        aeat_expense_concept = _optional_upper(aeat_expense_concept)
        if aeat_reverse_charge is not None and not isinstance(aeat_reverse_charge, bool):
            raise ValueError("AEAT reverse-charge flag must be boolean or null")
        if aeat_invoice_type is not None and aeat_invoice_type not in {
            "F1", "F2", "F3", "F4", "F5", "F6", "R1", "R2", "R3", "R4", "R5",
            "SF", "DV", "AJ", "LC",
        }:
            raise ValueError("Unsupported AEAT invoice type")
        if aeat_operation_key is not None and (
            len(aeat_operation_key) != 2 or not aeat_operation_key.isdigit()
        ):
            raise ValueError("AEAT operation key must contain two digits")
        if (
            aeat_operation_qualification is not None
            and aeat_operation_qualification not in {"S1", "S2", "N1", "N2"}
        ):
            raise ValueError("Unsupported AEAT operation qualification")
        if aeat_exemption_code is not None and aeat_exemption_code not in {
            "E1", "E2", "E3", "E4", "E5", "E6"
        }:
            raise ValueError("Unsupported AEAT exemption code")
        if aeat_expense_concept is not None and not (
            aeat_expense_concept.startswith("G")
            and 2 <= len(aeat_expense_concept) <= 4
            and aeat_expense_concept.isalnum()
        ):
            raise ValueError("Unsupported AEAT expense concept")
        transaction = self._fetch_one(
            "SELECT transaction_id, period_id FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        self._assert_period_mutable(transaction["period_id"])
        existing = self._fetch_optional(
            """
            SELECT * FROM tax_treatments
            WHERE transaction_id = ? AND treatment_type = ? AND jurisdiction = ?
            """,
            (transaction_id, treatment_type, jurisdiction),
        )
        # Older callers (including Sheets review) must not erase a saved choice.
        if vat_investment_good is ...:
            stored = existing["vat_investment_good"] if existing is not None else None
            vat_investment_good = None if stored is None else bool(stored)
        if vat_investment_good is not None and not isinstance(vat_investment_good, bool):
            raise ValueError("vat_investment_good must be boolean or null")
        is_vat_investment_good(vat_investment_good, legacy_asset=False, tax_code=tax_code)
        timestamp = _utc_now()
        payload = {
            "transaction_id": transaction_id,
            "treatment_type": treatment_type,
            "tax_code": tax_code,
            "aeat_invoice_type": aeat_invoice_type,
            "aeat_operation_key": aeat_operation_key,
            "aeat_operation_qualification": aeat_operation_qualification,
            "aeat_exemption_code": aeat_exemption_code,
            "aeat_reverse_charge": aeat_reverse_charge,
            "vat_investment_good": vat_investment_good,
            "aeat_expense_concept": aeat_expense_concept,
            "jurisdiction": jurisdiction,
            "rate_basis_points": rate_basis_points,
            "deductible_ratio": deductible_ratio,
            "taxable_base_minor": taxable_base_minor,
            "vat_minor": vat_minor,
            "deductible_irpf_minor": deductible_irpf_minor,
            "deductible_vat_minor": deductible_vat_minor,
            "withholding_minor": withholding_minor,
            "include_modelo130": include_modelo130,
            "include_modelo303": include_modelo303,
            "include_modelo347": include_modelo347,
            "rule_version_id": rule_version_id,
            "notes": notes,
        }
        effective_hash = source_hash or _stable_hash(payload)
        with _write_scope(self.connection):
            if existing is None:
                treatment_id = _new_id()
                self.connection.execute(
                    """
                    INSERT INTO tax_treatments (
                        treatment_id, transaction_id, treatment_type, jurisdiction, rate_basis_points,
                        deductible_ratio, notes, source_hash, row_version, created_at, updated_at,
                        tax_code, taxable_base_minor, vat_minor, deductible_irpf_minor,
                        deductible_vat_minor, withholding_minor, include_modelo130,
                        include_modelo303, include_modelo347, rule_version_id,
                        aeat_invoice_type, aeat_operation_key,
                        aeat_operation_qualification, aeat_exemption_code,
                        aeat_reverse_charge, aeat_expense_concept, vat_investment_good
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        treatment_id,
                        transaction_id,
                        treatment_type,
                        jurisdiction,
                        rate_basis_points,
                        deductible_ratio,
                        notes,
                        effective_hash,
                        timestamp,
                        timestamp,
                        tax_code,
                        taxable_base_minor,
                        vat_minor,
                        deductible_irpf_minor,
                        deductible_vat_minor,
                        withholding_minor,
                        int(include_modelo130),
                        int(include_modelo303),
                        int(include_modelo347),
                        rule_version_id,
                        aeat_invoice_type,
                        aeat_operation_key,
                        aeat_operation_qualification,
                        aeat_exemption_code,
                        None if aeat_reverse_charge is None else int(aeat_reverse_charge),
                        aeat_expense_concept,
                        None if vat_investment_good is None else int(vat_investment_good),
                    ),
                )
            else:
                treatment_id = existing["treatment_id"]
                self._check_row_version(existing, expected_row_version)
                self.connection.execute(
                    """
                    UPDATE tax_treatments
                    SET tax_code = ?, rate_basis_points = ?, deductible_ratio = ?, notes = ?,
                        taxable_base_minor = ?, vat_minor = ?, deductible_irpf_minor = ?,
                        deductible_vat_minor = ?, withholding_minor = ?, include_modelo130 = ?,
                        include_modelo303 = ?, include_modelo347 = ?, rule_version_id = ?,
                        aeat_invoice_type = ?, aeat_operation_key = ?,
                        aeat_operation_qualification = ?, aeat_exemption_code = ?,
                        aeat_reverse_charge = ?, aeat_expense_concept = ?, vat_investment_good = ?,
                        source_hash = ?, row_version = ?, updated_at = ?
                    WHERE treatment_id = ?
                    """,
                    (
                        tax_code,
                        rate_basis_points,
                        deductible_ratio,
                        notes,
                        taxable_base_minor,
                        vat_minor,
                        deductible_irpf_minor,
                        deductible_vat_minor,
                        withholding_minor,
                        int(include_modelo130),
                        int(include_modelo303),
                        int(include_modelo347),
                        rule_version_id,
                        aeat_invoice_type,
                        aeat_operation_key,
                        aeat_operation_qualification,
                        aeat_exemption_code,
                        None if aeat_reverse_charge is None else int(aeat_reverse_charge),
                        aeat_expense_concept,
                        None if vat_investment_good is None else int(vat_investment_good),
                        effective_hash,
                        existing["row_version"] + 1,
                        timestamp,
                        treatment_id,
                    ),
                )
        return self._fetch_one("SELECT * FROM tax_treatments WHERE treatment_id = ?", (treatment_id,))

    def add_rule_version(
        self,
        *,
        rule_name: str,
        version: str,
        source_hash: str,
        activated_at: str | None = None,
    ) -> dict[str, Any]:
        existing = self._fetch_optional(
            "SELECT * FROM rule_versions WHERE rule_name = ? AND version = ?",
            (rule_name, version),
        )
        if existing is not None:
            return existing
        timestamp = _utc_now()
        rule_id = _new_id()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO rule_versions (
                    rule_version_id, rule_name, version, activated_at, source_hash,
                    row_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (rule_id, rule_name, version, activated_at, source_hash, timestamp, timestamp),
            )
        return self._fetch_one("SELECT * FROM rule_versions WHERE rule_version_id = ?", (rule_id,))

    def add_fx_rate(
        self,
        *,
        rate_date: str,
        base_currency: str,
        quote_currency: str,
        rate: str,
        rate_source: str,
        source_hash: str,
        source_reference: str | None = None,
        rule_version_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_reference = _normalize_source_reference(source_reference)
        canonical_rate = _canonical_decimal_text(rate)
        if rate_source == "actual_settlement":
            # Schema v19 lets different documented settlements coexist for the
            # same date/currency; only a fully identical record is reused.
            existing = self._fetch_optional(
                """
                SELECT * FROM fx_rates
                WHERE rate_date = ? AND base_currency = ? AND quote_currency = ?
                    AND rate_source = ? AND rate = ?
                    AND COALESCE(source_reference, '') = ?
                """,
                (
                    rate_date,
                    base_currency,
                    quote_currency,
                    rate_source,
                    canonical_rate,
                    normalized_reference or "",
                ),
            )
            if existing is not None:
                return existing
        else:
            existing = self._fetch_optional(
                """
                SELECT * FROM fx_rates
                WHERE rate_date = ? AND base_currency = ? AND quote_currency = ? AND rate_source = ?
                """,
                (rate_date, base_currency, quote_currency, rate_source),
            )
            if existing is not None:
                existing_rate = _canonical_decimal_text(str(existing["rate"]))
                existing_reference = _normalize_source_reference(existing["source_reference"])
                if existing_rate != canonical_rate:
                    raise FxRateConflictError(
                        "Conflicting FX rate for "
                        f"{rate_date} {base_currency}/{quote_currency} {rate_source}"
                    )
                if (
                    existing_reference is not None
                    and normalized_reference is not None
                    and existing_reference != normalized_reference
                ):
                    raise FxRateConflictError(
                        "Conflicting FX source_reference for "
                        f"{rate_date} {base_currency}/{quote_currency} {rate_source}"
                    )
                needs_reference_backfill = (
                    existing_reference is None and normalized_reference is not None
                )
                needs_rule_backfill = (
                    existing["rule_version_id"] is None and rule_version_id is not None
                )
                if not needs_reference_backfill and not needs_rule_backfill:
                    return existing
                timestamp = _utc_now()
                with _write_scope(self.connection):
                    self.connection.execute(
                        """
                        UPDATE fx_rates
                        SET source_reference = COALESCE(source_reference, ?),
                            source_hash = ?,
                            rule_version_id = COALESCE(rule_version_id, ?),
                            updated_at = ?
                        WHERE fx_rate_id = ?
                        """,
                        (
                            normalized_reference,
                            source_hash,
                            rule_version_id,
                            timestamp,
                            existing["fx_rate_id"],
                        ),
                    )
                return self._fetch_one(
                    "SELECT * FROM fx_rates WHERE fx_rate_id = ?",
                    (existing["fx_rate_id"],),
                )
        timestamp = _utc_now()
        rate_id = _new_id()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                INSERT INTO fx_rates (
                    fx_rate_id, rate_date, base_currency, quote_currency, rate, rate_source,
                    source_reference, source_hash, row_version, created_at, updated_at, rule_version_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    rate_id,
                    rate_date,
                    base_currency,
                    quote_currency,
                    canonical_rate,
                    rate_source,
                    normalized_reference,
                    source_hash,
                    timestamp,
                    timestamp,
                    rule_version_id,
                ),
            )
            self._insert_fx_provenance(
                fx_rate_id=rate_id,
                rate_source=rate_source,
                primary_source_reference=normalized_reference,
                raw_observation=_fx_raw_observation(
                    provenance,
                    rate_date=rate_date,
                    base_currency=base_currency,
                    quote_currency=quote_currency,
                    rate=canonical_rate,
                    rate_source=rate_source,
                    source_reference=normalized_reference,
                ),
                supersedes_rate_id=(provenance or {}).get("supersedes_rate_id"),
            )
        return self._fetch_one("SELECT * FROM fx_rates WHERE fx_rate_id = ?", (rate_id,))

    def _insert_fx_provenance(
        self,
        *,
        fx_rate_id: str,
        rate_source: str,
        primary_source_reference: str | None,
        raw_observation: str,
        supersedes_rate_id: str | None = None,
    ) -> None:
        raw_text = str(raw_observation or "").strip()
        if not raw_text:
            raise ValueError("FX provenance requires a raw observation")
        if supersedes_rate_id:
            supersedes = self._fetch_one(
                "SELECT fx_provenance_id FROM fx_provenance WHERE fx_rate_id = ?",
                (str(supersedes_rate_id),),
            )
            supersedes_provenance_id = str(supersedes["fx_provenance_id"])
            provenance_kind = "manual_adjustment"
        else:
            supersedes_provenance_id = None
            provenance_kind = FX_PROVENANCE_KINDS.get(str(rate_source), "manual_adjustment")
        self.connection.execute(
            """
            INSERT INTO fx_provenance (
                fx_provenance_id, fx_rate_id, provenance_kind, primary_source_reference,
                raw_observation, raw_observation_hash, supersedes_provenance_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                fx_rate_id,
                provenance_kind,
                primary_source_reference,
                raw_text,
                hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
                supersedes_provenance_id,
                _utc_now(),
            ),
        )

    def fx_provenance_for_rate(self, fx_rate_id: str) -> dict[str, Any] | None:
        row = self._fetch_optional(
            "SELECT * FROM fx_provenance WHERE fx_rate_id = ?",
            (fx_rate_id,),
        )
        return dict(row) if row is not None else None

    def review_transaction_fx_rate(
        self,
        transaction_id: str,
        *,
        expected_row_version: int,
        rate_date: str,
        rate: str,
        rate_source: str,
        source_reference: str,
        rule_version_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_reference = _normalize_source_reference(source_reference)
        if normalized_reference is None:
            raise ValueError("FX review requires a nonblank source_reference")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            result = self._apply_review_fx_rate(
                transaction_id,
                expected_row_version=expected_row_version,
                rate_date=rate_date,
                rate=rate,
                rate_source=rate_source,
                source_reference=normalized_reference,
                rule_version_id=rule_version_id,
                provenance=provenance,
            )
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        return result

    def _apply_review_fx_rate(
        self,
        transaction_id: str,
        *,
        expected_row_version: int,
        rate_date: str,
        rate: str,
        rate_source: str,
        source_reference: str | None,
        rule_version_id: str | None = None,
        provenance: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply a reviewed FX rate inside the caller's transaction scope."""

        normalized_reference = _normalize_source_reference(source_reference)
        if normalized_reference is None:
            raise ValueError("FX review requires a nonblank source_reference")
        canonical_rate = _canonical_decimal_text(rate)
        rate_decimal = Decimal(canonical_rate)
        if rate_decimal <= 0:
            raise ValueError("FX rate must be positive")
        parsed_rate_date = date.fromisoformat(rate_date)
        normalized_source = rate_source.strip()
        transaction = self._fetch_one(
            """
            SELECT t.*, p.period_key, p.status AS period_status
            FROM transactions t
            JOIN periods p ON p.period_id = t.period_id
            WHERE t.transaction_id = ?
            """,
            (transaction_id,),
        )
        self._check_row_version(transaction, expected_row_version)
        if transaction["lifecycle_status"] not in {
            "received",
            "extracted",
            "needs_review",
            "approved",
        }:
            raise LifecycleError(
                "FX cannot change transaction in "
                f"lifecycle_status={transaction['lifecycle_status']}"
            )
        if transaction["period_status"] != "open":
            raise ClosedPeriodError(
                f"Period {transaction['period_key']} is immutable after close"
            )
        original_currency = str(
            transaction.get("original_currency") or transaction["currency"]
        ).upper()
        if original_currency == "EUR":
            raise ValueError("EUR transactions do not require FX review")
        if normalized_source not in ALLOWED_PRODUCTION_SOURCES:
            raise ValueError(
                f"FX source {normalized_source!r} is not allowed in production"
            )
        transaction_date = date.fromisoformat(str(transaction["transaction_date"])[:10])
        if (
            normalized_source == "xolo_recorded"
            and transaction_date > XOLO_RECORDED_PRODUCTION_THROUGH
        ):
            raise ValueError(
                "FX source 'xolo_recorded' is not allowed after "
                f"{XOLO_RECORDED_PRODUCTION_THROUGH.isoformat()}"
            )
        original_minor = transaction["amount_original_minor"]
        if original_minor is None:
            raise ValueError("Transaction is missing amount_original_minor")
        fx_source_hash = _stable_hash(
            {
                "rate_date": parsed_rate_date.isoformat(),
                "base_currency": original_currency,
                "quote_currency": "EUR",
                "rate": canonical_rate,
                "rate_source": normalized_source,
                "source_reference": normalized_reference,
                "rule_version_id": rule_version_id,
            }
        )
        fx_rate = self.add_fx_rate(
            rate_date=parsed_rate_date.isoformat(),
            base_currency=original_currency,
            quote_currency="EUR",
            rate=canonical_rate,
            rate_source=normalized_source,
            source_reference=normalized_reference,
            source_hash=fx_source_hash,
            rule_version_id=rule_version_id,
            provenance=provenance,
        )
        eur_minor = int(
            (Decimal(original_minor) * rate_decimal).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
        timestamp = _utc_now()
        self.connection.execute(
            """
            UPDATE transactions
            SET amount_eur_minor = ?, fx_rate_id = ?, row_version = ?, updated_at = ?
            WHERE transaction_id = ?
            """,
            (
                eur_minor,
                fx_rate["fx_rate_id"],
                transaction["row_version"] + 1,
                timestamp,
                transaction_id,
            ),
        )
        return self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )

    def add_validation_issue(
        self,
        *,
        period_key: str,
        issue_code: str,
        severity: str,
        message: str,
        blocking: bool = True,
        subject_table: str | None = None,
        subject_id: str | None = None,
        issue_status: str = "open",
        source_hash: str | None = None,
        dedupe_key: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        if issue_status not in ISSUE_STATUSES:
            raise ValueError(f"Unsupported issue_status: {issue_status}")
        period = (
            self._ensure_period_uncommitted(period_key)
            if self.connection.in_transaction
            else self.ensure_period(period_key)
        )
        self._assert_period_mutable(period["period_id"])
        if dedupe_key:
            existing = self._fetch_optional(
                "SELECT * FROM validation_issues WHERE period_id = ? AND dedupe_key = ?",
                (period["period_id"], dedupe_key),
            )
        else:
            existing = self._fetch_optional(
                """
                SELECT * FROM validation_issues
                WHERE period_id = ? AND issue_code = ? AND COALESCE(subject_table, '') = ? AND COALESCE(subject_id, '') = ?
                """,
                (period["period_id"], issue_code, subject_table or "", subject_id or ""),
            )
        timestamp = _utc_now()
        payload = {
            "period_id": period["period_id"],
            "issue_code": issue_code,
            "severity": severity,
            "message": message,
            "blocking": bool(blocking),
            "subject_table": subject_table,
            "subject_id": subject_id,
            "issue_status": issue_status,
            "dedupe_key": dedupe_key,
        }
        effective_source_hash = source_hash or _stable_hash(payload)
        if existing is not None and all(
            (
                existing["issue_code"] == issue_code,
                existing["severity"] == severity,
                existing["message"] == message,
                existing["blocking"] == (1 if blocking else 0),
                existing["subject_table"] == subject_table,
                existing["subject_id"] == subject_id,
                existing["issue_status"] == issue_status,
                existing["source_hash"] == effective_source_hash,
                existing["dedupe_key"] == dedupe_key,
            )
        ):
            return existing

        with _write_scope(self.connection):
            if existing is None:
                issue_id = _new_id()
                self.connection.execute(
                    """
                    INSERT INTO validation_issues (
                        validation_issue_id, period_id, subject_table, subject_id, issue_code, severity,
                        message, blocking, issue_status, source_hash, row_version, created_at, updated_at,
                        dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        issue_id,
                        period["period_id"],
                        subject_table,
                        subject_id,
                        issue_code,
                        severity,
                        message,
                        1 if blocking else 0,
                        issue_status,
                        effective_source_hash,
                        timestamp,
                        timestamp,
                        dedupe_key,
                    ),
                )
                return self._fetch_one(
                    "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
                    (issue_id,),
                )

            self._check_row_version(existing, expected_row_version)
            next_version = existing["row_version"] + 1
            self.connection.execute(
                """
                UPDATE validation_issues
                SET issue_code = ?, subject_table = ?, subject_id = ?, severity = ?, message = ?,
                    blocking = ?, issue_status = ?, source_hash = ?, dedupe_key = ?,
                    resolution_reason = CASE WHEN ? = 'open' THEN NULL ELSE resolution_reason END,
                    resolved_at = CASE WHEN ? = 'open' THEN NULL ELSE resolved_at END,
                    row_version = ?, updated_at = ?
                WHERE validation_issue_id = ?
                """,
                (
                    issue_code,
                    subject_table,
                    subject_id,
                    severity,
                    message,
                    1 if blocking else 0,
                    issue_status,
                    effective_source_hash,
                    dedupe_key,
                    issue_status,
                    issue_status,
                    next_version,
                    timestamp,
                    existing["validation_issue_id"],
                ),
            )
        return self._fetch_one(
            "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
            (existing["validation_issue_id"],),
        )

    def list_issues(
        self,
        *,
        period_key: str | None = None,
        blocking_only: bool = False,
        include_resolved: bool = False,
    ) -> list[dict[str, Any]]:
        sql = [
            """
            SELECT vi.*, p.period_key
            FROM validation_issues vi
            LEFT JOIN periods p ON p.period_id = vi.period_id
            WHERE 1 = 1
            """
        ]
        params: list[Any] = []
        if period_key is not None:
            sql.append("AND p.period_key = ?")
            params.append(period_key)
        if blocking_only:
            sql.append("AND vi.blocking = 1")
        if not include_resolved:
            sql.append("AND vi.issue_status = 'open'")
        sql.append("ORDER BY p.period_key, vi.severity DESC, vi.issue_code")
        return self._fetch_all(" ".join(sql), tuple(params))

    def waive_issue(
        self,
        validation_issue_id: str,
        *,
        reason: str,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("A waiver reason is required")
        existing = self._fetch_one(
            "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
            (validation_issue_id,),
        )
        if existing["period_id"]:
            self._assert_period_mutable(existing["period_id"])
        self._check_row_version(existing, expected_row_version)
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE validation_issues
                SET issue_status = 'ignored', waiver_reason = ?, waived_at = ?,
                    row_version = ?, updated_at = ?
                WHERE validation_issue_id = ?
                """,
                (
                    reason,
                    timestamp,
                    existing["row_version"] + 1,
                    timestamp,
                    validation_issue_id,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
            (validation_issue_id,),
        )

    def resolve_issue(
        self,
        validation_issue_id: str,
        *,
        reason: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("A resolution reason is required")
        existing = self._fetch_one(
            "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
            (validation_issue_id,),
        )
        if existing["period_id"]:
            self._assert_period_mutable(existing["period_id"])
        self._check_row_version(existing, expected_row_version)
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE validation_issues
                SET issue_status = 'resolved', resolution_reason = ?, resolved_at = ?,
                    row_version = ?, updated_at = ?
                WHERE validation_issue_id = ?
                """,
                (
                    reason,
                    timestamp,
                    existing["row_version"] + 1,
                    timestamp,
                    validation_issue_id,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
            (validation_issue_id,),
        )

    def add_obligation(
        self,
        *,
        period_key: str,
        obligation_code: str,
        due_on: str | None = None,
        filing_status: str = "unknown",
        filed_at: str | None = None,
        notes: str | None = None,
        source_citation: str | None = None,
        explanation: str | None = None,
        blocking: bool | None = None,
        determination: str | None = None,
        rule_version_id: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        if filing_status not in OBLIGATION_STATUSES:
            raise ValueError(f"Unsupported filing_status: {filing_status}")
        effective_determination = determination or _determination_from_filing_status(filing_status)
        if effective_determination not in OBLIGATION_DETERMINATIONS:
            raise ValueError(f"Unsupported obligation determination: {effective_determination}")
        effective_blocking = (
            blocking
            if blocking is not None
            else _obligation_blocks(effective_determination, filing_status)
        )
        period = self.ensure_period(period_key)
        self._assert_period_mutable(period["period_id"])
        existing = self._fetch_optional(
            "SELECT * FROM obligations WHERE period_id = ? AND obligation_code = ?",
            (period["period_id"], obligation_code),
        )
        timestamp = _utc_now()
        payload = {
            "period_id": period["period_id"],
            "obligation_code": obligation_code,
            "due_on": due_on,
            "filing_status": filing_status,
            "filed_at": filed_at,
            "notes": notes,
            "source_citation": source_citation,
            "explanation": explanation,
            "blocking": effective_blocking,
            "determination": effective_determination,
            "rule_version_id": rule_version_id,
        }
        effective_source_hash = source_hash or _stable_hash(payload)
        if existing is not None and all(
            (
                existing["due_on"] == due_on,
                existing["filing_status"] == filing_status,
                existing["filed_at"] == filed_at,
                existing["notes"] == notes,
                existing["source_hash"] == effective_source_hash,
                existing["source_citation"] == source_citation,
                existing["explanation"] == explanation,
                existing["blocking"] == int(effective_blocking),
                existing["determination"] == effective_determination,
                existing["rule_version_id"] == rule_version_id,
            )
        ):
            if (
                obligation_code == "216"
                and effective_determination in {"due", "not_due"}
            ):
                self._resolve_period_irnr_review_issues(
                    period_id=str(period["period_id"])
                )
            return existing

        with self.connection:
            if existing is None:
                obligation_id = _new_id()
                self.connection.execute(
                    """
                    INSERT INTO obligations (
                        obligation_id, period_id, obligation_code, due_on, filing_status, filed_at, notes,
                        source_hash, row_version, created_at, updated_at, source_citation,
                        explanation, blocking, determination, rule_version_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        obligation_id,
                        period["period_id"],
                        obligation_code,
                        due_on,
                        filing_status,
                        filed_at,
                        notes,
                        effective_source_hash,
                        timestamp,
                        timestamp,
                        source_citation,
                        explanation,
                        int(effective_blocking),
                        effective_determination,
                        rule_version_id,
                    ),
                )
            else:
                obligation_id = str(existing["obligation_id"])
                self._check_row_version(existing, expected_row_version)
                next_version = existing["row_version"] + 1
                self.connection.execute(
                    """
                    UPDATE obligations
                    SET due_on = ?, filing_status = ?, filed_at = ?, notes = ?, source_hash = ?,
                        source_citation = ?, explanation = ?, blocking = ?, determination = ?,
                        rule_version_id = ?, row_version = ?, updated_at = ?
                    WHERE obligation_id = ?
                    """,
                    (
                        due_on,
                        filing_status,
                        filed_at,
                        notes,
                        effective_source_hash,
                        source_citation,
                        explanation,
                        int(effective_blocking),
                        effective_determination,
                        rule_version_id,
                        next_version,
                        timestamp,
                        obligation_id,
                    ),
                )
        if (
            obligation_code == "216"
            and effective_determination in {"due", "not_due"}
        ):
            self._resolve_period_irnr_review_issues(
                period_id=str(period["period_id"])
            )
        return self._fetch_one(
            "SELECT * FROM obligations WHERE obligation_id = ?",
            (obligation_id,),
        )

    def list_obligations(self, *, period_key: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT o.*, p.period_key
            FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
        """
        params: tuple[Any, ...] = ()
        if period_key is not None:
            sql += " WHERE p.period_key = ?"
            params = (period_key,)
        sql += " ORDER BY p.starts_on, o.obligation_code"
        return self._fetch_all(sql, params)

    def add_obligation_evidence(
        self,
        *,
        obligation_id: str,
        evidence_kind: str,
        source_reference: str,
        source_hash: str,
        notes: str | None = None,
    ) -> dict[str, Any]:
        if evidence_kind not in OBLIGATION_EVIDENCE_KINDS:
            raise ValueError(f"Unsupported obligation evidence kind: {evidence_kind}")
        _validate_sha256(source_hash)
        self._fetch_one(
            "SELECT obligation_id FROM obligations WHERE obligation_id = ?",
            (obligation_id,),
        )
        existing = self._fetch_optional(
            """
            SELECT * FROM obligation_evidence
            WHERE obligation_id = ? AND evidence_kind = ? AND source_hash = ?
            """,
            (obligation_id, evidence_kind, source_hash.casefold()),
        )
        if existing is not None:
            desired = {
                "source_reference": source_reference,
                "notes": notes,
            }
            changed = [
                key for key, value in desired.items() if existing[key] != value
            ]
            if changed:
                raise LedgerDbError(
                    "Obligation evidence hash already exists with different fields: "
                    + ", ".join(sorted(changed))
                )
            return existing
        evidence_id = _new_id()
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO obligation_evidence (
                    obligation_evidence_id, obligation_id, evidence_kind,
                    source_reference, source_hash, notes, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    evidence_id,
                    obligation_id,
                    evidence_kind,
                    source_reference,
                    source_hash.casefold(),
                    notes,
                    timestamp,
                    timestamp,
                ),
            )
        return self._fetch_one(
            """
            SELECT * FROM obligation_evidence
            WHERE obligation_evidence_id = ?
            """,
            (evidence_id,),
        )

    def list_obligation_evidence(
        self,
        *,
        obligation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT oe.*, o.obligation_code, p.period_key
            FROM obligation_evidence oe
            JOIN obligations o ON o.obligation_id = oe.obligation_id
            JOIN periods p ON p.period_id = o.period_id
        """
        params: tuple[Any, ...] = ()
        if obligation_id is not None:
            sql += " WHERE oe.obligation_id = ?"
            params = (obligation_id,)
        sql += " ORDER BY p.starts_on, o.obligation_code, oe.evidence_kind, oe.created_at"
        return self._fetch_all(sql, params)

    def import_tax_calendar(self, entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
        rows = [dict(entry) for entry in entries]
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            period_ids: dict[str, str] = {}
            for row in rows:
                period_key = str(row["period_key"])
                period_ids[period_key] = self._ensure_period_uncommitted(period_key)["period_id"]
            for row in rows:
                period_key = str(row["period_key"])
                period_id = period_ids[period_key]
                identity = (period_id, str(row["form_code"]))
                existing = self._fetch_optional(
                    """
                    SELECT * FROM tax_calendar_entries
                    WHERE period_id = ? AND form_code = ?
                    """,
                    identity,
                )
                payload = {
                    "calendar_year": int(row["calendar_year"]),
                    "period_id": period_id,
                    "form_code": str(row["form_code"]),
                    "filing_opens_on": str(row["filing_opens_on"]),
                    "internal_due_on": str(row["internal_due_on"]),
                    "direct_debit_cutoff_on": (
                        str(row["direct_debit_cutoff_on"])
                        if row.get("direct_debit_cutoff_on")
                        else None
                    ),
                    "statutory_due_on": str(row["statutory_due_on"]),
                    "deadline_status": str(row["deadline_status"]),
                    "source_url": str(row["source_url"]),
                    "source_checked_on": str(row["source_checked_on"]),
                    "notes": str(row.get("notes") or ""),
                    "source_hash": str(row["source_hash"]),
                }
                _validate_tax_calendar_payload({**payload, "period_key": period_key})
                timestamp = _utc_now()
                if existing is None:
                    self.connection.execute(
                        """
                        INSERT INTO tax_calendar_entries (
                            tax_calendar_entry_id, calendar_year, period_id, form_code,
                            filing_opens_on, internal_due_on, direct_debit_cutoff_on,
                            statutory_due_on, deadline_status, source_url, source_checked_on,
                            notes, source_hash, row_version, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            _new_id(),
                            payload["calendar_year"],
                            payload["period_id"],
                            payload["form_code"],
                            payload["filing_opens_on"],
                            payload["internal_due_on"],
                            payload["direct_debit_cutoff_on"],
                            payload["statutory_due_on"],
                            payload["deadline_status"],
                            payload["source_url"],
                            payload["source_checked_on"],
                            payload["notes"],
                            payload["source_hash"],
                            timestamp,
                            timestamp,
                        ),
                    )
                    continue
                if all(existing[key] == value for key, value in payload.items()):
                    continue
                self.connection.execute(
                    """
                    UPDATE tax_calendar_entries
                    SET calendar_year = ?, filing_opens_on = ?, internal_due_on = ?,
                        direct_debit_cutoff_on = ?, statutory_due_on = ?, deadline_status = ?,
                        source_url = ?, source_checked_on = ?, notes = ?, source_hash = ?,
                        row_version = ?, updated_at = ?
                    WHERE tax_calendar_entry_id = ?
                    """,
                    (
                        payload["calendar_year"],
                        payload["filing_opens_on"],
                        payload["internal_due_on"],
                        payload["direct_debit_cutoff_on"],
                        payload["statutory_due_on"],
                        payload["deadline_status"],
                        payload["source_url"],
                        payload["source_checked_on"],
                        payload["notes"],
                        payload["source_hash"],
                        existing["row_version"] + 1,
                        timestamp,
                        existing["tax_calendar_entry_id"],
                    ),
                )
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        identities = {(str(row["period_key"]), str(row["form_code"])) for row in rows}
        return [
            row
            for row in self.list_tax_calendar_entries()
            if (row["period_key"], row["form_code"]) in identities
        ]

    def list_tax_calendar_entries(
        self,
        *,
        calendar_year: int | None = None,
        period_key: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if calendar_year is not None:
            clauses.append("tc.calendar_year = ?")
            params.append(calendar_year)
        if period_key is not None:
            clauses.append("p.period_key = ?")
            params.append(period_key)
        sql = """
            SELECT tc.*, p.period_key
            FROM tax_calendar_entries tc
            JOIN periods p ON p.period_id = tc.period_id
        """
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY tc.statutory_due_on, p.period_key, tc.form_code"
        return self._fetch_all(sql, tuple(params))

    def list_obligations_with_deadlines(self, *, period_key: str) -> list[dict[str, Any]]:
        rows = self._fetch_all(
            """
            SELECT o.*, p.period_key, o.due_on AS obligation_due_on,
                   tc.tax_calendar_entry_id, tc.filing_opens_on,
                   tc.internal_due_on, tc.direct_debit_cutoff_on,
                   tc.statutory_due_on AS calendar_statutory_due_on,
                   tc.deadline_status, tc.source_url AS calendar_source_url,
                   tc.source_checked_on AS calendar_source_checked_on
            FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            LEFT JOIN tax_calendar_entries tc
              ON tc.period_id = o.period_id AND tc.form_code = o.obligation_code
            WHERE p.period_key = ?
            ORDER BY o.obligation_code
            """,
            (period_key,),
        )
        for row in rows:
            confirmed_due = (
                row["calendar_statutory_due_on"]
                if row["deadline_status"] == "confirmed"
                else None
            )
            row["due_on"] = row["obligation_due_on"] or confirmed_due
            row["calendar_deadline_mismatch"] = bool(
                row["obligation_due_on"]
                and confirmed_due
                and row["obligation_due_on"] != confirmed_due
            )
        return rows

    def add_payment(
        self,
        *,
        paid_on: str,
        amount_minor: int,
        currency: str,
        source_hash: str,
        transaction_id: str | None = None,
        obligation_id: str | None = None,
        original_reference: str | None = None,
        fee_minor: int | None = None,
        fee_currency: str | None = None,
        match_status: str = "unmatched",
        source_system: str | None = None,
        external_id: str | None = None,
        account_name: str | None = None,
        counterparty_name: str | None = None,
        category: str | None = None,
        comment: str | None = None,
        amount_eur_minor: int | None = None,
        source_row_json: str | None = None,
    ) -> dict[str, Any]:
        if match_status not in {"exact", "ambiguous", "unmatched", "manual"}:
            raise ValueError(f"Unsupported payment match status: {match_status}")
        try:
            payment_date = date.fromisoformat(paid_on[:10])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"paid_on must begin with an ISO date: {paid_on!r}") from exc
        paid_on = payment_date.isoformat()
        existing = self._fetch_optional("SELECT * FROM payments WHERE source_hash = ?", (source_hash,))
        if existing is not None:
            desired = {
                "transaction_id": transaction_id,
                "obligation_id": obligation_id,
                "paid_on": paid_on,
                "amount_minor": amount_minor,
                "currency": currency,
                "original_reference": original_reference,
                "fee_minor": fee_minor,
                "fee_currency": fee_currency,
                "match_status": match_status,
                "source_system": source_system,
                "external_id": external_id,
                "account_name": account_name,
                "counterparty_name": counterparty_name,
                "category": category,
                "comment": comment,
                "amount_eur_minor": amount_eur_minor,
                "source_row_json": source_row_json,
            }
            changed = [key for key, value in desired.items() if existing[key] != value]
            if changed:
                raise LedgerDbError(
                    f"Payment source_hash already exists with different fields: {', '.join(sorted(changed))}"
                )
            if transaction_id is not None:
                self._ensure_future_irnr_payment_review(
                    transaction_id=transaction_id,
                    payment_date=payment_date,
                )
            return existing
        if transaction_id:
            self._fetch_one(
                "SELECT transaction_id FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            )
        if obligation_id:
            self._fetch_one(
                "SELECT obligation_id FROM obligations WHERE obligation_id = ?",
                (obligation_id,),
            )
        timestamp = _utc_now()
        payment_id = _new_id()
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO payments (
                    payment_id, transaction_id, obligation_id, paid_on, amount_minor, currency,
                    source_hash, row_version, created_at, updated_at, original_reference,
                    fee_minor, fee_currency, match_status, source_system, external_id,
                    account_name, counterparty_name, category, comment, amount_eur_minor,
                    source_row_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payment_id,
                    transaction_id,
                    obligation_id,
                    paid_on,
                    amount_minor,
                    currency,
                    source_hash,
                    timestamp,
                    timestamp,
                    original_reference,
                    fee_minor,
                    fee_currency,
                    match_status,
                    source_system,
                    external_id,
                    account_name,
                    counterparty_name,
                    category,
                    comment,
                    amount_eur_minor,
                    source_row_json,
                ),
            )
        if transaction_id is not None:
            self._ensure_future_irnr_payment_review(
                transaction_id=transaction_id,
                payment_date=payment_date,
            )
        return self._fetch_one("SELECT * FROM payments WHERE payment_id = ?", (payment_id,))

    def add_asset(
        self,
        *,
        asset_code: str,
        cost_minor: int,
        currency: str,
        depreciation_method: str,
        source_hash: str,
        document_id: str | None = None,
        acquisition_transaction_id: str | None = None,
        placed_in_service_on: str | None = None,
        useful_life_months: int | None = None,
        amortizable_base_minor: int | None = None,
        iva_treatment: str = "unknown",
        business_use_ratio: float | None = None,
        annual_rate_basis_points: int | None = None,
        advisor_decision: str | None = None,
        advisor_decision_on: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        if business_use_ratio is not None and not 0 <= business_use_ratio <= 1:
            raise ValueError("business_use_ratio must be between 0 and 1")
        existing = self._fetch_optional("SELECT * FROM assets WHERE asset_code = ?", (asset_code,))
        if existing is not None:
            self._assert_asset_links_mutable(existing["document_id"], existing["acquisition_transaction_id"])
            amortization_periods = self._fetch_all(
                "SELECT period_id FROM amortization_entries WHERE asset_id = ?",
                (existing["asset_id"],),
            )
            for amortization_period in amortization_periods:
                self._assert_period_mutable(amortization_period["period_id"])
            self._check_row_version(existing, expected_row_version)
        self._assert_asset_links_mutable(document_id, acquisition_transaction_id)
        timestamp = _utc_now()
        with _write_scope(self.connection):
            if existing is None:
                asset_id = _new_id()
                self.connection.execute(
                    """
                    INSERT INTO assets (
                        asset_id, document_id, acquisition_transaction_id, asset_code,
                        placed_in_service_on, cost_minor, currency, depreciation_method,
                        useful_life_months, source_hash, row_version, created_at, updated_at,
                        amortizable_base_minor, iva_treatment, business_use_ratio,
                        annual_rate_basis_points, advisor_decision, advisor_decision_on
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        asset_id,
                        document_id,
                        acquisition_transaction_id,
                        asset_code,
                        placed_in_service_on,
                        cost_minor,
                        currency,
                        depreciation_method,
                        useful_life_months,
                        source_hash,
                        timestamp,
                        timestamp,
                        amortizable_base_minor,
                        iva_treatment,
                        business_use_ratio,
                        annual_rate_basis_points,
                        advisor_decision,
                        advisor_decision_on,
                    ),
                )
            else:
                asset_id = existing["asset_id"]
                self.connection.execute(
                    """
                    UPDATE assets
                    SET document_id = ?, acquisition_transaction_id = ?, placed_in_service_on = ?,
                        cost_minor = ?, currency = ?, depreciation_method = ?, useful_life_months = ?,
                        source_hash = ?, amortizable_base_minor = ?, iva_treatment = ?,
                        business_use_ratio = ?, annual_rate_basis_points = ?, advisor_decision = ?,
                        advisor_decision_on = ?, row_version = ?, updated_at = ?
                    WHERE asset_id = ?
                    """,
                    (
                        document_id,
                        acquisition_transaction_id,
                        placed_in_service_on,
                        cost_minor,
                        currency,
                        depreciation_method,
                        useful_life_months,
                        source_hash,
                        amortizable_base_minor,
                        iva_treatment,
                        business_use_ratio,
                        annual_rate_basis_points,
                        advisor_decision,
                        advisor_decision_on,
                        existing["row_version"] + 1,
                        timestamp,
                        asset_id,
                    ),
                )
        return self._fetch_one("SELECT * FROM assets WHERE asset_id = ?", (asset_id,))

    def update_asset_decision(
        self,
        asset_id: str,
        *,
        advisor_decision: str | None,
        advisor_decision_on: str | None,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one("SELECT * FROM assets WHERE asset_id = ?", (asset_id,))
        return self.add_asset(
            asset_code=existing["asset_code"],
            cost_minor=existing["cost_minor"],
            currency=existing["currency"],
            depreciation_method=existing["depreciation_method"],
            source_hash=existing["source_hash"],
            document_id=existing["document_id"],
            acquisition_transaction_id=existing["acquisition_transaction_id"],
            placed_in_service_on=existing["placed_in_service_on"],
            useful_life_months=existing["useful_life_months"],
            amortizable_base_minor=existing["amortizable_base_minor"],
            iva_treatment=existing["iva_treatment"],
            business_use_ratio=existing["business_use_ratio"],
            annual_rate_basis_points=existing["annual_rate_basis_points"],
            advisor_decision=advisor_decision,
            advisor_decision_on=advisor_decision_on,
            expected_row_version=expected_row_version,
        )

    def update_asset_book_profile(
        self,
        asset_id: str,
        *,
        business_activity_id: str,
        aeat_asset_type: str,
        description: str,
        aeat_asset_identifier: str,
        aeat_amortization_method: str,
        source_invoice_number: str,
        acquisition_taxable_base_minor: int,
        acquisition_vat_rate_basis_points: int,
        acquisition_deductible_vat_minor: int,
        iva_treatment: str,
        business_use_ratio: float,
        book_profile_source_reference: str,
        book_profile_source_hash: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        existing = self._fetch_one("SELECT * FROM assets WHERE asset_id = ?", (asset_id,))
        if not existing["placed_in_service_on"]:
            raise ValueError("Asset book profile requires placed_in_service_on")
        effective_activity_id = self._resolve_business_activity_id(
            business_activity_id,
            transaction_date=existing["placed_in_service_on"],
        )
        aeat_asset_type = aeat_asset_type.strip()
        description = description.strip()
        aeat_asset_identifier = aeat_asset_identifier.strip()
        aeat_amortization_method = aeat_amortization_method.strip()
        source_invoice_number = source_invoice_number.strip()
        iva_treatment = iva_treatment.strip()
        book_profile_source_reference = book_profile_source_reference.strip()
        book_profile_source_hash = book_profile_source_hash.strip().lower()
        if len(aeat_asset_type) != 2 or not aeat_asset_type.isdigit():
            raise ValueError("AEAT asset type must contain two digits")
        if len(aeat_amortization_method) != 2:
            raise ValueError("AEAT amortization method must contain two characters")
        if not all(
            (
                description,
                aeat_asset_identifier,
                source_invoice_number,
                iva_treatment,
                book_profile_source_reference,
                book_profile_source_hash,
            )
        ):
            raise ValueError("Asset book profile fields and source evidence are required")
        if not 0 <= business_use_ratio <= 1:
            raise ValueError("business_use_ratio must be between 0 and 1")
        if not 0 <= acquisition_vat_rate_basis_points <= 10_000:
            raise ValueError("acquisition_vat_rate_basis_points must be between 0 and 10000")
        if min(
            acquisition_taxable_base_minor,
            acquisition_deductible_vat_minor,
        ) < 0:
            raise ValueError("Asset acquisition tax amounts cannot be negative")
        desired = {
            "business_activity_id": effective_activity_id,
            "aeat_asset_type": aeat_asset_type,
            "description": description,
            "aeat_asset_identifier": aeat_asset_identifier,
            "aeat_amortization_method": aeat_amortization_method,
            "source_invoice_number": source_invoice_number,
            "acquisition_taxable_base_minor": acquisition_taxable_base_minor,
            "acquisition_vat_rate_basis_points": acquisition_vat_rate_basis_points,
            "acquisition_deductible_vat_minor": acquisition_deductible_vat_minor,
            "iva_treatment": iva_treatment,
            "business_use_ratio": business_use_ratio,
            "book_profile_source_reference": book_profile_source_reference,
            "book_profile_source_hash": book_profile_source_hash,
        }
        if all(existing[key] == value for key, value in desired.items()):
            return existing
        self._check_row_version(existing, expected_row_version)
        self._assert_asset_links_mutable(
            existing["document_id"],
            existing["acquisition_transaction_id"],
        )
        for amortization_period in self._fetch_all(
            "SELECT period_id FROM amortization_entries WHERE asset_id = ?",
            (asset_id,),
        ):
            self._assert_period_mutable(amortization_period["period_id"])
        timestamp = _utc_now()
        with _write_scope(self.connection):
            self.connection.execute(
                """
                UPDATE assets
                SET business_activity_id = ?, aeat_asset_type = ?, description = ?,
                    aeat_asset_identifier = ?, aeat_amortization_method = ?,
                    source_invoice_number = ?, acquisition_taxable_base_minor = ?,
                    acquisition_vat_rate_basis_points = ?, acquisition_deductible_vat_minor = ?,
                    iva_treatment = ?, business_use_ratio = ?, book_profile_source_reference = ?,
                    book_profile_source_hash = ?, row_version = ?, updated_at = ?
                WHERE asset_id = ?
                """,
                (
                    effective_activity_id,
                    aeat_asset_type,
                    description,
                    aeat_asset_identifier,
                    aeat_amortization_method,
                    source_invoice_number,
                    acquisition_taxable_base_minor,
                    acquisition_vat_rate_basis_points,
                    acquisition_deductible_vat_minor,
                    iva_treatment,
                    business_use_ratio,
                    book_profile_source_reference,
                    book_profile_source_hash,
                    existing["row_version"] + 1,
                    timestamp,
                    asset_id,
                ),
            )
        return self._fetch_one("SELECT * FROM assets WHERE asset_id = ?", (asset_id,))

    def add_amortization_entry(
        self,
        *,
        asset_id: str,
        period_key: str,
        amount_minor: int,
        source_hash: str,
        entry_kind: str = "quarter_schedule",
        tax_year: int | None = None,
        source_book_line_id: str | None = None,
        include_in_books: bool | None = None,
    ) -> dict[str, Any]:
        if entry_kind not in {"quarter_schedule", "annual_evidence", "adjustment"}:
            raise ValueError(f"Unsupported amortization entry kind: {entry_kind}")
        effective_tax_year = tax_year or int(period_key[:4])
        effective_include = entry_kind != "annual_evidence" if include_in_books is None else include_in_books
        period = self.ensure_period(period_key)
        self._assert_period_mutable(period["period_id"])
        existing = self._fetch_optional(
            "SELECT * FROM amortization_entries WHERE asset_id = ? AND period_id = ?",
            (asset_id, period["period_id"]),
        )
        timestamp = _utc_now()
        with _write_scope(self.connection):
            if existing is None:
                entry_id = _new_id()
                self.connection.execute(
                    """
                    INSERT INTO amortization_entries (
                        amortization_entry_id, asset_id, period_id, amount_minor, source_hash,
                        row_version, created_at, updated_at, entry_kind, tax_year,
                        source_book_line_id, include_in_books
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        asset_id,
                        period["period_id"],
                        amount_minor,
                        source_hash,
                        timestamp,
                        timestamp,
                        entry_kind,
                        effective_tax_year,
                        source_book_line_id,
                        int(effective_include),
                    ),
                )
            else:
                entry_id = existing["amortization_entry_id"]
                self.connection.execute(
                    """
                    UPDATE amortization_entries
                    SET amount_minor = ?, source_hash = ?, entry_kind = ?, tax_year = ?,
                        source_book_line_id = ?, include_in_books = ?, row_version = ?, updated_at = ?
                    WHERE amortization_entry_id = ?
                    """,
                    (
                        amount_minor,
                        source_hash,
                        entry_kind,
                        effective_tax_year,
                        source_book_line_id,
                        int(effective_include),
                        existing["row_version"] + 1,
                        timestamp,
                        entry_id,
                    ),
                )
        return self._fetch_one(
            "SELECT * FROM amortization_entries WHERE amortization_entry_id = ?",
            (entry_id,),
        )

    def delete_amortization_entry(self, amortization_entry_id: str) -> None:
        existing = self._fetch_one(
            """
            SELECT amortization_entry_id, period_id
            FROM amortization_entries
            WHERE amortization_entry_id = ?
            """,
            (amortization_entry_id,),
        )
        self._assert_period_mutable(existing["period_id"])
        with self.connection:
            self.connection.execute(
                "DELETE FROM amortization_entries WHERE amortization_entry_id = ?",
                (amortization_entry_id,),
            )

    def validate_asset_year(self, tax_year: int) -> dict[str, Any]:
        rows = self._fetch_all(
            """
            SELECT ae.*, a.asset_code, p.period_key
            FROM amortization_entries ae
            JOIN assets a ON a.asset_id = ae.asset_id
            JOIN periods p ON p.period_id = ae.period_id
            WHERE ae.tax_year = ?
            ORDER BY a.asset_code, ae.entry_kind, p.period_key
            """,
            (tax_year,),
        )
        current_asset_ids = {row["asset_id"] for row in rows}
        assets = self._fetch_all(
            """
            SELECT a.*,
                   COALESCE(SUM(
                       CASE
                           WHEN ae.entry_kind = 'annual_evidence' AND ae.tax_year < ?
                           THEN ae.amount_minor
                           ELSE 0
                       END
                   ), 0) AS prior_annual_minor
            FROM assets a
            LEFT JOIN amortization_entries ae ON ae.asset_id = a.asset_id
            GROUP BY a.asset_id
            ORDER BY a.asset_code, a.asset_id
            """,
            (tax_year,),
        )
        year_end = date(tax_year, 12, 31)
        grouped: dict[str, dict[str, Any]] = {}
        issues: list[dict[str, Any]] = []
        for asset in assets:
            placed_on = (
                date.fromisoformat(asset["placed_in_service_on"])
                if asset["placed_in_service_on"]
                else None
            )
            has_current_rows = asset["asset_id"] in current_asset_ids
            if placed_on is not None and placed_on > year_end and not has_current_rows:
                continue
            effective_base_minor = _effective_asset_base_minor(asset)
            from .depreciation import native_year_summary
            native = native_year_summary(self.connection, asset["asset_id"], tax_year)
            if native is not None:
                bucket = _empty_asset_year_bucket(asset)
                bucket.update(annual_minor=native["current_minor"], quarter_minor=native["current_minor"],
                              source_kind="native_posted_journals")
                grouped[asset["asset_id"]] = bucket
                if native["pending"]:
                    issues.append({"issue_code": "pending_native_depreciation", **bucket})
                if native["prior_minor"] + native["current_minor"] > effective_base_minor:
                    issues.append({"issue_code": "native_depreciation_exceeds_basis", **bucket})
                continue
            fully_amortized_before_year = (
                effective_base_minor > 0
                and int(asset["prior_annual_minor"]) >= effective_base_minor - 1
            )
            if fully_amortized_before_year and not has_current_rows:
                continue
            grouped[asset["asset_id"]] = _empty_asset_year_bucket(asset)

        for row in rows:
            bucket = grouped.setdefault(
                row["asset_id"],
                _empty_asset_year_bucket(row),
            )
            if bucket.get("source_kind") == "native_posted_journals":
                continue
            if row["entry_kind"] == "annual_evidence":
                bucket["annual_minor"] += row["amount_minor"]
                bucket["annual_rows"] += 1
            elif row["entry_kind"] in {"quarter_schedule", "adjustment"}:
                bucket["quarter_minor"] += row["amount_minor"]
                bucket["quarter_rows"] += 1

        for bucket in grouped.values():
            if bucket.get("source_kind") == "native_posted_journals":
                continue
            if bucket["annual_rows"] == 0:
                issues.append({"issue_code": "missing_annual_asset_evidence", **bucket})
            if bucket["quarter_rows"] == 0:
                issues.append({"issue_code": "missing_quarter_asset_schedule", **bucket})
            elif abs(bucket["annual_minor"] - bucket["quarter_minor"]) > 1:
                issues.append({"issue_code": "asset_amortization_mismatch", **bucket})
        orphan_assets = {
            issue["asset_id"]
            for issue in issues
            if issue["issue_code"] in {
                "missing_annual_asset_evidence",
                "missing_quarter_asset_schedule",
            }
        }
        return {
            "tax_year": tax_year,
            "assets": list(grouped.values()),
            "issues": issues,
            "ready": not issues,
            "orphan_count": len(orphan_assets),
        }

    def close_period(
        self,
        period_key: str,
        *,
        expected_row_version: int | None = None,
        allow_authoritative_history: bool = False,
    ) -> dict[str, Any]:
        period = self._require_period(period_key)
        if period["status"] != "open":
            raise PeriodStateError(f"Only open periods can be closed: {period_key}")
        if period["period_type"] == "annual" or (len(period_key) == 4 and period_key.isdigit()):
            asset_validation = self.validate_asset_year(int(period_key))
            if not asset_validation["ready"]:
                raise BlockingIssueError(
                    f"Cannot close {period_key}; annual asset amortization is unresolved"
                )

        validation = self.validate_period(
            period_key,
            allow_authoritative_history=allow_authoritative_history,
        )
        blocking_issues = validation["blocking_issues"]
        if blocking_issues:
            codes = ", ".join(issue["issue_code"] for issue in blocking_issues)
            raise BlockingIssueError(f"Cannot close {period_key}; open blocking issues: {codes}")

        unresolved_obligations = validation["unresolved_obligations"]
        if unresolved_obligations:
            codes = ", ".join(obligation["obligation_code"] for obligation in unresolved_obligations)
            raise ObligationStateError(
                f"Cannot close {period_key}; unknown or due-but-unfiled obligations: {codes}"
            )
        review_items = validation["review_documents"] + validation["review_transactions"]
        if review_items:
            raise BlockingIssueError(
                f"Cannot close {period_key}; documents or transactions still require review"
            )
        if validation["target_derived_fx"]:
            raise BlockingIssueError(f"Cannot close {period_key}; target-derived FX is historical-only")
        if validation["xolo_recorded_fx_after_cutover"]:
            raise BlockingIssueError(
                f"Cannot close {period_key}; post-cutover Xolo FX is historical-only"
            )
        if validation["invalid_amount_transactions"]:
            raise BlockingIssueError(
                f"Cannot close {period_key}; posted transactions have invalid monetary amounts"
            )
        if validation["out_of_period_transactions"]:
            raise BlockingIssueError(
                f"Cannot close {period_key}; transaction dates fall outside period bounds"
            )

        timestamp = _utc_now()
        self._check_row_version(period, expected_row_version)
        with self.connection:
            self.connection.execute(
                """
                UPDATE periods
                SET status = 'closed', closed_at = ?, row_version = ?, updated_at = ?
                WHERE period_id = ?
                """,
                (
                    timestamp,
                    period["row_version"] + 1,
                    timestamp,
                    period["period_id"],
                ),
            )
        return self._require_period(period_key)

    def amend_period(
        self,
        period_key: str,
        *,
        amendment_period_key: str,
        reason: str,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        period = self._require_period(period_key)
        if period["status"] != "closed":
            raise PeriodStateError(f"Only closed periods can be amended: {period_key}")
        amendment_period = self.ensure_period(amendment_period_key)
        if amendment_period["status"] != "open":
            raise PeriodStateError(f"Amendment period must stay open: {amendment_period_key}")

        timestamp = _utc_now()
        self._check_row_version(period, expected_row_version)
        with self.connection:
            self.connection.execute(
                """
                UPDATE periods
                SET status = 'amended', amendment_period_id = ?, amendment_reason = ?,
                    row_version = ?, updated_at = ?
                WHERE period_id = ?
                """,
                (
                    amendment_period["period_id"],
                    reason,
                    period["row_version"] + 1,
                    timestamp,
                    period["period_id"],
                ),
            )
        return self._require_period(period_key)

    def create_filing_snapshot(
        self,
        period_key: str,
        *,
        payload: Mapping[str, Any] | None = None,
        filed_on: str | None = None,
        status: str = "draft",
        snapshot_hash: str | None = None,
        source_hash: str | None = None,
        form_code: str | None = None,
        submission_reference: str | None = None,
        justificante_number: str | None = None,
        verification_code: str | None = None,
        source_reference: str | None = None,
    ) -> dict[str, Any]:
        period = self._require_period(period_key)
        is_final = status in FINAL_SNAPSHOT_STATUSES
        if is_final and period["status"] not in {"closed", "amended"}:
            raise PeriodStateError(f"Final snapshots require a closed or amended period: {period_key}")

        payload_data = dict(payload or {})
        effective_form_code = (form_code or str(payload_data.get("form") or "")).strip() or None
        if effective_form_code is not None:
            payload_data.setdefault("form", effective_form_code)
        payload_json = json.dumps(payload_data, sort_keys=True, separators=(",", ":"))
        effective_filed_on = filed_on or _utc_now()
        snapshot_fingerprint = snapshot_hash or _stable_hash(
            {
                "period_key": period_key,
                "filed_on": effective_filed_on,
                "status": status,
                "payload": payload_json,
                "form_code": effective_form_code,
                "submission_reference": submission_reference,
                "justificante_number": justificante_number,
                "verification_code": verification_code,
                "source_reference": source_reference,
            }
        )
        timestamp = _utc_now()
        snapshot_id = _new_id()

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO filing_snapshots (
                    filing_snapshot_id, period_id, snapshot_hash, filed_on, status, payload_json,
                    source_hash, row_version, created_at, updated_at, form_code,
                    submission_reference, justificante_number, verification_code, source_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    period["period_id"],
                    snapshot_fingerprint,
                    effective_filed_on,
                    status,
                    payload_json,
                    source_hash or snapshot_fingerprint,
                    timestamp,
                    timestamp,
                    effective_form_code,
                    submission_reference,
                    justificante_number,
                    verification_code,
                    source_reference,
                ),
            )
            posted_transactions = self._fetch_all(
                """
                SELECT transaction_id, lifecycle_status, row_version
                FROM transactions
                WHERE period_id = ? AND lifecycle_status = 'posted'
                """,
                (period["period_id"],),
            )
            for transaction in posted_transactions if is_final else []:
                _validate_lifecycle_transition(transaction["lifecycle_status"], "included_in_snapshot")
                self.connection.execute(
                    """
                    UPDATE transactions
                    SET lifecycle_status = 'included_in_snapshot', included_snapshot_id = ?,
                        row_version = ?, updated_at = ?
                    WHERE transaction_id = ?
                    """,
                    (
                        snapshot_id,
                        transaction["row_version"] + 1,
                        timestamp,
                        transaction["transaction_id"],
                    ),
                )
        return self._fetch_one(
            "SELECT * FROM filing_snapshots WHERE filing_snapshot_id = ?",
            (snapshot_id,),
        )

    def list_filing_snapshots(self) -> list[dict[str, Any]]:
        return self._fetch_all(
            """
            SELECT fs.*, p.period_key
            FROM filing_snapshots fs
            JOIN periods p ON p.period_id = fs.period_id
            ORDER BY p.starts_on, fs.form_code, fs.filed_on, fs.created_at
            """
        )

    def validate_period(
        self,
        period_key: str,
        *,
        allow_authoritative_history: bool = False,
    ) -> dict[str, Any]:
        period = self._require_period(period_key)
        blocking_issues = self._fetch_all(
            """
            SELECT validation_issue_id, issue_code, message
            FROM validation_issues
            WHERE period_id = ? AND blocking = 1 AND issue_status = 'open'
            ORDER BY issue_code
            """,
            (period["period_id"],),
        )
        unresolved_obligations = self._fetch_all(
            """
            SELECT obligation_code, determination, filing_status, due_on, explanation
            FROM obligations
            WHERE period_id = ? AND blocking = 1
              AND (
                    determination = 'unknown'
                    OR (determination = 'due' AND filing_status NOT IN ('filed', 'waived'))
                  )
            ORDER BY obligation_code
            """,
            (period["period_id"],),
        )
        review_documents = self._fetch_all(
            """
            SELECT document_id, lifecycle_status
            FROM documents
            WHERE period_id = ? AND lifecycle_status IN ('received', 'extracted', 'needs_review')
            ORDER BY document_id
            """,
            (period["period_id"],),
        )
        review_transactions = self._fetch_all(
            """
            SELECT transaction_id, lifecycle_status
            FROM transactions
            WHERE period_id = ? AND lifecycle_status IN ('received', 'extracted', 'needs_review', 'approved')
            ORDER BY transaction_id
            """,
            (period["period_id"],),
        )
        authoritative_history_transactions = (
            self.list_authoritative_history_transactions(period_key=period_key)
            if allow_authoritative_history
            else []
        )
        authoritative_ids = {
            row["transaction_id"] for row in authoritative_history_transactions
        }
        review_transactions = [
            row for row in review_transactions if row["transaction_id"] not in authoritative_ids
        ]
        target_derived_fx = self._fetch_all(
            """
            SELECT t.transaction_id
            FROM transactions t
            JOIN fx_rates fx ON fx.fx_rate_id = t.fx_rate_id
            WHERE t.period_id = ? AND fx.rate_source = 'target_derived'
            ORDER BY t.transaction_id
            """,
            (period["period_id"],),
        )
        xolo_recorded_fx_after_cutover = self._fetch_all(
            """
            SELECT t.transaction_id, t.transaction_date
            FROM transactions t
            JOIN fx_rates fx ON fx.fx_rate_id = t.fx_rate_id
            WHERE t.period_id = ?
              AND fx.rate_source = 'xolo_recorded'
              AND t.transaction_date > ?
            ORDER BY t.transaction_id
            """,
            (period["period_id"], XOLO_RECORDED_PRODUCTION_THROUGH.isoformat()),
        )
        invalid_amount_transactions = self._fetch_all(
            """
            SELECT transaction_id, amount_minor, amount_eur_minor, lifecycle_status
            FROM transactions
            WHERE period_id = ?
              AND lifecycle_status IN ('posted', 'included_in_snapshot')
              AND (amount_minor = 0 OR amount_eur_minor = 0)
            ORDER BY transaction_id
            """,
            (period["period_id"],),
        )
        out_of_period_transactions = self._fetch_all(
            """
            SELECT transaction_id, transaction_date, lifecycle_status
            FROM transactions
            WHERE period_id = ?
              AND lifecycle_status IN ('received', 'extracted', 'needs_review', 'approved', 'posted', 'included_in_snapshot')
              AND (transaction_date < ? OR transaction_date > ?)
            ORDER BY transaction_id
            """,
            (period["period_id"], period["starts_on"], period["ends_on"]),
        )
        ready = not any(
            (
                blocking_issues,
                unresolved_obligations,
                review_documents,
                review_transactions,
                target_derived_fx,
                xolo_recorded_fx_after_cutover,
                invalid_amount_transactions,
                out_of_period_transactions,
            )
        )
        return {
            "period": period,
            "ready": ready,
            "blocking_issues": blocking_issues,
            "unresolved_obligations": unresolved_obligations,
            "review_documents": review_documents,
            "review_transactions": review_transactions,
            "authoritative_history_transactions": authoritative_history_transactions,
            "target_derived_fx": target_derived_fx,
            "xolo_recorded_fx_after_cutover": xolo_recorded_fx_after_cutover,
            "invalid_amount_transactions": invalid_amount_transactions,
            "out_of_period_transactions": out_of_period_transactions,
        }

    def list_periods(self) -> list[dict[str, Any]]:
        return self._fetch_all("SELECT * FROM periods ORDER BY starts_on, period_key")

    def list_transactions(self, *, period_key: str | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*, p.period_key, c.display_name AS counterparty_name, c.country_code,
                   c.vat_id, c.roi_status, c.retention_expected
            FROM transactions t
            JOIN periods p ON p.period_id = t.period_id
            LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
        """
        params: tuple[Any, ...] = ()
        if period_key:
            sql += " WHERE p.period_key = ?"
            params = (period_key,)
        sql += " ORDER BY t.transaction_date, t.transaction_id"
        return self._fetch_all(sql, params)

    def list_authoritative_history_transactions(
        self,
        *,
        period_key: str | None = None,
        year: int | None = None,
    ) -> list[dict[str, Any]]:
        conditions = [
            "t.lifecycle_status = 'approved'",
            "ib.source_name = 'xolo_source_book_rows'",
            "COALESCE(ds.source_file, '') <> ''",
            "EXISTS ("
            "SELECT 1 FROM filing_snapshots fs "
            "WHERE fs.period_id = t.period_id "
            "AND fs.status IN ('baseline', 'filed', 'submitted', 'final')"
            ")",
        ]
        params: list[Any] = []
        if period_key is not None:
            conditions.append("p.period_key = ?")
            params.append(period_key)
        if year is not None:
            conditions.append("p.starts_on >= ? AND p.starts_on <= ?")
            params.extend((f"{year}-01-01", f"{year}-12-31"))
        sql = f"""
            SELECT t.transaction_id, p.period_key,
                   MIN(ds.source_book_line_id) AS source_book_line_id,
                   MIN(ds.source_file) AS source_file,
                   MIN(ib.source_hash) AS import_source_hash
            FROM transactions t
            JOIN periods p ON p.period_id = t.period_id
            JOIN documents d ON d.document_id = t.document_id
            JOIN document_sources ds ON ds.document_id = d.document_id
            JOIN import_batches ib ON ib.import_batch_id = ds.import_batch_id
            WHERE {' AND '.join(conditions)}
            GROUP BY t.transaction_id, p.period_key
            ORDER BY p.period_key, t.transaction_id
        """
        return self._fetch_all(sql, tuple(params))

    def list_tax_rows(self, *, year: int | None = None) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*, p.period_key, c.display_name AS counterparty_name, c.country_code,
                   c.vat_id, tt.tax_code, tt.taxable_base_minor, tt.vat_minor,
                   tt.deductible_irpf_minor, tt.deductible_vat_minor, tt.withholding_minor,
                   tt.include_modelo130, tt.include_modelo303, tt.include_modelo347,
                   tt.treatment_type, tt.jurisdiction, tt.vat_investment_good,
                   CASE
                       WHEN COALESCE(at.asset_count, 0) + COALESCE(ad.asset_count, 0) > 0
                           THEN COALESCE(at.asset_id, ad.asset_id)
                       WHEN COALESCE(ai.asset_count, 0) = 1 THEN ai.asset_id
                       ELSE NULL
                   END AS asset_id,
                   CASE
                       WHEN COALESCE(at.asset_count, 0) + COALESCE(ad.asset_count, 0) > 0
                           THEN COALESCE(at.asset_count, 0) + COALESCE(ad.asset_count, 0)
                       ELSE COALESCE(ai.asset_count, 0)
                   END AS asset_count
            FROM transactions t
            JOIN periods p ON p.period_id = t.period_id
            LEFT JOIN documents td ON td.document_id = t.document_id
            LEFT JOIN counterparties c ON c.counterparty_id = t.counterparty_id
            LEFT JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            LEFT JOIN (
                SELECT acquisition_transaction_id, MIN(asset_id) AS asset_id, COUNT(*) AS asset_count
                FROM assets
                WHERE acquisition_transaction_id IS NOT NULL
                GROUP BY acquisition_transaction_id
            ) at ON at.acquisition_transaction_id = t.transaction_id
            LEFT JOIN (
                SELECT document_id, MIN(asset_id) AS asset_id, COUNT(*) AS asset_count
                FROM assets
                WHERE acquisition_transaction_id IS NULL AND document_id IS NOT NULL
                GROUP BY document_id
            ) ad ON ad.document_id = t.document_id
            LEFT JOIN (
                SELECT d.counterparty_id, d.issued_on, MIN(a.asset_id) AS asset_id,
                       COUNT(*) AS asset_count
                FROM assets a
                JOIN documents d ON d.document_id = a.document_id
                WHERE d.counterparty_id IS NOT NULL
                GROUP BY d.counterparty_id, d.issued_on
            ) ai ON ai.counterparty_id = t.counterparty_id
                AND ai.issued_on = td.issued_on
                AND tt.tax_code = 'historical_g03'
            WHERE t.lifecycle_status IN ('approved', 'posted', 'included_in_snapshot')
        """
        params: tuple[Any, ...] = ()
        if year is not None:
            sql += " AND substr(t.transaction_date, 1, 4) = ?"
            params = (str(year),)
        sql += " ORDER BY t.transaction_date, t.transaction_id, tt.treatment_id"
        return self._fetch_all(sql, params)

    def upsert_invoice_template(
        self,
        *,
        template_key: str,
        template_name: str,
        counterparty_id: str,
        currency: str,
        default_lines: list[Mapping[str, Any]],
        payment_terms_days: int = 30,
        withholding_rate_basis_points: int = 0,
        channel_hint: str | None = None,
        delivery_email: str | None = None,
        recipient_address_line1: str,
        recipient_city: str,
        recipient_address_line2: str | None = None,
        recipient_postal_code: str | None = None,
        recipient_region: str | None = None,
        active: bool = True,
        invoice_template_id: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        template_key = template_key.strip()
        template_name = template_name.strip()
        if not template_key:
            raise ValueError("Invoice template_key is required")
        if not template_name:
            raise ValueError("Invoice template_name is required")
        if isinstance(payment_terms_days, bool) or not isinstance(payment_terms_days, int):
            raise ValueError("payment_terms_days must be an integer")
        if payment_terms_days < 0 or payment_terms_days > 365:
            raise ValueError("payment_terms_days must be between 0 and 365")
        if not isinstance(active, bool):
            raise ValueError("Invoice template active must be a boolean")
        effective_currency = validate_currency(currency)
        effective_withholding_rate = validate_withholding_rate(withholding_rate_basis_points)
        normalized_lines = normalize_invoice_lines(default_lines)
        lines_json = canonical_lines_json(normalized_lines)
        effective_channel = (channel_hint or "").strip() or None
        effective_email = (delivery_email or "").strip() or None
        if effective_email is not None and "@" not in effective_email:
            raise ValueError("Invoice delivery_email must contain @")
        effective_address_line1 = recipient_address_line1.strip()
        effective_address_line2 = (recipient_address_line2 or "").strip() or None
        effective_postal_code = (recipient_postal_code or "").strip() or None
        effective_city = recipient_city.strip()
        effective_region = (recipient_region or "").strip() or None
        if not effective_address_line1 or not effective_city:
            raise ValueError("Invoice recipient address_line1 and city are required")
        self._fetch_one(
            "SELECT counterparty_id FROM counterparties WHERE counterparty_id = ?",
            (counterparty_id,),
        )

        existing = (
            self._fetch_optional(
                "SELECT * FROM invoice_templates WHERE invoice_template_id = ?",
                (invoice_template_id,),
            )
            if invoice_template_id is not None
            else self._fetch_optional(
                """
                SELECT * FROM invoice_templates
                WHERE template_key = ?
                ORDER BY template_version DESC
                LIMIT 1
                """,
                (template_key,),
            )
        )
        if existing is not None and existing["template_key"] != template_key:
            raise LedgerDbError(
                "An invoice template_key is stable; create a separate template to rename it"
            )
        payload = {
            "template_key": template_key,
            "template_name": template_name,
            "counterparty_id": counterparty_id,
            "currency": effective_currency,
            "default_lines_json": lines_json,
            "payment_terms_days": payment_terms_days,
            "withholding_rate_basis_points": effective_withholding_rate,
            "channel_hint": effective_channel,
            "delivery_email": effective_email,
            "recipient_address_line1": effective_address_line1,
            "recipient_address_line2": effective_address_line2,
            "recipient_postal_code": effective_postal_code,
            "recipient_city": effective_city,
            "recipient_region": effective_region,
            "active": int(active),
        }
        effective_source_hash = source_hash or _stable_hash(payload)
        desired = {**payload, "source_hash": effective_source_hash}
        if existing is not None and all(existing[key] == value for key, value in desired.items()):
            return existing
        if existing is not None:
            if expected_row_version is None:
                raise StaleRowVersionError(
                    "Updating an invoice template requires expected_row_version"
                )
            self._check_row_version(existing, expected_row_version)

        timestamp = _utc_now()
        new_id = invoice_template_id or _new_id()
        template_version = 1
        supersedes_id = None
        if existing is not None:
            new_id = _new_id()
            template_version = int(existing["template_version"]) + 1
            supersedes_id = existing["invoice_template_id"]
        with self.connection:
            if existing is not None and bool(existing["active"]):
                self.connection.execute(
                    """
                    UPDATE invoice_templates
                    SET active = 0, row_version = ?, updated_at = ?
                    WHERE invoice_template_id = ?
                    """,
                    (
                        existing["row_version"] + 1,
                        timestamp,
                        existing["invoice_template_id"],
                    ),
                )
            self.connection.execute(
                """
                INSERT INTO invoice_templates (
                    invoice_template_id, template_key, template_version,
                    supersedes_invoice_template_id, template_name, counterparty_id,
                    currency, default_lines_json, payment_terms_days,
                    withholding_rate_basis_points, channel_hint, delivery_email, active,
                    recipient_address_line1, recipient_address_line2,
                    recipient_postal_code, recipient_city, recipient_region,
                    source_hash, row_version, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    new_id,
                    template_key,
                    template_version,
                    supersedes_id,
                    template_name,
                    counterparty_id,
                    effective_currency,
                    lines_json,
                    payment_terms_days,
                    effective_withholding_rate,
                    effective_channel,
                    effective_email,
                    int(active),
                    effective_address_line1,
                    effective_address_line2,
                    effective_postal_code,
                    effective_city,
                    effective_region,
                    effective_source_hash,
                    timestamp,
                    timestamp,
                ),
            )
        return self._fetch_one(
            "SELECT * FROM invoice_templates WHERE invoice_template_id = ?",
            (new_id,),
        )

    def create_outgoing_invoice_draft(
        self,
        *,
        draft_key: str,
        period_key: str,
        service_on: str,
        planned_issue_on: str,
        payment_due_on: str | None = None,
        invoice_template_id: str | None = None,
        template_key: str | None = None,
        lines: list[Mapping[str, Any]] | None = None,
        currency: str | None = None,
        withholding_rate_basis_points: int | None = None,
        channel: str | None = None,
        delivery_email: str | None = None,
        notes: str | None = None,
        outgoing_invoice_draft_id: str | None = None,
        source_hash: str | None = None,
        expected_row_version: int | None = None,
    ) -> dict[str, Any]:
        draft_key = draft_key.strip()
        if not draft_key:
            raise ValueError("Outgoing invoice draft_key is required")
        if invoice_template_id is None and template_key is None:
            raise ValueError("invoice_template_id or template_key is required")
        if invoice_template_id is not None:
            template = self._fetch_one(
                "SELECT * FROM invoice_templates WHERE invoice_template_id = ?",
                (invoice_template_id,),
            )
            if template_key is not None and template["template_key"] != template_key:
                raise LedgerDbError("invoice_template_id and template_key identify different templates")
        else:
            template = self._fetch_optional(
                """
                SELECT * FROM invoice_templates
                WHERE template_key = ? AND active = 1
                ORDER BY template_version DESC
                LIMIT 1
                """,
                (template_key,),
            )
            if template is None:
                raise LedgerDbError(f"No active invoice template for template_key={template_key}")
        effective_template_id = template["invoice_template_id"]
        if not bool(template["active"]):
            raise LedgerDbError("Inactive invoice template cannot create a draft")
        period = self.ensure_period(period_key)
        self._assert_period_mutable(period["period_id"])
        service_date = _invoice_date(service_on, "service_on")
        planned_issue_date = _invoice_date(planned_issue_on, "planned_issue_on")
        period_start = date.fromisoformat(period["starts_on"])
        period_end = date.fromisoformat(period["ends_on"])
        if not period_start <= planned_issue_date <= period_end:
            raise ValueError("planned_issue_on must fall inside period_key")
        if service_date > planned_issue_date:
            raise ValueError("service_on must not be after planned_issue_on")

        effective_currency = validate_currency(currency or template["currency"])
        materialized_lines = (
            normalize_invoice_lines(lines)
            if lines is not None
            else parse_template_lines(template["default_lines_json"])
        )
        effective_withholding_rate = validate_withholding_rate(
            template["withholding_rate_basis_points"]
            if withholding_rate_basis_points is None
            else withholding_rate_basis_points
        )
        totals = calculate_invoice_totals(
            materialized_lines,
            withholding_rate_basis_points=effective_withholding_rate,
        )
        effective_channel = (channel or template["channel_hint"] or "").strip() or None
        effective_email = (
            delivery_email or template["delivery_email"] or ""
        ).strip() or None
        if effective_email is not None and "@" not in effective_email:
            raise ValueError("Invoice delivery_email must contain @")
        payment_due_date = (
            _invoice_date(payment_due_on, "payment_due_on")
            if payment_due_on is not None
            else planned_issue_date + timedelta(days=int(template["payment_terms_days"]))
        )
        if payment_due_date < planned_issue_date:
            raise ValueError("payment_due_on must not be before planned_issue_on")
        effective_payment_due_on = payment_due_date.isoformat()
        lines_json = canonical_lines_json(materialized_lines)
        payload = {
            "draft_key": draft_key,
            "invoice_template_id": effective_template_id,
            "counterparty_id": template["counterparty_id"],
            "period_id": period["period_id"],
            "service_on": service_date.isoformat(),
            "planned_issue_on": planned_issue_date.isoformat(),
            "payment_due_on": effective_payment_due_on,
            "currency": effective_currency,
            **totals.as_dict(),
            "withholding_rate_basis_points": effective_withholding_rate,
            "channel": effective_channel,
            "delivery_email": effective_email,
            "recipient_address_line1": template["recipient_address_line1"],
            "recipient_address_line2": template["recipient_address_line2"],
            "recipient_postal_code": template["recipient_postal_code"],
            "recipient_city": template["recipient_city"],
            "recipient_region": template["recipient_region"],
            "notes": notes,
            "lines": json.loads(lines_json),
        }
        effective_source_hash = source_hash or _stable_hash(payload)
        existing = (
            self._fetch_optional(
                "SELECT * FROM outgoing_invoice_drafts WHERE outgoing_invoice_draft_id = ?",
                (outgoing_invoice_draft_id,),
            )
            if outgoing_invoice_draft_id is not None
            else self._fetch_optional(
                "SELECT * FROM outgoing_invoice_drafts WHERE draft_key = ?",
                (draft_key,),
            )
        )
        desired = {
            key: value
            for key, value in payload.items()
            if key not in {"lines"}
        }
        if existing is not None:
            existing_lines = self._fetch_all(
                """
                SELECT line_number, description, quantity, unit_amount_minor, tax_code, channel_tax_code,
                       tax_rate_basis_points, subtotal_minor, tax_minor
                FROM outgoing_invoice_lines
                WHERE outgoing_invoice_draft_id = ?
                ORDER BY line_number
                """,
                (existing["outgoing_invoice_draft_id"],),
            )
            exact_header = all(existing[key] == value for key, value in desired.items())
            if exact_header and existing_lines == [line.as_dict() for line in materialized_lines]:
                return self.get_outgoing_invoice_draft(existing["outgoing_invoice_draft_id"])
            if existing["lifecycle_status"] != "draft":
                raise LifecycleError(
                    "Only a draft outgoing invoice can be changed; void and recreate it"
                )
            if expected_row_version is None:
                raise StaleRowVersionError(
                    "Updating an outgoing invoice draft requires expected_row_version"
                )
            self._check_row_version(existing, expected_row_version)

        timestamp = _utc_now()
        draft_id = (
            existing["outgoing_invoice_draft_id"]
            if existing is not None
            else (outgoing_invoice_draft_id or _new_id())
        )
        with self.connection:
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO outgoing_invoice_drafts (
                        outgoing_invoice_draft_id, draft_key, invoice_template_id,
                        counterparty_id, period_id, service_on, planned_issue_on,
                        payment_due_on, currency, subtotal_minor, vat_minor,
                        withholding_minor, total_minor, withholding_rate_basis_points,
                        channel, delivery_email, recipient_address_line1,
                        recipient_address_line2, recipient_postal_code, recipient_city,
                        recipient_region, lifecycle_status, notes, source_hash,
                        row_version, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              'draft', ?, ?, 1, ?, ?)
                    """,
                    (
                        draft_id,
                        draft_key,
                        effective_template_id,
                        template["counterparty_id"],
                        period["period_id"],
                        service_date.isoformat(),
                        planned_issue_date.isoformat(),
                        effective_payment_due_on,
                        effective_currency,
                        totals.subtotal_minor,
                        totals.vat_minor,
                        totals.withholding_minor,
                        totals.total_minor,
                        effective_withholding_rate,
                        effective_channel,
                        effective_email,
                        template["recipient_address_line1"],
                        template["recipient_address_line2"],
                        template["recipient_postal_code"],
                        template["recipient_city"],
                        template["recipient_region"],
                        notes,
                        effective_source_hash,
                        timestamp,
                        timestamp,
                    ),
                )
            else:
                self.connection.execute(
                    """
                    UPDATE outgoing_invoice_drafts
                    SET draft_key = ?, invoice_template_id = ?, counterparty_id = ?,
                        period_id = ?, service_on = ?, planned_issue_on = ?, payment_due_on = ?,
                        currency = ?, subtotal_minor = ?, vat_minor = ?, withholding_minor = ?,
                        total_minor = ?, withholding_rate_basis_points = ?, channel = ?,
                        delivery_email = ?, recipient_address_line1 = ?,
                        recipient_address_line2 = ?, recipient_postal_code = ?,
                        recipient_city = ?, recipient_region = ?, notes = ?, source_hash = ?, row_version = ?,
                        updated_at = ?
                    WHERE outgoing_invoice_draft_id = ?
                    """,
                    (
                        draft_key,
                        effective_template_id,
                        template["counterparty_id"],
                        period["period_id"],
                        service_date.isoformat(),
                        planned_issue_date.isoformat(),
                        effective_payment_due_on,
                        effective_currency,
                        totals.subtotal_minor,
                        totals.vat_minor,
                        totals.withholding_minor,
                        totals.total_minor,
                        effective_withholding_rate,
                        effective_channel,
                        effective_email,
                        template["recipient_address_line1"],
                        template["recipient_address_line2"],
                        template["recipient_postal_code"],
                        template["recipient_city"],
                        template["recipient_region"],
                        notes,
                        effective_source_hash,
                        existing["row_version"] + 1,
                        timestamp,
                        draft_id,
                    ),
                )
                self.connection.execute(
                    "DELETE FROM outgoing_invoice_lines WHERE outgoing_invoice_draft_id = ?",
                    (draft_id,),
                )
            for line in materialized_lines:
                self.connection.execute(
                    """
                    INSERT INTO outgoing_invoice_lines (
                        outgoing_invoice_line_id, outgoing_invoice_draft_id, line_number,
                        description, quantity, unit_amount_minor, tax_code, channel_tax_code,
                        tax_rate_basis_points, subtotal_minor, tax_minor, source_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _new_id(),
                        draft_id,
                        line.line_number,
                        line.description,
                        line.quantity,
                        line.unit_amount_minor,
                        line.tax_code,
                        line.channel_tax_code,
                        line.tax_rate_basis_points,
                        line.subtotal_minor,
                        line.tax_minor,
                        invoice_line_hash(draft_key, line),
                        timestamp,
                    ),
                )
        return self.get_outgoing_invoice_draft(draft_id)

    def review_outgoing_invoice_draft(
        self,
        outgoing_invoice_draft_id: str,
        *,
        expected_row_version: int,
    ) -> dict[str, Any]:
        draft = self._fetch_one(
            "SELECT * FROM outgoing_invoice_drafts WHERE outgoing_invoice_draft_id = ?",
            (outgoing_invoice_draft_id,),
        )
        if draft["lifecycle_status"] == "reviewed":
            return self.get_outgoing_invoice_draft(outgoing_invoice_draft_id)
        self._check_row_version(draft, expected_row_version)
        if draft["lifecycle_status"] != "draft":
            raise LifecycleError(
                f"Outgoing invoice cannot be reviewed from {draft['lifecycle_status']}"
            )
        self._assert_period_mutable(draft["period_id"])
        counterparty = self._fetch_one(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (draft["counterparty_id"],),
        )
        missing: list[str] = []
        if not str(counterparty["display_name"] or "").strip():
            missing.append("counterparty display_name")
        if len(str(counterparty["country_code"] or "")) != 2:
            missing.append("counterparty country_code")
        if not (counterparty["tax_id"] or counterparty["vat_id"]):
            missing.append("counterparty tax_id/vat_id")
        if not draft["delivery_email"]:
            missing.append("delivery_email")
        if not draft["recipient_address_line1"] or not draft["recipient_city"]:
            missing.append("recipient address")
        if not draft["channel"]:
            missing.append("channel")
        if missing:
            raise LedgerDbError(
                "Outgoing invoice draft is incomplete: " + ", ".join(missing)
            )
        open_issues = self._fetch_all(
            """
            SELECT issue_code
            FROM validation_issues
            WHERE blocking = 1 AND issue_status = 'open'
              AND ((subject_table = 'counterparties' AND subject_id = ?)
                   OR (subject_table = 'outgoing_invoice_drafts' AND subject_id = ?))
            ORDER BY issue_code
            """,
            (draft["counterparty_id"], outgoing_invoice_draft_id),
        )
        if open_issues:
            raise BlockingIssueError(
                "Open issues block outgoing invoice review: "
                + ", ".join(sorted({row["issue_code"] for row in open_issues}))
            )
        lines = self._fetch_all(
            "SELECT tax_code FROM outgoing_invoice_lines WHERE outgoing_invoice_draft_id = ?",
            (outgoing_invoice_draft_id,),
        )
        if not lines or any(row["tax_code"] == "unknown" for row in lines):
            raise LedgerDbError("Outgoing invoice lines require reviewed tax codes")
        distinct_tax_codes = {row["tax_code"] for row in lines}
        if len(distinct_tax_codes) != 1:
            raise LedgerDbError(
                "A reviewed outgoing invoice currently requires one tax_code across all lines"
            )
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE outgoing_invoice_drafts
                SET lifecycle_status = 'reviewed', source_hash = ?, row_version = ?, updated_at = ?
                WHERE outgoing_invoice_draft_id = ?
                """,
                (
                    hashlib.sha256(
                        f"{draft['source_hash']}:reviewed".encode("utf-8")
                    ).hexdigest(),
                    draft["row_version"] + 1,
                    timestamp,
                    outgoing_invoice_draft_id,
                ),
            )
        return self.get_outgoing_invoice_draft(outgoing_invoice_draft_id)

    def finalize_outgoing_invoice_draft(
        self,
        outgoing_invoice_draft_id: str,
        *,
        document_id: str,
        transaction_id: str,
        external_number: str,
        external_series: str | None = None,
        expected_row_version: int,
    ) -> dict[str, Any]:
        draft = self._fetch_one(
            "SELECT * FROM outgoing_invoice_drafts WHERE outgoing_invoice_draft_id = ?",
            (outgoing_invoice_draft_id,),
        )
        if draft["lifecycle_status"] == "issued":
            same_links = all(
                (
                    draft["document_id"] == document_id,
                    draft["transaction_id"] == transaction_id,
                    draft["external_number"] == external_number,
                    draft["external_series"] == ((external_series or "").strip() or None),
                )
            )
            if same_links:
                return self.get_outgoing_invoice_draft(outgoing_invoice_draft_id)
            raise LifecycleError("Issued invoice evidence links are immutable")
        self._check_row_version(draft, expected_row_version)
        if draft["lifecycle_status"] != "reviewed":
            raise LifecycleError(
                f"Outgoing invoice cannot be finalized from {draft['lifecycle_status']}"
            )
        document = self._fetch_one("SELECT * FROM documents WHERE document_id = ?", (document_id,))
        transaction = self._fetch_one(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        effective_external_number = external_number.strip()
        effective_external_series = (external_series or "").strip() or None
        if not effective_external_number:
            raise ValueError("external_number is required")
        mismatches: list[str] = []
        if document["document_type"] != "income_invoice":
            mismatches.append("document_type")
        if document["lifecycle_status"] not in {"approved", "posted", "included_in_snapshot"}:
            mismatches.append("document_lifecycle_status")
        if document["counterparty_id"] != draft["counterparty_id"]:
            mismatches.append("document_counterparty")
        if document["currency"] != draft["currency"]:
            mismatches.append("document_currency")
        if document["total_minor"] != draft["total_minor"]:
            mismatches.append("document_total")
        if document["issued_on"] != draft["planned_issue_on"]:
            mismatches.append("document_issued_on")
        if document["document_number"] != effective_external_number:
            mismatches.append("document_number")
        source_path = str(document["source_path"] or "").strip()
        if document["mime_type"] != "application/pdf":
            mismatches.append("document_mime_type")
        if not source_path:
            mismatches.append("document_source_path")
        else:
            evidence_path = Path(source_path)
            if not evidence_path.is_file():
                mismatches.append("document_source_missing")
            elif _sha256_file(evidence_path) != document["source_hash"]:
                mismatches.append("document_source_hash")
        if transaction["document_id"] != document_id:
            mismatches.append("transaction_document")
        if transaction["counterparty_id"] != draft["counterparty_id"]:
            mismatches.append("transaction_counterparty")
        if transaction["period_id"] != draft["period_id"]:
            mismatches.append("transaction_period")
        if transaction["entry_type"] != "income" or transaction["direction"] != "credit":
            mismatches.append("transaction_direction")
        if transaction["original_currency"] != draft["currency"]:
            mismatches.append("transaction_currency")
        if transaction["amount_original_minor"] != draft["total_minor"]:
            mismatches.append("transaction_amount")
        if transaction["transaction_date"] != document["issued_on"]:
            mismatches.append("transaction_date")
        if transaction["lifecycle_status"] not in {"approved", "posted", "included_in_snapshot"}:
            mismatches.append("transaction_lifecycle_status")
        draft_tax_codes = {
            row["tax_code"]
            for row in self._fetch_all(
                "SELECT DISTINCT tax_code FROM outgoing_invoice_lines WHERE outgoing_invoice_draft_id = ?",
                (outgoing_invoice_draft_id,),
            )
        }
        transaction_tax_codes = {
            row["tax_code"]
            for row in self._fetch_all(
                """
                SELECT tax_code FROM tax_treatments
                WHERE transaction_id = ? AND jurisdiction = 'ES' AND tax_code <> 'unknown'
                """,
                (transaction_id,),
            )
        }
        if len(draft_tax_codes) != 1 or not draft_tax_codes.issubset(transaction_tax_codes):
            mismatches.append("transaction_tax_code")
        if mismatches:
            raise LedgerDbError(
                "Issued invoice evidence does not match reviewed draft: "
                + ", ".join(sorted(mismatches))
            )
        timestamp = _utc_now()
        with self.connection:
            self.connection.execute(
                """
                UPDATE outgoing_invoice_drafts
                SET lifecycle_status = 'issued', external_series = ?, external_number = ?,
                    document_id = ?, transaction_id = ?, source_hash = ?, row_version = ?,
                    updated_at = ?
                WHERE outgoing_invoice_draft_id = ?
                """,
                (
                    effective_external_series,
                    effective_external_number,
                    document_id,
                    transaction_id,
                    hashlib.sha256(
                        (
                            f"{draft['source_hash']}:issued:{effective_external_series}:"
                            f"{effective_external_number}:{document_id}:{transaction_id}"
                        ).encode("utf-8")
                    ).hexdigest(),
                    draft["row_version"] + 1,
                    timestamp,
                    outgoing_invoice_draft_id,
                ),
            )
        return self.get_outgoing_invoice_draft(outgoing_invoice_draft_id)

    def void_outgoing_invoice_draft(
        self,
        outgoing_invoice_draft_id: str,
        *,
        reason: str,
        expected_row_version: int,
    ) -> dict[str, Any]:
        reason = reason.strip()
        if not reason:
            raise ValueError("Voiding an outgoing invoice draft requires a reason")
        draft = self._fetch_one(
            "SELECT * FROM outgoing_invoice_drafts WHERE outgoing_invoice_draft_id = ?",
            (outgoing_invoice_draft_id,),
        )
        if draft["lifecycle_status"] == "void":
            if draft["void_reason"] == reason:
                return self.get_outgoing_invoice_draft(outgoing_invoice_draft_id)
            raise LifecycleError("A voided invoice draft reason is immutable")
        if draft["lifecycle_status"] == "issued":
            raise LifecycleError(
                "An issued invoice cannot be voided as a draft; use the external correction workflow"
            )
        self._check_row_version(draft, expected_row_version)
        self._assert_period_mutable(draft["period_id"])
        timestamp = _utc_now()
        notes = f"{draft['notes']}; void reason: {reason}" if draft["notes"] else f"void reason: {reason}"
        with self.connection:
            self.connection.execute(
                """
                UPDATE outgoing_invoice_drafts
                SET lifecycle_status = 'void', void_reason = ?, notes = ?, source_hash = ?, row_version = ?,
                    updated_at = ?
                WHERE outgoing_invoice_draft_id = ?
                """,
                (
                    reason,
                    notes,
                    hashlib.sha256(
                        f"{draft['source_hash']}:void:{reason}".encode("utf-8")
                    ).hexdigest(),
                    draft["row_version"] + 1,
                    timestamp,
                    outgoing_invoice_draft_id,
                ),
            )
        return self.get_outgoing_invoice_draft(outgoing_invoice_draft_id)

    def get_outgoing_invoice_draft(self, outgoing_invoice_draft_id: str) -> dict[str, Any]:
        row = self._fetch_one(
            """
            SELECT d.*, p.period_key, t.template_key, t.template_version, t.template_name,
                   c.display_name AS counterparty_name, c.country_code,
                   c.tax_id AS counterparty_tax_id, c.vat_id AS counterparty_vat_id
            FROM outgoing_invoice_drafts d
            JOIN periods p ON p.period_id = d.period_id
            JOIN invoice_templates t ON t.invoice_template_id = d.invoice_template_id
            JOIN counterparties c ON c.counterparty_id = d.counterparty_id
            WHERE d.outgoing_invoice_draft_id = ?
            """,
            (outgoing_invoice_draft_id,),
        )
        row["lines"] = self._fetch_all(
            """
            SELECT line_number, description, quantity, unit_amount_minor, tax_code, channel_tax_code,
                   tax_rate_basis_points, subtotal_minor, tax_minor
            FROM outgoing_invoice_lines
            WHERE outgoing_invoice_draft_id = ?
            ORDER BY line_number
            """,
            (outgoing_invoice_draft_id,),
        )
        row["issuance_guard"] = (
            "issued_evidence_linked"
            if row["lifecycle_status"] == "issued"
            else "not_issued_do_not_book_as_income"
        )
        return row

    def list_outgoing_invoice_drafts(
        self,
        *,
        period_key: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT d.outgoing_invoice_draft_id, d.draft_key, d.lifecycle_status,
                   d.service_on, d.planned_issue_on, d.payment_due_on, d.currency,
                   d.total_minor, d.channel, d.external_series, d.external_number,
                   d.row_version, p.period_key, t.template_key, t.template_version,
                   c.display_name AS counterparty_name
            FROM outgoing_invoice_drafts d
            JOIN periods p ON p.period_id = d.period_id
            JOIN invoice_templates t ON t.invoice_template_id = d.invoice_template_id
            JOIN counterparties c ON c.counterparty_id = d.counterparty_id
        """
        params: tuple[Any, ...] = ()
        if period_key is not None:
            sql += " WHERE p.period_key = ?"
            params = (period_key,)
        sql += " ORDER BY d.planned_issue_on, d.draft_key"
        return self._fetch_all(sql, params)

    def list_invoice_templates(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        sql = """
            SELECT t.*, c.display_name AS counterparty_name, c.country_code,
                   c.tax_id AS counterparty_tax_id, c.vat_id AS counterparty_vat_id
            FROM invoice_templates t
            JOIN counterparties c ON c.counterparty_id = t.counterparty_id
        """
        if not include_inactive:
            sql += " WHERE t.active = 1"
        sql += " ORDER BY t.template_key"
        rows = self._fetch_all(sql)
        for row in rows:
            row["default_lines"] = json.loads(row.pop("default_lines_json"))
        return rows

    def table_counts(self) -> dict[str, int]:
        tables = (
            "taxpayer_profile",
            "business_activities",
            "counterparties",
            "counterparty_name_changes",
            "counterparty_identities",
            "documents",
            "files",
            "document_attachments",
            "storage_backends",
            "file_replicas",
            "document_sources",
            "intake_receipts",
            "transactions",
            "tax_treatments",
            "assets",
            "amortization_entries",
            "payments",
            "fx_rates",
            "obligations",
            "tax_calendar_entries",
            "validation_issues",
            "filing_snapshots",
            "invoice_templates",
            "outgoing_invoice_drafts",
            "outgoing_invoice_lines",
        )
        return {
            table: int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in tables
        }

    def _apply_migrations(self) -> None:
        current_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if current_version > LATEST_SCHEMA_VERSION:
            raise SchemaVersionError(
                f"Database schema version {current_version} is newer than supported version {LATEST_SCHEMA_VERSION}"
            )
        for version in range(current_version + 1, LATEST_SCHEMA_VERSION + 1):
            migration = _MIGRATIONS[version]
            if version < 8:
                with self.connection:
                    migration(self.connection)
                    self.connection.execute(f"PRAGMA user_version = {version}")
                continue
            if version in FK_REBUILD_MIGRATIONS:
                self._run_fk_rebuild_migration(migration, version)
                continue
            self.connection.execute("BEGIN IMMEDIATE")
            try:
                migration(self.connection)
                self.connection.execute(f"PRAGMA user_version = {version}")
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def _run_fk_rebuild_migration(self, migration, version: int) -> None:
        """Run one migration that must drop a foreign-key parent table.

        ``PRAGMA foreign_keys`` is a no-op inside a transaction, so the
        enforcement is disabled before the transaction starts and restored
        afterwards; the DDL itself stays atomic inside the transaction.
        """

        self.connection.execute("PRAGMA foreign_keys = OFF")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            self.connection.execute("PRAGMA foreign_keys = ON")
            raise
        try:
            migration(self.connection)
            self.connection.execute(f"PRAGMA user_version = {version}")
        except Exception:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()
        finally:
            self.connection.execute("PRAGMA foreign_keys = ON")

    def _find_counterparty(
        self,
        counterparty_id: str | None,
        external_key: str | None,
        tax_id: str | None,
    ) -> dict[str, Any] | None:
        if counterparty_id is not None:
            return self._fetch_optional(
                "SELECT * FROM counterparties WHERE counterparty_id = ?",
                (counterparty_id,),
            )
        if external_key is not None:
            record = self._fetch_optional(
                "SELECT * FROM counterparties WHERE external_key = ?",
                (external_key,),
            )
            if record is not None:
                return record
        if tax_id is not None:
            return self._fetch_optional(
                "SELECT * FROM counterparties WHERE tax_id = ?",
                (tax_id,),
            )
        return None

    def _resolve_business_activity_id(
        self,
        business_activity_id: str | None,
        *,
        transaction_date: str,
    ) -> str | None:
        tax_date = date.fromisoformat(transaction_date)
        if business_activity_id is not None:
            activity = self._fetch_one(
                "SELECT * FROM business_activities WHERE business_activity_id = ?",
                (business_activity_id,),
            )
            if date.fromisoformat(activity["starts_on"]) > tax_date:
                raise ValueError("Business activity starts after the transaction date")
            if activity["ends_on"] and date.fromisoformat(activity["ends_on"]) < tax_date:
                raise ValueError("Business activity ended before the transaction date")
            return activity["business_activity_id"]

        candidates = self._fetch_all(
            """
            SELECT business_activity_id
            FROM business_activities
            WHERE starts_on <= ? AND (ends_on IS NULL OR ends_on >= ?)
            ORDER BY activity_key
            """,
            (tax_date.isoformat(), tax_date.isoformat()),
        )
        return candidates[0]["business_activity_id"] if len(candidates) == 1 else None

    def _find_document(
        self,
        document_id: str | None,
        external_key: str | None,
        source_hash: str | None,
    ) -> dict[str, Any] | None:
        if document_id is not None:
            return self._fetch_optional("SELECT * FROM documents WHERE document_id = ?", (document_id,))
        if external_key is not None:
            record = self._fetch_optional("SELECT * FROM documents WHERE external_key = ?", (external_key,))
            if record is not None:
                return record
        if source_hash is not None:
            return self._fetch_optional("SELECT * FROM documents WHERE source_hash = ?", (source_hash,))
        return None

    def _find_transaction(
        self,
        transaction_id: str | None,
        external_key: str | None,
    ) -> dict[str, Any] | None:
        if transaction_id is not None:
            return self._fetch_optional(
                "SELECT * FROM transactions WHERE transaction_id = ?",
                (transaction_id,),
            )
        if external_key is not None:
            return self._fetch_optional(
                "SELECT * FROM transactions WHERE external_key = ?",
                (external_key,),
            )
        return None

    def _fetch_period(self, period_key: str) -> dict[str, Any] | None:
        return self._fetch_optional("SELECT * FROM periods WHERE period_key = ?", (period_key,))

    def _require_period(self, period_key: str) -> dict[str, Any]:
        period = self._fetch_period(period_key)
        if period is None:
            raise KeyError(f"Unknown period: {period_key}")
        return period

    def _assert_period_mutable(self, period_id: str) -> None:
        period = self._fetch_one("SELECT period_key, status FROM periods WHERE period_id = ?", (period_id,))
        if period["status"] in {"closed", "amended"}:
            raise ClosedPeriodError(f"Period {period['period_key']} is immutable after close")

    def _assert_asset_links_mutable(
        self,
        document_id: str | None,
        acquisition_transaction_id: str | None,
    ) -> None:
        period_ids: set[str] = set()
        if document_id is not None:
            document = self._fetch_one(
                "SELECT period_id FROM documents WHERE document_id = ?",
                (document_id,),
            )
            if document["period_id"]:
                period_ids.add(document["period_id"])
        if acquisition_transaction_id is not None:
            transaction = self._fetch_one(
                "SELECT period_id FROM transactions WHERE transaction_id = ?",
                (acquisition_transaction_id,),
            )
            period_ids.add(transaction["period_id"])
        for period_id in period_ids:
            self._assert_period_mutable(period_id)

    def _check_row_version(self, record: Mapping[str, Any], expected_row_version: int | None) -> None:
        if expected_row_version is None:
            return
        current = int(record["row_version"])
        if current != expected_row_version:
            raise StaleRowVersionError(f"Expected row_version {expected_row_version}, found {current}")

    def _fetch_one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        row = self.connection.execute(sql, params).fetchone()
        if row is None:
            raise KeyError(f"Expected row for query: {sql}")
        return dict(row)

    def _fetch_optional(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row is not None else None

    def _fetch_all(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]


def _validate_lifecycle_status(status: str) -> None:
    if status not in ALL_LIFECYCLE_STATUSES:
        raise ValueError(f"Unsupported lifecycle_status: {status}")


def _validate_lifecycle_transition(current: str, target: str) -> None:
    _validate_lifecycle_status(current)
    _validate_lifecycle_status(target)
    if current == target:
        return
    allowed_targets = VALID_LIFECYCLE_TRANSITIONS[current]
    if target not in allowed_targets:
        raise LifecycleError(f"Invalid lifecycle transition: {current} -> {target}")


def _terminal_update_is_noop(existing: Mapping[str, Any], desired: Mapping[str, Any]) -> bool:
    status = str(existing["lifecycle_status"])
    if status not in TERMINAL_LIFECYCLE_STATUSES:
        return False
    changed = [key for key, value in desired.items() if existing.get(key) != value]
    if changed:
        raise LifecycleError(
            f"Terminal lifecycle row {status} is immutable; changed fields: {', '.join(sorted(changed))}"
        )
    return True


def _new_id() -> str:
    return str(uuid4())


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _invoice_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be an ISO date") from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_hash(payload: Mapping[str, Any]) -> str:
    normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _canonical_decimal_text(value: str | Decimal) -> str:
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("FX rate must be a valid Decimal") from exc
    return format(decimal_value.normalize(), "f")


def _normalize_source_reference(value: str | None) -> str | None:
    normalized = (value or "").strip()
    return normalized or None


def _fx_raw_observation(
    provenance: Mapping[str, Any] | None,
    *,
    rate_date: str,
    base_currency: str,
    quote_currency: str,
    rate: str,
    rate_source: str,
    source_reference: str | None,
) -> str:
    """Return the caller-supplied raw observation or a deterministic fallback."""

    if provenance:
        supplied = str(provenance.get("raw_observation") or "").strip()
        if supplied:
            return supplied
    return json.dumps(
        {
            "base_currency": base_currency,
            "quote_currency": quote_currency,
            "rate": rate,
            "rate_date": rate_date,
            "rate_source": rate_source,
            "source_reference": source_reference,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _validate_counterparty_legal_form(value: str) -> None:
    if value not in COUNTERPARTY_LEGAL_FORMS:
        raise ValueError(f"Unsupported counterparty legal_form: {value}")


def _validate_sha256(value: str) -> None:
    normalized = value.casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("source_hash must be a SHA-256 hex digest")


def _normalize_sha256(value: str) -> str:
    normalized = value.casefold()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef" for character in normalized
    ):
        raise ValueError("content_sha256 must be a SHA-256 hex digest")
    return normalized


def _storage_config_json(config: Mapping[str, Any] | None) -> str:
    payload = dict(config or {})
    _reject_secret_storage_fields(payload)
    try:
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Storage metadata must be JSON serializable") from exc


def _reject_secret_storage_fields(value: Any) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
            if normalized in {
                "access_key",
                "secret",
                "secret_key",
                "password",
                "private_key",
                "token",
                "refresh_token",
            }:
                raise ValueError("Storage credentials must be referenced through credential_ref")
            _reject_secret_storage_fields(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            _reject_secret_storage_fields(nested)


def _validate_tax_calendar_payload(payload: Mapping[str, Any]) -> None:
    period_key = str(payload["period_key"])
    _default_period_bounds(period_key)
    form_code = str(payload["form_code"])
    if form_code not in ALL_FORM_CODES:
        raise ValueError(f"Unsupported tax calendar form_code: {form_code}")
    status = str(payload["deadline_status"])
    if status not in {"confirmed", "provisional"}:
        raise ValueError(f"Unsupported tax calendar deadline_status: {status}")
    filing_opens = date.fromisoformat(str(payload["filing_opens_on"]))
    internal_due = date.fromisoformat(str(payload["internal_due_on"]))
    statutory_due = date.fromisoformat(str(payload["statutory_due_on"]))
    direct_debit = (
        date.fromisoformat(str(payload["direct_debit_cutoff_on"]))
        if payload.get("direct_debit_cutoff_on")
        else None
    )
    date.fromisoformat(str(payload["source_checked_on"]))
    if statutory_due.year != int(payload["calendar_year"]):
        raise ValueError("Tax calendar statutory deadline is outside calendar_year")
    if not filing_opens <= internal_due <= statutory_due:
        raise ValueError(
            "Tax calendar dates must satisfy filing_opens_on <= internal_due_on <= statutory_due_on"
        )
    if direct_debit is not None and not filing_opens <= direct_debit <= statutory_due:
        raise ValueError("Tax calendar direct debit cutoff must fall inside the filing window")
    source_url = str(payload["source_url"])
    if not source_url:
        raise ValueError("Tax calendar rows require a source URL")
    if status == "confirmed" and not source_url.startswith(
        "https://sede.agenciatributaria.gob.es/"
    ):
        raise ValueError("Confirmed tax calendar rows require an official AEAT source URL")
    if status == "provisional" and "replace" not in str(payload.get("notes") or "").casefold():
        raise ValueError("Provisional tax calendar row notes must say that the date must be replaced")
    if not str(payload["source_hash"]):
        raise ValueError("Tax calendar rows require a source_hash")


def _optional_bool_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def _write_scope(connection: sqlite3.Connection):
    return nullcontext() if connection.in_transaction else connection


def _optional_upper(value: str | None) -> str | None:
    normalized = (value or "").strip().upper()
    return normalized or None


def _determination_from_filing_status(filing_status: str) -> str:
    if filing_status in {"due", "filed"}:
        return "due"
    if filing_status == "waived":
        return "not_due"
    return "unknown"


def _obligation_blocks(determination: str, filing_status: str) -> bool:
    return determination == "unknown" or (
        determination == "due" and filing_status not in {"filed", "waived"}
    )


def _empty_asset_year_bucket(asset: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "asset_id": asset["asset_id"],
        "asset_code": asset["asset_code"],
        "annual_minor": 0,
        "quarter_minor": 0,
        "annual_rows": 0,
        "quarter_rows": 0,
    }


def _effective_asset_base_minor(asset: Mapping[str, Any]) -> int:
    base_minor = int(
        asset["amortizable_base_minor"]
        if asset.get("amortizable_base_minor") is not None
        else asset["cost_minor"]
    )
    ratio = asset.get("business_use_ratio")
    if ratio is None:
        return base_minor
    return int(
        (Decimal(base_minor) * Decimal(str(ratio))).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    )


def _default_period_bounds(period_key: str) -> tuple[str, str]:
    if len(period_key) == 7 and period_key[4:6] == "-Q":
        year = int(period_key[:4])
        quarter = int(period_key[-1])
        if quarter not in {1, 2, 3, 4}:
            raise ValueError(f"Unsupported quarter in period key: {period_key}")
        start_month = ((quarter - 1) * 3) + 1
        start = date(year, start_month, 1)
        if quarter == 4:
            end = date(year, 12, 31)
        else:
            next_start = date(year, start_month + 3, 1)
            end = next_start.fromordinal(next_start.toordinal() - 1)
        return start.isoformat(), end.isoformat()
    if len(period_key) == 4 and period_key.isdigit():
        year = int(period_key)
        return date(year, 1, 1).isoformat(), date(year, 12, 31).isoformat()
    raise ValueError(f"Unsupported period key without explicit bounds: {period_key}")


def _migration_1(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE taxpayer_profile (
            taxpayer_profile_id TEXT PRIMARY KEY,
            tax_id TEXT NOT NULL UNIQUE,
            full_name TEXT NOT NULL,
            residency_country TEXT NOT NULL,
            tax_year_start_month INTEGER NOT NULL DEFAULT 1,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE periods (
            period_id TEXT PRIMARY KEY,
            period_key TEXT NOT NULL UNIQUE,
            period_type TEXT NOT NULL,
            starts_on TEXT NOT NULL,
            ends_on TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('open', 'closed', 'amended')),
            amendment_period_id TEXT REFERENCES periods(period_id),
            amendment_reason TEXT,
            closed_at TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE household_members (
            household_member_id TEXT PRIMARY KEY,
            taxpayer_profile_id TEXT NOT NULL REFERENCES taxpayer_profile(taxpayer_profile_id),
            full_name TEXT NOT NULL,
            relationship TEXT NOT NULL,
            tax_id TEXT,
            birth_date TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (taxpayer_profile_id, full_name, relationship)
        );

        CREATE TABLE import_batches (
            import_batch_id TEXT PRIMARY KEY,
            source_name TEXT NOT NULL,
            batch_key TEXT,
            imported_at TEXT NOT NULL,
            notes TEXT,
            source_hash TEXT NOT NULL UNIQUE,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE counterparties (
            counterparty_id TEXT PRIMARY KEY,
            external_key TEXT,
            tax_id TEXT,
            display_name TEXT NOT NULL,
            country_code TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE documents (
            document_id TEXT PRIMARY KEY,
            external_key TEXT,
            counterparty_id TEXT REFERENCES counterparties(counterparty_id),
            import_batch_id TEXT REFERENCES import_batches(import_batch_id),
            document_type TEXT NOT NULL,
            document_number TEXT,
            issued_on TEXT NOT NULL,
            period_id TEXT REFERENCES periods(period_id),
            currency TEXT NOT NULL,
            total_minor INTEGER,
            lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
                'received', 'extracted', 'needs_review', 'approved', 'posted',
                'included_in_snapshot', 'duplicate', 'rejected', 'void'
            )),
            source_hash TEXT NOT NULL UNIQUE,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE obligations (
            obligation_id TEXT PRIMARY KEY,
            period_id TEXT NOT NULL REFERENCES periods(period_id),
            obligation_code TEXT NOT NULL,
            due_on TEXT,
            filing_status TEXT NOT NULL CHECK (filing_status IN ('unknown', 'due', 'filed', 'waived')),
            filed_at TEXT,
            notes TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (period_id, obligation_code)
        );

        CREATE TABLE rule_versions (
            rule_version_id TEXT PRIMARY KEY,
            rule_name TEXT NOT NULL,
            version TEXT NOT NULL,
            activated_at TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (rule_name, version)
        );

        CREATE TABLE filing_snapshots (
            filing_snapshot_id TEXT PRIMARY KEY,
            period_id TEXT NOT NULL REFERENCES periods(period_id),
            snapshot_hash TEXT NOT NULL,
            filed_on TEXT NOT NULL,
            status TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (period_id, snapshot_hash)
        );

        CREATE TABLE transactions (
            transaction_id TEXT PRIMARY KEY,
            external_key TEXT,
            period_id TEXT NOT NULL REFERENCES periods(period_id),
            transaction_date TEXT NOT NULL,
            booking_date TEXT NOT NULL,
            entry_type TEXT NOT NULL,
            description TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('debit', 'credit')),
            lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
                'received', 'extracted', 'needs_review', 'approved', 'posted',
                'included_in_snapshot', 'duplicate', 'rejected', 'void'
            )),
            document_id TEXT REFERENCES documents(document_id),
            counterparty_id TEXT REFERENCES counterparties(counterparty_id),
            correction_of_transaction_id TEXT REFERENCES transactions(transaction_id),
            correction_kind TEXT CHECK (correction_kind IN ('reversing', 'correcting')),
            included_snapshot_id TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (correction_of_transaction_id IS NULL AND correction_kind IS NULL)
                OR (correction_of_transaction_id IS NOT NULL AND correction_kind IS NOT NULL)
            )
        );

        CREATE TABLE tax_treatments (
            treatment_id TEXT PRIMARY KEY,
            transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
            treatment_type TEXT NOT NULL,
            jurisdiction TEXT NOT NULL,
            rate_basis_points INTEGER,
            deductible_ratio REAL,
            notes TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (transaction_id, treatment_type, jurisdiction)
        );

        CREATE TABLE payments (
            payment_id TEXT PRIMARY KEY,
            transaction_id TEXT REFERENCES transactions(transaction_id),
            obligation_id TEXT REFERENCES obligations(obligation_id),
            paid_on TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (transaction_id, paid_on, amount_minor, currency)
        );

        CREATE TABLE fx_rates (
            fx_rate_id TEXT PRIMARY KEY,
            rate_date TEXT NOT NULL,
            base_currency TEXT NOT NULL,
            quote_currency TEXT NOT NULL,
            rate TEXT NOT NULL,
            rate_source TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (rate_date, base_currency, quote_currency, rate_source)
        );

        CREATE TABLE assets (
            asset_id TEXT PRIMARY KEY,
            document_id TEXT REFERENCES documents(document_id),
            acquisition_transaction_id TEXT REFERENCES transactions(transaction_id),
            asset_code TEXT NOT NULL,
            placed_in_service_on TEXT,
            cost_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            depreciation_method TEXT NOT NULL,
            useful_life_months INTEGER,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (asset_code)
        );

        CREATE TABLE amortization_entries (
            amortization_entry_id TEXT PRIMARY KEY,
            asset_id TEXT NOT NULL REFERENCES assets(asset_id),
            period_id TEXT NOT NULL REFERENCES periods(period_id),
            amount_minor INTEGER NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (asset_id, period_id)
        );

        CREATE TABLE validation_issues (
            validation_issue_id TEXT PRIMARY KEY,
            period_id TEXT REFERENCES periods(period_id),
            subject_table TEXT,
            subject_id TEXT,
            issue_code TEXT NOT NULL,
            severity TEXT NOT NULL,
            message TEXT NOT NULL,
            blocking INTEGER NOT NULL DEFAULT 1 CHECK (blocking IN (0, 1)),
            issue_status TEXT NOT NULL CHECK (issue_status IN ('open', 'resolved', 'ignored')),
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (period_id, issue_code, subject_table, subject_id)
        );

        CREATE TABLE annual_adjustments (
            annual_adjustment_id TEXT PRIMARY KEY,
            tax_year INTEGER NOT NULL,
            period_id TEXT REFERENCES periods(period_id),
            adjustment_code TEXT NOT NULL,
            amount_minor INTEGER NOT NULL,
            currency TEXT NOT NULL,
            rationale TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tax_year, adjustment_code, period_id)
        );

        CREATE UNIQUE INDEX counterparties_external_key_unique
            ON counterparties(external_key)
            WHERE external_key IS NOT NULL;

        CREATE UNIQUE INDEX counterparties_tax_id_unique
            ON counterparties(tax_id)
            WHERE tax_id IS NOT NULL;

        CREATE UNIQUE INDEX documents_external_key_unique
            ON documents(external_key)
            WHERE external_key IS NOT NULL;

        CREATE UNIQUE INDEX documents_counterparty_number_unique
            ON documents(counterparty_id, document_type, document_number, issued_on)
            WHERE document_number IS NOT NULL;

        CREATE UNIQUE INDEX transactions_external_key_unique
            ON transactions(external_key)
            WHERE external_key IS NOT NULL;

        CREATE INDEX transactions_period_status_idx
            ON transactions(period_id, lifecycle_status);

        CREATE INDEX obligations_period_status_idx
            ON obligations(period_id, filing_status);

        CREATE INDEX validation_issues_period_status_idx
            ON validation_issues(period_id, issue_status, blocking);
        """
    )


def _migration_2(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        ALTER TABLE counterparties ADD COLUMN vat_id TEXT;
        ALTER TABLE counterparties ADD COLUMN roi_status TEXT NOT NULL DEFAULT 'unknown';
        ALTER TABLE counterparties ADD COLUMN professional_supplier INTEGER;
        ALTER TABLE counterparties ADD COLUMN retention_expected INTEGER;

        ALTER TABLE documents ADD COLUMN source_path TEXT;
        ALTER TABLE documents ADD COLUMN drive_file_id TEXT;
        ALTER TABLE documents ADD COLUMN mime_type TEXT;

        ALTER TABLE transactions ADD COLUMN amount_original_minor INTEGER;
        ALTER TABLE transactions ADD COLUMN original_currency TEXT;
        ALTER TABLE transactions ADD COLUMN amount_eur_minor INTEGER;
        ALTER TABLE transactions ADD COLUMN fx_rate_id TEXT REFERENCES fx_rates(fx_rate_id);

        ALTER TABLE tax_treatments ADD COLUMN tax_code TEXT NOT NULL DEFAULT 'unknown';
        ALTER TABLE tax_treatments ADD COLUMN taxable_base_minor INTEGER;
        ALTER TABLE tax_treatments ADD COLUMN vat_minor INTEGER;
        ALTER TABLE tax_treatments ADD COLUMN deductible_irpf_minor INTEGER;
        ALTER TABLE tax_treatments ADD COLUMN deductible_vat_minor INTEGER;
        ALTER TABLE tax_treatments ADD COLUMN withholding_minor INTEGER;
        ALTER TABLE tax_treatments ADD COLUMN include_modelo130 INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE tax_treatments ADD COLUMN include_modelo303 INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE tax_treatments ADD COLUMN include_modelo347 INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE tax_treatments ADD COLUMN rule_version_id TEXT REFERENCES rule_versions(rule_version_id);

        ALTER TABLE obligations ADD COLUMN source_citation TEXT;
        ALTER TABLE obligations ADD COLUMN explanation TEXT;
        ALTER TABLE obligations ADD COLUMN blocking INTEGER NOT NULL DEFAULT 1;

        ALTER TABLE payments ADD COLUMN original_reference TEXT;
        ALTER TABLE payments ADD COLUMN fee_minor INTEGER;
        ALTER TABLE payments ADD COLUMN fee_currency TEXT;
        ALTER TABLE payments ADD COLUMN match_status TEXT NOT NULL DEFAULT 'unmatched';

        ALTER TABLE fx_rates ADD COLUMN rule_version_id TEXT REFERENCES rule_versions(rule_version_id);

        ALTER TABLE assets ADD COLUMN amortizable_base_minor INTEGER;
        ALTER TABLE assets ADD COLUMN iva_treatment TEXT NOT NULL DEFAULT 'unknown';
        ALTER TABLE assets ADD COLUMN business_use_ratio REAL;
        ALTER TABLE assets ADD COLUMN annual_rate_basis_points INTEGER;
        ALTER TABLE assets ADD COLUMN advisor_decision TEXT;
        ALTER TABLE assets ADD COLUMN advisor_decision_on TEXT;

        ALTER TABLE validation_issues ADD COLUMN waiver_reason TEXT;
        ALTER TABLE validation_issues ADD COLUMN waived_at TEXT;

        ALTER TABLE annual_adjustments ADD COLUMN evidence_ref TEXT;
        ALTER TABLE annual_adjustments ADD COLUMN advisor_confirmed INTEGER NOT NULL DEFAULT 0;

        ALTER TABLE filing_snapshots ADD COLUMN manifest_path TEXT;

        CREATE UNIQUE INDEX counterparties_vat_id_unique
            ON counterparties(vat_id)
            WHERE vat_id IS NOT NULL;
        CREATE INDEX transactions_tax_date_idx
            ON transactions(transaction_date, entry_type);
        CREATE INDEX tax_treatments_tax_code_idx
            ON tax_treatments(tax_code);
        """
    )


def _migration_3(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        ALTER TABLE obligations ADD COLUMN determination TEXT NOT NULL DEFAULT 'unknown';
        ALTER TABLE obligations ADD COLUMN rule_version_id TEXT REFERENCES rule_versions(rule_version_id);

        ALTER TABLE documents ADD COLUMN rectifies_document_id TEXT REFERENCES documents(document_id);

        ALTER TABLE amortization_entries ADD COLUMN entry_kind TEXT NOT NULL DEFAULT 'quarter_schedule';
        ALTER TABLE amortization_entries ADD COLUMN tax_year INTEGER;
        ALTER TABLE amortization_entries ADD COLUMN source_book_line_id TEXT;
        ALTER TABLE amortization_entries ADD COLUMN include_in_books INTEGER NOT NULL DEFAULT 1;

        UPDATE obligations
        SET determination = CASE
            WHEN filing_status IN ('due', 'filed') THEN 'due'
            WHEN filing_status = 'waived' THEN 'not_due'
            ELSE 'unknown'
        END;
        UPDATE amortization_entries
        SET tax_year = CAST(substr((
            SELECT period_key FROM periods WHERE periods.period_id = amortization_entries.period_id
        ), 1, 4) AS INTEGER)
        WHERE tax_year IS NULL;

        CREATE INDEX amortization_entries_asset_year_idx
            ON amortization_entries(asset_id, tax_year, entry_kind);
        CREATE INDEX obligations_determination_idx
            ON obligations(period_id, determination, filing_status);
        """
    )


def _migration_4(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE document_sources (
            document_source_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            import_batch_id TEXT REFERENCES import_batches(import_batch_id),
            source_book_line_id TEXT NOT NULL,
            source_file TEXT,
            source_row_number TEXT,
            source_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            UNIQUE (document_id, source_book_line_id)
        );
        CREATE INDEX document_sources_document_idx ON document_sources(document_id);
        """
    )


def _migration_5(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        ALTER TABLE validation_issues ADD COLUMN resolution_reason TEXT;
        ALTER TABLE validation_issues ADD COLUMN resolved_at TEXT;
        """
    )


def _migration_6(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        ALTER TABLE validation_issues ADD COLUMN dedupe_key TEXT;

        ALTER TABLE filing_snapshots ADD COLUMN form_code TEXT;
        ALTER TABLE filing_snapshots ADD COLUMN submission_reference TEXT;
        ALTER TABLE filing_snapshots ADD COLUMN justificante_number TEXT;
        ALTER TABLE filing_snapshots ADD COLUMN verification_code TEXT;
        ALTER TABLE filing_snapshots ADD COLUMN source_reference TEXT;
        """
    )


def _migration_7(connection: sqlite3.Connection) -> None:
    legacy_statuses = {
        "amount_mismatch",
        "pending_confirmation",
        "deductibility_pending_confirmation",
        "missing_locally",
    }
    rows = connection.execute(
        """
        SELECT validation_issue_id, period_id, subject_id, updated_at
        FROM validation_issues
        WHERE subject_table = 'xolo_expense_reconcile'
          AND COALESCE(subject_id, '') <> ''
        ORDER BY period_id, updated_at DESC, validation_issue_id DESC
        """
    ).fetchall()
    grouped: dict[tuple[str, str], list[sqlite3.Row]] = {}
    for row in rows:
        parts = row["subject_id"].split(":")
        if len(parts) >= 4 and parts[0] == "reconcile-row" and parts[2] in legacy_statuses:
            canonical_subject = ":".join([parts[0], parts[1], *parts[3:]])
        else:
            canonical_subject = row["subject_id"]
        grouped.setdefault((row["period_id"], canonical_subject), []).append(row)

    for (_period_id, canonical_subject), candidates in grouped.items():
        keeper = candidates[0]
        for duplicate in candidates[1:]:
            connection.execute(
                "DELETE FROM validation_issues WHERE validation_issue_id = ?",
                (duplicate["validation_issue_id"],),
            )
        connection.execute(
            """
            UPDATE validation_issues
            SET subject_id = ?, dedupe_key = ?
            WHERE validation_issue_id = ?
            """,
            (
                canonical_subject,
                f"xolo-reconcile:{canonical_subject}",
                keeper["validation_issue_id"],
            ),
        )

    reconciliation_rows = connection.execute(
        """
        SELECT validation_issue_id, period_id, subject_id, updated_at
        FROM validation_issues
        WHERE subject_table = 'xolo_expense_reconcile'
          AND COALESCE(subject_id, '') <> ''
        ORDER BY period_id, subject_id, updated_at DESC, validation_issue_id DESC
        """
    ).fetchall()
    keeper_by_subject: dict[tuple[str, str], str] = {}
    for row in reconciliation_rows:
        key = (row["period_id"], row["subject_id"])
        keeper = keeper_by_subject.get(key)
        if keeper is None:
            keeper_by_subject[key] = row["validation_issue_id"]
            connection.execute(
                "UPDATE validation_issues SET dedupe_key = ? WHERE validation_issue_id = ?",
                (f"xolo-reconcile:{row['subject_id']}", row["validation_issue_id"]),
            )
        else:
            connection.execute(
                "DELETE FROM validation_issues WHERE validation_issue_id = ?",
                (row["validation_issue_id"],),
            )

    snapshot_rows = connection.execute(
        "SELECT filing_snapshot_id, payload_json FROM filing_snapshots"
    ).fetchall()
    for row in snapshot_rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, json.JSONDecodeError):
            continue
        form_code = str(payload.get("form") or "").strip()
        if form_code:
            connection.execute(
                "UPDATE filing_snapshots SET form_code = ? WHERE filing_snapshot_id = ?",
                (form_code, row["filing_snapshot_id"]),
            )

    connection.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS validation_issues_period_dedupe_unique
            ON validation_issues(period_id, dedupe_key)
            WHERE dedupe_key IS NOT NULL;
        CREATE INDEX IF NOT EXISTS filing_snapshots_period_form_idx
            ON filing_snapshots(period_id, form_code, status);
        """
    )


def _migration_8(connection: sqlite3.Connection) -> None:
    statements = (
        "ALTER TABLE payments ADD COLUMN source_system TEXT",
        "ALTER TABLE payments ADD COLUMN external_id TEXT",
        "ALTER TABLE payments ADD COLUMN account_name TEXT",
        "ALTER TABLE payments ADD COLUMN counterparty_name TEXT",
        "ALTER TABLE payments ADD COLUMN category TEXT",
        "ALTER TABLE payments ADD COLUMN comment TEXT",
        "ALTER TABLE payments ADD COLUMN amount_eur_minor INTEGER",
        "ALTER TABLE payments ADD COLUMN source_row_json TEXT",
        """
        CREATE UNIQUE INDEX payments_source_external_id_unique
            ON payments(source_system, external_id)
            WHERE source_system IS NOT NULL AND external_id IS NOT NULL
        """,
        """
        CREATE INDEX payments_paid_on_source_idx
            ON payments(paid_on, source_system, account_name)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_9(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE invoice_templates (
            invoice_template_id TEXT PRIMARY KEY,
            template_key TEXT NOT NULL,
            template_version INTEGER NOT NULL CHECK (template_version > 0),
            supersedes_invoice_template_id TEXT REFERENCES invoice_templates(invoice_template_id),
            template_name TEXT NOT NULL,
            counterparty_id TEXT NOT NULL REFERENCES counterparties(counterparty_id),
            currency TEXT NOT NULL,
            default_lines_json TEXT NOT NULL,
            payment_terms_days INTEGER NOT NULL CHECK (payment_terms_days BETWEEN 0 AND 365),
            withholding_rate_basis_points INTEGER NOT NULL
                CHECK (withholding_rate_basis_points BETWEEN 0 AND 10000),
            channel_hint TEXT,
            delivery_email TEXT,
            active INTEGER NOT NULL CHECK (active IN (0, 1)),
            recipient_address_line1 TEXT NOT NULL,
            recipient_address_line2 TEXT,
            recipient_postal_code TEXT,
            recipient_city TEXT NOT NULL,
            recipient_region TEXT,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (template_key, template_version)
        )
        """,
        """
        CREATE TABLE outgoing_invoice_drafts (
            outgoing_invoice_draft_id TEXT PRIMARY KEY,
            draft_key TEXT NOT NULL UNIQUE,
            invoice_template_id TEXT NOT NULL REFERENCES invoice_templates(invoice_template_id),
            counterparty_id TEXT NOT NULL REFERENCES counterparties(counterparty_id),
            period_id TEXT NOT NULL REFERENCES periods(period_id),
            service_on TEXT NOT NULL,
            planned_issue_on TEXT NOT NULL,
            payment_due_on TEXT NOT NULL,
            currency TEXT NOT NULL,
            subtotal_minor INTEGER NOT NULL,
            vat_minor INTEGER NOT NULL,
            withholding_minor INTEGER NOT NULL,
            total_minor INTEGER NOT NULL CHECK (total_minor > 0),
            withholding_rate_basis_points INTEGER NOT NULL
                CHECK (withholding_rate_basis_points BETWEEN 0 AND 10000),
            channel TEXT,
            delivery_email TEXT,
            recipient_address_line1 TEXT NOT NULL,
            recipient_address_line2 TEXT,
            recipient_postal_code TEXT,
            recipient_city TEXT NOT NULL,
            recipient_region TEXT,
            lifecycle_status TEXT NOT NULL
                CHECK (lifecycle_status IN ('draft', 'reviewed', 'issued', 'void')),
            notes TEXT,
            void_reason TEXT,
            external_series TEXT,
            external_number TEXT,
            document_id TEXT REFERENCES documents(document_id),
            transaction_id TEXT REFERENCES transactions(transaction_id),
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            CHECK (
                (lifecycle_status = 'issued'
                 AND external_number IS NOT NULL
                 AND document_id IS NOT NULL
                 AND transaction_id IS NOT NULL)
                OR
                (lifecycle_status <> 'issued'
                 AND external_series IS NULL
                 AND external_number IS NULL
                 AND document_id IS NULL
                 AND transaction_id IS NULL)
            ),
            CHECK (
                (lifecycle_status = 'void' AND void_reason IS NOT NULL)
                OR (lifecycle_status <> 'void' AND void_reason IS NULL)
            )
        )
        """,
        """
        CREATE TABLE outgoing_invoice_lines (
            outgoing_invoice_line_id TEXT PRIMARY KEY,
            outgoing_invoice_draft_id TEXT NOT NULL
                REFERENCES outgoing_invoice_drafts(outgoing_invoice_draft_id) ON DELETE CASCADE,
            line_number INTEGER NOT NULL CHECK (line_number > 0),
            description TEXT NOT NULL,
            quantity TEXT NOT NULL,
            unit_amount_minor INTEGER NOT NULL CHECK (unit_amount_minor > 0),
            tax_code TEXT NOT NULL,
            channel_tax_code TEXT NOT NULL,
            tax_rate_basis_points INTEGER NOT NULL
                CHECK (tax_rate_basis_points BETWEEN 0 AND 10000),
            subtotal_minor INTEGER NOT NULL CHECK (subtotal_minor > 0),
            tax_minor INTEGER NOT NULL CHECK (tax_minor >= 0),
            source_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            UNIQUE (outgoing_invoice_draft_id, line_number)
        )
        """,
        """
        CREATE UNIQUE INDEX outgoing_invoice_drafts_external_number_unique
            ON outgoing_invoice_drafts(COALESCE(external_series, ''), external_number)
            WHERE lifecycle_status = 'issued'
        """,
        """
        CREATE UNIQUE INDEX outgoing_invoice_drafts_document_unique
            ON outgoing_invoice_drafts(document_id)
            WHERE document_id IS NOT NULL
        """,
        """
        CREATE UNIQUE INDEX outgoing_invoice_drafts_transaction_unique
            ON outgoing_invoice_drafts(transaction_id)
            WHERE transaction_id IS NOT NULL
        """,
        """
        CREATE INDEX outgoing_invoice_drafts_period_status_idx
            ON outgoing_invoice_drafts(period_id, lifecycle_status, planned_issue_on)
        """,
        """
        CREATE INDEX invoice_templates_counterparty_active_idx
            ON invoice_templates(counterparty_id, active)
        """,
        """
        CREATE UNIQUE INDEX invoice_templates_active_key_unique
            ON invoice_templates(template_key)
            WHERE active = 1
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_10(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE business_activities (
            business_activity_id TEXT PRIMARY KEY,
            taxpayer_profile_id TEXT NOT NULL
                REFERENCES taxpayer_profile(taxpayer_profile_id),
            activity_key TEXT NOT NULL,
            aeat_activity_code TEXT NOT NULL
                CHECK (aeat_activity_code IN ('A', 'B', 'C')),
            aeat_activity_type TEXT NOT NULL,
            iae_section TEXT CHECK (iae_section IN ('1', '2', '3')),
            iae_group_epigraph TEXT NOT NULL,
            description TEXT NOT NULL,
            starts_on TEXT NOT NULL,
            ends_on TEXT,
            irpf_method TEXT NOT NULL,
            iva_regime TEXT NOT NULL,
            source_reference TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (taxpayer_profile_id, activity_key),
            CHECK (ends_on IS NULL OR ends_on >= starts_on)
        )
        """,
        """
        ALTER TABLE transactions
            ADD COLUMN business_activity_id TEXT REFERENCES business_activities(business_activity_id)
        """,
        """
        ALTER TABLE assets
            ADD COLUMN business_activity_id TEXT REFERENCES business_activities(business_activity_id)
        """,
        "ALTER TABLE assets ADD COLUMN aeat_asset_type TEXT",
        "ALTER TABLE assets ADD COLUMN description TEXT",
        """
        CREATE INDEX business_activities_dates_idx
            ON business_activities(taxpayer_profile_id, starts_on, ends_on)
        """,
        """
        CREATE INDEX transactions_business_activity_idx
            ON transactions(business_activity_id, transaction_date)
        """,
        """
        CREATE INDEX assets_business_activity_idx
            ON assets(business_activity_id, placed_in_service_on)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_11(connection: sqlite3.Connection) -> None:
    statements = (
        "ALTER TABLE assets ADD COLUMN aeat_asset_identifier TEXT",
        "ALTER TABLE assets ADD COLUMN aeat_amortization_method TEXT",
        "ALTER TABLE assets ADD COLUMN source_invoice_number TEXT",
        "ALTER TABLE assets ADD COLUMN acquisition_taxable_base_minor INTEGER",
        "ALTER TABLE assets ADD COLUMN acquisition_vat_rate_basis_points INTEGER",
        "ALTER TABLE assets ADD COLUMN acquisition_deductible_vat_minor INTEGER",
        "ALTER TABLE assets ADD COLUMN book_profile_source_reference TEXT",
        "ALTER TABLE assets ADD COLUMN book_profile_source_hash TEXT",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_12(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE counterparty_identities (
            counterparty_identity_id TEXT PRIMARY KEY,
            counterparty_id TEXT NOT NULL REFERENCES counterparties(counterparty_id),
            identity_kind TEXT NOT NULL CHECK (identity_kind IN (
                'vat_id', 'passport', 'official_id',
                'residence_certificate', 'other_proof'
            )),
            aeat_id_type TEXT NOT NULL CHECK (aeat_id_type IN ('02', '03', '04', '05', '06')),
            country_code TEXT NOT NULL,
            identifier TEXT NOT NULL CHECK (length(identifier) BETWEEN 1 AND 20),
            is_primary INTEGER NOT NULL DEFAULT 1 CHECK (is_primary IN (0, 1)),
            source_reference TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (counterparty_id, aeat_id_type, country_code, identifier)
        )
        """,
        """
        CREATE UNIQUE INDEX counterparty_identities_primary_idx
            ON counterparty_identities(counterparty_id)
            WHERE is_primary = 1
        """,
        """
        CREATE INDEX counterparty_identities_lookup_idx
            ON counterparty_identities(counterparty_id, aeat_id_type, country_code)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_13(connection: sqlite3.Connection) -> None:
    statements = (
        "ALTER TABLE tax_treatments ADD COLUMN aeat_invoice_type TEXT",
        "ALTER TABLE tax_treatments ADD COLUMN aeat_operation_key TEXT",
        "ALTER TABLE tax_treatments ADD COLUMN aeat_operation_qualification TEXT",
        "ALTER TABLE tax_treatments ADD COLUMN aeat_exemption_code TEXT",
        "ALTER TABLE tax_treatments ADD COLUMN aeat_reverse_charge INTEGER",
        "ALTER TABLE tax_treatments ADD COLUMN aeat_expense_concept TEXT",
    )
    for statement in statements:
        connection.execute(statement)


def _migration_14(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE tax_calendar_entries (
            tax_calendar_entry_id TEXT PRIMARY KEY,
            calendar_year INTEGER NOT NULL,
            period_id TEXT NOT NULL REFERENCES periods(period_id),
            form_code TEXT NOT NULL,
            filing_opens_on TEXT NOT NULL,
            internal_due_on TEXT NOT NULL,
            direct_debit_cutoff_on TEXT,
            statutory_due_on TEXT NOT NULL,
            deadline_status TEXT NOT NULL CHECK (deadline_status IN ('confirmed', 'provisional')),
            source_url TEXT NOT NULL,
            source_checked_on TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (period_id, form_code)
        )
        """,
        """
        CREATE INDEX tax_calendar_entries_due_idx
            ON tax_calendar_entries(calendar_year, statutory_due_on, form_code)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_15(connection: sqlite3.Connection) -> None:
    statements = (
        """
        CREATE TABLE intake_receipts (
            intake_receipt_id TEXT PRIMARY KEY,
            intake_tab TEXT NOT NULL CHECK (intake_tab IN ('expense_intake', 'income_intake')),
            source_row_number INTEGER NOT NULL CHECK (source_row_number >= 2),
            row_fingerprint TEXT NOT NULL UNIQUE,
            evidence_sha256 TEXT NOT NULL UNIQUE,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
            treatment_id TEXT NOT NULL REFERENCES tax_treatments(treatment_id),
            input_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE (document_id),
            UNIQUE (transaction_id),
            UNIQUE (treatment_id)
        )
        """,
        """
        CREATE INDEX intake_receipts_tab_created_idx
            ON intake_receipts(intake_tab, created_at)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_16(connection: sqlite3.Connection) -> None:
    statements = (
        """
        ALTER TABLE counterparties
        ADD COLUMN legal_form TEXT NOT NULL DEFAULT 'unknown'
            CHECK (legal_form IN ('unknown', 'individual', 'legal_entity', 'public_body'))
        """,
        """
        CREATE TABLE obligation_evidence (
            obligation_evidence_id TEXT PRIMARY KEY,
            obligation_id TEXT NOT NULL REFERENCES obligations(obligation_id),
            evidence_kind TEXT NOT NULL CHECK (
                evidence_kind IN ('aeat_account_check', 'independent_calculation')
            ),
            source_reference TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (obligation_id, evidence_kind, source_hash)
        )
        """,
        """
        CREATE INDEX obligation_evidence_lookup_idx
            ON obligation_evidence(obligation_id, evidence_kind)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_17(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE fx_rates ADD COLUMN source_reference TEXT")


def _backfill_fx_provenance(connection: sqlite3.Connection) -> None:
    """Give every pre-existing rate row its immutable provenance record."""

    rows = connection.execute(
        """
        SELECT fx_rate_id, rate_date, base_currency, quote_currency, rate,
            rate_source, source_reference, created_at
        FROM fx_rates
        """
    ).fetchall()
    for row in rows:
        raw = json.dumps(
            {
                "base_currency": row["base_currency"],
                "quote_currency": row["quote_currency"],
                "rate": row["rate"],
                "rate_date": row["rate_date"],
                "rate_source": row["rate_source"],
                "source_reference": row["source_reference"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        connection.execute(
            """
            INSERT INTO fx_provenance (
                fx_provenance_id, fx_rate_id, provenance_kind, primary_source_reference,
                raw_observation, raw_observation_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id(),
                row["fx_rate_id"],
                FX_PROVENANCE_KINDS.get(str(row["rate_source"]), "manual_adjustment"),
                row["source_reference"],
                raw,
                hashlib.sha256(raw.encode("utf-8")).hexdigest(),
                row["created_at"],
            ),
        )


def _migration_18(connection: sqlite3.Connection) -> None:
    """Add provider-neutral storage metadata without modifying business documents."""
    statements = (
        """
        CREATE TABLE files (
            file_id TEXT PRIMARY KEY,
            content_sha256 TEXT NOT NULL UNIQUE
                CHECK (length(content_sha256) = 64 AND content_sha256 NOT GLOB '*[^0-9a-f]*'),
            byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
            media_type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE storage_backends (
            storage_backend_id TEXT PRIMARY KEY,
            backend_key TEXT NOT NULL UNIQUE,
            display_name TEXT NOT NULL,
            driver_key TEXT NOT NULL,
            provider_key TEXT NOT NULL,
            access_mode TEXT NOT NULL CHECK (access_mode IN ('read_only', 'read_write')),
            config_json TEXT NOT NULL DEFAULT '{}',
            credential_ref TEXT,
            read_priority INTEGER NOT NULL DEFAULT 100,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE document_attachments (
            document_attachment_id TEXT PRIMARY KEY,
            document_id TEXT NOT NULL REFERENCES documents(document_id),
            file_id TEXT NOT NULL REFERENCES files(file_id),
            attachment_role TEXT NOT NULL CHECK (attachment_role IN ('source', 'supporting', 'generated')),
            display_name TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (document_id, file_id, attachment_role)
        )
        """,
        """
        CREATE TABLE file_replicas (
            file_replica_id TEXT PRIMARY KEY,
            file_id TEXT NOT NULL REFERENCES files(file_id),
            storage_backend_id TEXT NOT NULL REFERENCES storage_backends(storage_backend_id),
            provider_locator TEXT NOT NULL,
            provider_version TEXT,
            replica_status TEXT NOT NULL CHECK (replica_status IN ('available', 'missing', 'corrupt', 'retired')),
            is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
            web_url TEXT,
            provider_metadata_json TEXT NOT NULL DEFAULT '{}',
            last_verified_at TEXT,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (file_id, storage_backend_id),
            UNIQUE (storage_backend_id, provider_locator),
            CHECK (is_primary = 0 OR replica_status = 'available')
        )
        """,
        """
        CREATE UNIQUE INDEX document_attachments_one_source_per_document
            ON document_attachments(document_id)
            WHERE attachment_role = 'source'
        """,
        """
        CREATE UNIQUE INDEX file_replicas_one_primary_per_file
            ON file_replicas(file_id)
            WHERE is_primary = 1
        """,
        """
        CREATE INDEX file_replicas_read_lookup_idx
            ON file_replicas(file_id, replica_status, storage_backend_id)
        """,
    )
    for statement in statements:
        connection.execute(statement)


def _migration_19(connection: sqlite3.Connection) -> None:
    """Release immutable FX provenance (schema v19).

    Official and recorded rates stay unique per date/currency/source, while
    different documented settlements may now coexist. Every rate row gets
    exactly one immutable provenance record with its primary source,
    raw observation hash, and optional superseded-rate link.
    """
    statements = (
        """
        CREATE TABLE fx_rates_v19 (
            fx_rate_id TEXT PRIMARY KEY,
            rate_date TEXT NOT NULL,
            base_currency TEXT NOT NULL,
            quote_currency TEXT NOT NULL,
            rate TEXT NOT NULL,
            rate_source TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_reference TEXT,
            rule_version_id TEXT REFERENCES rule_versions(rule_version_id)
        )
        """,
        """
        INSERT INTO fx_rates_v19 (
            fx_rate_id, rate_date, base_currency, quote_currency, rate, rate_source,
            source_hash, row_version, created_at, updated_at, source_reference, rule_version_id
        )
        SELECT fx_rate_id, rate_date, base_currency, quote_currency, rate, rate_source,
            source_hash, row_version, created_at, updated_at, source_reference, rule_version_id
        FROM fx_rates
        """,
        "DROP TABLE fx_rates",
        "ALTER TABLE fx_rates_v19 RENAME TO fx_rates",
        """
        CREATE INDEX fx_rates_lookup_idx
            ON fx_rates(rate_date, base_currency, quote_currency, rate_source)
        """,
        """
        CREATE UNIQUE INDEX fx_rates_official_rate_unique
            ON fx_rates(rate_date, base_currency, quote_currency, rate_source)
        WHERE rate_source IN ('ecb', 'banco_de_espana', 'xolo_recorded')
        """,
        """
        CREATE TABLE fx_provenance (
            fx_provenance_id TEXT PRIMARY KEY,
            fx_rate_id TEXT NOT NULL REFERENCES fx_rates(fx_rate_id),
            provenance_kind TEXT NOT NULL CHECK (provenance_kind IN (
                'official', 'recorded', 'documented_settlement', 'manual_adjustment', 'derived'
            )),
            primary_source_reference TEXT,
            raw_observation TEXT NOT NULL,
            raw_observation_hash TEXT NOT NULL
                CHECK (length(raw_observation_hash) = 64 AND raw_observation_hash NOT GLOB '*[^0-9a-f]*'),
            supersedes_provenance_id TEXT REFERENCES fx_provenance(fx_provenance_id),
            created_at TEXT NOT NULL
        )
        """,
        "CREATE UNIQUE INDEX fx_provenance_one_per_rate ON fx_provenance(fx_rate_id)",
        "CREATE INDEX fx_provenance_supersedes_idx ON fx_provenance(supersedes_provenance_id)",
    )
    for statement in statements:
        connection.execute(statement)
    _backfill_fx_provenance(connection)


def _migration_20(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE counterparties ADD COLUMN name_is_manual INTEGER NOT NULL"
        " DEFAULT 0 CHECK (name_is_manual IN (0, 1))"
    )
    connection.execute("""
        CREATE TABLE counterparty_name_changes (
            change_id TEXT PRIMARY KEY,
            counterparty_id TEXT NOT NULL REFERENCES counterparties(counterparty_id),
            old_name TEXT NOT NULL, new_name TEXT NOT NULL,
            old_name_normalized TEXT NOT NULL, new_name_normalized TEXT NOT NULL,
            changed_at TEXT NOT NULL, change_source TEXT NOT NULL,
            actor TEXT,
            from_row_version INTEGER NOT NULL,
            to_row_version INTEGER NOT NULL CHECK (to_row_version = from_row_version + 1),
            UNIQUE (counterparty_id, to_row_version)
        )
    """)
    for column in ("old_name_normalized", "new_name_normalized"):
        connection.execute(
            f"CREATE INDEX counterparty_name_changes_{column}_idx"
            f" ON counterparty_name_changes({column}, counterparty_id)"
        )


def _migration_21(connection: sqlite3.Connection) -> None:
    connection.execute(
        "ALTER TABLE tax_treatments ADD COLUMN vat_investment_good INTEGER"
        " CHECK (vat_investment_good IN (0, 1))"
    )


def _migration_22(connection: sqlite3.Connection) -> None:
    statements = (
        """CREATE TABLE expense_drafts (
            transaction_id TEXT PRIMARY KEY REFERENCES transactions(transaction_id),
            payload_json TEXT NOT NULL, source_snapshot_hash TEXT NOT NULL,
            row_version INTEGER NOT NULL DEFAULT 1, actor TEXT NOT NULL, updated_at TEXT NOT NULL
        )""",
        """CREATE TABLE expense_draft_events (
            event_id TEXT PRIMARY KEY, transaction_id TEXT NOT NULL REFERENCES transactions(transaction_id),
            before_json TEXT NOT NULL, after_json TEXT NOT NULL, reason TEXT NOT NULL,
            actor TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE expense_actions (
            request_id TEXT PRIMARY KEY, action_kind TEXT NOT NULL, subject_id TEXT NOT NULL,
            request_hash TEXT NOT NULL, result_json TEXT NOT NULL, actor TEXT NOT NULL, created_at TEXT NOT NULL
        )""",
        """CREATE TABLE asset_depreciation_plans (
            asset_id TEXT PRIMARY KEY REFERENCES assets(asset_id), method TEXT NOT NULL CHECK(method IN ('immediate','linear')),
            calculation_version TEXT NOT NULL, parameters_json TEXT NOT NULL, source_hash TEXT NOT NULL
        )""",
        "ALTER TABLE amortization_entries ADD COLUMN recognition_transaction_id TEXT REFERENCES transactions(transaction_id)",
        "ALTER TABLE amortization_entries ADD COLUMN recognition_on TEXT",
        "CREATE UNIQUE INDEX amortization_one_recognition ON amortization_entries(recognition_transaction_id)",
    )
    for statement in statements:
        connection.execute(statement)
    # The previous private native workflow left an explicit transaction reference.
    # Adopt only a unique, matching posted journal; never infer from descriptions.
    connection.execute("""
        UPDATE amortization_entries AS ae SET recognition_transaction_id = substr(source_book_line_id,13),
            recognition_on = (SELECT transaction_date FROM transactions WHERE transaction_id=substr(ae.source_book_line_id,13))
        WHERE source_book_line_id LIKE 'transaction:%' AND entry_kind='quarter_schedule' AND include_in_books=1
          AND (SELECT count(*) FROM amortization_entries other WHERE other.source_book_line_id=ae.source_book_line_id)=1
          AND EXISTS (
            SELECT 1 FROM transactions t JOIN tax_treatments tt ON tt.transaction_id=t.transaction_id
            JOIN assets a ON a.asset_id=ae.asset_id
            JOIN transactions acquisition ON acquisition.transaction_id=a.acquisition_transaction_id
            WHERE t.transaction_id=substr(ae.source_book_line_id,13) AND t.period_id=ae.period_id
              AND t.entry_type='expense' AND t.lifecycle_status IN ('posted','included_in_snapshot')
              AND t.amount_minor=ae.amount_minor AND t.currency='EUR' AND t.document_id<>a.document_id
              AND t.counterparty_id=acquisition.counterparty_id AND tt.aeat_expense_concept='G31'
              AND tt.deductible_irpf_minor=ae.amount_minor AND tt.vat_minor=0
              AND tt.deductible_vat_minor=0 AND tt.include_modelo130=1 AND tt.include_modelo303=0
          )
    """)


def _migration_23(connection: sqlite3.Connection) -> None:
    connection.execute("""
        CREATE TABLE taxpayer_profile_changes (
            change_id TEXT PRIMARY KEY,
            taxpayer_profile_id TEXT NOT NULL REFERENCES taxpayer_profile(taxpayer_profile_id),
            old_values_json TEXT NOT NULL,
            new_values_json TEXT NOT NULL,
            actor TEXT,
            changed_at TEXT NOT NULL,
            from_row_version INTEGER NOT NULL,
            to_row_version INTEGER NOT NULL CHECK (to_row_version = from_row_version + 1),
            UNIQUE (taxpayer_profile_id, to_row_version)
        )
    """)


_MIGRATIONS = {
    1: _migration_1,
    2: _migration_2,
    3: _migration_3,
    4: _migration_4,
    5: _migration_5,
    6: _migration_6,
    7: _migration_7,
    8: _migration_8,
    9: _migration_9,
    10: _migration_10,
    11: _migration_11,
    12: _migration_12,
    13: _migration_13,
    14: _migration_14,
    15: _migration_15,
    16: _migration_16,
    17: _migration_17,
    18: _migration_18,
    19: _migration_19,
    20: _migration_20,
    21: _migration_21,
    22: _migration_22,
    23: _migration_23,
}
