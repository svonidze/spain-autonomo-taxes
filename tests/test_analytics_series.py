import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from autonomo_taxes.analytics_series import AnalyticsQuery, build_analytics
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.ledger_db import open as open_ledger_db
from autonomo_taxes.tax_engine import calculate_modelo130_rows
from autonomo_taxes.tax_row_loader import load_tax_rows

AS_OF = date(2026, 8, 31)


def _add_transaction(db: LedgerDB, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "external_key": overrides.get("external_key", "txn"),
        "period_key": "2026-Q3",
        "transaction_date": "2026-07-01",
        "booking_date": "2026-07-01",
        "entry_type": "income",
        "description": "synthetic",
        "amount_minor": 10000,
        "amount_eur_minor": 10000,
        "direction": "credit",
        "lifecycle_status": "posted",
    }
    payload.update(overrides)
    return db.add_transaction(**payload)


def _build(
    db_path: Path,
    *,
    period_key: str = "2026-Q3",
    as_of: date = AS_OF,
    year_forms: dict | None = None,
) -> dict:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        return build_analytics(
            connection,
            AnalyticsQuery(period_key=period_key, as_of=as_of),
            year_forms=year_forms or {},
        )
    finally:
        connection.close()


def _leaves(node: object, path: str) -> list[tuple[str, object]]:
    if isinstance(node, dict):
        found: list[tuple[str, object]] = []
        for key, value in node.items():
            found.extend(_leaves(value, f"{path}.{key}"))
        return found
    if isinstance(node, list):
        found = []
        for index, value in enumerate(node):
            found.extend(_leaves(value, f"{path}[{index}]"))
        return found
    return [(path, node)]


def _walk_minor_values(node: object, path: str = "") -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child_path = f"{path}.{key}" if path else str(key)
            if str(key).endswith("_minor"):
                found.extend(_leaves(value, child_path))
            else:
                found.extend(_walk_minor_values(value, child_path))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk_minor_values(value, f"{path}[{index}]"))
    return found


