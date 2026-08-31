from __future__ import annotations

from datetime import date
import hashlib
from decimal import Decimal
from pathlib import Path

import pytest

from autonomo_taxes.ledger_db import initialize, open as open_ledger_db
from autonomo_taxes.review_packet import (
    ReviewPacketError,
    build_fx_suggestion,
    classify_review_packet_failure,
    confirm_review_packet,
    prepare_review_work_item,
)


def _period_key(value: date) -> str:
    return f"{value.year}-Q{((value.month - 1) // 3) + 1}"


def _invoice_fixture(
    tmp_path: Path,
    *,
    currency: str = "EUR",
    transaction_date: str | None = None,
) -> dict[str, object]:
    database = tmp_path / "ledger.sqlite"
    source = tmp_path / "private" / "supplier-invoice.pdf"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"immutable invoice fixture")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    tax_date = transaction_date or date.today().isoformat()
    period = _period_key(date.fromisoformat(tax_date))
    original_minor = 12100
    with initialize(database) as db:
        counterparty = db.upsert_counterparty(
            external_key="confirm-counterparty",
            display_name="Supplier Example SL",
            country_code="ES",
        )
        document = db.upsert_document(
            external_key=f"sha256:{digest}",
            counterparty_id=counterparty["counterparty_id"],
            document_type="expense_invoice",
            document_number="INV-CONFIRM-1",
            issued_on=tax_date,
            period_key=period,
            currency=currency,
            total_minor=original_minor,
            lifecycle_status="needs_review",
            source_hash=digest,
        )
        document = db.set_document_storage(
            document["document_id"],
            source_path=str(source),
            mime_type="application/pdf",
            expected_row_version=document["row_version"],
        )
        transaction = db.add_transaction(
            external_key="confirm-transaction",
            period_key=period,
            transaction_date=tax_date,
            booking_date=tax_date,
            entry_type="expense",
            description="Confirmed invoice fixture",
            amount_minor=original_minor,
            currency=currency,
            amount_original_minor=original_minor,
            original_currency=currency,
            amount_eur_minor=original_minor if currency == "EUR" else None,
            direction="debit",
            lifecycle_status="needs_review",
            document_id=document["document_id"],
            counterparty_id=counterparty["counterparty_id"],
            source_hash=hashlib.sha256(b"confirm-transaction").hexdigest(),
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="invoice_review",
            tax_code="unknown",
            jurisdiction="ES",
            taxable_base_minor=10000,
            vat_minor=2100,
            notes="Extraction candidate only",
            source_hash=hashlib.sha256(b"confirm-treatment").hexdigest(),
        )
        db.add_validation_issue(
            period_key=period,
            issue_code="transaction_tax_review",
            severity="warning",
            message="Review IRPF and IVA treatment",
            subject_table="transactions",
            subject_id=transaction["transaction_id"],
            blocking=True,
            source_hash=digest,
        )
    return {
        "database": database,
        "period": period,
        "document_id": document["document_id"],
        "transaction_id": transaction["transaction_id"],
        "counterparty_id": counterparty["counterparty_id"],
    }


def _packet(fixture: dict[str, object]) -> dict[str, object]:
    with open_ledger_db(fixture["database"], read_only=True) as db:
        work_item = prepare_review_work_item(db, f"transaction:{fixture['transaction_id']}")
    return dict(work_item["packet"])


