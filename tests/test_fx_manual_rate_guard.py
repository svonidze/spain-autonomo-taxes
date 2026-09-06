"""Manual official FX rates are EUR per unit and must stay close to the ECB reference."""

from __future__ import annotations

import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from autonomo_taxes import operational_cli
from autonomo_taxes.cli import main
from autonomo_taxes.fx_reference import (
    ECBRateObservation,
    ECBRateResult,
    FXRateUnavailableError,
)
from autonomo_taxes.ledger_db import LedgerDB, StaleRowVersionError, initialize

RATE_DATE = date(2026, 7, 1)
# A round synthetic quote: 1.2500 units per EUR is exactly 0.8000 EUR per unit.
UNITS_PER_EUR = "1.2500"
ANNOTATED_REFERENCE = (
    "Synthetic official bulletin (rate not verified against the ECB reference: "
    "deviates 12.50% from 0.80000000 EUR per USD on 2026-07-01)"
)


def synthetic_reference(
    units: str = UNITS_PER_EUR,
    currency: str = "USD",
    rate_date: date = RATE_DATE,
) -> ECBRateResult:
    units_per_eur = Decimal(units)
    raw = json.dumps(
        {"currency": currency, "date": rate_date.isoformat(), "value": units},
        sort_keys=True,
        separators=(",", ":"),
    )
    observation = ECBRateObservation(
        currency=currency,
        rate_date=rate_date,
        units_per_eur=units_per_eur,
        eur_per_unit=Decimal(1) / units_per_eur,
        source_url="https://example.invalid/ecb/EXR/D.USD.EUR.SP00.A",
        raw_observation=raw,
        raw_observation_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    return ECBRateResult(status="exact", observation=observation)


@pytest.fixture
def reference_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, date]]:
    calls: list[tuple[str, date]] = []

    def lookup(currency: str, as_of: date) -> ECBRateResult:
        calls.append((currency, as_of))
        return synthetic_reference(currency=currency, rate_date=as_of)

    monkeypatch.setattr(operational_cli, "fetch_eur_rate", lookup)
    return calls


def _ledger(tmp_path: Path) -> tuple[Path, dict]:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        transaction = db.add_transaction(
            external_key="fx-guard:USD-1",
            period_key="2026-Q3",
            transaction_date=RATE_DATE.isoformat(),
            booking_date=RATE_DATE.isoformat(),
            entry_type="income",
            description="USD invoice",
            amount_minor=10000,
            currency="USD",
            amount_original_minor=10000,
            original_currency="USD",
            lifecycle_status="needs_review",
        )
    return database, transaction


def _review_apply_fx(
    tmp_path: Path,
    database: Path,
    transaction: dict,
    rate: str,
    *,
    rate_source: str = "ecb",
    flags: tuple[str, ...] = (),
) -> int:
    payload = tmp_path / "fx-review.json"
    payload.write_text(
        json.dumps(
            {
                "review_id": f"transaction:{transaction['transaction_id']}",
                "expected_row_version": transaction["row_version"],
                "rate_date": RATE_DATE.isoformat(),
                "rate": rate,
                "rate_source": rate_source,
                "source_reference": "Synthetic official bulletin",
            }
        ),
        encoding="utf-8",
    )
    return main(["review", "apply-fx", "--db", str(database), "--input", str(payload), *flags])


def _stored_state(database: Path) -> tuple[dict, list[str]]:
    with LedgerDB.open(database, read_only=True) as db:
        transaction = dict(db.connection.execute("SELECT * FROM transactions").fetchone())
        references = [
            str(row["source_reference"])
            for row in db.connection.execute("SELECT source_reference FROM fx_rates")
        ]
    return transaction, references


