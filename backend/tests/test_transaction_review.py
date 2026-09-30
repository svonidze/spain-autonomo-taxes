from __future__ import annotations

import pytest

from autonomo_taxes.ledger_db import LifecycleError, StaleRowVersionError, initialize


def test_transaction_counterparty_correction_is_versioned(tmp_path) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        wrong = db.upsert_counterparty(external_key="wrong", display_name="Wrong supplier")
        correct = db.upsert_counterparty(external_key="correct", display_name="Correct supplier")
        transaction = db.add_transaction(
            external_key="review-counterparty",
            period_key="2026-Q3",
            transaction_date="2026-09-30",
            booking_date="2026-04-08",
            entry_type="expense",
            description="Asset amortization",
            amount_minor=10000,
            currency="EUR",
            lifecycle_status="approved",
            counterparty_id=wrong["counterparty_id"],
        )

        updated = db.update_transaction_counterparty(
            transaction["transaction_id"],
            counterparty_id=correct["counterparty_id"],
            expected_row_version=1,
        )
        assert updated["counterparty_id"] == correct["counterparty_id"]
        assert updated["row_version"] == 2

        with pytest.raises(StaleRowVersionError):
            db.update_transaction_counterparty(
                transaction["transaction_id"],
                counterparty_id=wrong["counterparty_id"],
                expected_row_version=1,
            )


@pytest.mark.parametrize(
    "lifecycle_status",
    ("posted", "included_in_snapshot", "duplicate", "rejected", "void"),
)
def test_transaction_counterparty_cannot_change_after_review(
    tmp_path, lifecycle_status: str
) -> None:
    database = tmp_path / f"ledger-{lifecycle_status}.sqlite"
    with initialize(database) as db:
        wrong = db.upsert_counterparty(external_key="wrong", display_name="Wrong supplier")
        correct = db.upsert_counterparty(external_key="correct", display_name="Correct supplier")
        transaction = db.add_transaction(
            external_key=f"locked-{lifecycle_status}",
            period_key="2026-Q3",
            transaction_date="2026-09-30",
            booking_date="2026-09-30",
            entry_type="expense",
            description="Locked transaction",
            amount_minor=10000,
            currency="EUR",
            lifecycle_status=lifecycle_status,
            counterparty_id=wrong["counterparty_id"],
        )

        with pytest.raises(LifecycleError, match="Counterparty cannot change"):
            db.update_transaction_counterparty(
                transaction["transaction_id"],
                counterparty_id=correct["counterparty_id"],
                expected_row_version=1,
            )
