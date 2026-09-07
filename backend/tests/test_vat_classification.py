from datetime import date
from decimal import Decimal
import sqlite3

import pytest

from autonomo_taxes.ledger_db import initialize, open as open_ledger_db
from autonomo_taxes.review_packet import ReviewPacketError, confirm_review_packet, prepare_review_work_item
from autonomo_taxes.tax_engine import CalculationBlocked, calculate_modelo303_rows, calculate_modelo390
from autonomo_taxes.tax_row_loader import load_tax_rows
from autonomo_taxes.vat_classification import LEGACY_VAT_CLASSIFICATION_WARNING
from autonomo_test_support.review_confirm import _invoice_fixture, _packet, _approve_decision
from autonomo_test_support.tax_engine import row


@pytest.mark.parametrize("investment,asset_id,box", [
    (False, "irpf-equipment", "29"),
    (True, "irpf-equipment", "31"),
    (False, "", "29"),
    (None, "irpf-equipment", "31"),
    (None, "", "29"),
])
def test_reviewed_iva_classification_overrides_irpf_link(investment, asset_id, box):
    purchase = row("synthetic-device", tax_code="domestic_input", include_modelo303=True,
                   asset_id=asset_id, vat_investment_good=investment)
    result = calculate_modelo303_rows([purchase], year=2026, quarter=2)
    assert result.values[box] == Decimal("21.00")
    assert result.values["45"] == Decimal("21.00")
    assert result.values["31" if box == "29" else "29"] == 0
    assert result.lineage[box] == (purchase.transaction_id,)
    assert (LEGACY_VAT_CLASSIFICATION_WARNING in result.warnings) == (investment is None)


def test_annual_report_retains_legacy_warning_and_current_goods_totals():
    reports = []
    carry = Decimal("0")
    for quarter in range(1, 5):
        purchase = row(f"device-{quarter}", tax_date=date(2026, quarter * 3, 1),
                       tax_code="domestic_input", include_modelo303=True,
                       asset_id="irpf-device", vat_investment_good=False)
        report = calculate_modelo303_rows([purchase], year=2026, quarter=quarter,
                                         previous_compensation=carry)
        reports.append(report)
        carry = report.values["compensation_carryforward"]
    annual = calculate_modelo390(reports, year=2026)
    assert annual.values["48"] == Decimal("400.00")
    assert annual.values["49"] == Decimal("84.00")
    assert annual.values["50"] == annual.values["51"] == 0
    assert annual.values["64"] == Decimal("84.00")
    assert LEGACY_VAT_CLASSIFICATION_WARNING not in annual.warnings
    # A saved legacy quarter carries its uncertainty into the annual summary.
    from dataclasses import replace
    reports[0] = replace(reports[0], warnings=reports[0].warnings + (LEGACY_VAT_CLASSIFICATION_WARNING,))
    assert LEGACY_VAT_CLASSIFICATION_WARNING in calculate_modelo390(reports, year=2026).warnings


@pytest.mark.parametrize("value", [None, "false", 0, 1])
def test_confirmation_requires_explicit_boolean_and_leaves_state_unchanged(tmp_path, value):
    fixture = _invoice_fixture(tmp_path)
    packet = _packet(fixture)
    _approve_decision(packet)
    packet["decision"]["tax_treatment"]["vat_investment_good"] = value
    with open_ledger_db(fixture["database"]) as db:
        before = db.connection.total_changes
        with pytest.raises(ReviewPacketError, match="vat_investment_good"):
            confirm_review_packet(db, packet, None)
        assert db.connection.total_changes == before


@pytest.mark.parametrize("investment", [False, True])
def test_confirmation_roundtrip_and_stale_packet(tmp_path, investment):
    fixture = _invoice_fixture(tmp_path)
    packet = _packet(fixture)
    _approve_decision(packet)
    packet["decision"]["tax_treatment"]["vat_investment_good"] = investment
    with open_ledger_db(fixture["database"]) as db:
        confirm_review_packet(db, packet, None)
        fresh = prepare_review_work_item(db, packet["review_id"])
        assert fresh["packet"]["decision"]["tax_treatment"]["vat_investment_good"] is investment
        assert f"IVA investment good: {str(investment).lower()}" in fresh["packet"]["state"]["tax_treatment"]["notes"]
        rows = load_tax_rows(db, date.today().year, include_approved_periods={fixture["period"]})
        assert rows[0].vat_investment_good is investment
        with pytest.raises(ReviewPacketError, match="stale"):
            confirm_review_packet(db, packet, None)