def test_inverted_quote_is_refused_with_both_conventions(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)

    with pytest.raises(ValueError, match="looks like a units-per-EUR quote") as raised:
        _review_apply_fx(tmp_path, database, transaction, UNITS_PER_EUR)
    message = str(raised.value)
    assert "1.2500 USD per EUR" in message
    assert "expected EUR-per-unit close to 0.80000000" in message
    # The override flag is for genuinely different official rates, not for a wrong direction.
    with pytest.raises(ValueError, match="looks like a units-per-EUR quote"):
        _review_apply_fx(
            tmp_path, database, transaction, "1.2550", flags=("--allow-unverified-rate",)
        )
    stored, references = _stored_state(database)
    assert stored["amount_eur_minor"] is None
    assert stored["fx_rate_id"] is None
    assert references == []
    assert reference_calls == [("USD", RATE_DATE)] * 2


def test_rate_matching_reference_is_recorded_unchanged(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)

    assert _review_apply_fx(tmp_path, database, transaction, "0.8000") == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["amount_eur_minor"] == 8000
    assert applied["fx_rate_check"]["status"] == "verified"
    _, references = _stored_state(database)
    assert references == ["Synthetic official bulletin"]


def test_deviating_rate_needs_explicit_override_and_is_annotated(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)

    with pytest.raises(ValueError, match="deviates 12.50% from the ECB reference") as raised:
        _review_apply_fx(tmp_path, database, transaction, "0.7000")
    assert "--allow-unverified-rate" in str(raised.value)
    stored, references = _stored_state(database)
    assert stored["amount_eur_minor"] is None
    assert references == []

    assert (
        _review_apply_fx(
            tmp_path, database, transaction, "0.7000", flags=("--allow-unverified-rate",)
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["amount_eur_minor"] == 7000
    _, references = _stored_state(database)
    assert references == [ANNOTATED_REFERENCE]


def test_unavailable_reference_warns_but_does_not_block(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    def offline(currency: str, as_of: date) -> ECBRateResult:
        raise FXRateUnavailableError("ECB reference service is unreachable")

    monkeypatch.setattr(operational_cli, "fetch_eur_rate", offline)
    database, transaction = _ledger(tmp_path)

    assert _review_apply_fx(tmp_path, database, transaction, UNITS_PER_EUR) == 0
    captured = capsys.readouterr()
    applied = json.loads(captured.out)
    assert applied["amount_eur_minor"] == 12500
    assert applied["fx_rate_check"]["status"] == "unavailable"
    detail = applied["fx_rate_check"]["detail"]
    assert "could not be verified against the ECB reference" in detail
    assert _stored_state(database)[1] == [f"Synthetic official bulletin ({detail})"]
    assert "could not be verified against the ECB reference" in captured.err
    assert "ECB reference service is unreachable" in captured.err


def test_settlement_rate_is_exempt_from_the_reference_check(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)

    assert (
        _review_apply_fx(
            tmp_path, database, transaction, UNITS_PER_EUR, rate_source="actual_settlement"
        )
        == 0
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["amount_eur_minor"] == 12500
    assert captured.err == ""
    assert reference_calls == []
    _, references = _stored_state(database)
    assert references == ["Synthetic official bulletin"]


def _stored_rate(database: Path, rate: str, rate_source: str = "banco_de_espana") -> dict:
    with LedgerDB.open(database) as db:
        return db.add_fx_rate(
            rate_date=RATE_DATE.isoformat(),
            base_currency="USD",
            quote_currency="EUR",
            rate=rate,
            rate_source=rate_source,
            source_reference="Synthetic official bulletin",
            source_hash=f"synthetic-{rate}",
        )


def _transaction_apply_fx(
    database: Path, transaction: dict, fx_rate: dict, *, flags: tuple[str, ...] = ()
) -> int:
    return main(
        [
            "transactions",
            "apply-fx",
            "--db",
            str(database),
            transaction["transaction_id"],
            "--fx-rate-id",
            fx_rate["fx_rate_id"],
            "--expected-row-version",
            str(transaction["row_version"]),
            *flags,
        ]
    )


def test_stored_inverted_quote_is_refused_before_it_is_applied(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)
    inverted = _stored_rate(database, UNITS_PER_EUR)

    with pytest.raises(ValueError, match="looks like a units-per-EUR quote"):
        _transaction_apply_fx(database, transaction, inverted)
    stored, references = _stored_state(database)
    assert stored["fx_rate_id"] is None
    assert references == ["Synthetic official bulletin"]
    assert reference_calls == [("USD", RATE_DATE)]


def test_stored_deviating_rate_override_annotates_the_rate_once(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)
    deviating = _stored_rate(database, "0.7000")

    with pytest.raises(ValueError, match="pass --allow-unverified-rate"):
        _transaction_apply_fx(database, transaction, deviating)

    assert (
        _transaction_apply_fx(
            database, transaction, deviating, flags=("--allow-unverified-rate",)
        )
        == 0
    )
    applied = json.loads(capsys.readouterr().out)
    assert applied["amount_eur_minor"] == 7000
    assert applied["row_version"] == 2
    _, references = _stored_state(database)
    assert references == [ANNOTATED_REFERENCE]

    assert (
        _transaction_apply_fx(
            database, applied, deviating, flags=("--allow-unverified-rate",)
        )
        == 0
    )
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["row_version"] == 2
    _, references = _stored_state(database)
    assert references == [ANNOTATED_REFERENCE]


def test_stored_rate_matching_reference_applies_without_annotation(
    tmp_path: Path, capsys, reference_calls: list[tuple[str, date]]
) -> None:
    database, transaction = _ledger(tmp_path)
    official = _stored_rate(database, "0.8000")

    assert _transaction_apply_fx(database, transaction, official) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["amount_eur_minor"] == 8000
    _, references = _stored_state(database)
    assert references == ["Synthetic official bulletin"]


def test_stored_unavailable_reference_preserves_outcome_and_deduplicates_audit(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(*_args):
        raise FXRateUnavailableError("Synthetic network outage")

    monkeypatch.setattr(operational_cli, "fetch_eur_rate", offline)
    database, transaction = _ledger(tmp_path)
    rate = _stored_rate(database, "0.8000")

    assert _transaction_apply_fx(database, transaction, rate) == 0
    applied = json.loads(capsys.readouterr().out)
    check = applied["fx_rate_check"]
    assert check["status"] == "unavailable"
    assert _stored_state(database)[1] == [f"Synthetic official bulletin ({check['detail']})"]

    assert _transaction_apply_fx(database, applied, rate) == 0
    repeated = json.loads(capsys.readouterr().out)
    assert repeated["row_version"] == applied["row_version"]
    assert repeated["fx_rate_check"] == check
    assert _stored_state(database)[1] == [f"Synthetic official bulletin ({check['detail']})"]


def test_unavailable_check_does_not_write_audit_when_transaction_is_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    def offline(*_args):
        raise FXRateUnavailableError("Synthetic network outage")

    monkeypatch.setattr(operational_cli, "fetch_eur_rate", offline)
    database, transaction = _ledger(tmp_path)
    rate = _stored_rate(database, "0.8000")
    with pytest.raises(StaleRowVersionError, match="Expected row_version"):
        _transaction_apply_fx(database, {**transaction, "row_version": 999}, rate)
    stored, references = _stored_state(database)
    assert stored["fx_rate_id"] is None
    assert references == ["Synthetic official bulletin"]


@pytest.mark.parametrize("reference", ["", "   ", None])
def test_warning_cannot_replace_a_missing_source_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reference,
) -> None:
    def offline(*_args):
        raise FXRateUnavailableError("Synthetic network outage")

    monkeypatch.setattr(operational_cli, "fetch_eur_rate", offline)
    database, transaction = _ledger(tmp_path)
    payload = tmp_path / "missing-reference.json"
    payload.write_text(json.dumps({
        "review_id": f"transaction:{transaction['transaction_id']}",
        "expected_row_version": transaction["row_version"],
        "rate_date": RATE_DATE.isoformat(), "rate": "0.8000", "rate_source": "ecb",
        "source_reference": reference,
    }))
    with pytest.raises(ValueError, match="nonblank source_reference"):
        main(["review", "apply-fx", "--db", str(database), "--input", str(payload)])
    stored, references = _stored_state(database)
    assert stored["fx_rate_id"] is None
    assert references == []
