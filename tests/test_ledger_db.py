from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from autonomo_taxes import ledger_db as ledger_db_module
from autonomo_taxes.ledger_db import (
    BlockingIssueError,
    ClosedPeriodError,
    FxRateConflictError,
    LedgerDB,
    LedgerDbError,
    LifecycleError,
    ObligationStateError,
    StaleRowVersionError,
    initialize,
    open as open_ledger_db,
)


EXPECTED_TABLES = {
    "taxpayer_profile",
    "household_members",
    "counterparties",
    "counterparty_identities",
    "documents",
    "document_sources",
    "intake_receipts",
    "import_batches",
    "transactions",
    "tax_treatments",
    "payments",
    "fx_rates",
    "assets",
    "amortization_entries",
    "obligations",
    "obligation_evidence",
    "tax_calendar_entries",
    "periods",
    "filing_snapshots",
    "validation_issues",
    "rule_versions",
    "annual_adjustments",
    "invoice_templates",
    "outgoing_invoice_drafts",
    "outgoing_invoice_lines",
    "business_activities",
}


class LedgerDbTests(unittest.TestCase):
    def test_open_applies_migrations_and_sets_user_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.execute("PRAGMA user_version = 0")
            finally:
                connection.close()

            with open_ledger_db(path, apply_migrations=True) as db:
                version = db.connection.execute("PRAGMA user_version").fetchone()[0]
                self.assertEqual(version, 18)

                tables = {
                    row[0]
                    for row in db.connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    ).fetchall()
                }
                self.assertTrue(EXPECTED_TABLES.issubset(tables))

    def test_real_v5_schema_migrates_filing_evidence_and_issue_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v5.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 6):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.execute(
                    """
                    INSERT INTO periods (
                        period_id, period_key, period_type, starts_on, ends_on, status,
                        source_hash, row_version, created_at, updated_at
                    ) VALUES ('period-q2', '2026-Q2', 'quarter', '2026-04-01', '2026-06-30',
                              'open', 'period-hash', 1, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z')
                    """
                )
                for issue_id, issue_code, subject_id, updated_at in (
                    (
                        "legacy-issue",
                        "amount_mismatch",
                        "reconcile-row:2026-Q2:amount_mismatch:123:INV-1:2026-04-01",
                        "2026-07-01T00:00:00Z",
                    ),
                    (
                        "canonical-issue",
                        "pending_confirmation",
                        "reconcile-row:2026-Q2:123:INV-1:2026-04-01",
                        "2026-07-02T00:00:00Z",
                    ),
                ):
                    connection.execute(
                        """
                        INSERT INTO validation_issues (
                            validation_issue_id, period_id, subject_table, subject_id, issue_code,
                            severity, message, blocking, issue_status, source_hash, row_version,
                            created_at, updated_at
                        ) VALUES (?, 'period-q2', 'xolo_expense_reconcile', ?, ?, 'error',
                                  'legacy issue', 1, 'open', ?, 1, ?, ?)
                        """,
                        (issue_id, subject_id, issue_code, f"hash-{issue_id}", updated_at, updated_at),
                    )
                connection.execute(
                    """
                    INSERT INTO filing_snapshots (
                        filing_snapshot_id, period_id, snapshot_hash, filed_on, status,
                        payload_json, source_hash, row_version, created_at, updated_at
                    ) VALUES ('snapshot-303', 'period-q2', 'snapshot-hash', '2026-07-08',
                              'baseline', '{"form":"303"}', 'pdf-hash', 1,
                              '2026-07-08T00:00:00Z', '2026-07-08T00:00:00Z')
                    """
                )
                connection.execute(
                    """
                    INSERT INTO payments (
                        payment_id, paid_on, amount_minor, currency, source_hash,
                        row_version, created_at, updated_at, original_reference, match_status
                    ) VALUES ('legacy-payment', '2026-06-30', -1000, 'EUR', 'legacy-payment-hash',
                              1, '2026-07-01T00:00:00Z', '2026-07-01T00:00:00Z',
                              'LEGACY-REF', 'unmatched')
                    """
                )
                connection.commit()
            finally:
                connection.close()

            with open_ledger_db(path, apply_migrations=True) as db:
                version = db.connection.execute("PRAGMA user_version").fetchone()[0]
                issues = db.connection.execute(
                    "SELECT subject_id, dedupe_key FROM validation_issues"
                ).fetchall()
                snapshot = db.connection.execute(
                    "SELECT form_code, source_reference FROM filing_snapshots"
                ).fetchone()
                payment = db.connection.execute(
                    "SELECT * FROM payments WHERE payment_id = 'legacy-payment'"
                ).fetchone()

                self.assertEqual(version, 18)
                self.assertEqual(len(issues), 1)
                self.assertEqual(
                    issues[0]["dedupe_key"],
                    "xolo-reconcile:reconcile-row:2026-Q2:123:INV-1:2026-04-01",
                )
                self.assertEqual(snapshot["form_code"], "303")
                self.assertIsNone(snapshot["source_reference"])
                self.assertEqual(payment["amount_minor"], -1000)
                self.assertEqual(payment["source_hash"], "legacy-payment-hash")
                self.assertIsNone(payment["source_system"])
                self.assertIsNone(payment["external_id"])

    def test_schema_8_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v7.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 8):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[8]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute("ALTER TABLE payments ADD COLUMN source_system TEXT")
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[8] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[8] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(payments)").fetchall()
                }
            finally:
                connection.close()
            self.assertEqual(version, 7)
            self.assertNotIn("source_system", columns)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 18)

    def test_schema_9_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v8.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 9):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[9]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "CREATE TABLE invoice_templates (invoice_template_id TEXT PRIMARY KEY)"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[9] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[9] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'invoice_templates'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(version, 8)
            self.assertIsNone(table)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 18)

    def test_schema_12_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v11.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 12):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[12]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "CREATE TABLE counterparty_identities "
                    "(counterparty_identity_id TEXT PRIMARY KEY)"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[12] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[12] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                table = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'counterparty_identities'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(version, 11)
            self.assertIsNone(table)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 18)

    def test_schema_13_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v12.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 13):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[13]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "ALTER TABLE tax_treatments ADD COLUMN aeat_invoice_type TEXT"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[13] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[13] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(tax_treatments)"
                    ).fetchall()
                }
            finally:
                connection.close()
            self.assertEqual(version, 12)
            self.assertNotIn("aeat_invoice_type", columns)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(
                    db.connection.execute("PRAGMA user_version").fetchone()[0], 18
                )

    def test_schema_14_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v13.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 14):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[14]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "CREATE TABLE tax_calendar_entries "
                    "(tax_calendar_entry_id TEXT PRIMARY KEY)"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[14] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[14] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                table = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'tax_calendar_entries'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(version, 13)
            self.assertIsNone(table)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(
                    db.connection.execute("PRAGMA user_version").fetchone()[0], 18
                )

    def test_schema_15_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v14.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 15):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[15]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "CREATE TABLE intake_receipts "
                    "(intake_receipt_id TEXT PRIMARY KEY)"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[15] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[15] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                table = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'intake_receipts'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(version, 14)
            self.assertIsNone(table)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(
                    db.connection.execute("PRAGMA user_version").fetchone()[0], 18
                )

    def test_schema_16_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v15.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 16):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[16]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "ALTER TABLE counterparties ADD COLUMN legal_form TEXT"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[16] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[16] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(counterparties)"
                    ).fetchall()
                }
                table = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name = 'obligation_evidence'"
                ).fetchone()
            finally:
                connection.close()
            self.assertEqual(version, 15)
            self.assertNotIn("legal_form", columns)
            self.assertIsNone(table)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(
                    db.connection.execute("PRAGMA user_version").fetchone()[0], 18
                )

    def test_schema_17_migrates_fx_rates_source_reference_as_nullable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v16.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 17):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.execute(
                    """
                    INSERT INTO fx_rates (
                        fx_rate_id, rate_date, base_currency, quote_currency, rate, rate_source,
                        source_hash, row_version, created_at, updated_at, rule_version_id
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, NULL)
                    """,
                    (
                        "fx-v16-row",
                        "2026-06-18",
                        "USD",
                        "EUR",
                        "0.86",
                        "ecb",
                        "legacy-fx-hash",
                        "2026-06-18T10:00:00+00:00",
                        "2026-06-18T10:00:00+00:00",
                    ),
                )
                connection.commit()
            finally:
                connection.close()

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(
                    db.connection.execute("PRAGMA user_version").fetchone()[0], 18
                )
                row = db.connection.execute(
                    "SELECT source_reference FROM fx_rates WHERE fx_rate_id = ?",
                    ("fx-v16-row",),
                ).fetchone()

            self.assertIsNotNone(row)
            self.assertIsNone(row[0])

    def test_schema_17_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v16.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 17):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[17]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "ALTER TABLE fx_rates ADD COLUMN source_reference TEXT"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[17] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[17] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(fx_rates)")
                }
            finally:
                connection.close()
            self.assertEqual(version, 16)
            self.assertNotIn("source_reference", columns)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(
                    db.connection.execute("PRAGMA user_version").fetchone()[0], 18
                )

    def test_counterparty_identity_is_source_backed_versioned_and_singular(self) -> None:
        with self._database() as db:
            supplier = db.upsert_counterparty(
                external_key="supplier-ge",
                display_name="Foreign Supplier",
                country_code="GE",
            )
            with self.assertRaisesRegex(ValueError, "requires an existing primary"):
                db.upsert_counterparty_identity(
                    counterparty_id=supplier["counterparty_id"],
                    identity_kind="other_proof",
                    country_code="GE",
                    identifier="SECONDARY-FIRST",
                    is_primary=False,
                    source_reference="Other evidence",
                    source_hash="other-evidence-hash",
                )
            identity = db.upsert_counterparty_identity(
                counterparty_id=supplier["counterparty_id"],
                identity_kind="official_id",
                country_code="GE",
                identifier="345795641",
                source_reference="Public registry extract",
                source_hash="registry-extract-hash",
            )
            self.assertEqual(identity["aeat_id_type"], "04")
            self.assertEqual(identity["row_version"], 1)
            repeated = db.upsert_counterparty_identity(
                counterparty_id=supplier["counterparty_id"],
                identity_kind="official_id",
                country_code="GE",
                identifier="345795641",
                source_reference="Public registry extract",
                source_hash="registry-extract-hash",
            )
            self.assertEqual(repeated["row_version"], 1)
            secondary = db.upsert_counterparty_identity(
                counterparty_id=supplier["counterparty_id"],
                identity_kind="other_proof",
                country_code="GE",
                identifier="SECOND-ID",
                is_primary=False,
                source_reference="Other evidence",
                source_hash="other-evidence-hash",
            )
            with self.assertRaisesRegex(ValueError, "duplicates existing"):
                db.upsert_counterparty_identity(
                    counterparty_identity_id=secondary["counterparty_identity_id"],
                    counterparty_id=supplier["counterparty_id"],
                    identity_kind="official_id",
                    country_code="GE",
                    identifier="345795641",
                    is_primary=False,
                    source_reference="Other evidence",
                    source_hash="other-evidence-hash",
                    expected_row_version=secondary["row_version"],
                )
            with self.assertRaisesRegex(ValueError, "already has a primary"):
                db.upsert_counterparty_identity(
                    counterparty_id=supplier["counterparty_id"],
                    identity_kind="other_proof",
                    country_code="GE",
                    identifier="ANOTHER-PRIMARY",
                    source_reference="Other evidence",
                    source_hash="other-evidence-hash",
                )
            with self.assertRaisesRegex(ValueError, "cannot be demoted"):
                db.upsert_counterparty_identity(
                    counterparty_identity_id=identity["counterparty_identity_id"],
                    counterparty_id=supplier["counterparty_id"],
                    identity_kind="official_id",
                    country_code="GE",
                    identifier="345795641",
                    is_primary=False,
                    source_reference="Public registry extract",
                    source_hash="registry-extract-hash",
                    expected_row_version=identity["row_version"],
                )
            listed = db.list_counterparty_identities(
                counterparty_id=supplier["counterparty_id"]
            )
            self.assertEqual(
                [row["identifier"] for row in listed],
                ["345795641", "SECOND-ID"],
            )

    def test_detailed_tax_treatment_stores_reviewed_aeat_book_classification(self) -> None:
        with self._database() as db:
            transaction = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="expense",
                description="Reviewed foreign service",
                amount_minor=10000,
            )
            treatment = db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code="non_eu_service_expense",
                aeat_invoice_type="f1",
                aeat_operation_key="01",
                aeat_reverse_charge=True,
                aeat_expense_concept="g19",
                rate_basis_points=2100,
                taxable_base_minor=10000,
                vat_minor=2100,
                deductible_irpf_minor=10000,
                deductible_vat_minor=2100,
            )

            self.assertEqual(treatment["aeat_invoice_type"], "F1")
            self.assertEqual(treatment["aeat_operation_key"], "01")
            self.assertEqual(treatment["aeat_reverse_charge"], 1)
            self.assertEqual(treatment["aeat_expense_concept"], "G19")
            with self.assertRaisesRegex(ValueError, "operation key"):
                db.add_detailed_tax_treatment(
                    transaction_id=transaction["transaction_id"],
                    treatment_type="expense",
                    tax_code="non_eu_service_expense",
                    aeat_operation_key="1",
                )

    def test_counterparty_identity_concurrent_first_insert_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.sqlite3"
            with initialize(path) as db:
                supplier = db.upsert_counterparty(
                    external_key="concurrent-supplier",
                    display_name="Concurrent Supplier",
                    country_code="US",
                )
                counterparty_id = supplier["counterparty_id"]

            barrier = threading.Barrier(2)

            def insert_identity() -> dict:
                with open_ledger_db(path) as db:
                    barrier.wait(timeout=10)
                    return db.upsert_counterparty_identity(
                        counterparty_id=counterparty_id,
                        identity_kind="official_id",
                        country_code="US",
                        identifier="12-3456789",
                        source_reference="Official registry evidence",
                        source_hash="official-registry-hash",
                    )

            with ThreadPoolExecutor(max_workers=2) as executor:
                rows = list(executor.map(lambda _: insert_identity(), range(2)))

            self.assertEqual(
                {row["counterparty_identity_id"] for row in rows},
                {rows[0]["counterparty_identity_id"]},
            )
            with open_ledger_db(path, read_only=True) as db:
                self.assertEqual(len(db.list_counterparty_identities()), 1)

    def test_schema_10_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v9.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 10):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[10]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute(
                    "CREATE TABLE business_activities (business_activity_id TEXT PRIMARY KEY)"
                )
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[10] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[10] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'business_activities'"
                ).fetchone()
                transaction_columns = {
                    row[1]
                    for row in connection.execute("PRAGMA table_info(transactions)").fetchall()
                }
            finally:
                connection.close()
            self.assertEqual(version, 9)
            self.assertIsNone(table)
            self.assertNotIn("business_activity_id", transaction_columns)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 18)

    def test_schema_11_ddl_rolls_back_atomically_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger-v10.sqlite3"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                for version in range(1, 11):
                    ledger_db_module._MIGRATIONS[version](connection)
                    connection.execute(f"PRAGMA user_version = {version}")
                connection.commit()
            finally:
                connection.close()

            original = ledger_db_module._MIGRATIONS[11]

            def fail_after_first_ddl(connection: sqlite3.Connection) -> None:
                connection.execute("ALTER TABLE assets ADD COLUMN aeat_asset_identifier TEXT")
                raise RuntimeError("forced migration failure")

            ledger_db_module._MIGRATIONS[11] = fail_after_first_ddl
            try:
                with self.assertRaisesRegex(RuntimeError, "forced migration failure"):
                    open_ledger_db(path, apply_migrations=True)
            finally:
                ledger_db_module._MIGRATIONS[11] = original

            connection = sqlite3.connect(path)
            try:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(assets)").fetchall()
                }
            finally:
                connection.close()
            self.assertEqual(version, 10)
            self.assertNotIn("aeat_asset_identifier", columns)

            with open_ledger_db(path, apply_migrations=True) as db:
                self.assertEqual(db.connection.execute("PRAGMA user_version").fetchone()[0], 18)

    def test_business_activity_is_versioned_and_auto_assigned_when_unambiguous(self) -> None:
        with self._database() as db:
            profile = db.upsert_taxpayer_profile(
                tax_id="X0000000A",
                full_name="Example Taxpayer",
                source_hash="modelo-036-hash",
            )
            repeated_profile = db.upsert_taxpayer_profile(
                tax_id="X0000000A",
                full_name="Example Taxpayer",
                source_hash="modelo-036-hash",
            )
            self.assertEqual(repeated_profile["row_version"], 1)
            with self.assertRaises(StaleRowVersionError):
                db.upsert_taxpayer_profile(
                    tax_id="X0000000A",
                    full_name="Changed Taxpayer",
                    source_hash="changed-modelo-036-hash",
                )

            activity = db.upsert_business_activity(
                taxpayer_profile_id=profile["taxpayer_profile_id"],
                activity_key="software-development",
                aeat_activity_code="A",
                aeat_activity_type="05",
                iae_section="2",
                iae_group_epigraph="763",
                description="Programmers and computer analysts",
                starts_on="2023-05-31",
                source_reference="Modelo 036 / 2023C3609400132R",
                source_hash="activity-source-hash",
            )
            repeated_activity = db.upsert_business_activity(
                taxpayer_profile_id=profile["taxpayer_profile_id"],
                activity_key="software-development",
                aeat_activity_code="A",
                aeat_activity_type="05",
                iae_section="2",
                iae_group_epigraph="763",
                description="Programmers and computer analysts",
                starts_on="2023-05-31",
                source_reference="Modelo 036 / 2023C3609400132R",
                source_hash="activity-source-hash",
            )
            self.assertEqual(repeated_activity["row_version"], 1)

            transaction = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="income",
                description="Software development",
                amount_minor=10000,
            )
            self.assertEqual(
                transaction["business_activity_id"],
                activity["business_activity_id"],
            )

            second = db.upsert_business_activity(
                taxpayer_profile_id=profile["taxpayer_profile_id"],
                activity_key="second-activity",
                aeat_activity_code="A",
                aeat_activity_type="05",
                iae_section="2",
                iae_group_epigraph="769",
                description="Other professional activity",
                starts_on="2026-01-01",
                source_reference="Reviewed activity decision",
                source_hash="second-activity-hash",
            )
            ambiguous = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-07-02",
                booking_date="2026-07-02",
                entry_type="income",
                description="Needs explicit activity",
                amount_minor=20000,
            )
            self.assertIsNone(ambiguous["business_activity_id"])
            assigned = db.set_transaction_business_activity(
                ambiguous["transaction_id"],
                business_activity_id=second["business_activity_id"],
                expected_row_version=ambiguous["row_version"],
            )
            self.assertEqual(
                assigned["business_activity_id"],
                second["business_activity_id"],
            )

            posted = db.add_transaction(
                period_key="2026-Q3",
                transaction_date="2026-07-03",
                booking_date="2026-07-03",
                entry_type="income",
                description="Posted activity is immutable",
                amount_minor=30000,
                lifecycle_status="posted",
                business_activity_id=activity["business_activity_id"],
            )
            with self.assertRaisesRegex(
                LifecycleError,
                "lifecycle_status=posted",
            ):
                db.set_transaction_business_activity(
                    posted["transaction_id"],
                    business_activity_id=second["business_activity_id"],
                    expected_row_version=posted["row_version"],
                )

    def test_lifecycle_validation_and_snapshot_transition(self) -> None:
        with self._database() as db:
            counterparty = db.upsert_counterparty(
                external_key="vendor-acme",
                tax_id="ESA12345678",
                display_name="Acme Supplies",
            )
            document = db.upsert_document(
                external_key="doc-1",
                counterparty_id=counterparty["counterparty_id"],
                document_type="invoice",
                document_number="INV-2026-001",
                issued_on="2026-04-02",
                period_key="2026-Q2",
                lifecycle_status="received",
            )

            with self.assertRaises(LifecycleError):
                db.upsert_document(
                    document_id=document["document_id"],
                    external_key="doc-1",
                    counterparty_id=counterparty["counterparty_id"],
                    document_type="invoice",
                    document_number="INV-2026-001",
                    issued_on="2026-04-02",
                    period_key="2026-Q2",
                    lifecycle_status="posted",
                    expected_row_version=document["row_version"],
                )

            for next_status in ("extracted", "needs_review", "approved", "posted"):
                document = db.upsert_document(
                    document_id=document["document_id"],
                    external_key="doc-1",
                    counterparty_id=counterparty["counterparty_id"],
                    document_type="invoice",
                    document_number="INV-2026-001",
                    issued_on="2026-04-02",
                    period_key="2026-Q2",
                    lifecycle_status=next_status,
                    expected_row_version=document["row_version"],
                )

            transaction = db.add_transaction(
                external_key="tx-1",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Office supplies",
                amount_minor=12500,
                lifecycle_status="received",
                document_id=document["document_id"],
                counterparty_id=counterparty["counterparty_id"],
            )
            for next_status in ("extracted", "needs_review", "approved", "posted"):
                transaction = db.add_transaction(
                    transaction_id=transaction["transaction_id"],
                    external_key="tx-1",
                    period_key="2026-Q2",
                    transaction_date="2026-04-02",
                    booking_date="2026-04-02",
                    entry_type="expense",
                    description="Office supplies",
                    amount_minor=12500,
                    lifecycle_status=next_status,
                    document_id=document["document_id"],
                    counterparty_id=counterparty["counterparty_id"],
                    expected_row_version=transaction["row_version"],
                )

            period = db.close_period("2026-Q2", expected_row_version=1)
            self.assertEqual(period["status"], "closed")

            snapshot = db.create_filing_snapshot(
                "2026-Q2",
                payload={"form": "modelo130"},
                status="filed",
            )
            self.assertEqual(snapshot["status"], "filed")

            stored_transaction = db.connection.execute(
                "SELECT lifecycle_status, included_snapshot_id FROM transactions WHERE transaction_id = ?",
                (transaction["transaction_id"],),
            ).fetchone()
            self.assertEqual(stored_transaction["lifecycle_status"], "included_in_snapshot")
            self.assertEqual(stored_transaction["included_snapshot_id"], snapshot["filing_snapshot_id"])

    def test_stale_row_version_is_rejected(self) -> None:
        with self._database() as db:
            counterparty = db.upsert_counterparty(
                external_key="cp-1",
                tax_id="ESB12345678",
                display_name="Original Name",
            )

            with self.assertRaises(StaleRowVersionError):
                db.upsert_counterparty(
                    counterparty_id=counterparty["counterparty_id"],
                    external_key="cp-1",
                    tax_id="ESB12345678",
                    display_name="Updated Name",
                    expected_row_version=counterparty["row_version"] + 7,
                )

    def test_terminal_documents_and_transactions_allow_only_exact_noops(self) -> None:
        with self._database() as db:
            document = db.upsert_document(
                external_key="terminal-document",
                document_type="expense_invoice",
                document_number="TERM-1",
                issued_on="2026-04-02",
                period_key="2026-Q2",
                lifecycle_status="rejected",
                source_hash="terminal-document-hash",
            )
            transaction = db.add_transaction(
                external_key="terminal-transaction",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Duplicate expense",
                amount_minor=12500,
                lifecycle_status="duplicate",
                source_hash="terminal-transaction-hash",
            )

            same_document = db.upsert_document(
                document_id=document["document_id"],
                external_key="terminal-document",
                document_type="expense_invoice",
                document_number="TERM-1",
                issued_on="2026-04-02",
                period_key="2026-Q2",
                lifecycle_status="rejected",
                source_hash="terminal-document-hash",
                expected_row_version=1,
            )
            same_transaction = db.add_transaction(
                transaction_id=transaction["transaction_id"],
                external_key="terminal-transaction",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Duplicate expense",
                amount_minor=12500,
                lifecycle_status="duplicate",
                source_hash="terminal-transaction-hash",
                expected_row_version=1,
            )
            self.assertEqual(same_document["row_version"], 1)
            self.assertEqual(same_transaction["row_version"], 1)

            with self.assertRaisesRegex(LifecycleError, "Terminal lifecycle row rejected is immutable"):
                db.upsert_document(
                    document_id=document["document_id"],
                    external_key="terminal-document",
                    document_type="expense_invoice",
                    document_number="TERM-1",
                    issued_on="2026-04-02",
                    period_key="2026-Q2",
                    total_minor=999,
                    lifecycle_status="rejected",
                    source_hash="terminal-document-hash",
                    expected_row_version=1,
                )

            with self.assertRaisesRegex(LifecycleError, "Terminal lifecycle row duplicate is immutable"):
                db.add_transaction(
                    transaction_id=transaction["transaction_id"],
                    external_key="terminal-transaction",
                    period_key="2026-Q2",
                    transaction_date="2026-04-02",
                    booking_date="2026-04-02",
                    entry_type="expense",
                    description="Changed duplicate expense",
                    amount_minor=12500,
                    lifecycle_status="duplicate",
                    source_hash="terminal-transaction-hash",
                    expected_row_version=1,
                )

    def test_close_period_blocks_open_issues_and_unfiled_obligations(self) -> None:
        with self._database() as db:
            issue = db.add_validation_issue(
                period_key="2026-Q2",
                issue_code="missing-source-proof",
                severity="error",
                message="Source document still missing.",
                blocking=True,
            )
            obligation = db.add_obligation(
                period_key="2026-Q2",
                obligation_code="modelo303",
                due_on="2026-07-20",
                filing_status="unknown",
            )

            with self.assertRaises(BlockingIssueError):
                db.close_period("2026-Q2", expected_row_version=1)

            resolved_issue = db.add_validation_issue(
                period_key="2026-Q2",
                issue_code="missing-source-proof",
                severity="info",
                message="Evidence uploaded.",
                blocking=False,
                issue_status="resolved",
                expected_row_version=issue["row_version"],
            )
            self.assertEqual(db.list_issues(period_key="2026-Q2"), [])
            self.assertEqual(resolved_issue["issue_status"], "resolved")

            with self.assertRaises(ObligationStateError):
                db.close_period("2026-Q2", expected_row_version=1)

            filed = db.add_obligation(
                period_key="2026-Q2",
                obligation_code="modelo303",
                due_on="2026-07-20",
                filing_status="filed",
                filed_at="2026-07-18",
                expected_row_version=obligation["row_version"],
            )
            period = db.close_period("2026-Q2", expected_row_version=1)

            self.assertEqual(filed["filing_status"], "filed")
            self.assertEqual(period["status"], "closed")

    def test_closed_periods_are_immutable_for_new_and_existing_transactions(self) -> None:
        with self._database() as db:
            transaction = db.add_transaction(
                external_key="tx-immutability",
                period_key="2026-Q2",
                transaction_date="2026-04-04",
                booking_date="2026-04-04",
                entry_type="expense",
                description="Immutable expense",
                amount_minor=9000,
                lifecycle_status="posted",
            )
            db.close_period("2026-Q2", expected_row_version=1)

            with self.assertRaises(ClosedPeriodError):
                db.add_transaction(
                    external_key="tx-late-insert",
                    period_key="2026-Q2",
                    transaction_date="2026-04-05",
                    booking_date="2026-04-05",
                    entry_type="expense",
                    description="Late insert",
                    amount_minor=1000,
                    lifecycle_status="received",
                )

            with self.assertRaises(ClosedPeriodError):
                db.add_transaction(
                    transaction_id=transaction["transaction_id"],
                    external_key="tx-immutability",
                    period_key="2026-Q2",
                    transaction_date="2026-04-04",
                    booking_date="2026-04-04",
                    entry_type="expense",
                    description="Attempted edit after close",
                    amount_minor=9000,
                    lifecycle_status="void",
                    expected_row_version=transaction["row_version"],
                )

    def test_closed_periods_are_immutable_for_documents_and_linked_assets(self) -> None:
        with self._database() as db:
            document = db.upsert_document(
                external_key="asset-document",
                document_type="expense_invoice",
                document_number="ASSET-1",
                issued_on="2026-04-04",
                period_key="2026-Q2",
                lifecycle_status="posted",
                source_hash="asset-document-hash",
            )
            transaction = db.add_transaction(
                external_key="asset-transaction",
                period_key="2026-Q2",
                transaction_date="2026-04-04",
                booking_date="2026-04-04",
                entry_type="expense",
                description="Capital purchase",
                amount_minor=12100,
                lifecycle_status="posted",
                document_id=document["document_id"],
            )
            asset = db.add_asset(
                asset_code="CAPITAL-1",
                cost_minor=10000,
                currency="EUR",
                depreciation_method="straight_line",
                document_id=document["document_id"],
                acquisition_transaction_id=transaction["transaction_id"],
                useful_life_months=24,
                source_hash="capital-asset",
            )
            db.close_period("2026-Q2", expected_row_version=1)

            with self.assertRaises(ClosedPeriodError):
                db.upsert_document(
                    external_key="late-document",
                    document_type="expense_invoice",
                    document_number="LATE-1",
                    issued_on="2026-04-05",
                    period_key="2026-Q2",
                    lifecycle_status="received",
                    source_hash="late-document-hash",
                )

            with self.assertRaises(ClosedPeriodError):
                db.add_asset(
                    asset_code="CAPITAL-1",
                    cost_minor=10000,
                    currency="EUR",
                    depreciation_method="straight_line",
                    document_id=document["document_id"],
                    acquisition_transaction_id=transaction["transaction_id"],
                    useful_life_months=12,
                    source_hash="capital-asset-updated",
                    expected_row_version=asset["row_version"],
                )

    def test_amendment_links_corrections_to_closed_period_transactions(self) -> None:
        with self._database() as db:
            original = db.add_transaction(
                external_key="tx-original",
                period_key="2026-Q2",
                transaction_date="2026-04-08",
                booking_date="2026-04-08",
                entry_type="expense",
                description="Original filing row",
                amount_minor=22000,
                lifecycle_status="posted",
            )
            closed_period = db.close_period("2026-Q2", expected_row_version=1)
            amended_period = db.amend_period(
                "2026-Q2",
                amendment_period_key="2026-Q3",
                reason="Late supplier correction",
                expected_row_version=closed_period["row_version"],
            )

            correction = db.add_transaction(
                external_key="tx-original-reversal",
                period_key="2026-Q3",
                transaction_date="2026-07-02",
                booking_date="2026-07-02",
                entry_type="expense_correction",
                description="Reversal for Q2 late correction",
                amount_minor=-22000,
                lifecycle_status="posted",
                correction_of_transaction_id=original["transaction_id"],
                correction_kind="reversing",
            )

            self.assertEqual(amended_period["status"], "amended")
            self.assertIsNotNone(amended_period["amendment_period_id"])
            self.assertEqual(correction["correction_of_transaction_id"], original["transaction_id"])
            self.assertEqual(correction["correction_kind"], "reversing")

    def test_backup_and_restore_round_trip_with_sqlite_backup_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            primary_path = root / "primary.sqlite3"
            backup_path = root / "backup.sqlite3"
            restored_path = root / "restored.sqlite3"

            with initialize(primary_path) as primary:
                primary.upsert_counterparty(
                    external_key="restore-me",
                    tax_id="ESC12345678",
                    display_name="Backup Vendor",
                )
                primary.add_transaction(
                    external_key="restore-tx",
                    period_key="2026-Q2",
                    transaction_date="2026-04-10",
                    booking_date="2026-04-10",
                    entry_type="expense",
                    description="Persist through backup",
                    amount_minor=3300,
                    lifecycle_status="posted",
                )

                primary.backup_to(backup_path)

            with LedgerDB.initialize(restored_path) as restored:
                restored.restore_from(backup_path)

                counterparty_names = restored.connection.execute(
                    "SELECT display_name FROM counterparties ORDER BY display_name"
                ).fetchall()
                transaction_count = restored.connection.execute(
                    "SELECT COUNT(*) FROM transactions"
                ).fetchone()[0]

                self.assertEqual([row["display_name"] for row in counterparty_names], ["Backup Vendor"])
                self.assertEqual(transaction_count, 1)

    def test_restore_rejects_missing_or_corrupt_source_without_mutating_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target_path = root / "target.sqlite3"
            corrupt_path = root / "corrupt.sqlite3"
            corrupt_path.write_text("not a sqlite database", encoding="utf-8")

            with initialize(target_path) as target:
                target.upsert_counterparty(
                    external_key="preserve-me",
                    tax_id="TEST-TAX-ID-PRESERVE",
                    display_name="Preserved Vendor",
                )
                with self.assertRaises(FileNotFoundError):
                    target.restore_from(root / "typo.sqlite3")
                with self.assertRaises(sqlite3.DatabaseError):
                    target.restore_from(corrupt_path)

                names = target.connection.execute(
                    "SELECT display_name FROM counterparties"
                ).fetchall()
                self.assertEqual([row["display_name"] for row in names], ["Preserved Vendor"])

    def test_restore_rolls_back_target_when_post_copy_validation_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source_path = root / "source.sqlite3"
            target_path = root / "target.sqlite3"

            with initialize(source_path) as source:
                source.upsert_counterparty(
                    external_key="replacement",
                    tax_id="TEST-TAX-ID-REPLACEMENT",
                    display_name="Replacement Vendor",
                )

            with initialize(target_path) as target:
                target.upsert_counterparty(
                    external_key="preserve-me",
                    tax_id="TEST-TAX-ID-PRESERVE",
                    display_name="Preserved Vendor",
                )
                with patch.object(
                    LedgerDB,
                    "_apply_migrations",
                    side_effect=RuntimeError("simulated migration failure"),
                ):
                    with self.assertRaisesRegex(RuntimeError, "simulated migration failure"):
                        target.restore_from(source_path)

                names = target.connection.execute(
                    "SELECT display_name FROM counterparties ORDER BY display_name"
                ).fetchall()
                integrity = target.connection.execute("PRAGMA integrity_check").fetchone()[0]
                self.assertEqual([row["display_name"] for row in names], ["Preserved Vendor"])
                self.assertEqual(integrity, "ok")

    def test_payment_evidence_can_be_appended_and_reimported_after_period_close(self) -> None:
        with self._database() as db:
            transaction = db.add_transaction(
                external_key="closed-payment-transaction",
                period_key="2026-Q2",
                transaction_date="2026-06-29",
                booking_date="2026-06-29",
                entry_type="expense",
                description="Closed period expense",
                amount_minor=1000,
                lifecycle_status="posted",
            )
            db.close_period("2026-Q2", expected_row_version=1)

            payment = db.add_payment(
                transaction_id=transaction["transaction_id"],
                paid_on="2026-06-30T12:00:00Z",
                amount_minor=1000,
                currency="EUR",
                source_hash="late-payment",
                match_status="exact",
            )
            repeated = db.add_payment(
                transaction_id=transaction["transaction_id"],
                paid_on="2026-06-30",
                amount_minor=1000,
                currency="EUR",
                source_hash="late-payment",
                match_status="exact",
            )

            self.assertEqual(repeated["payment_id"], payment["payment_id"])
            self.assertEqual(db.table_counts()["payments"], 1)

            with self.assertRaisesRegex(LedgerDbError, "different fields: amount_minor"):
                db.add_payment(
                    transaction_id=transaction["transaction_id"],
                    paid_on="2026-06-30",
                    amount_minor=2000,
                    currency="EUR",
                    source_hash="late-payment",
                    match_status="exact",
                )

    def test_asset_with_closed_amortization_rows_is_immutable_even_without_links(self) -> None:
        with self._database() as db:
            asset = db.add_asset(
                asset_code="UNLINKED-CLOSED",
                cost_minor=12000,
                currency="EUR",
                depreciation_method="straight_line",
                source_hash="unlinked-asset",
            )
            entry = db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2026-Q2",
                amount_minor=1000,
                source_hash="unlinked-amortization",
            )
            db.close_period("2026-Q2", expected_row_version=1)

            with self.assertRaises(ClosedPeriodError):
                db.add_asset(
                    asset_code="UNLINKED-CLOSED",
                    cost_minor=12000,
                    currency="EUR",
                    depreciation_method="straight_line",
                    source_hash="unlinked-asset-update",
                    advisor_decision="Attempted late decision",
                    expected_row_version=asset["row_version"],
                )

            with self.assertRaises(ClosedPeriodError):
                db.delete_amortization_entry(entry["amortization_entry_id"])

    def test_obligation_determination_is_separate_from_filing_status(self) -> None:
        with self._database() as db:
            not_due = db.add_obligation(
                period_key="2026-Q2",
                obligation_code="349",
                filing_status="waived",
                determination="not_due",
                explanation="No intracommunity operations.",
                source_citation="AEAT Modelo 349",
            )
            due = db.add_obligation(
                period_key="2026-Q2",
                obligation_code="303",
                filing_status="due",
                determination="due",
            )

            self.assertEqual(not_due["determination"], "not_due")
            self.assertEqual(not_due["blocking"], 0)
            self.assertEqual(due["blocking"], 1)
            validation = db.validate_period("2026-Q2")
            self.assertEqual(
                [row["obligation_code"] for row in validation["unresolved_obligations"]],
                ["303"],
            )

            filed = db.add_obligation(
                period_key="2026-Q2",
                obligation_code="303",
                filing_status="filed",
                determination="due",
                filed_at="2026-07-18",
                expected_row_version=due["row_version"],
            )
            self.assertEqual(filed["blocking"], 0)
            self.assertEqual(db.validate_period("2026-Q2")["unresolved_obligations"], [])

    def test_asset_year_requires_quarter_schedule_to_match_annual_evidence(self) -> None:
        with self._database() as db:
            asset = db.add_asset(
                asset_code="AIRPODS-2026",
                cost_minor=19900,
                currency="EUR",
                depreciation_method="straight_line",
                amortizable_base_minor=19900,
                business_use_ratio=1.0,
                annual_rate_basis_points=2600,
                source_hash="asset-airpods",
            )
            db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2026",
                amount_minor=5174,
                source_hash="annual-row",
                entry_kind="annual_evidence",
                tax_year=2026,
                source_book_line_id="0A-42",
            )
            for quarter, amount in ((1, 1293), (2, 1293), (3, 1294), (4, 1294)):
                db.add_amortization_entry(
                    asset_id=asset["asset_id"],
                    period_key=f"2026-Q{quarter}",
                    amount_minor=amount,
                    source_hash=f"quarter-{quarter}",
                    tax_year=2026,
                )

            result = db.validate_asset_year(2026)
            self.assertTrue(result["ready"])
            self.assertEqual(result["orphan_count"], 0)
            self.assertEqual(result["assets"][0]["annual_minor"], 5174)
            self.assertEqual(result["assets"][0]["quarter_minor"], 5174)

            db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2026-Q4",
                amount_minor=1200,
                source_hash="quarter-4-corrected",
                tax_year=2026,
            )
            mismatch = db.validate_asset_year(2026)
            self.assertFalse(mismatch["ready"])
            self.assertEqual(mismatch["issues"][0]["issue_code"], "asset_amortization_mismatch")

    def test_asset_year_blocks_asset_without_any_amortization_rows(self) -> None:
        with self._database() as db:
            asset = db.add_asset(
                asset_code="MISSING-YEAR-SCHEDULE",
                cost_minor=10000,
                currency="EUR",
                depreciation_method="straight_line",
                placed_in_service_on="2026-04-01",
                amortizable_base_minor=10000,
                business_use_ratio=1.0,
                annual_rate_basis_points=2600,
                source_hash="missing-year-schedule",
            )

            result = db.validate_asset_year(2026)

            self.assertFalse(result["ready"])
            self.assertEqual(result["orphan_count"], 1)
            self.assertEqual(result["assets"][0]["asset_id"], asset["asset_id"])
            self.assertEqual(
                {issue["issue_code"] for issue in result["issues"]},
                {"missing_annual_asset_evidence", "missing_quarter_asset_schedule"},
            )

    def test_asset_year_ignores_future_and_previously_fully_amortized_assets(self) -> None:
        with self._database() as db:
            future = db.add_asset(
                asset_code="FUTURE-ASSET",
                cost_minor=10000,
                currency="EUR",
                depreciation_method="straight_line",
                placed_in_service_on="2027-01-01",
                source_hash="future-asset",
            )
            completed = db.add_asset(
                asset_code="COMPLETED-ASSET",
                cost_minor=10000,
                currency="EUR",
                depreciation_method="straight_line",
                placed_in_service_on="2025-01-01",
                amortizable_base_minor=20000,
                business_use_ratio=0.5,
                source_hash="completed-asset",
            )
            db.add_amortization_entry(
                asset_id=completed["asset_id"],
                period_key="2025",
                amount_minor=10000,
                source_hash="completed-annual",
                entry_kind="annual_evidence",
                tax_year=2025,
            )

            result = db.validate_asset_year(2026)

            self.assertTrue(result["ready"])
            self.assertEqual(result["assets"], [])
            self.assertNotEqual(future["asset_id"], completed["asset_id"])

    def test_direct_annual_close_enforces_asset_year_invariant(self) -> None:
        with self._database() as db:
            asset = db.add_asset(
                asset_code="ANNUAL-GATE",
                cost_minor=10000,
                currency="EUR",
                depreciation_method="straight_line",
                source_hash="annual-gate-asset",
            )
            db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2026",
                amount_minor=1000,
                source_hash="annual-gate-evidence",
                entry_kind="annual_evidence",
                tax_year=2026,
            )
            db.add_amortization_entry(
                asset_id=asset["asset_id"],
                period_key="2026-Q4",
                amount_minor=900,
                source_hash="annual-gate-quarter",
                tax_year=2026,
            )

            with self.assertRaisesRegex(BlockingIssueError, "annual asset amortization"):
                db.close_period("2026", expected_row_version=1)

    def test_add_fx_rate_is_idempotent_and_backfills_source_reference(self) -> None:
        with self._database() as db:
            created = db.add_fx_rate(
                rate_date="2026-06-18",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.8600",
                rate_source="ecb",
                source_hash="fx-created",
            )

            backfilled = db.add_fx_rate(
                rate_date="2026-06-18",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.86",
                rate_source="ecb",
                source_reference="ECB daily XML",
                source_hash="fx-backfill",
            )
            preserved = db.add_fx_rate(
                rate_date="2026-06-18",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.860",
                rate_source="ecb",
                source_hash="fx-preserved",
            )

            self.assertEqual(created["fx_rate_id"], backfilled["fx_rate_id"])
            self.assertEqual(backfilled["source_reference"], "ECB daily XML")
            self.assertEqual(preserved["fx_rate_id"], created["fx_rate_id"])
            self.assertEqual(preserved["source_reference"], "ECB daily XML")
            self.assertEqual(
                db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0],
                1,
            )

    def test_add_fx_rate_rejects_conflicting_rate_or_reference_for_same_key(self) -> None:
        with self._database() as db:
            db.add_fx_rate(
                rate_date="2026-06-18",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.86",
                rate_source="ecb",
                source_reference="ECB daily XML",
                source_hash="fx-original",
            )

            with self.assertRaisesRegex(FxRateConflictError, "Conflicting FX rate"):
                db.add_fx_rate(
                    rate_date="2026-06-18",
                    base_currency="USD",
                    quote_currency="EUR",
                    rate="0.87",
                    rate_source="ecb",
                    source_reference="ECB daily XML",
                    source_hash="fx-rate-conflict",
                )
            with self.assertRaisesRegex(
                FxRateConflictError,
                "Conflicting FX source_reference",
            ):
                db.add_fx_rate(
                    rate_date="2026-06-18",
                    base_currency="USD",
                    quote_currency="EUR",
                    rate="0.86",
                    rate_source="ecb",
                    source_reference="Banco de Espana bulletin",
                    source_hash="fx-reference-conflict",
                )

    def test_review_transaction_fx_rate_updates_transaction_and_detects_stale_version(self) -> None:
        with self._database() as db:
            transaction = db.add_transaction(
                external_key="usd-expense-review",
                period_key="2026-Q2",
                transaction_date="2026-06-18",
                booking_date="2026-06-18",
                entry_type="expense",
                description="USD expense pending review",
                amount_minor=10000,
                currency="USD",
                amount_original_minor=10000,
                original_currency="USD",
                lifecycle_status="needs_review",
            )

            reviewed = db.review_transaction_fx_rate(
                transaction["transaction_id"],
                expected_row_version=transaction["row_version"],
                rate_date="2026-06-18",
                rate="0.86",
                rate_source="ecb",
                source_reference="ECB daily XML",
            )

            fx_row = db.connection.execute(
                "SELECT rate, rate_source, source_reference, source_hash FROM fx_rates WHERE fx_rate_id = ?",
                (reviewed["fx_rate_id"],),
            ).fetchone()

            self.assertEqual(reviewed["amount_eur_minor"], 8600)
            self.assertEqual(reviewed["row_version"], transaction["row_version"] + 1)
            self.assertEqual(fx_row["rate"], "0.86")
            self.assertEqual(fx_row["rate_source"], "ecb")
            self.assertEqual(fx_row["source_reference"], "ECB daily XML")
            self.assertEqual(len(fx_row["source_hash"]), 64)

            with self.assertRaises(StaleRowVersionError):
                db.review_transaction_fx_rate(
                    transaction["transaction_id"],
                    expected_row_version=transaction["row_version"],
                    rate_date="2026-06-18",
                    rate="0.86",
                    rate_source="ecb",
                    source_reference="ECB daily XML",
                )

    def test_review_transaction_fx_rate_enforces_source_reference_and_production_policy(
        self,
    ) -> None:
        with self._database() as db:
            transaction = db.add_transaction(
                external_key="usd-income-review",
                period_key="2026-Q2",
                transaction_date="2026-06-18",
                booking_date="2026-06-18",
                entry_type="income",
                description="USD income pending review",
                amount_minor=10000,
                currency="USD",
                amount_original_minor=10000,
                original_currency="USD",
                lifecycle_status="approved",
            )
            future_transaction = db.add_transaction(
                external_key="usd-income-post-cutover",
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="income",
                description="Post-cutover Xolo transaction",
                amount_minor=10000,
                currency="USD",
                amount_original_minor=10000,
                original_currency="USD",
                lifecycle_status="approved",
            )

            with self.assertRaisesRegex(ValueError, "nonblank source_reference"):
                db.review_transaction_fx_rate(
                    transaction["transaction_id"],
                    expected_row_version=transaction["row_version"],
                    rate_date="2026-06-18",
                    rate="0.86",
                    rate_source="ecb",
                    source_reference="   ",
                )
            with self.assertRaisesRegex(ValueError, "not allowed in production"):
                db.review_transaction_fx_rate(
                    transaction["transaction_id"],
                    expected_row_version=transaction["row_version"],
                    rate_date="2026-06-18",
                    rate="0.86",
                    rate_source="target_derived",
                    source_reference="Derived from target total",
                )
            with self.assertRaisesRegex(ValueError, "not allowed after 2026-06-30"):
                db.review_transaction_fx_rate(
                    future_transaction["transaction_id"],
                    expected_row_version=future_transaction["row_version"],
                    rate_date="2026-06-30",
                    rate="0.86",
                    rate_source="xolo_recorded",
                    source_reference="Captured in Xolo source book",
                )

    def test_review_transaction_fx_rate_rolls_back_transaction_on_fx_conflict(self) -> None:
        with self._database() as db:
            db.add_fx_rate(
                rate_date="2026-06-18",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.86",
                rate_source="ecb",
                source_reference="ECB daily XML",
                source_hash="fx-existing",
            )
            transaction = db.add_transaction(
                external_key="usd-income-conflict",
                period_key="2026-Q2",
                transaction_date="2026-06-18",
                booking_date="2026-06-18",
                entry_type="income",
                description="USD income with conflicting FX review",
                amount_minor=10000,
                currency="USD",
                amount_original_minor=10000,
                original_currency="USD",
                lifecycle_status="approved",
            )

            with self.assertRaises(FxRateConflictError):
                db.review_transaction_fx_rate(
                    transaction["transaction_id"],
                    expected_row_version=transaction["row_version"],
                    rate_date="2026-06-18",
                    rate="0.87",
                    rate_source="ecb",
                    source_reference="ECB daily XML",
                )

            stored = db.connection.execute(
                "SELECT row_version, amount_eur_minor, fx_rate_id FROM transactions WHERE transaction_id = ?",
                (transaction["transaction_id"],),
            ).fetchone()

            self.assertEqual(stored["row_version"], transaction["row_version"])
            self.assertIsNone(stored["amount_eur_minor"])
            self.assertIsNone(stored["fx_rate_id"])

    def test_target_derived_fx_blocks_period_close(self) -> None:
        with self._database() as db:
            fx = db.add_fx_rate(
                rate_date="2026-04-02",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.85",
                rate_source="target_derived",
                source_hash="target-derived-rate",
            )
            db.add_transaction(
                external_key="usd-income",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="income",
                description="Historical target-derived income",
                amount_minor=10000,
                currency="USD",
                amount_original_minor=10000,
                original_currency="USD",
                amount_eur_minor=8500,
                fx_rate_id=fx["fx_rate_id"],
                lifecycle_status="posted",
            )

            validation = db.validate_period("2026-Q2")
            self.assertFalse(validation["ready"])
            self.assertEqual(len(validation["target_derived_fx"]), 1)
            with self.assertRaises(BlockingIssueError):
                db.close_period("2026-Q2", expected_row_version=1)

    def test_xolo_recorded_fx_after_cutover_blocks_period_close(self) -> None:
        with self._database() as db:
            fx = db.add_fx_rate(
                rate_date="2026-07-01",
                base_currency="USD",
                quote_currency="EUR",
                rate="0.85",
                rate_source="xolo_recorded",
                source_hash="xolo-q3-rate",
            )
            db.add_transaction(
                external_key="q3-xolo-fx",
                period_key="2026-Q3",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="income",
                description="Invalid post-cutover Xolo FX",
                amount_minor=10000,
                currency="USD",
                amount_original_minor=10000,
                original_currency="USD",
                amount_eur_minor=8500,
                fx_rate_id=fx["fx_rate_id"],
                lifecycle_status="posted",
            )

            validation = db.validate_period("2026-Q3")
            self.assertFalse(validation["ready"])
            self.assertEqual(len(validation["xolo_recorded_fx_after_cutover"]), 1)
            with self.assertRaisesRegex(BlockingIssueError, "post-cutover Xolo FX"):
                db.close_period("2026-Q3", expected_row_version=1)

    def test_zero_posted_amount_blocks_period_close(self) -> None:
        with self._database() as db:
            db.add_transaction(
                external_key="zero-expense",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Unexplained zero expense",
                amount_minor=0,
                lifecycle_status="posted",
            )

            validation = db.validate_period("2026-Q2")

            self.assertFalse(validation["ready"])
            self.assertEqual(len(validation["invalid_amount_transactions"]), 1)
            with self.assertRaisesRegex(BlockingIssueError, "invalid monetary amounts"):
                db.close_period("2026-Q2", expected_row_version=1)

    def test_transaction_date_outside_period_blocks_close(self) -> None:
        with self._database() as db:
            db.add_transaction(
                external_key="wrong-quarter",
                period_key="2026-Q2",
                transaction_date="2026-07-01",
                booking_date="2026-07-01",
                entry_type="expense",
                description="Wrong period assignment",
                amount_minor=1000,
                lifecycle_status="posted",
            )

            validation = db.validate_period("2026-Q2")

            self.assertFalse(validation["ready"])
            self.assertEqual(len(validation["out_of_period_transactions"]), 1)
            with self.assertRaisesRegex(BlockingIssueError, "outside period bounds"):
                db.close_period("2026-Q2", expected_row_version=1)

    def test_filed_baseline_can_be_preserved_before_recomputed_period_closes(self) -> None:
        with self._database() as db:
            transaction = db.add_transaction(
                external_key="historical-row",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="income",
                description="Historical approved row",
                amount_minor=10000,
                lifecycle_status="approved",
            )
            snapshot = db.create_filing_snapshot(
                "2026-Q2",
                status="baseline",
                filed_on="2026-07-08",
                payload={"form": "130", "filed_values": {"01": "100.00"}},
            )

            self.assertEqual(snapshot["status"], "baseline")
            stored = db.connection.execute(
                "SELECT lifecycle_status, included_snapshot_id FROM transactions WHERE transaction_id = ?",
                (transaction["transaction_id"],),
            ).fetchone()
            self.assertEqual(stored["lifecycle_status"], "approved")
            self.assertIsNone(stored["included_snapshot_id"])

    def test_filing_snapshot_keeps_form_level_submission_evidence(self) -> None:
        with self._database() as db:
            db.ensure_period("2026-Q2")
            snapshot = db.create_filing_snapshot(
                "2026-Q2",
                status="baseline",
                filed_on="2026-07-08T12:49:25",
                payload={"filed_values": {"result": "-407.36"}},
                form_code="303",
                submission_reference="202630309400430F",
                justificante_number="3037001157635",
                verification_code="QD558KKTDLCUTEG4",
                source_reference="Xolo evidence archive/tax_reports/2026/MOD 303 2T 2026.pdf",
            )

            self.assertEqual(snapshot["form_code"], "303")
            self.assertEqual(snapshot["submission_reference"], "202630309400430F")
            self.assertEqual(snapshot["justificante_number"], "3037001157635")
            self.assertEqual(snapshot["verification_code"], "QD558KKTDLCUTEG4")
            self.assertIn('"form":"303"', snapshot["payload_json"])

    def test_authoritative_history_gate_requires_lineage_and_filed_period_evidence(self) -> None:
        with self._database() as db:
            batch = db.add_import_batch(
                source_name="xolo_source_book_rows",
                source_hash="official-source-book-hash",
            )
            document = db.upsert_document(
                external_key="official-book-document",
                import_batch_id=batch["import_batch_id"],
                document_type="gastos_book",
                document_number="INV-1",
                issued_on="2026-04-02",
                period_key="2026-Q2",
                lifecycle_status="approved",
                source_hash="official-book-document-hash",
            )
            db.add_document_source(
                document_id=document["document_id"],
                import_batch_id=batch["import_batch_id"],
                source_book_line_id="official-row-1",
                source_file="Libros_contables_2026.xlsx",
                source_row_number="7",
                source_hash="official-row-hash",
            )
            official = db.add_transaction(
                external_key="official-book-transaction",
                period_key="2026-Q2",
                transaction_date="2026-04-02",
                booking_date="2026-04-02",
                entry_type="expense",
                description="Official Xolo source-book expense",
                amount_minor=10000,
                lifecycle_status="approved",
                document_id=document["document_id"],
            )
            ordinary = db.add_transaction(
                external_key="ordinary-approved-transaction",
                period_key="2026-Q2",
                transaction_date="2026-04-03",
                booking_date="2026-04-03",
                entry_type="expense",
                description="Ordinary approved expense",
                amount_minor=5000,
                lifecycle_status="approved",
            )
            db.add_detailed_tax_treatment(
                transaction_id=ordinary["transaction_id"],
                treatment_type="expense",
                tax_code="domestic_input",
                taxable_base_minor=5000,
                deductible_irpf_minor=5000,
                include_modelo130=True,
            )

            before_baseline = db.validate_period(
                "2026-Q2",
                allow_authoritative_history=True,
            )
            self.assertEqual(len(before_baseline["review_transactions"]), 2)
            self.assertEqual(before_baseline["authoritative_history_transactions"], [])

            db.create_filing_snapshot(
                "2026-Q2",
                status="baseline",
                filed_on="2026-07-08",
                payload={"form": "130", "filed_values": {"02": "100.00"}},
            )
            default_validation = db.validate_period("2026-Q2")
            gated_validation = db.validate_period(
                "2026-Q2",
                allow_authoritative_history=True,
            )

            self.assertEqual(len(default_validation["review_transactions"]), 2)
            self.assertEqual(
                [row["transaction_id"] for row in gated_validation["review_transactions"]],
                [ordinary["transaction_id"]],
            )
            self.assertEqual(
                [row["transaction_id"] for row in gated_validation["authoritative_history_transactions"]],
                [official["transaction_id"]],
            )

            db.transition_transaction(
                ordinary["transaction_id"],
                lifecycle_status="posted",
                expected_row_version=ordinary["row_version"],
            )
            closed = db.close_period(
                "2026-Q2",
                expected_row_version=1,
                allow_authoritative_history=True,
            )
            self.assertEqual(closed["status"], "closed")

    def _database(self) -> LedgerDB:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return initialize(Path(tmp.name) / "ledger.sqlite3")


if __name__ == "__main__":
    unittest.main()