def _approve_decision(packet: dict[str, object]) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    decision.update(
        {
            "outcome": "approve",
            "reason": "Valid business software expense",
            "business_purpose": "Software used exclusively for the professional activity",
            "document_valid": True,
            "asset_decision": "current_expense",
            "asset_id": None,
            "counterparty_changes": {},
            "tax_treatment": {
                "tax_code": "domestic_input",
                "aeat_invoice_type": "F1",
                "aeat_operation_key": "01",
                "aeat_operation_qualification": "S1",
                "aeat_exemption_code": None,
                "aeat_reverse_charge": False,
                "vat_investment_good": False,
                "aeat_expense_concept": "G03",
                "rate_basis_points": 2100,
                "deductible_ratio": 1.0,
                "taxable_base_minor": 10000,
                "vat_minor": 2100,
                "deductible_irpf_minor": 10000,
                "deductible_vat_minor": 2100,
                "withholding_minor": 0,
                "include_modelo130": True,
                "include_modelo303": True,
                "include_modelo347": False,
                "rule_version_id": None,
                "notes": "Reviewed domestic current expense; 100% business use",
            },
        }
    )
    for resolution in decision["issue_resolutions"]:
        resolution["action"] = "resolve"
        resolution["reason"] = "Reviewed against the immutable source invoice"


def _reject_decision(packet: dict[str, object]) -> None:
    decision = packet["decision"]
    assert isinstance(decision, dict)
    decision.update(
        {
            "outcome": "reject",
            "reason": "Document does not describe the contracted service",
            "business_purpose": None,
            "document_valid": False,
            "asset_decision": None,
            "asset_id": None,
            "counterparty_changes": {},
            "tax_treatment": None,
        }
    )
    for resolution in decision["issue_resolutions"]:
        resolution["action"] = "resolve"
        resolution["reason"] = "Closed by the rejection decision"


def _ecb_fx_spec(rate: str = "0.9216") -> dict[str, object]:
    rate_date = date.today().isoformat()
    raw_observation = (
        '{"currency":"USD","date":"' + rate_date + '","value":"1.0850"}'
    )
    return {
        "rate_date": rate_date,
        "rate": rate,
        "rate_source": "ecb",
        "source_reference": "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        "raw_observation": raw_observation,
        "raw_observation_hash": hashlib.sha256(raw_observation.encode("utf-8")).hexdigest(),
        "supersedes_rate_id": None,
    }


def _settlement_fx_spec(rate: str = "0.9100", reference: str = "Bank settlement advice 42") -> dict[str, object]:
    rate_date = date.today().isoformat()
    return {
        "rate_date": rate_date,
        "rate": rate,
        "rate_source": "actual_settlement",
        "source_reference": reference,
        "raw_observation": (
            '{"kind":"documented_settlement","rate":"' + rate + '",'
            '"rate_date":"' + rate_date + '","source_reference":"' + reference + '"}'
        ),
        "raw_observation_hash": None,
        "supersedes_rate_id": None,
    }