def test_business_result_status_policy_and_bucketing(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        _add_transaction(
            db,
            external_key="income-jan",
            period_key="2026-Q1",
            transaction_date="2026-01-15",
            booking_date="2026-01-15",
            amount_minor=100000,
            amount_eur_minor=100000,
        )
        income_feb = _add_transaction(
            db,
            external_key="income-feb-base",
            period_key="2026-Q1",
            transaction_date="2026-02-10",
            booking_date="2026-02-10",
            amount_minor=60500,
            amount_eur_minor=60500,
        )
        db.add_detailed_tax_treatment(
            transaction_id=income_feb["transaction_id"],
            treatment_type="income",
            tax_code="domestic_service_income",
            taxable_base_minor=50000,
            vat_minor=10500,
            include_modelo130=True,
            include_modelo303=True,
        )
        expense_backlog = _add_transaction(
            db,
            external_key="expense-approved-past",
            transaction_date="2026-07-05",
            booking_date="2026-07-05",
            entry_type="expense",
            direction="debit",
            amount_minor=12100,
            amount_eur_minor=12100,
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense_backlog["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            taxable_base_minor=10000,
            vat_minor=2100,
            deductible_irpf_minor=10000,
            deductible_vat_minor=2100,
        )
        expense_future = _add_transaction(
            db,
            external_key="expense-approved-future",
            transaction_date="2026-09-15",
            booking_date="2026-09-15",
            entry_type="expense",
            direction="debit",
            amount_minor=4400,
            amount_eur_minor=4400,
            lifecycle_status="approved",
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense_future["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=4000,
        )
        _add_transaction(
            db,
            external_key="income-posted-future",
            transaction_date="2026-09-20",
            booking_date="2026-09-20",
            amount_minor=70000,
            amount_eur_minor=70000,
        )
        _add_transaction(
            db,
            external_key="expense-review",
            transaction_date="2026-08-01",
            booking_date="2026-08-01",
            entry_type="expense",
            direction="debit",
            amount_minor=9900,
            amount_eur_minor=9900,
            lifecycle_status="needs_review",
        )
        _add_transaction(
            db,
            external_key="expense-void",
            transaction_date="2026-08-02",
            booking_date="2026-08-02",
            entry_type="expense",
            direction="debit",
            amount_minor=1,
            amount_eur_minor=1,
            lifecycle_status="void",
        )
        _add_transaction(
            db,
            external_key="income-usd-missing-fx",
            transaction_date="2026-03-03",
            booking_date="2026-03-03",
            amount_minor=5000,
            amount_eur_minor=None,
            currency="USD",
        )

    analytics = _build(db_path)
    business = analytics["datasets"]["business_result"]
    monthly = business["monthly"]

    assert monthly["buckets"] == [f"2026-{month:02d}" for month in range(1, 10)]
    assert monthly["actual"]["income_base_minor"] == [
        100000,
        50000,
        0,
        0,
        0,
        0,
        0,
        0,
        None,
    ]
    assert monthly["actual"]["deductible_expense_minor"] == [0] * 8 + [None]
    assert monthly["approved_unposted"]["deductible_expense_minor"] == [
        0,
        0,
        0,
        0,
        0,
        0,
        10000,
        0,
        0,
    ]
    assert monthly["approved_future"]["deductible_expense_minor"] == [0] * 8 + [4000]

    quarterly = business["quarterly"]
    assert quarterly["buckets"] == ["2026-Q1", "2026-Q2", "2026-Q3"]
    assert quarterly["actual"]["income_base_minor"] == [150000, 0, 0]
    for scope in ("actual", "approved_unposted", "approved_future"):
        for measure in ("income_base_minor", "deductible_expense_minor"):
            monthly_sum = sum(
                value or 0 for value in monthly[scope][measure]
            )
            quarterly_sum = sum(
                value or 0 for value in quarterly[scope][measure]
            )
            assert monthly_sum == quarterly_sum

    assert analytics["quality"]["future_posted_transaction_count"] == 1
    assert analytics["quality"]["missing_fx_transaction_count"] == 1

    cumulative = analytics["datasets"]["cumulative_net"]
    assert cumulative["buckets"] == monthly["buckets"]
    assert cumulative["actual_minor"][0] == 100000
    assert cumulative["actual_minor"][7] == 150000
    assert cumulative["actual_minor"][8] is None
    assert cumulative["projected_minor"][8] == 150000 - 10000 - 4000


def test_as_of_boundary_counts_as_actual(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        _add_transaction(
            db,
            external_key="income-as-of",
            transaction_date=AS_OF.isoformat(),
            booking_date=AS_OF.isoformat(),
            amount_minor=1000,
            amount_eur_minor=1000,
        )
    analytics = _build(db_path)
    monthly = analytics["datasets"]["business_result"]["monthly"]
    assert monthly["actual"]["income_base_minor"][7] == 1000
    assert analytics["quality"]["future_posted_transaction_count"] == 0


def test_signed_corrections_sum_through(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        original = _add_transaction(
            db,
            external_key="expense-original",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="expense",
            direction="debit",
            amount_minor=5000,
            amount_eur_minor=5000,
        )
        db.add_detailed_tax_treatment(
            transaction_id=original["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=5000,
        )
        correction = _add_transaction(
            db,
            external_key="expense-correction",
            transaction_date="2026-07-10",
            booking_date="2026-07-10",
            entry_type="expense",
            direction="debit",
            amount_minor=-2000,
            amount_eur_minor=-2000,
            correction_of_transaction_id=original["transaction_id"],
            correction_kind="correcting",
        )
        db.add_detailed_tax_treatment(
            transaction_id=correction["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=-2000,
        )
    analytics = _build(db_path)
    monthly = analytics["datasets"]["business_result"]["monthly"]
    assert monthly["actual"]["deductible_expense_minor"][6] == 3000
    structure = analytics["datasets"]["expense_structure"]["buckets"]
    assert structure[0]["concept"] == "unclassified"
    assert structure[0]["gross_minor"] == 3000
    assert structure[0]["deductible_minor"] == 3000


def test_multiple_treatments_deduplicate_and_conflicts_are_excluded(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        duplicated = _add_transaction(
            db,
            external_key="expense-two-treatments",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="expense",
            direction="debit",
            amount_minor=10000,
            amount_eur_minor=10000,
        )
        db.add_detailed_tax_treatment(
            transaction_id=duplicated["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=8000,
        )
        conflicted = _add_transaction(
            db,
            external_key="expense-conflicting",
            transaction_date="2026-07-02",
            booking_date="2026-07-02",
            entry_type="expense",
            direction="debit",
            amount_minor=7000,
            amount_eur_minor=7000,
        )
        db.add_detailed_tax_treatment(
            transaction_id=conflicted["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=7000,
        )
    tamper = sqlite3.connect(db_path)
    try:
        for treatment_id, transaction_id, deductible in (
            ("raw-duplicate", duplicated["transaction_id"], 8000),
            ("raw-conflict", conflicted["transaction_id"], 1),
        ):
            tamper.execute(
                """
                INSERT INTO tax_treatments (
                    treatment_id, transaction_id, treatment_type, jurisdiction,
                    deductible_irpf_minor, source_hash, created_at, updated_at
                ) VALUES (?, ?, 'expense', 'ES-second', ?, 'raw', '2026-07-02T00:00:00+00:00', '2026-07-02T00:00:00+00:00')
                """,
                (treatment_id, transaction_id, deductible),
            )
        tamper.commit()
    finally:
        tamper.close()
    analytics = _build(db_path)
    monthly = analytics["datasets"]["business_result"]["monthly"]
    assert monthly["actual"]["deductible_expense_minor"][6] == 8000
    assert analytics["quality"]["conflicting_treatment_count"] == 1


def test_business_result_matches_modelo130_engine_arithmetic(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        income = _add_transaction(
            db,
            external_key="income-engine",
            period_key="2026-Q1",
            transaction_date="2026-02-01",
            booking_date="2026-02-01",
            amount_minor=121000,
            amount_eur_minor=121000,
        )
        db.add_detailed_tax_treatment(
            transaction_id=income["transaction_id"],
            treatment_type="income",
            tax_code="domestic_service_income",
            taxable_base_minor=100000,
            vat_minor=21000,
            include_modelo130=True,
            include_modelo303=True,
        )
        expense = _add_transaction(
            db,
            external_key="expense-engine",
            transaction_date="2026-07-15",
            booking_date="2026-07-15",
            entry_type="expense",
            direction="debit",
            amount_minor=24200,
            amount_eur_minor=24200,
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            taxable_base_minor=20000,
            vat_minor=4200,
            deductible_irpf_minor=20000,
            deductible_vat_minor=4200,
            include_modelo130=True,
            include_modelo303=True,
        )
        rows = load_tax_rows(db, 2026)
    _, calculation = calculate_modelo130_rows(
        rows,
        year=2026,
        quarter=3,
        difficult_expenses_rate=Decimal("0.05"),
        include_difficult_expenses=False,
    )
    analytics = _build(db_path)
    quarterly = analytics["datasets"]["business_result"]["quarterly"]
    income_ytd = sum(value or 0 for value in quarterly["actual"]["income_base_minor"])
    deductible_ytd = sum(
        value or 0 for value in quarterly["actual"]["deductible_expense_minor"]
    )
    assert income_ytd == int(calculation.values["01"] * 100)
    assert deductible_ytd == int(calculation.values["02"] * 100)


def test_tax_points_translate_form_values(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path):
        pass
    year_forms = {
        "2026-Q1": {
            "period": {
                "period_key": "2026-Q1",
                "status": "closed",
                "starts_on": "2026-01-01",
                "ends_on": "2026-03-31",
                "amendment_period_key": None,
                "amendment_reason": None,
            },
            "obligations": [],
            "forms": {
                "modelo130": {"display_state": "filed", "values": {"19": "263.91"}},
                "modelo303": {
                    "display_state": "filed",
                    "values": {
                        "27": "100.00",
                        "45": "140.74",
                        "71": "-40.74",
                        "72": "40.74",
                        "73": "0.00",
                        "compensation_carryforward": "40.74",
                    },
                },
            },
        },
        "2026-Q2": {
            "period": None,
            "obligations": [],
            "forms": {
                "modelo130": {"display_state": "filed_without_values", "values": {}},
                "modelo303": {
                    "display_state": "filed",
                    "values": {"71": "-10.00", "73": "10.00", "72": "0.00"},
                },
            },
        },
    }
    analytics = _build(db_path, year_forms=year_forms)

    tax_points = analytics["datasets"]["quarterly_tax_due"]["points"]
    first = tax_points[0]
    assert first["modelo130"] == {
        "result_minor": 26391,
        "payable_minor": 26391,
        "source": "filed",
    }
    assert first["modelo303"]["result_minor"] == -4074
    assert first["modelo303"]["payable_minor"] == 0
    assert first["modelo303"]["carryforward_minor"] == 4074
    assert first["modelo303"]["disposition"] == "compensate"

    second = tax_points[1]
    assert second["modelo130"]["result_minor"] is None
    assert second["modelo130"]["payable_minor"] is None
    assert second["modelo130"]["source"] == "filed_without_values"
    assert second["modelo303"]["disposition"] == "refund"
    assert second["modelo303"]["refund_requested_minor"] == 1000

    iva = analytics["datasets"]["iva_position"]["points"]
    assert iva[0]["output_vat_minor"] == 10000
    assert iva[0]["deductible_input_vat_minor"] == 14074
    assert iva[1]["output_vat_minor"] is None

    periods = analytics["periods"]
    assert periods[0]["status"] == "closed"
    assert periods[1]["status"] == "missing"


def test_reserve_bullet_states(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path):
        pass

    def forms(values_130: dict | None, values_303: dict | None) -> dict:
        return {
            "modelo130": {
                "display_state": "preview" if values_130 else "unavailable",
                "values": values_130 or {},
            },
            "modelo303": {
                "display_state": "preview" if values_303 else "unavailable",
                "values": values_303 or {},
            },
        }

    due = [
        {"obligation_code": "130", "determination": "due"},
        {"obligation_code": "303", "determination": "due"},
    ]
    year_forms = {
        "2026-Q3": {
            "period": None,
            "obligations": due,
            "forms": forms({"19": "263.91"}, {"71": "-40.74"}),
        }
    }
    bullet = _build(db_path, year_forms=year_forms)["datasets"]["reserve_bullet"]
    assert bullet["status"] == "not_checked"
    assert bullet["required_tax_minor"] == 26391
    assert bullet["buffer_minor"] == 10000
    assert bullet["recommended_reserve_minor"] == 36391
    assert bullet["available_minor"] is None
    assert bullet["tax_shortfall_minor"] is None
    forms_by_code = {row["form"]: row for row in bullet["forms"]}
    assert forms_by_code["130"]["payable_minor"] == 26391
    assert forms_by_code["303"]["payable_minor"] == 0
    assert forms_by_code["303"]["status"] == "no_payment"

    no_due = {"2026-Q3": {"period": None, "obligations": [], "forms": forms(None, None)}}
    bullet = _build(db_path, year_forms=no_due)["datasets"]["reserve_bullet"]
    assert bullet["status"] == "not_required"
    assert bullet["required_tax_minor"] == 0

    blocked = {
        "2026-Q3": {
            "period": None,
            "obligations": [{"obligation_code": "130", "determination": "due"}],
            "forms": forms(None, None),
        }
    }
    bullet = _build(db_path, year_forms=blocked)["datasets"]["reserve_bullet"]
    assert bullet["status"] == "calculation_blocked"


def test_expense_structure_concept_buckets(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        classified = _add_transaction(
            db,
            external_key="expense-g45",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="expense",
            direction="debit",
            amount_minor=30000,
            amount_eur_minor=30000,
        )
        db.add_detailed_tax_treatment(
            transaction_id=classified["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=20000,
            aeat_expense_concept="G45",
        )
        _add_transaction(
            db,
            external_key="expense-unclassified",
            transaction_date="2026-07-02",
            booking_date="2026-07-02",
            entry_type="expense",
            direction="debit",
            amount_minor=5000,
            amount_eur_minor=5000,
            lifecycle_status="approved",
        )
    analytics = _build(db_path)
    structure = analytics["datasets"]["expense_structure"]["buckets"]
    assert [bucket["concept"] for bucket in structure] == ["G45", "unclassified"]
    assert structure[0] == {
        "concept": "G45",
        "gross_minor": 30000,
        "deductible_minor": 20000,
        "non_deductible_minor": 10000,
    }
    assert analytics["quality"]["unclassified_expense_count"] == 1


def test_review_aging_buckets_by_created_at(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        _add_transaction(
            db,
            external_key="review-fresh",
            transaction_date="2026-08-01",
            booking_date="2026-08-01",
            entry_type="expense",
            direction="debit",
            amount_minor=9900,
            amount_eur_minor=9900,
            lifecycle_status="needs_review",
        )
        old = _add_transaction(
            db,
            external_key="approved-old",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="expense",
            direction="debit",
            amount_minor=5000,
            amount_eur_minor=5000,
            lifecycle_status="approved",
        )
    tamper = sqlite3.connect(db_path)
    try:
        tamper.execute(
            "UPDATE transactions SET created_at = ? WHERE transaction_id = ?",
            ("2026-07-01T00:00:00+00:00", old["transaction_id"]),
        )
        tamper.commit()
    finally:
        tamper.close()

    analytics = _build(db_path)
    aging = analytics["datasets"]["review_aging"]
    assert aging["buckets"] == ["0-7", "8-30", "31-90", "90+"]
    assert aging["counts"]["needs_review"][0] == 1
    assert aging["counts"]["approved_unposted"] == [0, 0, 1, 0]
    assert aging["known_amount_minor"]["approved_unposted"][2] == 5000
    assert aging["approved_overdue_count"] == 1


def test_ytd_comparison_previous_year_null_when_absent(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        _add_transaction(
            db,
            external_key="income-current",
            transaction_date="2026-05-01",
            booking_date="2026-05-01",
            period_key="2026-Q2",
            amount_minor=80000,
            amount_eur_minor=80000,
        )
    analytics = _build(db_path)
    comparison = analytics["datasets"]["ytd_comparison"]
    assert comparison["through_month"] == 8
    assert comparison["current_year"]["taxable_income_minor"] == 80000
    assert comparison["current_year"]["net_minor"] == 80000
    assert comparison["previous_year"]["taxable_income_minor"] is None

    with open_ledger_db(db_path) as db:
        _add_transaction(
            db,
            external_key="income-previous",
            period_key="2025-Q2",
            transaction_date="2025-04-01",
            booking_date="2025-04-01",
            amount_minor=50000,
            amount_eur_minor=50000,
        )
        _add_transaction(
            db,
            external_key="income-previous-late",
            period_key="2025-Q4",
            transaction_date="2025-11-01",
            booking_date="2025-11-01",
            amount_minor=99999,
            amount_eur_minor=99999,
        )
    analytics = _build(db_path)
    comparison = analytics["datasets"]["ytd_comparison"]
    assert comparison["previous_year"]["taxable_income_minor"] == 50000


def test_amortization_respects_include_in_books(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        asset = db.add_asset(
            asset_code="LAPTOP-1",
            cost_minor=260000,
            currency="EUR",
            depreciation_method="straight_line",
            source_hash="asset-hash",
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2026-Q1",
            amount_minor=6500,
            source_hash="amort-q1",
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2026-Q2",
            amount_minor=6500,
            source_hash="amort-q2",
        )
        db.add_amortization_entry(
            asset_id=asset["asset_id"],
            period_key="2026-Q3",
            amount_minor=990000,
            source_hash="amort-annual",
            entry_kind="annual_evidence",
        )
    tamper = sqlite3.connect(db_path)
    try:
        tamper.execute(
            "UPDATE assets SET description = 'Laptop' WHERE asset_id = ?",
            (asset["asset_id"],),
        )
        tamper.commit()
    finally:
        tamper.close()
    analytics = _build(db_path)
    amortization = analytics["datasets"]["amortization"]
    assert amortization["includes"] == "include_in_books_only"
    assert [point["period_key"] for point in amortization["points"]] == [
        "2026-Q1",
        "2026-Q2",
    ]
    assert amortization["points"][0]["total_minor"] == 6500
    assert amortization["points"][0]["assets"] == [
        {
            "asset_id": asset["asset_id"],
            "label": "Laptop",
            "amount_minor": 6500,
        }
    ]


def test_every_minor_value_is_int_or_none(tmp_path: Path) -> None:
    db_path = tmp_path / "autonomo.sqlite"
    with LedgerDB.initialize(db_path) as db:
        expense = _add_transaction(
            db,
            external_key="expense-typed",
            transaction_date="2026-07-01",
            booking_date="2026-07-01",
            entry_type="expense",
            direction="debit",
            amount_minor=12100,
            amount_eur_minor=12100,
        )
        db.add_detailed_tax_treatment(
            transaction_id=expense["transaction_id"],
            treatment_type="expense",
            tax_code="G03",
            deductible_irpf_minor=10000,
            aeat_expense_concept="G45",
        )
    year_forms = {
        "2026-Q3": {
            "period": None,
            "obligations": [{"obligation_code": "130", "determination": "due"}],
            "forms": {
                "modelo130": {"display_state": "preview", "values": {"19": "10.00"}},
                "modelo303": {"display_state": "unavailable", "values": {}},
            },
        }
    }
    analytics = _build(db_path, year_forms=year_forms)
    checked = _walk_minor_values(analytics)
    assert checked
    for path, value in checked:
        assert value is None or type(value) is int, (path, value)
