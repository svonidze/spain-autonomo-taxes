from __future__ import annotations

import json
from pathlib import Path

import pytest

from autonomo_taxes.annual_readiness import assess_annual_readiness
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.tax_engine import CalculationBlocked


def _add_activity(db, *, starts_on: str) -> None:
    profile = db.upsert_taxpayer_profile(
        tax_id="X0000000A",
        full_name="Example Taxpayer",
        source_hash="profile-source",
    )
    db.upsert_business_activity(
        taxpayer_profile_id=profile["taxpayer_profile_id"],
        activity_key="software-development",
        aeat_activity_code="A",
        aeat_activity_type="05",
        iae_section="2",
        iae_group_epigraph="763",
        description="Software development",
        starts_on=starts_on,
        source_reference="Reviewed Modelo 036",
        source_hash="activity-source",
    )


def _close_empty_period(db, period_key: str) -> None:
    period = db.ensure_period(period_key)
    db.close_period(period_key, expected_row_version=period["row_version"])


def _modelo303_values(**overrides: str) -> dict[str, str]:
    values = {
        "10": "0.00",
        "11": "0.00",
        "12": "0.00",
        "13": "0.00",
        "27": "0.00",
        "28": "0.00",
        "29": "0.00",
        "30": "0.00",
        "31": "0.00",
        "45": "0.00",
        "64": "0.00",
        "69": "0.00",
        "110": "0.00",
        "78": "0.00",
        "87": "0.00",
        "71": "0.00",
        "72": "0.00",
        "73": "0.00",
        "compensation_carryforward": "0.00",
    }
    values.update(overrides)
    return values


def _prepare_modelo390_year(db, *, year: int = 2026) -> tuple[str, ...]:
    _add_activity(db, starts_on=f"{year}-01-01")
    db.ensure_period(str(year))
    db.add_obligation(
        period_key=str(year),
        obligation_code="390",
        determination="due",
        filing_status="due",
        blocking=True,
        explanation="Annual IVA summary is due.",
    )
    periods = tuple(f"{year}-Q{quarter}" for quarter in range(1, 5))
    for period_key in periods:
        db.add_obligation(
            period_key=period_key,
            obligation_code="303",
            determination="due",
            filing_status="filed",
            blocking=False,
            explanation="Quarterly IVA return was filed.",
        )
    return periods


def _close_with_modelo303_evidence(
    db,
    period_key: str,
    values: dict[str, str],
    *,
    suffix: str = "evidence",
) -> None:
    period = db.ensure_period(period_key)
    if period["status"] == "open":
        db.close_period(period_key, expected_row_version=period["row_version"])
    db.create_filing_snapshot(
        period_key,
        form_code="303",
        status="baseline",
        snapshot_hash=f"{period_key}-{suffix}",
        payload={
            "form": "303",
            "period": period_key,
            "values": values,
            "receipt_verification": {"status": "matched"},
        },
    )


def _gate(report: dict, category: str) -> dict:
    return next(
        row
        for row in report["form_specific_gates"]["390"]["categories"]
        if row["category"] == category
    )