def test_schema_migration_keeps_legacy_treatment_and_source_hash(tmp_path):
    database = tmp_path / "legacy.sqlite"
    from unittest.mock import patch
    with patch("autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION", 21), initialize(database) as db:
        transaction = db.add_transaction(external_key="legacy-device", period_key="2026-Q2",
            transaction_date="2026-04-01", booking_date="2026-04-01", entry_type="expense",
            description="Synthetic equipment", amount_minor=12100, lifecycle_status="posted")
        treatment = db.add_detailed_tax_treatment(transaction_id=transaction["transaction_id"],
            treatment_type="accounting", tax_code="domestic_input", source_hash="legacy-evidence")
    with sqlite3.connect(database) as connection:
        connection.execute("ALTER TABLE tax_treatments DROP COLUMN vat_investment_good")
        connection.execute("PRAGMA user_version=20")
    with open_ledger_db(database, apply_migrations=True) as db:
        stored = dict(db.connection.execute("SELECT * FROM tax_treatments").fetchone())
        assert stored == treatment
        assert stored["vat_investment_good"] is None
        with pytest.raises(sqlite3.IntegrityError):
            db.connection.execute("UPDATE tax_treatments SET vat_investment_good=2")


def test_loader_rejects_conflicting_classifications(tmp_path):
    with initialize(tmp_path / "ledger.sqlite") as db:
        transaction = db.add_transaction(external_key="conflict-device", period_key="2026-Q2",
            transaction_date="2026-04-01", booking_date="2026-04-01", entry_type="expense",
            description="Synthetic device", amount_minor=12100, lifecycle_status="posted")
        for kind, flag in (("accounting", False), ("invoice_review", True)):
            db.add_detailed_tax_treatment(transaction_id=transaction["transaction_id"],
                treatment_type=kind, tax_code="domestic_input", vat_investment_good=flag)
        with pytest.raises(CalculationBlocked, match="Conflicting vat_investment_good"):
            load_tax_rows(db, 2026)


@pytest.mark.parametrize("flag", [False, True])
def test_older_update_callers_preserve_explicit_iva_choice(tmp_path, flag):
    with initialize(tmp_path / "ledger.sqlite") as db:
        transaction = db.add_transaction(external_key="update-device", period_key="2026-Q2",
            transaction_date="2026-04-01", booking_date="2026-04-01", entry_type="expense",
            description="Synthetic device", amount_minor=12100)
        arguments = dict(transaction_id=transaction["transaction_id"],
                         treatment_type="invoice_review", tax_code="domestic_input")
        treatment = db.add_detailed_tax_treatment(**arguments, vat_investment_good=flag)
        updated = db.add_detailed_tax_treatment(**arguments, notes="Unrelated legacy caller update",
                                              expected_row_version=treatment["row_version"])
        assert updated["vat_investment_good"] == int(flag)
        cleared = db.add_detailed_tax_treatment(**arguments, vat_investment_good=None,
                                              expected_row_version=updated["row_version"])
        assert cleared["vat_investment_good"] is None


