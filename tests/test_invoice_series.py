from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from autonomo_taxes.cli import main
from autonomo_taxes.invoice_series import InvoiceNumberObservation, invoice_series_status
from autonomo_taxes.ledger_db import initialize


def test_series_status_collapses_book_and_final_invoice_and_suggests_next() -> None:
    observations = _bare_sequence(1, 11)
    observations.extend(_final_invoice_representations())

    report = invoice_series_status(
        observations,
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=True,
    )

    assert report["status"] == "ok"
    assert report["in_series_suffixes"] == list(range(1, 12))
    assert report["gaps"] == []
    assert report["next_number_suggestion"] == "FACT-2026-00012"
    assert report["next_number_reserved"] is False
    assert [item["suffix"] for item in report["same_invoice_matches"]] == [11]
    assert report["duplicates"] == []


def test_series_status_reports_gap_but_still_uses_highest_number() -> None:
    observations = [item for item in _bare_sequence(1, 11) if item.number != "00007"]
    observations.extend(_final_invoice_representations())

    report = invoice_series_status(
        observations,
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=True,
    )

    assert report["status"] == "ok"
    assert report["gaps"] == [7]
    assert report["next_number_suggestion"] == "FACT-2026-00012"


def test_series_status_requires_explicit_binding_for_bare_history() -> None:
    report = invoice_series_status(
        [*_bare_sequence(1, 2), *_final_invoice_representations()],
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=False,
    )

    assert report["status"] == "blocked"
    assert "unresolved_bare" in report["blocking_reasons"]
    assert report["next_number_suggestion"] is None
    assert len(report["unresolved_bare"]) == 2


def test_series_status_fails_when_series_cannot_be_inferred() -> None:
    report = invoice_series_status(
        [
            _observation("FACT-2026-001", record_id="a"),
            _observation("RECT-2026-001", record_id="b"),
        ],
        year=2026,
        requested_series=None,
        bare_belongs_to_series=False,
    )

    assert report["status"] == "blocked"
    assert "ambiguous_series" in report["blocking_reasons"]
    assert report["target_series"] is None
    assert report["next_number_suggestion"] is None


def test_series_status_infers_the_only_observed_full_series() -> None:
    report = invoice_series_status(
        [_observation("FACT-2026-00011", record_id="issued-11")],
        year=2026,
        requested_series=None,
        bare_belongs_to_series=False,
    )

    assert report["status"] == "ok"
    assert report["target_series"] == "FACT-2026"
    assert report["series_source"] == "inferred"
    assert report["next_number_suggestion"] == "FACT-2026-00012"


def test_series_status_fails_on_mixed_suffix_widths() -> None:
    report = invoice_series_status(
        [
            _observation("FACT-2026-001", record_id="a"),
            _observation("FACT-2026-0002", record_id="b"),
        ],
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=False,
    )

    assert report["status"] == "blocked"
    assert "ambiguous_width" in report["blocking_reasons"]
    assert report["next_number_suggestion"] is None


def test_series_status_fails_on_distinct_full_invoices_with_same_suffix() -> None:
    report = invoice_series_status(
        [
            _observation("FACT-2026-00011", record_id="document-a"),
            _observation("FACT-2026-00011", record_id="document-b"),
        ],
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=False,
    )

    assert report["status"] == "blocked"
    assert "duplicate_conflict" in report["blocking_reasons"]
    assert report["duplicates"][0]["reasons"] == ["multiple_full_invoices"]


def test_series_status_fails_when_bare_and_full_identity_disagree() -> None:
    bare = _observation(
        "00011",
        record_id="book-11",
        issued_on="2026-07-01",
        amount_eur_minor=579443,
    )
    full = _observation(
        "FACT-2026-00011",
        record_id="full-11",
        issued_on="2026-07-02",
        amount_eur_minor=579443,
    )

    report = invoice_series_status(
        [bare, full],
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=True,
    )

    assert report["status"] == "blocked"
    assert report["duplicates"][0]["reasons"] == ["bare_full_identity_mismatch"]
    assert report["next_number_suggestion"] is None


