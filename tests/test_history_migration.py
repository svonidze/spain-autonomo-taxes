from __future__ import annotations

import csv
from decimal import Decimal
import json
from pathlib import Path

import pytest

from autonomo_taxes.cli import main
from autonomo_taxes.filing_evidence import FilingEvidence
from autonomo_taxes.history_migration import (
    _find_acquisition_transaction,
    _prune_unreferenced_migration_counterparty,
    _prune_superseded_source_book_records,
    _upsert_counterparty,
    _usable_tax_id,
    migrate_xolo_history,
    refresh_filing_inventory,
)
from autonomo_taxes.ledger_db import BlockingIssueError, LedgerDB


SOURCE_BOOK_FIELDS = [
    "source_book_type",
    "source_scope",
    "source_file",
    "source_file_format",
    "source_row_number",
    "source_book_line_id",
    "period",
    "date",
    "booking_date",
    "supplier",
    "document_number",
    "counterparty_country_code",
    "counterparty_tax_id",
    "counterparty_vat_id",
    "invoice_type",
    "operation_key",
    "operation_qualification",
    "exempt_operation",
    "reverse_charge",
    "original_amount",
    "original_currency",
    "gross_eur",
    "invoice_total_eur",
    "taxable_base_eur",
    "vat_rate_percent",
    "vat_eur",
    "deductible_vat_eur",
    "withholding_rate_percent",
    "withholding_eur",
    "deductible_base_eur",
    "irpf_deductible_eur",
    "vat_treatment",
    "reason_code",
    "asset_id",
    "amortizable_base_eur",
    "amortization_period",
    "amortization_amount_eur",
    "rate_or_life",
    "casilla01_ytd",
    "casilla02_ytd",
    "casilla07",
    "casilla19",
    "import_status",
    "missing_required_groups",
    "notes",
]
RECONCILIATION_FIELDS = [
    "status",
    "date",
    "recipient",
    "number",
    "currency",
    "amount_original",
    "gross_eur",
    "irpf_deductible_eur",
    "local_deductible_eur",
    "deductible_diff_eur",
    "local_document",
    "xolo_id",
    "notes",
]