def test_readiness_uses_activity_start_and_accepts_complete_partial_first_year(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_activity(db, starts_on="2023-05-31")
        for period_key in ("2023-Q2", "2023-Q3", "2023-Q4"):
            _close_empty_period(db, period_key)
        db.ensure_period("2023")
        db.add_obligation(
            period_key="2023",
            obligation_code="347",
            determination="not_due",
            filing_status="waived",
            blocking=False,
            explanation="No qualifying annual counterparty total.",
        )

        report = assess_annual_readiness(db, year=2023, form_code="347")

    assert report["ready"] is True
    assert report["expected_quarters"] == ["2023-Q2", "2023-Q3", "2023-Q4"]
    assert report["blockers"] == []


def test_readiness_blocks_open_or_missing_quarters_and_unknown_form(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_activity(db, starts_on="2026-01-01")
        _close_empty_period(db, "2026-Q1")
        db.ensure_period("2026-Q2")
        db.ensure_period("2026")
        db.add_obligation(
            period_key="2026",
            obligation_code="347",
            determination="unknown",
            filing_status="unknown",
            blocking=True,
            explanation="Year is incomplete.",
        )

        report = assess_annual_readiness(db, year=2026, form_code="347")

    blockers = {
        (row["kind"], row.get("period") or row.get("form"))
        for row in report["blockers"]
    }
    assert ("quarter_not_closed", "2026-Q2") in blockers
    assert ("quarter_missing", "2026-Q3") in blockers
    assert ("quarter_missing", "2026-Q4") in blockers
    assert ("annual_obligation_unknown", "347") in blockers


def test_production_annual_calculation_fails_closed_but_history_mode_remains_available(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_activity(db, starts_on="2026-01-01")
        db.ensure_period("2026-Q1")
        db.ensure_period("2026")
        db.add_obligation(
            period_key="2026",
            obligation_code="347",
            determination="unknown",
            filing_status="unknown",
            blocking=True,
            explanation="Year is incomplete.",
        )

    with pytest.raises(CalculationBlocked, match="annual production calculation is not ready"):
        main(
            [
                "calculate",
                "--db",
                str(database),
                "--form",
                "347",
                "--year",
                "2026",
                "--allow-authoritative-history",
            ]
        )

    assert main(
        [
            "calculate",
            "--db",
            str(database),
            "--form",
            "347",
            "--year",
            "2026",
            "--mode",
            "verify_history",
        ]
    ) == 0
    historical = json.loads(capsys.readouterr().out)
    assert historical["calculation_mode"] == "verify_history"


def test_annual_status_cli_returns_structured_blockers(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_activity(db, starts_on="2026-01-01")
        db.ensure_period("2026")
        db.add_obligation(
            period_key="2026",
            obligation_code="100",
            determination="due",
            filing_status="due",
            blocking=True,
            explanation="Annual return will be due after year end.",
        )

    assert main(
        [
            "period",
            "annual-status",
            "--db",
            str(database),
            "--year",
            "2026",
            "--form",
            "100",
        ]
    ) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ready"] is False
    assert report["requested_form"] == "100"
    assert {row["kind"] for row in report["blockers"]} >= {"quarter_missing"}


def test_complete_year_production_calculation_includes_readiness_evidence(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_activity(db, starts_on="2026-01-01")
        for period_key in ("2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"):
            _close_empty_period(db, period_key)
        db.ensure_period("2026")
        db.add_obligation(
            period_key="2026",
            obligation_code="347",
            determination="not_due",
            filing_status="waived",
            blocking=False,
            explanation="No qualifying annual counterparty total.",
        )

    assert main(
        [
            "calculate",
            "--db",
            str(database),
            "--form",
            "347",
            "--year",
            "2026",
        ]
    ) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["annual_calculation_ready"] is True
    assert report["annual_readiness"]["ready"] is True
    assert report["annual_readiness"]["calculation_stage"] == "complete_year"


def test_asset_schedule_blocks_modelo100_but_not_unrelated_annual_form(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        _add_activity(db, starts_on="2026-01-01")
        for period_key in ("2026-Q1", "2026-Q2", "2026-Q3", "2026-Q4"):
            _close_empty_period(db, period_key)
        db.ensure_period("2026")
        db.add_asset(
            asset_code="UNRESOLVED-ASSET",
            cost_minor=10000,
            amortizable_base_minor=10000,
            currency="EUR",
            depreciation_method="linear",
            placed_in_service_on="2026-01-01",
            source_hash="asset-source",
        )
        for form in ("347", "100"):
            db.add_obligation(
                period_key="2026",
                obligation_code=form,
                determination="due" if form == "100" else "not_due",
                filing_status="due" if form == "100" else "waived",
                blocking=form == "100",
                explanation="Reviewed annual obligation.",
            )

        modelo347 = assess_annual_readiness(db, year=2026, form_code="347")
        modelo100 = assess_annual_readiness(db, year=2026, form_code="100")

    assert modelo347["ready"] is True
    assert modelo347["asset_year"]["ready"] is False
    assert modelo347["asset_year"]["required_for_requested_scope"] is False
    assert modelo100["ready"] is False
    assert modelo100["asset_year"]["required_for_requested_scope"] is True
    assert any(
        row["kind"] == "annual_asset_schedule_unresolved"
        for row in modelo100["blockers"]
    )


def test_modelo390_gates_accept_explicit_zero_category_evidence(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        periods = _prepare_modelo390_year(db)
        for period_key in periods:
            _close_with_modelo303_evidence(db, period_key, _modelo303_values())

        report = assess_annual_readiness(db, year=2026, form_code="390")

    assert report["ready"] is True
    assert _gate(report, "quarterly_303_evidence")["status"] == "ready"
    assert _gate(report, "reverse_charge")["status"] == "not_required"
    assert _gate(report, "compensation_carryforward")["status"] == "ready"
    final_gate = _gate(report, "final_refund_or_compensation")
    assert final_gate["status"] == "not_required"
    assert final_gate["choice"] == "compensate"


def test_modelo390_reverse_charge_gate_compares_direct_quarterly_boxes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        periods = _prepare_modelo390_year(db)
        transaction = db.add_transaction(
            external_key="reverse-charge-service",
            period_key="2026-Q1",
            transaction_date="2026-02-01",
            booking_date="2026-02-01",
            entry_type="expense",
            description="Non-EU software service",
            amount_minor=10000,
            currency="EUR",
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="expense",
            tax_code="non_eu_service_expense",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=10000,
            deductible_vat_minor=2100,
            include_modelo130=True,
            include_modelo303=True,
        )
        q1 = _modelo303_values(
            **{
                "12": "100.00",
                "13": "21.00",
                "27": "21.00",
                "28": "100.00",
                "29": "21.00",
                "45": "21.00",
            }
        )
        _close_with_modelo303_evidence(db, periods[0], q1)
        for period_key in periods[1:]:
            _close_with_modelo303_evidence(db, period_key, _modelo303_values())

        matched = assess_annual_readiness(db, year=2026, form_code="390")
        bad_q1 = dict(q1)
        bad_q1["12"] = "0.00"
        _close_with_modelo303_evidence(
            db,
            periods[0],
            bad_q1,
            suffix="newer-mismatch",
        )
        mismatched = assess_annual_readiness(db, year=2026, form_code="390")

    matched_gate = _gate(matched, "reverse_charge")
    assert matched_gate["status"] == "ready"
    assert matched_gate["expected_annual_service_base_eur"] == "100.00"
    mismatch_gate = _gate(mismatched, "reverse_charge")
    assert mismatch_gate["status"] == "blocked"
    assert any("casilla 12" in reason for reason in mismatch_gate["reasons"])


def test_modelo390_compensation_gate_blocks_a_broken_quarter_chain(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        periods = _prepare_modelo390_year(db)
        _close_with_modelo303_evidence(
            db,
            periods[0],
            _modelo303_values(
                **{
                    "64": "-10.00",
                    "69": "-10.00",
                    "71": "-10.00",
                    "72": "10.00",
                    "compensation_carryforward": "10.00",
                }
            ),
        )
        for period_key in periods[1:]:
            _close_with_modelo303_evidence(db, period_key, _modelo303_values())

        report = assess_annual_readiness(db, year=2026, form_code="390")

    gate = _gate(report, "compensation_carryforward")
    assert gate["status"] == "blocked"
    assert any("opening compensation" in reason for reason in gate["reasons"])
    assert report["ready"] is False


def test_modelo390_derives_refund_choice_from_final_filed_modelo303(
    tmp_path: Path,
    capsys,
) -> None:
    database = tmp_path / "ledger.sqlite"
    with initialize(database) as db:
        periods = _prepare_modelo390_year(db)
        transaction = db.add_transaction(
            external_key="q4-domestic-input",
            period_key="2026-Q4",
            transaction_date="2026-12-01",
            booking_date="2026-12-01",
            entry_type="expense",
            description="Domestic professional expense",
            amount_minor=57619,
            currency="EUR",
            lifecycle_status="posted",
        )
        db.add_detailed_tax_treatment(
            transaction_id=transaction["transaction_id"],
            treatment_type="expense",
            tax_code="domestic_input",
            taxable_base_minor=47619,
            vat_minor=10000,
            deductible_irpf_minor=47619,
            deductible_vat_minor=10000,
            include_modelo130=True,
            include_modelo303=True,
        )
        for period_key in periods[:3]:
            _close_with_modelo303_evidence(db, period_key, _modelo303_values())
        _close_with_modelo303_evidence(
            db,
            periods[3],
            _modelo303_values(
                **{
                    "28": "476.19",
                    "29": "100.00",
                    "45": "100.00",
                    "64": "-100.00",
                    "69": "-100.00",
                    "71": "-100.00",
                    "73": "100.00",
                }
            ),
        )

        readiness = assess_annual_readiness(db, year=2026, form_code="390")

    final_gate = _gate(readiness, "final_refund_or_compensation")
    assert readiness["ready"] is True
    assert final_gate["status"] == "ready"
    assert final_gate["choice"] == "refund"

    assert main(
        [
            "calculate",
            "--db",
            str(database),
            "--form",
            "390",
            "--year",
            "2026",
        ]
    ) == 0
    calculation = json.loads(capsys.readouterr().out)
    assert calculation["values"]["98"] == "100.00"
    assert calculation["values"]["97"] == "0.00"

    with pytest.raises(CalculationBlocked, match="conflicts with filed Q4 Modelo 303"):
        main(
            [
                "calculate",
                "--db",
                str(database),
                "--form",
                "390",
                "--year",
                "2026",
                "--final-vat-settlement",
                "compensate",
            ]
        )