@pytest.mark.parametrize("with_accounting_treatment", [False, True])
def test_posted_irpf_asset_is_current_iva_purchase_in_books_and_calculation(tmp_path, with_accounting_treatment):
    from autonomo_taxes.aeat_books import build_aeat_book_projection
    from autonomo_test_support.aeat_books import _profile_and_activity

    fixture = _invoice_fixture(tmp_path)
    with open_ledger_db(fixture["database"]) as db:
        activity = _profile_and_activity(db)
        transaction = dict(db.connection.execute("SELECT * FROM transactions").fetchone())
        db.set_transaction_business_activity(transaction["transaction_id"],
            business_activity_id=activity["business_activity_id"],
            expected_row_version=transaction["row_version"])
        db.upsert_counterparty_identity(counterparty_id=fixture["counterparty_id"],
            identity_kind="official_id", country_code="ES", identifier="TEST-TAX-ID-001",
            source_reference="Synthetic invoice", source_hash="synthetic-party-identity")
        asset = db.add_asset(asset_code="synthetic-test-device", cost_minor=10000,
            currency="EUR", depreciation_method="free", source_hash="synthetic-device",
            document_id=fixture["document_id"], acquisition_transaction_id=fixture["transaction_id"],
            placed_in_service_on=date.today().isoformat(), amortizable_base_minor=10000,
            iva_treatment="fully_deductible", business_use_ratio=1.0, annual_rate_basis_points=10000)
        packet = prepare_review_work_item(db, f"transaction:{fixture['transaction_id']}")["packet"]
        _approve_decision(packet)
        packet["decision"]["counterparty_changes"] = {"tax_id": "TEST-TAX-ID-001"}
        packet["decision"]["asset_decision"] = "asset"
        packet["decision"]["asset_id"] = asset["asset_id"]
        packet["decision"]["tax_treatment"].update(deductible_irpf_minor=0, include_modelo130=False)
        accounting = None
        if with_accounting_treatment:
            legacy = dict(packet["decision"]["tax_treatment"])
            legacy.pop("vat_investment_good")
            accounting = db.add_detailed_tax_treatment(
                transaction_id=fixture["transaction_id"], treatment_type="accounting", **legacy)
        confirm_review_packet(db, packet, None)
        approved = dict(db.connection.execute("SELECT * FROM transactions").fetchone())
        db.transition_transaction(approved["transaction_id"], lifecycle_status="posted",
                                  expected_row_version=approved["row_version"])
        rows = load_tax_rows(db, date.today().year)
        assert len(rows) == 1 and rows[0].asset_id == asset["asset_id"]
        assert rows[0].deductible_irpf_eur == 0
        report = calculate_modelo303_rows(rows, year=date.today().year,
                                         quarter=(date.today().month - 1) // 3 + 1)
        assert report.values["29"] == Decimal("21.00") and report.values["31"] == 0
        projection = build_aeat_book_projection(db, period_key=fixture["period"])
        assert len(projection["expense_rows"]) == 1, projection["blockers"]
        assert projection["expense_rows"][0]["bien_inversion"] == "N"
        assert LEGACY_VAT_CLASSIFICATION_WARNING not in projection["warnings"]
        if accounting is not None:
            db.add_detailed_tax_treatment(transaction_id=fixture["transaction_id"],
                treatment_type="accounting", **legacy, vat_investment_good=True,
                expected_row_version=accounting["row_version"])
            conflict = build_aeat_book_projection(db, period_key=fixture["period"])
            assert conflict["expense_rows"] == []
            assert any("Conflicting vat_investment_good" in issue["message"] for issue in conflict["blockers"])


@pytest.mark.parametrize("tax_code", ["eu_service_expense", "non_eu_service_expense", "import_service_expense"])
def test_services_cannot_be_reviewed_as_iva_investment_goods(tmp_path, tax_code):
    fixture = _invoice_fixture(tmp_path)
    packet = _packet(fixture)
    _approve_decision(packet)
    packet["decision"]["tax_treatment"].update(tax_code=tax_code, vat_investment_good=True)
    with open_ledger_db(fixture["database"]) as db:
        with pytest.raises(ReviewPacketError, match="service expense"):
            confirm_review_packet(db, packet, None)
        with pytest.raises(ValueError, match="service expense"):
            db.add_detailed_tax_treatment(transaction_id=fixture["transaction_id"],
                treatment_type="accounting", tax_code=tax_code, vat_investment_good=True)
    with pytest.raises(ValueError, match="service expense"):
        row("synthetic-service", tax_code=tax_code, vat_investment_good=True)
    # Legacy null remains readable; no silent reclassification of saved history.
    row("legacy-service", tax_code=tax_code, asset_id="legacy-asset")


@pytest.mark.parametrize("tax_code,current_box,asset_box,current_annual,asset_annual", [
    ("domestic_input", "29", "31", "49", "51"),
    ("import_goods_expense", "33", "35", "53", "55"),
    ("eu_goods_expense", "37", "39", "57", "59"),
])
@pytest.mark.parametrize("investment", [False, True])
def test_303_and_390_agree_for_goods_categories(tax_code, current_box, asset_box, current_annual, asset_annual, investment):
    reports = []
    carry = Decimal("0")
    for quarter in range(1, 5):
        purchases = [row("test-goods", tax_code=tax_code, include_modelo303=True,
                         asset_id="irpf-test-asset", vat_investment_good=investment)] if quarter == 2 else []
        report = calculate_modelo303_rows(purchases, year=2026, quarter=quarter,
                                         previous_compensation=carry)
        reports.append(report)
        carry = report.values["compensation_carryforward"]
    selected, other = (asset_box, current_box) if investment else (current_box, asset_box)
    assert reports[1].values[selected] == Decimal("21.00")
    assert reports[1].values[other] == 0
    annual = calculate_modelo390(reports, year=2026)
    selected, other = (asset_annual, current_annual) if investment else (current_annual, asset_annual)
    assert annual.values[selected] == annual.values["64"] == Decimal("21.00")
    assert annual.values[other] == 0