def test_confirm_applies_verified_fx_and_decision_in_one_commit(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    rate = "0.9216"
    ecb_verified: list[tuple[str, date]] = []

    def ecb_verify(currency: str, rate_date: date) -> Decimal:
        ecb_verified.append((currency, rate_date))
        return Decimal(rate)

    with open_ledger_db(fixture["database"]) as db:
        result = confirm_review_packet(
            db, packet, _ecb_fx_spec(rate), ecb_verify=ecb_verify
        )

    assert result["fx_applied"] is True
    assert result["outcome"] == "approve"
    assert ecb_verified == [("USD", date.today())]
    with open_ledger_db(fixture["database"], read_only=True) as db:
        transaction = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert transaction["fx_rate_id"] is not None
        assert transaction["lifecycle_status"] == "approved"
        assert transaction["amount_eur_minor"] == int(
            (Decimal(12100) * Decimal(rate)).quantize(Decimal("1"))
        )
        fx = db.connection.execute(
            "SELECT * FROM fx_rates WHERE fx_rate_id = ?", (transaction["fx_rate_id"],)
        ).fetchone()
        assert fx["rate_source"] == "ecb"
        provenance = db.connection.execute(
            "SELECT * FROM fx_provenance WHERE fx_rate_id = ?", (transaction["fx_rate_id"],)
        ).fetchone()
        assert provenance is not None
        assert provenance["provenance_kind"] == "official"
        assert provenance["raw_observation_hash"] == hashlib.sha256(
            provenance["raw_observation"].encode("utf-8")
        ).hexdigest()
        issue = db.connection.execute(
            "SELECT issue_status FROM validation_issues WHERE subject_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert issue["issue_status"] == "resolved"
        document = db.connection.execute(
            "SELECT lifecycle_status FROM documents WHERE document_id = ?",
            (fixture["document_id"],),
        ).fetchone()
        assert document["lifecycle_status"] == "approved"


def test_confirm_rejects_stale_snapshot(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        db.connection.execute(
            "UPDATE transactions SET description = 'Edited by someone else', "
            "row_version = row_version + 1 WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        )
        db.connection.commit()
        with pytest.raises(ReviewPacketError, match="stale|edited|row_version"):
            confirm_review_packet(db, packet, _ecb_fx_spec(), ecb_verify=lambda *_: Decimal("0.9216"))
    with open_ledger_db(fixture["database"], read_only=True) as db:
        count = db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
        assert count == 0


def test_confirm_detects_concurrent_row_version_change(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as first:
        # A concurrent review applies a settlement rate and moves the row version.
        first.review_transaction_fx_rate(
            fixture["transaction_id"],
            expected_row_version=1,
            rate_date=date.today().isoformat(),
            rate="0.9100",
            rate_source="actual_settlement",
            source_reference="Bank settlement advice 7",
        )
        with pytest.raises(ReviewPacketError):
            confirm_review_packet(
                first,
                packet,
                _ecb_fx_spec(),
                ecb_verify=lambda *_: Decimal("0.9216"),
            )
    with open_ledger_db(fixture["database"], read_only=True) as db:
        rows = db.connection.execute(
            "SELECT rate_source, rate FROM fx_rates ORDER BY created_at"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["rate_source"] == "actual_settlement"


def test_manual_settlement_confirm_does_not_call_ecb_verification(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    fx_spec = _settlement_fx_spec()

    def failing_verify(*_args: object) -> Decimal:
        raise AssertionError("ECB verification must not run for settlements")

    with open_ledger_db(fixture["database"]) as db:
        result = confirm_review_packet(db, packet, fx_spec, ecb_verify=failing_verify)

    assert result["fx_applied"] is True
    with open_ledger_db(fixture["database"], read_only=True) as db:
        transaction = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        provenance = db.connection.execute(
            "SELECT * FROM fx_provenance WHERE fx_rate_id = ?", (transaction["fx_rate_id"],)
        ).fetchone()
        assert provenance["provenance_kind"] == "documented_settlement"
        assert provenance["primary_source_reference"] == fx_spec["source_reference"]


def test_reject_works_without_fx(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _reject_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        result = confirm_review_packet(db, packet, None)
    assert result["fx_applied"] is False
    assert result["outcome"] == "reject"
    with open_ledger_db(fixture["database"], read_only=True) as db:
        transaction = db.connection.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert transaction["lifecycle_status"] == "rejected"
        assert transaction["fx_rate_id"] is None
        count = db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
        assert count == 0


def test_closed_period_blocks_confirm_and_rolls_back(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        db.connection.execute(
            "UPDATE periods SET status = 'closed', row_version = row_version + 1 "
            "WHERE period_key = ?",
            (fixture["period"],),
        )
        db.connection.commit()
        with pytest.raises(ReviewPacketError, match="not open|immutable|close"):
            confirm_review_packet(db, packet, _ecb_fx_spec(), ecb_verify=lambda *_: Decimal("0.9216"))
    with open_ledger_db(fixture["database"], read_only=True) as db:
        count = db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
        assert count == 0
        transaction = db.connection.execute(
            "SELECT lifecycle_status FROM transactions WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert transaction["lifecycle_status"] == "needs_review"


def test_failed_decision_rolls_back_applied_fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import autonomo_taxes.review_packet as review_packet

    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)

    def exploding_apply(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise ReviewPacketError("forced validation failure")

    monkeypatch.setattr(review_packet, "_apply_decision", exploding_apply)
    with open_ledger_db(fixture["database"]) as db:
        before = db.connection.execute(
            "SELECT row_version, amount_eur_minor, lifecycle_status FROM transactions "
            "WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        with pytest.raises(ReviewPacketError, match="forced validation failure"):
            confirm_review_packet(db, packet, _ecb_fx_spec(), ecb_verify=lambda *_: Decimal("0.9216"))
        after = db.connection.execute(
            "SELECT row_version, amount_eur_minor, lifecycle_status FROM transactions "
            "WHERE transaction_id = ?",
            (fixture["transaction_id"],),
        ).fetchone()
        assert before == after
        fx_count = db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
        assert fx_count == 0
        provenance_count = db.connection.execute(
            "SELECT COUNT(*) FROM fx_provenance"
        ).fetchone()[0]
        assert provenance_count == 0


def test_ecb_rate_mismatch_is_rejected(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="does not match"):
            confirm_review_packet(
                db, packet, _ecb_fx_spec("0.9216"), ecb_verify=lambda *_: Decimal("0.9999")
            )
    code, status = classify_review_packet_failure(
        "ECB rate verification failed: the official rate does not match the submitted rate"
    )
    assert (code, status) == ("ecb_verification_failed", 409)


def test_ecb_verification_without_official_observation_is_rejected(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="no official observation"):
            confirm_review_packet(db, packet, _ecb_fx_spec(), ecb_verify=lambda *_: None)


def test_ecb_confirmation_requires_server_verification(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="not available"):
            confirm_review_packet(db, packet, _ecb_fx_spec())


def test_raw_observation_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    fx_spec = _ecb_fx_spec()
    fx_spec["raw_observation_hash"] = "0" * 64
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="raw_observation_hash"):
            confirm_review_packet(db, packet, fx_spec, ecb_verify=lambda *_: Decimal("0.9216"))


def test_fx_rate_cannot_be_dated_after_the_transaction(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    fx_spec = _ecb_fx_spec()
    fx_spec["rate_date"] = "2999-01-01"
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="after the transaction"):
            confirm_review_packet(db, packet, fx_spec)


def test_eur_transactions_do_not_accept_fx(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path)
    packet = _packet(fixture)
    _approve_decision(packet)
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="EUR transactions"):
            confirm_review_packet(db, packet, _ecb_fx_spec(), ecb_verify=lambda *_: Decimal("1"))


def test_fx_spec_shape_is_strict(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    packet = _packet(fixture)
    _approve_decision(packet)
    fx_spec = _ecb_fx_spec()
    del fx_spec["source_reference"]
    fx_spec["extra_field"] = True
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="missing=source_reference"):
            confirm_review_packet(db, packet, fx_spec, ecb_verify=lambda *_: Decimal("1"))


def test_documented_settlement_rates_can_coexist(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    rate_date = date.today().isoformat()
    with open_ledger_db(fixture["database"]) as db:
        first = db.add_fx_rate(
            rate_date=rate_date,
            base_currency="USD",
            quote_currency="EUR",
            rate="0.9100",
            rate_source="actual_settlement",
            source_hash=hashlib.sha256(b"settlement-one").hexdigest(),
            source_reference="Bank settlement advice 1",
        )
        second = db.add_fx_rate(
            rate_date=rate_date,
            base_currency="USD",
            quote_currency="EUR",
            rate="0.9200",
            rate_source="actual_settlement",
            source_hash=hashlib.sha256(b"settlement-two").hexdigest(),
            source_reference="Bank settlement advice 2",
        )
        duplicate = db.add_fx_rate(
            rate_date=rate_date,
            base_currency="USD",
            quote_currency="EUR",
            rate="0.9100",
            rate_source="actual_settlement",
            source_hash=hashlib.sha256(b"settlement-one-again").hexdigest(),
            source_reference="Bank settlement advice 1",
        )
        assert first["fx_rate_id"] != second["fx_rate_id"]
        assert duplicate["fx_rate_id"] == first["fx_rate_id"]
        count = db.connection.execute("SELECT COUNT(*) FROM fx_rates").fetchone()[0]
        assert count == 2


def test_official_rates_stay_conflicting_when_disagreed(tmp_path: Path) -> None:
    from autonomo_taxes.ledger_db import FxRateConflictError

    fixture = _invoice_fixture(tmp_path, currency="USD")
    rate_date = date.today().isoformat()
    with open_ledger_db(fixture["database"]) as db:
        db.add_fx_rate(
            rate_date=rate_date,
            base_currency="USD",
            quote_currency="EUR",
            rate="0.9100",
            rate_source="ecb",
            source_hash=hashlib.sha256(b"ecb-one").hexdigest(),
            source_reference="ECB data API",
        )
        with pytest.raises(FxRateConflictError):
            db.add_fx_rate(
                rate_date=rate_date,
                base_currency="USD",
                quote_currency="EUR",
                rate="0.9200",
                rate_source="ecb",
                source_hash=hashlib.sha256(b"ecb-two").hexdigest(),
                source_reference="ECB data API",
            )


def test_fx_suggestion_projected_without_db_access() -> None:
    transaction_date = date(2026, 7, 6)
    state = {
        "transaction": {
            "original_currency": "USD",
            "currency": "USD",
            "transaction_date": transaction_date.isoformat(),
            "fx_rate_id": None,
            "amount_original_minor": 12100,
            "amount_eur_minor": None,
        },
        "fx": None,
    }
    unavailable = build_fx_suggestion(state, ecb_error="ECB reference service is unreachable")
    assert unavailable is not None
    assert unavailable["status"] == "unavailable"
    assert unavailable["note"] == "ECB reference service is unreachable"
    assert unavailable["currency"] == "USD"
    assert unavailable["rate_date"] is None

    class Observation:
        rate_date = transaction_date
        eur_per_unit = Decimal("0.9216")
        units_per_eur = Decimal("1.0850")
        source_url = "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
        raw_observation = '{"currency":"USD","date":"2026-07-06","value":"1.0850"}'
        raw_observation_hash = hashlib.sha256(b'{"currency":"USD","date":"2026-07-06","value":"1.0850"}').hexdigest()

    class Result:
        status = "exact"
        observation = Observation

    exact = build_fx_suggestion(state, ecb_result=Result())
    assert exact is not None
    assert exact["status"] == "exact"
    assert exact["eur_per_unit"] == "0.9216"
    assert exact["amount_eur"] == "111.51"
    assert exact["note"] is None

    state["transaction"]["fx_rate_id"] = "fx-1"
    state["fx"] = {
        "rate_date": transaction_date.isoformat(),
        "rate": "0.9100",
        "rate_source": "actual_settlement",
        "source_reference": "Bank settlement advice 1",
    }
    existing = build_fx_suggestion(state)
    assert existing is not None
    assert existing["status"] == "existing"
    assert existing["rate_source"] == "actual_settlement"

    assert build_fx_suggestion(
        {**state, "transaction": {**state["transaction"], "original_currency": "EUR", "fx_rate_id": None}},
    ) is None


def test_guidance_describes_profiles_questions_and_coverage(tmp_path: Path) -> None:
    fixture = _invoice_fixture(tmp_path, currency="USD")
    with open_ledger_db(fixture["database"], read_only=True) as db:
        work_item = prepare_review_work_item(db, f"transaction:{fixture['transaction_id']}")
    guidance = work_item["guidance"]
    profiles = {profile["code"]: profile["required_questions"] for profile in guidance["decision_profiles"]}
    assert "approve" in profiles and "reject" in profiles
    assert "business_purpose" in profiles["approve"]
    assert set(profiles["reject"]) == {"reason", "document_valid"}
    question_ids = {question["id"] for question in guidance["questions"]}
    assert {"business_purpose", "tax_code", "fx_rate", "document_valid", "reason"} <= question_ids
    assert "decision.tax_treatment.aeat_invoice_type" in guidance["hidden_technical_fields"]
    assert guidance["issue_coverage"]["transaction_tax_review"] == [
        "business_purpose",
        "tax_code",
    ]