def test_series_status_fails_on_unrecognized_income_number() -> None:
    report = invoice_series_status(
        [_observation("INV/ABC", record_id="bad")],
        year=2026,
        requested_series="FACT-2026",
        bare_belongs_to_series=False,
    )

    assert report["status"] == "blocked"
    assert "unrecognized_number" in report["blocking_reasons"]
    assert report["unrecognized"][0]["reason"] == "unsupported_number_format"


def test_series_status_cli_is_read_only(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        for suffix, lifecycle_status in ((1, "approved"), (2, "approved"), (3, "rejected")):
            db.upsert_document(
                external_key=f"book:2026:{suffix}",
                document_type="ingresos_book",
                document_number=f"{suffix:05d}",
                issued_on=f"2026-01-{suffix:02d}",
                period_key="2026-Q1",
                currency="EUR",
                total_minor=10000 * suffix,
                lifecycle_status=lifecycle_status,
                source_hash=f"{suffix:064x}",
            )
        before_counts = db.table_counts()
        before_versions = db.connection.execute(
            "SELECT document_id, row_version FROM documents ORDER BY document_id"
        ).fetchall()

    blocked_exit = main(
        [
            "invoice",
            "series-status",
            "--db",
            str(database),
            "--year",
            "2026",
            "--series",
            "FACT-2026",
        ]
    )
    blocked = json.loads(capsys.readouterr().out)
    assert blocked_exit == 2
    assert "unresolved_bare" in blocked["blocking_reasons"]
    assert blocked["next_number_suggestion"] is None

    exit_code = main(
        [
            "invoice",
            "series-status",
            "--db",
            str(database),
            "--year",
            "2026",
            "--series",
            "FACT-2026",
            "--bare-belongs-to-series",
        ]
    )
    report = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert report["next_number_suggestion"] == "FACT-2026-00004"
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        after_counts = {
            row["name"]: row["count"]
            for row in connection.execute(
                """
                SELECT 'documents' AS name, COUNT(*) AS count FROM documents
                UNION ALL
                SELECT 'transactions', COUNT(*) FROM transactions
                UNION ALL
                SELECT 'outgoing_invoice_drafts', COUNT(*) FROM outgoing_invoice_drafts
                """
            )
        }
        after_versions = connection.execute(
            "SELECT document_id, row_version FROM documents ORDER BY document_id"
        ).fetchall()
    assert after_counts["documents"] == before_counts["documents"]
    assert after_counts["transactions"] == before_counts["transactions"]
    assert after_counts["outgoing_invoice_drafts"] == before_counts["outgoing_invoice_drafts"]
    assert [tuple(row) for row in after_versions] == [tuple(row) for row in before_versions]


def _bare_sequence(first: int, last: int) -> list[InvoiceNumberObservation]:
    observations = []
    for suffix in range(first, last + 1):
        issued_on = "2026-07-01" if suffix == 11 else f"2026-01-{suffix:02d}"
        amount = 579443 if suffix == 11 else suffix * 10000
        observations.append(
            _observation(
                f"{suffix:05d}",
                record_id=f"book-{suffix}",
                source="ingresos_book",
                issued_on=issued_on,
                amount_eur_minor=amount,
            )
        )
    return observations


def _final_invoice_representations() -> list[InvoiceNumberObservation]:
    document = _observation(
        "FACT-2026-00011",
        record_id="final-document-11",
        issued_on="2026-07-01",
        amount_eur_minor=579443,
    )
    draft = _observation(
        "FACT-2026-00011",
        source="issued_draft",
        record_id="issued-draft-11",
        issued_on="2026-07-01",
        amount_eur_minor=579443,
        declared_series="FACT-2026",
        linked_document_id="final-document-11",
    )
    return [document, draft]


def _observation(
    number: str,
    *,
    record_id: str,
    source: str = "income_invoice",
    issued_on: str = "2026-01-01",
    amount_eur_minor: int = 10000,
    declared_series: str | None = None,
    linked_document_id: str | None = None,
) -> InvoiceNumberObservation:
    return InvoiceNumberObservation(
        source=source,
        record_id=record_id,
        number=number,
        lifecycle_status="issued" if source == "issued_draft" else "approved",
        issued_on=issued_on,
        counterparty_id="customer-1",
        amount_eur_minor=amount_eur_minor,
        declared_series=declared_series,
        linked_document_id=linked_document_id,
    )