def test_migrate_xolo_history_is_idempotent_and_never_auto_posts(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        first = migrate_xolo_history(db, source_csv, reconciliation_csv)
        transaction_identity_before = _transaction_identity(db)
        row_versions_before = _row_versions(db)
        counts_after_first = db.table_counts()

        second = migrate_xolo_history(db, source_csv, reconciliation_csv)
        counts_after_second = db.table_counts()

        assert first["counts"] == second["counts"]
        assert first["counts"] == {
            "import_batches": 2,
            "quarterly_rows": 2,
            "documents": 3,
            "transactions": 2,
            "tax_treatments": 2,
            "annual_asset_rows": 1,
            "assets": 1,
            "amortization_entries": 1,
            "derived_quarter_schedule_rows": 3,
            "validation_issues": 3,
            "ignored_reconciliation_rows": 1,
            "classified_source_book_obligations": 1,
            "seeded_obligations": 27,
        }
        assert len(first["issues"]) == 3
        assert counts_after_first == counts_after_second
        assert transaction_identity_before == _transaction_identity(db)
        assert row_versions_before == _row_versions(db)
        assert counts_after_second["documents"] == 3
        assert counts_after_second["transactions"] == 2
        assert counts_after_second["tax_treatments"] == 2
        assert counts_after_second["assets"] == 1
        assert counts_after_second["amortization_entries"] == 4
        assert counts_after_second["validation_issues"] == 3
        assert counts_after_second["obligations"] == 27

        modelo349 = db.connection.execute(
            """
            SELECT o.determination, o.filing_status, o.blocking
            FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = '2026-Q2' AND o.obligation_code = '349'
            """
        ).fetchone()
        assert modelo349["determination"] == "not_due"
        assert modelo349["filing_status"] == "waived"
        assert modelo349["blocking"] == 0

        transactions = db.list_transactions(period_key="2026-Q2")
        assert {row["lifecycle_status"] for row in transactions} == {"approved"}
        assert all(row["lifecycle_status"] != "posted" for row in transactions)
        historical_asset_expense = next(row for row in transactions if row["entry_type"] == "expense")
        assert historical_asset_expense["transaction_date"] == "2026-06-30"
        document_date = db.connection.execute(
            "SELECT issued_on FROM documents WHERE document_id = ?",
            (historical_asset_expense["document_id"],),
        ).fetchone()["issued_on"]
        assert document_date == "2024-04-21"


def test_reimport_preserves_reviewed_identity_and_historical_external_key(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1].update(
        {
            "supplier": "Synthetic Party 007",
            "document_number": "SYNTH-DOCUMENT-028",
            "counterparty_country_code": "US",
            "counterparty_tax_id": "7804391",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        historical_key = "counterparty-tax:US:7804391"
        imported = db.connection.execute(
            "SELECT * FROM counterparties WHERE external_key = ?",
            (historical_key,),
        ).fetchone()
        assert imported["tax_id"] == "7804391"
        transaction_source_hash = db.connection.execute(
            """
            SELECT source_hash
            FROM transactions
            WHERE counterparty_id = ?
            """,
            (imported["counterparty_id"],),
        ).fetchone()["source_hash"]

        reviewed = db.upsert_counterparty(
            external_key=historical_key,
            tax_id="TEST-TAX-ID-003",
            display_name="Synthetic Party 007",
            country_code="US",
            source_hash="reviewed-anthropic-master-hash",
        )
        db.upsert_counterparty_identity(
            counterparty_id=reviewed["counterparty_id"],
            identity_kind="official_id",
            country_code="US",
            identifier="TEST-TAX-ID-003",
            source_reference="Synthetic public registry record REGISTRATION-001",
            source_hash="reviewed-anthropic-identity-hash",
        )

        rows[1]["notes"] = "Changed source row forces a non-idempotent re-import"
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        stored = db.connection.execute(
            "SELECT * FROM counterparties WHERE counterparty_id = ?",
            (reviewed["counterparty_id"],),
        ).fetchone()
        identities = db.list_counterparty_identities(
            counterparty_id=reviewed["counterparty_id"]
        )

        assert stored["external_key"] == historical_key
        assert stored["tax_id"] == "TEST-TAX-ID-003"
        assert db.connection.execute(
            """
            SELECT source_hash
            FROM transactions
            WHERE counterparty_id = ?
            """,
            (reviewed["counterparty_id"],),
        ).fetchone()["source_hash"] != transaction_source_hash
        assert len(identities) == 1
        assert identities[0]["aeat_id_type"] == "04"
        assert identities[0]["identifier"] == "TEST-TAX-ID-003"
        assert identities[0]["is_primary"] == 1


def test_placeholder_vat_id_never_merges_unrelated_counterparties(tmp_path: Path) -> None:
    placeholder = "ES0000000T"
    assert _usable_tax_id(placeholder) is False

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        apple_id = _upsert_counterparty(
            db,
            {
                "supplier": "Synthetic Party 016",
                "counterparty_country_code": "ES",
                "counterparty_vat_id": placeholder,
            },
        )
        tgss_id = _upsert_counterparty(
            db,
            {
                "supplier": "Synthetic Party 011",
                "counterparty_country_code": "ES",
                "counterparty_vat_id": placeholder,
            },
        )
        assert apple_id != tgss_id

        upgraded_apple_id = _upsert_counterparty(
            db,
            {
                "supplier": "Synthetic Party 016",
                "counterparty_country_code": "ES",
                "counterparty_vat_id": "TEST-TAX-ID-002",
            },
        )
        repeated_weak_apple_id = _upsert_counterparty(
            db,
            {
                "supplier": "Synthetic Party 016",
                "counterparty_country_code": "ES",
                "counterparty_vat_id": placeholder,
            },
        )
        apple = db.connection.execute(
            "SELECT external_key, vat_id FROM counterparties WHERE counterparty_id = ?",
            (apple_id,),
        ).fetchone()

        legacy = db.upsert_counterparty(
            external_key=f"counterparty-vat:{placeholder}",
            display_name="Synthetic Party 008",
            country_code="ES",
        )
        legacy_id = legacy["counterparty_id"]
        db.set_counterparty_tax_profile(
            legacy_id,
            vat_id=placeholder,
            expected_row_version=legacy["row_version"],
        )
        repeated_legacy_id = _upsert_counterparty(
            db,
            {
                "supplier": "Synthetic Party 008",
                "counterparty_country_code": "ES",
            },
        )
        cleaned_legacy = db.connection.execute(
            "SELECT external_key, vat_id FROM counterparties WHERE counterparty_id = ?",
            (legacy_id,),
        ).fetchone()

    assert upgraded_apple_id == apple_id
    assert repeated_weak_apple_id == apple_id
    assert apple["external_key"] == "counterparty-vat:TEST-TAX-ID-002"
    assert apple["vat_id"] == "TEST-TAX-ID-002"
    assert repeated_legacy_id == legacy_id
    assert cleaned_legacy["external_key"] == "counterparty-name:syntheticparty008"
    assert cleaned_legacy["vat_id"] is None


def test_placeholder_row_reuses_unique_reviewed_identity_for_exact_supplier_name(
    tmp_path: Path,
) -> None:
    placeholder = "ES0000000T"
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        weak = db.upsert_counterparty(
            external_key="counterparty-name:synthetictreasury",
            display_name="Synthetic Treasury",
            country_code="ES",
        )
        reviewed = db.upsert_counterparty(
            external_key="counterparty-vat:TEST-TAX-ID-001",
            tax_id="TEST-TAX-ID-001",
            display_name="Synthetic Treasury",
            country_code="ES",
        )
        reviewed = db.set_counterparty_tax_profile(
            reviewed["counterparty_id"],
            vat_id="TEST-TAX-ID-001",
            expected_row_version=reviewed["row_version"],
        )

        selected_id = _upsert_counterparty(
            db,
            {
                "supplier": "Synthetic Treasury",
                "counterparty_country_code": "",
                "counterparty_tax_id": "0000000T",
                "counterparty_vat_id": placeholder,
            },
        )

        assert selected_id == reviewed["counterparty_id"]
        assert selected_id != weak["counterparty_id"]
        stored = db.connection.execute(
            "SELECT display_name, country_code FROM counterparties WHERE counterparty_id = ?",
            (selected_id,),
        ).fetchone()
        assert tuple(stored) == ("Synthetic Treasury", "ES")


def test_identity_match_does_not_replace_reviewed_name_with_source_alias(
    tmp_path: Path,
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        reviewed = db.upsert_counterparty(
            external_key="counterparty-vat:TEST-TAX-ID-002",
            tax_id="TEST-TAX-ID-002",
            display_name="Synthetic Party 016",
            country_code="ES",
        )
        reviewed = db.set_counterparty_tax_profile(
            reviewed["counterparty_id"],
            vat_id="TEST-TAX-ID-002",
            expected_row_version=reviewed["row_version"],
        )

        selected_id = _upsert_counterparty(
            db,
            {
                "supplier": "Apple Retal Spain, s.L.U.",
                "counterparty_country_code": "ES",
                "counterparty_tax_id": "TEST-TAX-ID-002",
                "counterparty_vat_id": "TEST-TAX-ID-002",
            },
        )
        stored = db.connection.execute(
            "SELECT display_name FROM counterparties WHERE counterparty_id = ?",
            (selected_id,),
        ).fetchone()

        assert selected_id == reviewed["counterparty_id"]
        assert stored["display_name"] == "Synthetic Party 016"


def test_corrected_source_row_relinks_document_without_changing_document_identity(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    expense = rows[1]
    expense.update(
        {
            "supplier": "Synthetic Party 011",
            "counterparty_country_code": "ES",
            "counterparty_tax_id": "0000000T",
            "counterparty_vat_id": "ES0000000T",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        before = db.connection.execute(
            """
            SELECT t.transaction_id, t.document_id, t.counterparty_id
            FROM transactions t
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()
        reviewed = db.upsert_counterparty(
            external_key="counterparty-vat:TEST-TAX-ID-001",
            tax_id="TEST-TAX-ID-001",
            display_name="Synthetic Party 011",
            country_code="ES",
        )
        reviewed = db.set_counterparty_tax_profile(
            reviewed["counterparty_id"],
            vat_id="TEST-TAX-ID-001",
            expected_row_version=reviewed["row_version"],
        )
        rows[1]["notes"] = "Reviewed supplier identity now available"
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

        migrate_xolo_history(db, source_csv, reconciliation_csv)
        after = db.connection.execute(
            """
            SELECT t.transaction_id, t.document_id, t.counterparty_id,
                   d.counterparty_id AS document_counterparty_id
            FROM transactions t
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()

        assert after["transaction_id"] == before["transaction_id"]
        assert after["document_id"] == before["document_id"]
        assert after["counterparty_id"] == reviewed["counterparty_id"]
        assert after["document_counterparty_id"] == reviewed["counterparty_id"]


def test_source_line_id_is_scoped_by_book_type(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1]["source_book_line_id"] = rows[0]["source_book_line_id"]
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        documents = db.connection.execute(
            """
            SELECT d.document_type, d.document_number
            FROM documents d
            JOIN document_sources ds ON ds.document_id = d.document_id
            WHERE ds.source_book_line_id = ?
            ORDER BY d.document_type
            """,
            (rows[0]["source_book_line_id"],),
        ).fetchall()

        assert [tuple(row) for row in documents] == [
            ("gastos_book", "204355514"),
            ("ingresos_book", "INV-2026-001"),
        ]


def test_blank_source_line_ids_do_not_collapse_documents(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1]["source_book_line_id"] = ""
    second_expense = {
        **rows[1],
        "source_row_number": "5",
        "document_number": "SYNTH-DOCUMENT-017",
        "date": "22/04/2024",
        "booking_date": "23/04/2024",
        "source_book_line_id": "",
    }
    rows.insert(2, second_expense)
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        documents = db.connection.execute(
            """
            SELECT document_number, document_id
            FROM documents
            WHERE document_type = 'gastos_book'
              AND document_number IN ('204355514', 'SYNTH-DOCUMENT-017')
            ORDER BY document_number
            """
        ).fetchall()

        assert [row["document_number"] for row in documents] == [
            "204355514",
            "SYNTH-DOCUMENT-017",
        ]
        assert len({row["document_id"] for row in documents}) == 2


def test_counterparty_prune_preserves_invoice_template_reference(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        counterparty = db.upsert_counterparty(
            external_key="counterparty-name:futurecustomer",
            display_name="Future Customer",
            country_code="US",
        )
        db.upsert_invoice_template(
            template_key="future-customer",
            template_name="Future customer services",
            counterparty_id=counterparty["counterparty_id"],
            currency="USD",
            default_lines=[
                {
                    "description": "Software development",
                    "quantity": "1",
                    "unit_amount_minor": 10000,
                    "tax_code": "outside_scope",
                    "channel_tax_code": "N2",
                    "tax_rate_basis_points": 0,
                }
            ],
            recipient_address_line1="100 Example Street",
            recipient_city="New York",
        )

        _prune_unreferenced_migration_counterparty(db, counterparty["counterparty_id"])

        assert db.connection.execute(
            "SELECT 1 FROM counterparties WHERE counterparty_id = ?",
            (counterparty["counterparty_id"],),
        ).fetchone()


@pytest.mark.parametrize(
    "tax_codes",
    [
        ("domestic_input", "domestic_input"),
        ("historical_g03", "historical_g03"),
    ],
)
def test_acquisition_lookup_rejects_ambiguous_transactions(
    tmp_path: Path,
    tax_codes: tuple[str, str],
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        counterparty = db.upsert_counterparty(
            external_key="apple-spain",
            display_name="Synthetic Party 016",
        )
        for index, tax_code in enumerate(tax_codes, start=1):
            document = db.upsert_document(
                external_key=f"apple-document-{index}",
                counterparty_id=counterparty["counterparty_id"],
                document_type="gastos_book",
                document_number=f"SYNTH-DOCUMENT-031-{index}",
                issued_on="2026-04-08",
                period_key="2026-Q2",
                lifecycle_status="approved",
            )
            transaction = db.add_transaction(
                external_key=f"apple-transaction-{index}",
                period_key="2026-Q2",
                transaction_date="2026-04-08",
                booking_date="2026-04-08",
                entry_type="expense",
                description=f"Apple acquisition candidate {index}",
                amount_minor=199400,
                lifecycle_status="approved",
                document_id=document["document_id"],
                counterparty_id=counterparty["counterparty_id"],
            )
            db.add_detailed_tax_treatment(
                transaction_id=transaction["transaction_id"],
                treatment_type="expense",
                tax_code=tax_code,
                taxable_base_minor=164793,
                deductible_irpf_minor=164793,
                include_modelo130=True,
            )

        match = _find_acquisition_transaction(
            db,
            counterparty_id=counterparty["counterparty_id"],
            issued_on="2026-04-08",
        )

    assert match is None


def test_migrate_xolo_history_updates_corrected_source_line_in_place(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        before = db.connection.execute(
            "SELECT document_source_id, source_hash FROM document_sources WHERE source_book_line_id = ?",
            ("income-line-1",),
        ).fetchone()

        rows = _load_csv(source_csv)
        rows[0]["notes"] = "Corrected source-book note"
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        sources = db.connection.execute(
            "SELECT document_source_id, source_hash FROM document_sources WHERE source_book_line_id = ?",
            ("income-line-1",),
        ).fetchall()
        treatment = db.connection.execute(
            """
            SELECT tt.notes
            FROM tax_treatments tt
            JOIN transactions t ON t.transaction_id=tt.transaction_id
            JOIN document_sources ds ON ds.document_id=t.document_id
            WHERE ds.source_book_line_id = 'income-line-1'
            """
        ).fetchone()

        assert len(sources) == 1
        assert sources[0]["document_source_id"] == before["document_source_id"]
        assert sources[0]["source_hash"] != before["source_hash"]
        assert "Corrected source-book note" in treatment["notes"]


def test_migration_enriches_terminal_duplicate_without_mutating_transaction(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        transaction = db.connection.execute(
            """
            SELECT t.*
            FROM transactions t
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()
        db.connection.execute(
            "UPDATE transactions SET lifecycle_status = 'duplicate' WHERE transaction_id = ?",
            (transaction["transaction_id"],),
        )
        db.connection.execute(
            """
            UPDATE tax_treatments
            SET aeat_invoice_type = NULL, aeat_operation_key = NULL,
                aeat_reverse_charge = NULL, aeat_expense_concept = NULL,
                source_hash = 'legacy-treatment-hash'
            WHERE transaction_id = ?
            """,
            (transaction["transaction_id"],),
        )
        db.connection.commit()

        migrate_xolo_history(db, source_csv, reconciliation_csv)
        stored = db.connection.execute(
            """
            SELECT t.lifecycle_status, t.source_hash, tt.aeat_invoice_type,
                   tt.aeat_operation_key, tt.aeat_reverse_charge,
                   tt.aeat_expense_concept
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.transaction_id = ?
            """,
            (transaction["transaction_id"],),
        ).fetchone()

        assert stored["lifecycle_status"] == "duplicate"
        assert stored["source_hash"] == transaction["source_hash"]
        assert stored["aeat_invoice_type"] == "F1"
        assert stored["aeat_operation_key"] == "01"
        assert stored["aeat_reverse_charge"] == 0
        assert stored["aeat_expense_concept"] == "G03"


def test_migration_backfills_posted_row_without_regressing_lifecycle(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        transaction = db.connection.execute(
            """
            SELECT t.*
            FROM transactions t
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = 'INV-2026-001'
            """
        ).fetchone()
        posted = db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="posted",
            expected_row_version=transaction["row_version"],
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="unknown",
            notes="Unrelated secondary treatment",
        )
        db.connection.execute(
            """
            UPDATE tax_treatments
            SET aeat_invoice_type = NULL, aeat_operation_key = NULL,
                source_hash = 'legacy-income-treatment-hash'
            WHERE transaction_id = ? AND treatment_type = 'income'
            """,
            (transaction["transaction_id"],),
        )
        db.connection.commit()

        migrate_xolo_history(db, source_csv, reconciliation_csv)
        stored = db.connection.execute(
            """
            SELECT t.lifecycle_status, tt.aeat_invoice_type, tt.aeat_operation_key
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.transaction_id = ? AND tt.treatment_type = 'income'
            """,
            (transaction["transaction_id"],),
        ).fetchone()

        assert posted["lifecycle_status"] == "posted"
        assert stored["lifecycle_status"] == "posted"
        assert stored["aeat_invoice_type"] == "F1"
        assert stored["aeat_operation_key"] == "01"

        current = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (transaction["transaction_id"],),
        ).fetchone()
        db.transition_transaction(
            transaction["transaction_id"],
            lifecycle_status="included_in_snapshot",
            expected_row_version=current["row_version"],
        )
        db.connection.execute(
            """
            UPDATE tax_treatments
            SET aeat_invoice_type = NULL, aeat_operation_key = NULL,
                source_hash = 'legacy-included-treatment-hash'
            WHERE transaction_id = ? AND treatment_type = 'income'
            """,
            (transaction["transaction_id"],),
        )
        db.connection.commit()
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        included = db.connection.execute(
            """
            SELECT t.lifecycle_status, tt.aeat_invoice_type, tt.aeat_operation_key
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.transaction_id = ? AND tt.treatment_type = 'income'
            """,
            (transaction["transaction_id"],),
        ).fetchone()
        assert included["lifecycle_status"] == "included_in_snapshot"
        assert included["aeat_invoice_type"] == "F1"
        assert included["aeat_operation_key"] == "01"


def test_late_booked_document_uses_booking_date_inside_source_book_period(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1]["date"] = "31/03/2026"
    rows[1]["booking_date"] = "01/04/2026"
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        transaction = db.connection.execute(
            """
            SELECT t.transaction_date, tt.notes
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()

        assert transaction["transaction_date"] == "2026-04-01"
        assert "document_period=2026-Q1" in transaction["notes"]
        assert "tax_period=2026-Q2" in transaction["notes"]
        assert "booking_date=2026-04-01" in transaction["notes"]


def test_migrate_rejects_vat_marked_income_without_explicit_base_before_ledger_writes(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[0]["vat_treatment"] = "intra_community_service"
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        with pytest.raises(ValueError, match="explicit taxable base"):
            migrate_xolo_history(db, source_csv, reconciliation_csv)

        assert db.table_counts()["transactions"] == 0
        assert db.table_counts()["documents"] == 0
        assert db.table_counts()["counterparties"] == 0


def test_migrate_xolo_history_blocks_q2_close_with_unresolved_reconciliation_issues(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        with pytest.raises(BlockingIssueError, match="amount_mismatch"):
            db.close_period("2026-Q2", expected_row_version=1)

        issue_codes = {row["issue_code"] for row in db.list_issues(period_key="2026-Q2")}
        assert issue_codes == {
            "amount_mismatch",
            "deductibility_pending_confirmation",
            "missing_locally",
        }


def test_corrected_reconciliation_resolves_obsolete_blocking_issue(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        rows = _load_csv(reconciliation_csv)
        rows[0]["status"] = "matched_local"
        _write_csv(reconciliation_csv, RECONCILIATION_FIELDS, rows)

        result = migrate_xolo_history(db, source_csv, reconciliation_csv)
        open_codes = {row["issue_code"] for row in db.list_issues(period_key="2026-Q2")}
        resolved = db.connection.execute(
            """
            SELECT issue_status, resolution_reason
            FROM validation_issues
            WHERE issue_code = 'amount_mismatch'
            """
        ).fetchone()

        assert result["counts"]["resolved_reconciliation_issues"] == 1
        assert "amount_mismatch" not in open_codes
        assert resolved["issue_status"] == "resolved"
        assert "No longer unresolved" in resolved["resolution_reason"]


def test_reconciliation_status_change_updates_one_issue_identity(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        rows = _load_csv(reconciliation_csv)
        rows[0]["status"] = "pending_confirmation"
        rows[0]["notes"] = "Classification was refined during review"
        _write_csv(reconciliation_csv, RECONCILIATION_FIELDS, rows)

        migrate_xolo_history(db, source_csv, reconciliation_csv)
        subject_rows = db.connection.execute(
            """
            SELECT issue_code, issue_status, message
            FROM validation_issues
            WHERE subject_table = 'xolo_expense_reconcile'
              AND subject_id = (
                  SELECT subject_id
                  FROM validation_issues
                  WHERE issue_code = 'pending_confirmation'
                  LIMIT 1
              )
            """
        ).fetchall()

        assert len(subject_rows) == 1
        assert subject_rows[0]["issue_code"] == "pending_confirmation"
        assert subject_rows[0]["issue_status"] == "open"
        assert "Classification was refined" in subject_rows[0]["message"]


def test_identical_reconciliation_import_preserves_manual_resolution(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        issue = db.connection.execute(
            "SELECT * FROM validation_issues WHERE issue_code = 'amount_mismatch'"
        ).fetchone()
        resolved = db.resolve_issue(
            issue["validation_issue_id"],
            reason="Reviewed and accepted with supporting evidence",
            expected_row_version=issue["row_version"],
        )

        migrate_xolo_history(db, source_csv, reconciliation_csv)
        stored = db.connection.execute(
            "SELECT * FROM validation_issues WHERE validation_issue_id = ?",
            (issue["validation_issue_id"],),
        ).fetchone()

        assert stored["issue_status"] == "resolved"
        assert stored["resolution_reason"] == resolved["resolution_reason"]
        assert stored["resolved_at"] == resolved["resolved_at"]


def test_migration_preserves_official_vat_classification_and_document_total(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    expense = rows[1]
    expense.update(
        {
            "counterparty_country_code": "US",
            "counterparty_tax_id": "12-3456789",
            "operation_key": "13",
            "reverse_charge": "S",
            "invoice_total_eur": "20.73",
            "taxable_base_eur": "17.14",
            "vat_rate_percent": "0%",
            "vat_eur": "0",
            "deductible_vat_eur": "3.60",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        imported = db.connection.execute(
            """
            SELECT c.country_code, c.tax_id, d.total_minor,
                   tt.tax_code, tt.taxable_base_minor, tt.vat_minor,
                   tt.deductible_vat_minor, tt.include_modelo303,
                   tt.aeat_invoice_type, tt.aeat_operation_key,
                   tt.aeat_reverse_charge, tt.aeat_expense_concept,
                   tt.rate_basis_points, tt.notes
            FROM transactions t
            JOIN counterparties c ON c.counterparty_id = t.counterparty_id
            JOIN documents d ON d.document_id = t.document_id
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()

        assert imported["country_code"] == "US"
        assert imported["tax_id"] == "12-3456789"
        assert imported["total_minor"] == 2073
        assert imported["tax_code"] == "non_eu_service_expense"
        assert imported["taxable_base_minor"] == 1714
        assert imported["vat_minor"] == 360
        assert imported["deductible_vat_minor"] == 360
        assert imported["include_modelo303"] == 1
        assert imported["aeat_invoice_type"] == "F1"
        assert imported["aeat_operation_key"] == "01"
        assert imported["aeat_reverse_charge"] == 1
        assert imported["aeat_expense_concept"] == "G03"
        assert imported["rate_basis_points"] == 2100
        assert "source_operation_key=13" in imported["notes"]
        assert "normalized_operation_key=01" in imported["notes"]


def test_enriching_existing_source_row_reuses_counterparty_and_document(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        before = db.table_counts()
        transaction_before = db.connection.execute(
            """
            SELECT t.transaction_id, t.counterparty_id, t.document_id
            FROM transactions t
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()

        rows = _load_csv(source_csv)
        rows[1].update(
            {
                "supplier": "Cursor Software LLC",
                "counterparty_country_code": "US",
                "counterparty_tax_id": "12-3456789",
                "operation_key": "13",
                "reverse_charge": "S",
                "invoice_total_eur": "20.73",
                "taxable_base_eur": "17.14",
                "vat_rate_percent": "0%",
                "vat_eur": "0",
                "deductible_vat_eur": "3.60",
            }
        )
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        after = db.table_counts()
        transaction_after = db.connection.execute(
            """
            SELECT t.transaction_id, t.counterparty_id, t.document_id, c.country_code, c.tax_id
            FROM transactions t
            JOIN documents d ON d.document_id = t.document_id
            JOIN counterparties c ON c.counterparty_id = t.counterparty_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()

        assert after["counterparties"] == before["counterparties"]
        assert after["documents"] == before["documents"]
        assert transaction_after["transaction_id"] == transaction_before["transaction_id"]
        assert transaction_after["country_code"] == "US"
        assert transaction_after["tax_id"] == "12-3456789"


def test_prune_selects_latest_document_when_duplicate_line_has_no_live_references(
    tmp_path: Path,
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        _add_duplicate_source_documents(db, "2026-Q2")

        counts = _prune_superseded_source_book_records(db)

        assert counts["pruned_document_sources"] == 1
        assert counts["pruned_documents"] == 1
        assert db.table_counts()["documents"] == 1
        assert db.table_counts()["document_sources"] == 1


def test_prune_does_not_mutate_superseded_document_in_closed_period(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        _add_duplicate_source_documents(db, "2026-Q2")
        db.connection.execute(
            "UPDATE periods SET status = 'closed' WHERE period_key = '2026-Q2'"
        )
        db.connection.commit()

        counts = _prune_superseded_source_book_records(db)

        assert counts["prune_skipped_immutable"] == 1
        assert db.table_counts()["documents"] == 2
        assert db.table_counts()["document_sources"] == 2


def _treatment_vat_for_document(db: LedgerDB, document_number: str):
    return db.connection.execute(
        """
        SELECT tt.vat_minor, tt.deductible_vat_minor, tt.tax_code, tt.aeat_operation_key
        FROM tax_treatments tt
        JOIN transactions t ON t.transaction_id = tt.transaction_id
        JOIN documents d ON d.document_id = t.document_id
        WHERE d.document_number = ?
        """,
        (document_number,),
    ).fetchone()


def test_clave_09_expense_without_reverse_charge_flag_self_assesses_output_vat(
    tmp_path: Path,
) -> None:
    # Xolo exports mark intra-EU service purchases with operation key 09 and
    # leave the reverse-charge column blank, so the output VAT has to be
    # inferred from the deductible VAT for the Modelo 303 chain to close.
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1].update(
        {
            "counterparty_country_code": "IE",
            "counterparty_tax_id": "TEST-TAX-ID-004",
            "counterparty_vat_id": "IETEST-TAX-ID-004",
            "operation_key": "09",
            "reverse_charge": "",
            "gross_eur": "17,14",
            "invoice_total_eur": "17.14",
            "taxable_base_eur": "17.14",
            "vat_rate_percent": "21%",
            "vat_eur": "",
            "deductible_vat_eur": "3.60",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        treatment = _treatment_vat_for_document(db, "204355514")

    assert treatment["aeat_operation_key"] == "09"
    assert treatment["deductible_vat_minor"] == 360
    assert treatment["vat_minor"] == 360


def test_domestic_expense_without_reverse_charge_flag_keeps_zero_output_vat(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1].update(
        {
            "operation_key": "01",
            "reverse_charge": "",
            "gross_eur": "17,14",
            "invoice_total_eur": "17.14",
            "taxable_base_eur": "17.14",
            "vat_eur": "",
            "deductible_vat_eur": "3.60",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        treatment = _treatment_vat_for_document(db, "204355514")

    assert treatment["deductible_vat_minor"] == 360
    assert treatment["vat_minor"] == 0


def test_intra_community_source_book_row_makes_modelo349_due(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    rows[1].update(
        {
            "counterparty_country_code": "IE",
            "counterparty_tax_id": "TEST-TAX-ID-004",
            "counterparty_vat_id": "IETEST-TAX-ID-004",
            "operation_key": "09",
            "reverse_charge": "S",
            "invoice_total_eur": "20.73",
            "taxable_base_eur": "17.14",
            "vat_rate_percent": "21%",
            "vat_eur": "3.60",
            "deductible_vat_eur": "3.60",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        obligation = db.connection.execute(
            """
            SELECT o.determination, o.filing_status, o.blocking
            FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = '2026-Q2' AND o.obligation_code = '349'
            """
        ).fetchone()
        treatment = db.connection.execute(
            """
            SELECT tt.tax_code, tt.aeat_operation_key, tt.aeat_reverse_charge,
                   tt.rate_basis_points
            FROM tax_treatments tt
            JOIN transactions t ON t.transaction_id = tt.transaction_id
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = '204355514'
            """
        ).fetchone()

        assert treatment["tax_code"] == "eu_service_expense"
        assert treatment["aeat_operation_key"] == "09"
        assert treatment["aeat_reverse_charge"] == 0
        assert treatment["rate_basis_points"] == 2100
        assert obligation["determination"] == "due"
        assert obligation["filing_status"] == "due"
        assert obligation["blocking"] == 1


def test_official_source_book_supersedes_pre_book_reconciliation_issues(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        source_rows = _load_csv(source_csv)
        source_rows[1]["date"] = "01/05/2026"
        source_rows.append(
            {
                **{field: "" for field in SOURCE_BOOK_FIELDS},
                "source_book_type": "gastos_book",
                "source_scope": "2026",
                "source_file": str(tmp_path / "evidence" / "Libros_contables_2026.xlsx"),
                "source_file_format": "xlsx:gastos:r6-r7",
                "source_row_number": "10",
                "source_book_line_id": "official-tgss-line",
                "period": "2026-Q2",
                "date": "10/06/2026",
                "booking_date": "10/06/2026",
                "supplier": "Synthetic Party 011",
                "document_number": "SYNTH-DOCUMENT-018",
                "gross_eur": "104.33",
                "deductible_base_eur": "0",
                "irpf_deductible_eur": "86.94",
                "reason_code": "G45",
                "import_status": "imported",
            }
        )
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, source_rows)

        result = migrate_xolo_history(db, source_csv, reconciliation_csv)
        open_codes = {row["issue_code"] for row in db.list_issues(period_key="2026-Q2")}
        resolved_rows = db.connection.execute(
            """
            SELECT issue_code, issue_status, resolution_reason
            FROM validation_issues
            WHERE issue_code IN ('amount_mismatch', 'deductibility_pending_confirmation')
            ORDER BY issue_code
            """
        ).fetchall()
        tgss_treatment = db.connection.execute(
            """
            SELECT tt.aeat_invoice_type, tt.aeat_expense_concept,
                   tt.aeat_operation_key, tt.aeat_reverse_charge
            FROM tax_treatments tt
            JOIN transactions t ON t.transaction_id = tt.transaction_id
            JOIN documents d ON d.document_id = t.document_id
            WHERE d.document_number = 'SYNTH-DOCUMENT-018'
            """
        ).fetchone()

        assert result["counts"]["reconciliation_rows_superseded_by_source_book"] == 2
        assert result["counts"]["resolved_reconciliation_issues"] == 2
        assert open_codes == {"missing_locally"}
        assert {row["issue_status"] for row in resolved_rows} == {"resolved"}
        assert all("Official Xolo source-book row" in row["resolution_reason"] for row in resolved_rows)
        assert dict(tgss_treatment) == {
            "aeat_invoice_type": "F6",
            "aeat_expense_concept": "G45",
            "aeat_operation_key": "01",
            "aeat_reverse_charge": 0,
        }


def test_migrate_xolo_history_creates_annual_asset_evidence_without_extra_transaction(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        asset = db.connection.execute(
            """
            SELECT a.asset_code, a.document_id, a.acquisition_transaction_id, a.cost_minor,
                   a.amortizable_base_minor, a.annual_rate_basis_points, ae.amount_minor, p.period_key,
                   ae.entry_kind, ae.tax_year, ae.source_book_line_id, ae.include_in_books
            FROM assets a
            JOIN amortization_entries ae ON ae.asset_id = a.asset_id
            JOIN periods p ON p.period_id = ae.period_id
            WHERE ae.entry_kind = 'annual_evidence'
            """
        ).fetchone()

        assert asset["period_key"] == "2026"
        assert asset["amount_minor"] == 31312
        assert asset["cost_minor"] == 164793
        assert asset["amortizable_base_minor"] == 164793
        assert asset["annual_rate_basis_points"] == 2600
        assert asset["document_id"]
        assert asset["acquisition_transaction_id"] is None
        assert asset["entry_kind"] == "annual_evidence"
        assert asset["tax_year"] == 2026
        assert asset["source_book_line_id"] == "asset-line-1"
        assert asset["include_in_books"] == 0
        assert db.table_counts()["transactions"] == 2
        schedule = db.validate_asset_year(2026)
        assert schedule["ready"] is True
        assert schedule["assets"][0]["annual_minor"] == 31312
        assert schedule["assets"][0]["quarter_minor"] == 31312
        assert schedule["assets"][0]["quarter_rows"] == 3


def test_migrate_links_asset_to_unique_direct_acquisition_transaction(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    expense = rows[1]
    expense.update(
        {
            "date": "08/04/2026",
            "booking_date": "08/04/2026",
            "supplier": "Synthetic Party 016",
            "document_number": "SYNTH-DOCUMENT-031",
            "counterparty_country_code": "ES",
            "original_amount": "1994.00",
            "original_currency": "EUR",
            "gross_eur": "1994.00",
            "invoice_total_eur": "1994.00",
            "taxable_base_eur": "1647.93",
            "vat_rate_percent": "21%",
            "vat_eur": "346.07",
            "deductible_vat_eur": "346.07",
            "deductible_base_eur": "1647.93",
            "irpf_deductible_eur": "0.00",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        asset = db.connection.execute(
            """
            SELECT a.acquisition_transaction_id, a.cost_minor, a.amortizable_base_minor,
                   a.business_use_ratio, tt.tax_code, d.document_number
            FROM assets a
            JOIN transactions t ON t.transaction_id = a.acquisition_transaction_id
            JOIN documents d ON d.document_id = t.document_id
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            """
        ).fetchone()

    assert asset["acquisition_transaction_id"]
    assert asset["document_number"] == "SYNTH-DOCUMENT-031"
    assert asset["tax_code"] == "domestic_input"
    assert asset["cost_minor"] == 199400
    assert asset["amortizable_base_minor"] == 164793
    assert asset["business_use_ratio"] == pytest.approx(1.0)


def test_migrate_preserves_pre_business_use_asset_base_without_double_reduction(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    rows = _load_csv(source_csv)
    expense = rows[1]
    expense.update(
        {
            "date": "07/05/2024",
            "booking_date": "07/05/2024",
            "supplier": "Synthetic Party 016",
            "document_number": "AIRPODS-2024",
            "original_amount": "642.95",
            "original_currency": "EUR",
            "gross_eur": "642.95",
            "deductible_base_eur": "531.36",
            "irpf_deductible_eur": "0.00",
        }
    )
    asset = rows[2]
    asset.update(
        {
            "date": "07/05/2024",
            "supplier": "Synthetic Party 016",
            "asset_id": "AirPods Max",
            "amortizable_base_eur": "478.51",
            "amortization_amount_eur": "81.00",
        }
    )
    _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        with db.connection:
            db.connection.execute(
                """
                UPDATE assets
                SET cost_minor = 47851,
                    amortizable_base_minor = 47851,
                    advisor_decision = 'business use confirmed'
                """
            )
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        imported = db.connection.execute(
            """
            SELECT cost_minor, amortizable_base_minor, business_use_ratio,
                   annual_rate_basis_points, acquisition_transaction_id,
                   advisor_decision
            FROM assets
            """
        ).fetchone()

    assert imported["cost_minor"] == 64295
    assert imported["amortizable_base_minor"] == 53136
    assert imported["business_use_ratio"] == pytest.approx(47851 / 53136)
    assert round(imported["amortizable_base_minor"] * imported["business_use_ratio"]) == 47851
    assert imported["annual_rate_basis_points"] == 2600
    assert imported["acquisition_transaction_id"]
    assert imported["advisor_decision"] == "business use confirmed"


def test_migrate_updates_corrected_annual_asset_row_without_changing_identity(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(db, source_csv, reconciliation_csv)
        before = db.connection.execute(
            "SELECT asset_id, asset_code FROM assets"
        ).fetchone()

        rows = _load_csv(source_csv)
        rows[2]["amortizable_base_eur"] = "1700.00"
        rows[2]["amortization_amount_eur"] = "320.00"
        rows[2]["notes"] = "Corrected annual asset evidence"
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        assets = db.connection.execute(
            "SELECT asset_id, asset_code, amortizable_base_minor FROM assets"
        ).fetchall()
        annual = db.connection.execute(
            """
            SELECT ae.amount_minor
            FROM amortization_entries ae
            WHERE ae.entry_kind = 'annual_evidence'
            """
        ).fetchall()
        schedule = db.validate_asset_year(2026)

        assert len(assets) == 1
        assert assets[0]["asset_id"] == before["asset_id"]
        assert assets[0]["asset_code"] == before["asset_code"]
        assert assets[0]["amortizable_base_minor"] == 170000
        assert [row["amount_minor"] for row in annual] == [32000]
        assert schedule["ready"] is True
        assert schedule["assets"][0]["annual_minor"] == 32000
        assert schedule["assets"][0]["quarter_minor"] == 32000

        rows[2]["date"] = "01/07/2026"
        rows[2]["notes"] = "Corrected placed-in-service date"
        _write_csv(source_csv, SOURCE_BOOK_FIELDS, rows)
        migrate_xolo_history(db, source_csv, reconciliation_csv)

        quarter_rows = db.connection.execute(
            """
            SELECT p.period_key, ae.amount_minor
            FROM amortization_entries ae
            JOIN periods p ON p.period_id = ae.period_id
            WHERE ae.entry_kind = 'quarter_schedule'
            ORDER BY p.period_key
            """
        ).fetchall()
        corrected_schedule = db.validate_asset_year(2026)

        assert [row["period_key"] for row in quarter_rows] == ["2026-Q3", "2026-Q4"]
        assert sum(row["amount_minor"] for row in quarter_rows) == 32000
        assert corrected_schedule["ready"] is True
        assert corrected_schedule["assets"][0]["quarter_rows"] == 2


def test_migrate_preserves_submitted_xolo_values_as_immutable_baseline(tmp_path: Path) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    calculations_csv = tmp_path / "runs" / "xolo_modelo130_calculations.csv"
    fields = [
        "year",
        "quarter",
        "period",
        "report_id",
        "file_id",
        "filename",
        "submitted_date",
        "xolo_status",
        "amount_due",
        "total_compounded_sales_ytd",
        "total_compounded_deductible_expenses_ytd",
        "net_results_ytd",
        "net_results_20_percent",
        "previous_quarters_compensation",
        "withholding_taxes",
        "article_110_3_reduction",
        "payable_irpf_for_quarter",
    ]
    _write_csv(
        calculations_csv,
        fields,
        [
            {
                "year": "2026",
                "quarter": "2",
                "period": "2026-Q2",
                "report_id": "18877",
                "file_id": "18135895",
                "filename": "MOD 130 2T 2026.pdf",
                "submitted_date": "08 Jul 2026",
                "xolo_status": "submitted",
                "amount_due": "2639.12",
                "total_compounded_sales_ytd": "36770.89",
                "total_compounded_deductible_expenses_ytd": "10280.23",
                "net_results_ytd": "26490.66",
                "net_results_20_percent": "5298.13",
                "previous_quarters_compensation": "2659.01",
                "withholding_taxes": "0.00",
                "article_110_3_reduction": "",
                "payable_irpf_for_quarter": "2639.12",
            },
            {
                "year": "2026",
                "quarter": "3",
                "period": "2026-Q3",
                "xolo_status": "forecast_or_unsubmitted",
            },
        ],
    )

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        result = migrate_xolo_history(db, source_csv, reconciliation_csv, calculations_csv)
        snapshot = db.connection.execute("SELECT * FROM filing_snapshots").fetchone()
        payload = json.loads(snapshot["payload_json"])

        assert result["counts"]["filing_snapshots"] == 1
        assert result["counts"]["ignored_calculation_rows"] == 1
        assert snapshot["status"] == "baseline"
        assert snapshot["filed_on"] == "2026-07-08"
        assert payload["filed_values"]["01"] == "36770.89"
        assert payload["filed_values"]["02"] == "10280.23"
        assert payload["filed_values"]["05"] == "2659.01"
        assert payload["filed_values"]["19"] == "2639.12"


def test_migrate_imports_inventory_obligations_and_documented_history_adjustments(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    annual_reconciliation = tmp_path / "runs" / "xolo_source_book_reconciliation.csv"
    _write_csv(
        annual_reconciliation,
        ["period", "status", "annual_adjustment_delta", "notes"],
        [
            {
                "period": "2026-Q2",
                "status": "rows_match_target_after_annual_adjustment_no_tieout",
                "annual_adjustment_delta": "10.00",
                "notes": "Modelo 100 comparison evidence",
            }
        ],
    )
    inventory = tmp_path / "runs" / "document_index.csv"
    _write_csv(
        inventory,
        [
            "category",
            "original_name",
            "drive_relative_path",
            "local_source_path",
            "sha256",
        ],
        [
            {
                "category": "tax_report",
                "original_name": "MOD 303 2T 2026 Example Taxpayer.pdf",
                "drive_relative_path": "Xolo export/TAX_REPORT/MOD 303 2T 2026.pdf",
                "local_source_path": "",
                "sha256": "a" * 64,
            },
            {
                "category": "tax_report",
                "original_name": "DRAFT_MOD 100 0A 2026 Example Taxpayer.pdf",
                "drive_relative_path": "Xolo export/TAX_REPORT/DRAFT_MOD 100 0A 2026.pdf",
                "local_source_path": "",
                "sha256": "b" * 64,
            },
        ],
    )

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        result = migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            modelo130_reconciliation_csv=annual_reconciliation,
            document_inventory_csv=inventory,
        )
        adjustment = db.connection.execute(
            """
            SELECT t.entry_type, t.amount_minor, tt.deductible_irpf_minor, tt.tax_code
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.entry_type = 'verify_history_adjustment'
            """
        ).fetchone()
        filed_303 = db.connection.execute(
            """
            SELECT o.* FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = '2026-Q2' AND o.obligation_code = '303'
            """
        ).fetchone()
        annual_100 = db.connection.execute(
            """
            SELECT o.* FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = '2026' AND o.obligation_code = '100'
            """
        ).fetchone()

        assert result["counts"]["historical_adjustments"] == 1
        assert result["counts"]["filed_obligations_from_inventory"] == 1
        assert adjustment["amount_minor"] == -1000
        assert adjustment["deductible_irpf_minor"] == -1000
        assert adjustment["tax_code"] == "verify_history_annual_close_adjustment"
        assert filed_303["determination"] == "due"
        assert filed_303["filing_status"] == "filed"
        assert filed_303["blocking"] == 0
        assert annual_100["determination"] == "unknown"
        assert annual_100["filing_status"] == "unknown"
        assert annual_100["blocking"] == 1


def test_migrate_enriches_filed_303_snapshot_once_when_extraction_schema_advances(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    local_pdf = tmp_path / "MOD 303 2T 2026 Example Taxpayer.pdf"
    local_pdf.write_bytes(b"fixture bytes; parser is replaced in this test")
    inventory = tmp_path / "document_index.csv"
    source_hash = "c" * 64
    _write_csv(
        inventory,
        ["category", "original_name", "drive_relative_path", "local_source_path", "sha256"],
        [
            {
                "category": "tax_report",
                "original_name": local_pdf.name,
                "drive_relative_path": f"Xolo export/TAX_REPORT/{local_pdf.name}",
                "local_source_path": str(local_pdf),
                "sha256": source_hash,
            }
        ],
    )
    payload = {
        "form": "303",
        "period": "2026-Q2",
        "filed_values": {"result": "-10.00"},
    }

    def fake_extract(_path: Path) -> FilingEvidence:
        return FilingEvidence(
            form_code="303",
            period_key="2026-Q2",
            filed_on="2026-07-08T12:49:25",
            submission_reference="fixture-reference",
            justificante_number="3037000000000",
            verification_code="FIXTURECSV",
            source_sha256=source_hash.upper(),
            source_reference=str(local_pdf),
            payload=dict(payload),
        )

    monkeypatch.setattr("autonomo_taxes.history_migration.extract_filing_evidence", fake_extract)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            document_inventory_csv=inventory,
        )
        assert db.connection.execute("SELECT COUNT(*) FROM filing_snapshots").fetchone()[0] == 1
        db.connection.execute(
            "UPDATE documents SET lifecycle_status = 'posted' WHERE lifecycle_status = 'approved'"
        )
        db.connection.execute(
            """
            UPDATE documents
            SET document_type = 'expense_invoice'
            WHERE document_id = (
                SELECT document_id FROM documents
                WHERE external_key LIKE '%|gastos_book|%'
                LIMIT 1
            )
            """
        )
        db.connection.execute(
            "UPDATE transactions SET lifecycle_status = 'posted' WHERE lifecycle_status = 'approved'"
        )
        db.connection.commit()

        payload.update(
            {
                "filed_values": {
                    "result": "-10.00",
                    "87": "50.00",
                    "72": "10.00",
                    "compensation_carryforward": "60.00",
                },
                "value_extraction_schema": "modelo303_v2",
            }
        )
        migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            document_inventory_csv=inventory,
        )
        migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            document_inventory_csv=inventory,
        )

        snapshots = db.connection.execute(
            "SELECT payload_json FROM filing_snapshots ORDER BY created_at"
        ).fetchall()
        assert len(snapshots) == 2
        assert json.loads(snapshots[-1]["payload_json"])["value_extraction_schema"] == "modelo303_v2"
        assert {
            row["lifecycle_status"]
            for row in db.connection.execute("SELECT lifecycle_status FROM documents")
        } == {"posted"}
        assert {
            row["lifecycle_status"]
            for row in db.connection.execute("SELECT lifecycle_status FROM transactions")
        } == {"posted"}


def test_refresh_filing_inventory_adds_modelo390_schema_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    local_pdf = tmp_path / "MOD 390 2025 Example Taxpayer.pdf"
    local_pdf.write_bytes(b"fixture bytes; parser is replaced in this test")
    inventory = tmp_path / "document_index.csv"
    source_hash = "d" * 64
    _write_csv(
        inventory,
        ["category", "original_name", "drive_relative_path", "local_source_path", "sha256"],
        [
            {
                "category": "tax_report",
                "original_name": local_pdf.name,
                "drive_relative_path": f"Xolo export/TAX_REPORT/{local_pdf.name}",
                "local_source_path": str(local_pdf),
                "sha256": source_hash,
            }
        ],
    )

    def fake_extract(_path: Path) -> FilingEvidence:
        return FilingEvidence(
            form_code="390",
            period_key="2025",
            filed_on="2026-01-30T12:00:00",
            submission_reference="fixture-reference",
            justificante_number="3907000000000",
            verification_code="FIXTURE390CSV",
            source_sha256=source_hash.upper(),
            source_reference=str(local_pdf),
            payload={
                "form": "390",
                "period": "2025",
                "filed_values": {"86": "-922.27", "523": "6096.19"},
                "value_extraction_schema": "modelo390_v2",
            },
        )

    monkeypatch.setattr("autonomo_taxes.history_migration.extract_filing_evidence", fake_extract)
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        first = refresh_filing_inventory(db, inventory)
        second = refresh_filing_inventory(db, inventory)
        snapshots = db.connection.execute(
            "SELECT payload_json FROM filing_snapshots"
        ).fetchall()

        assert first["new_snapshots"] == 1
        assert second["new_snapshots"] == 0
        assert len(snapshots) == 1
        assert json.loads(snapshots[0]["payload_json"])["value_extraction_schema"] == (
            "modelo390_v2"
        )
        assert db.connection.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_migrate_updates_corrected_history_adjustment_without_duplicate(
    tmp_path: Path,
) -> None:
    source_csv, reconciliation_csv = _write_fixture_csvs(tmp_path)
    annual_reconciliation = tmp_path / "runs" / "xolo_source_book_reconciliation.csv"
    fields = ["period", "status", "annual_adjustment_delta", "notes"]
    rows = [
        {
            "period": "2026-Q2",
            "status": "rows_match_target_after_annual_adjustment_no_tieout",
            "annual_adjustment_delta": "10.00",
            "notes": "Initial comparison evidence",
        }
    ]
    _write_csv(annual_reconciliation, fields, rows)

    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            modelo130_reconciliation_csv=annual_reconciliation,
        )
        before = db.connection.execute(
            "SELECT transaction_id FROM transactions WHERE entry_type = 'verify_history_adjustment'"
        ).fetchone()

        rows[0]["annual_adjustment_delta"] = "12.00"
        rows[0]["notes"] = "Corrected comparison evidence"
        _write_csv(annual_reconciliation, fields, rows)
        migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            modelo130_reconciliation_csv=annual_reconciliation,
        )

        adjustments = db.connection.execute(
            """
            SELECT t.transaction_id, t.amount_minor, tt.deductible_irpf_minor, tt.notes
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.entry_type = 'verify_history_adjustment'
            """
        ).fetchall()

        assert len(adjustments) == 1
        assert adjustments[0]["transaction_id"] == before["transaction_id"]
        assert adjustments[0]["amount_minor"] == -1200
        assert adjustments[0]["deductible_irpf_minor"] == -1200
        assert "Corrected comparison evidence" in adjustments[0]["notes"]

        rows[0]["annual_adjustment_delta"] = "0.00"
        rows[0]["notes"] = "Comparison no longer needs an adjustment"
        _write_csv(annual_reconciliation, fields, rows)
        migrate_xolo_history(
            db,
            source_csv,
            reconciliation_csv,
            modelo130_reconciliation_csv=annual_reconciliation,
        )
        cleared = db.connection.execute(
            """
            SELECT t.transaction_id, t.amount_minor, tt.deductible_irpf_minor
            FROM transactions t
            JOIN tax_treatments tt ON tt.transaction_id = t.transaction_id
            WHERE t.entry_type = 'verify_history_adjustment'
            """
        ).fetchall()

        assert len(cleared) == 1
        assert cleared[0]["transaction_id"] == before["transaction_id"]
        assert cleared[0]["amount_minor"] == 0
        assert cleared[0]["deductible_irpf_minor"] == 0


def _transaction_identity(db: LedgerDB) -> dict[str, str]:
    rows = db.connection.execute(
        "SELECT external_key, transaction_id FROM transactions ORDER BY external_key"
    ).fetchall()
    return {row["external_key"]: row["transaction_id"] for row in rows}


def _add_duplicate_source_documents(db: LedgerDB, period_key: str) -> None:
    for index in (1, 2):
        document = db.upsert_document(
            external_key=f"duplicate-document-{index}",
            document_type="gastos_book",
            document_number=f"DUP-{index}",
            issued_on="2026-04-01",
            period_key=period_key,
            lifecycle_status="approved",
            source_hash=f"duplicate-document-hash-{index}",
        )
        db.add_document_source(
            document_id=document["document_id"],
            source_book_line_id="duplicate-source-line",
            source_hash=f"duplicate-source-row-hash-{index}",
        )


def _row_versions(db: LedgerDB) -> dict[str, list[tuple[str, int]]]:
    keys = {
        "counterparties": "counterparty_id",
        "documents": "document_id",
        "transactions": "transaction_id",
        "tax_treatments": "treatment_id",
        "assets": "asset_id",
        "amortization_entries": "amortization_entry_id",
        "validation_issues": "validation_issue_id",
    }
    return {
        table: [
            (row[0], row[1])
            for row in db.connection.execute(
                f"SELECT {key}, row_version FROM {table} ORDER BY {key}"
            ).fetchall()
        ]
        for table, key in keys.items()
    }


def _write_fixture_csvs(tmp_path: Path) -> tuple[Path, Path]:
    source_csv = tmp_path / "runs" / "xolo_source_book_rows.csv"
    reconciliation_csv = tmp_path / "runs" / "2026-Q2" / "xolo_expense_reconcile.csv"

    _write_csv(
        source_csv,
        SOURCE_BOOK_FIELDS,
        [
            {
                "source_book_type": "ingresos_book",
                "source_scope": "2026",
                "source_file": str(tmp_path / "evidence" / "income.xlsx"),
                "source_file_format": "xlsx:sheet1:r6-r7",
                "source_row_number": "2",
                "source_book_line_id": "income-line-1",
                "period": "2026-Q2",
                "date": "15/04/2026",
                "booking_date": "",
                "supplier": "Example Customer One",
                "document_number": "INV-2026-001",
                "original_amount": "",
                "original_currency": "",
                "gross_eur": "1500.00",
                "deductible_base_eur": "",
                "irpf_deductible_eur": "",
                "vat_treatment": "",
                "reason_code": "I01",
                "asset_id": "",
                "amortizable_base_eur": "",
                "amortization_period": "",
                "amortization_amount_eur": "",
                "rate_or_life": "",
                "casilla01_ytd": "",
                "casilla02_ytd": "",
                "casilla07": "",
                "casilla19": "",
                "import_status": "imported",
                "missing_required_groups": "",
                "notes": "",
            },
            {
                "source_book_type": "gastos_book",
                "source_scope": "2026",
                "source_file": str(tmp_path / "evidence" / "expense.xlsx"),
                "source_file_format": "xlsx:sheet2:r6-r7",
                "source_row_number": "3",
                "source_book_line_id": "expense-line-1",
                "period": "2026-Q2",
                "date": "21/04/2024",
                "booking_date": "22/04/2024",
                "supplier": "Cursor",
                "document_number": "204355514",
                "original_amount": "24,20",
                "original_currency": "USD",
                "gross_eur": "20,73",
                "deductible_base_eur": "17,14",
                "irpf_deductible_eur": "17,14",
                "vat_treatment": "",
                "reason_code": "G03",
                "asset_id": "",
                "amortizable_base_eur": "",
                "amortization_period": "",
                "amortization_amount_eur": "",
                "rate_or_life": "",
                "casilla01_ytd": "",
                "casilla02_ytd": "",
                "casilla07": "",
                "casilla19": "",
                "import_status": "imported",
                "missing_required_groups": "",
                "notes": "Historical expense import",
            },
            {
                "source_book_type": "bienes_inversion_book",
                "source_scope": "2026",
                "source_file": str(tmp_path / "evidence" / "assets.xlsx"),
                "source_file_format": "xlsx:sheet3:r6-r7",
                "source_row_number": "4",
                "source_book_line_id": "asset-line-1",
                "period": "",
                "date": "08/04/2026",
                "booking_date": "",
                "supplier": "Synthetic Party 016",
                "document_number": "",
                "original_amount": "",
                "original_currency": "",
                "gross_eur": "",
                "deductible_base_eur": "",
                "irpf_deductible_eur": "",
                "vat_treatment": "",
                "reason_code": "",
                "asset_id": "MacBook Pro 14",
                "amortizable_base_eur": "1647.93",
                "amortization_period": "0A",
                "amortization_amount_eur": "313.12",
                "rate_or_life": "26%",
                "casilla01_ytd": "",
                "casilla02_ytd": "",
                "casilla07": "",
                "casilla19": "",
                "import_status": "imported",
                "missing_required_groups": "",
                "notes": "Annual asset evidence only",
            },
        ],
    )

    _write_csv(
        reconciliation_csv,
        RECONCILIATION_FIELDS,
        [
            {
                "status": "amount_mismatch",
                "date": "2026-05-01",
                "recipient": "Cursor",
                "number": "204355514",
                "currency": "USD",
                "amount_original": "24,20",
                "gross_eur": "20,73",
                "irpf_deductible_eur": "20,73",
                "local_deductible_eur": "17,14",
                "deductible_diff_eur": "3,59",
                "local_document": "Invoice-204355514.pdf",
                "xolo_id": "2918422",
                "notes": "Difference still unresolved",
            },
            {
                "status": "deductibility_pending_confirmation",
                "date": "2026-06-10",
                "recipient": "Synthetic Party 011",
                "number": "SYNTH-DOCUMENT-018",
                "currency": "EUR",
                "amount_original": "104,33",
                "gross_eur": "104,33",
                "irpf_deductible_eur": "104,33",
                "local_deductible_eur": "",
                "deductible_diff_eur": "",
                "local_document": "03_Apremio_08-2024_104.33EUR.pdf",
                "xolo_id": "3099343",
                "notes": "Needs tax treatment confirmation",
            },
            {
                "status": "missing_locally",
                "date": "2026-06-30",
                "recipient": "Synthetic Party 002",
                "number": "SYNTH-DOCUMENT-030",
                "currency": "USD",
                "amount_original": "75,98",
                "gross_eur": "65,10",
                "irpf_deductible_eur": "65,10",
                "local_deductible_eur": "",
                "deductible_diff_eur": "",
                "local_document": "",
                "xolo_id": "3141770",
                "notes": "Missing in local archive",
            },
            {
                "status": "matched_local",
                "date": "2026-04-24",
                "recipient": "Synthetic Party 007",
                "number": "00012",
                "currency": "EUR",
                "amount_original": "18,00",
                "gross_eur": "18,00",
                "irpf_deductible_eur": "18,00",
                "local_deductible_eur": "18,00",
                "deductible_diff_eur": "0,00",
                "local_document": "Invoice-00012.pdf",
                "xolo_id": "2887712",
                "notes": "",
            },
        ],
    )

    return source_csv, reconciliation_csv


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))
