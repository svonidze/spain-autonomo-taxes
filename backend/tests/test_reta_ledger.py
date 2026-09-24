from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from pathlib import Path

import pytest
from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION, LedgerDB
from autonomo_taxes.reta_ledger import _plus_year, ledger_bracket_check
from autonomo_taxes.tax_engine import CalculationBlocked
from autonomo_taxes.tax_row_loader import load_tax_rows
from autonomo_test_support.paths import REPO_ROOT

TABLE_PATH = REPO_ROOT / "reference" / "reta" / "2026.json"
AS_OF = date(2026, 7, 15)
THROUGH = date(2026, 6, 30)


def _ledger(tmp_path: Path, *, alta: str = "2025-06-01") -> Path:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        profile = db.upsert_taxpayer_profile(
            tax_id="X0000000A", full_name="Example Taxpayer", source_hash="profile-source"
        )
        db.upsert_business_activity(
            taxpayer_profile_id=profile["taxpayer_profile_id"],
            activity_key="software-development",
            aeat_activity_code="A",
            aeat_activity_type="05",
            iae_section="2",
            iae_group_epigraph="763",
            description="Software development",
            starts_on=alta,
            source_reference="Synthetic Modelo 036",
            source_hash="activity-source",
        )
        db.import_reta_table(TABLE_PATH.read_bytes())
    return database


def _row(
    db: LedgerDB,
    key: str,
    day: str,
    kind: str,
    minor: int,
    *,
    status: str = "posted",
    concept: str | None = None,
    document_id: str | None = None,
) -> dict:
    row = db.add_transaction(
        external_key=key,
        period_key=f"{day[:4]}-Q{(int(day[5:7]) + 2) // 3}",
        transaction_date=day,
        booking_date=day,
        entry_type=kind,
        description=f"Synthetic {kind}",
        amount_minor=minor,
        lifecycle_status=status,
        document_id=document_id,
    )
    db.add_detailed_tax_treatment(
        transaction_id=row["transaction_id"],
        treatment_type=kind,
        tax_code="outside_scope" if kind == "income" else "domestic_expense",
        taxable_base_minor=minor,
        deductible_irpf_minor=None if kind == "income" else minor,
        aeat_expense_concept=concept,
        include_modelo130=True,
    )
    return row


def _posted_half_year(db: LedgerDB, income_minor: int) -> None:
    _row(db, "social-security", "2026-02-10", "expense", 60000, concept="G45")
    _row(db, "income", "2026-03-15", "income", income_minor)
    _row(db, "software", "2026-04-10", "expense", 40000, concept="G24")


def _base(db: LedgerDB, effective_from: str, regime: str = "base", base: int | None = 95098, kind: str = "individual"):
    return db.add_reta_base_election(
        effective_from=effective_from,
        regime=regime,
        monthly_base_minor=None if regime == "tarifa_plana" else base,
        worker_kind=kind,
        source_reference=f"Synthetic TGSS resolution {effective_from}",
    )


def _check(database: Path, **kwargs) -> dict:
    with LedgerDB.open(database, read_only=True) as db:
        return ledger_bracket_check(db, year=2026, as_of=AS_OF, **{"through": THROUGH, **kwargs})


def test_migrates_schema_25_to_26_and_keeps_elections_append_only(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        election = _base(db, "2026-01-01")
        db.void_reta_base_election(election_id=election["election_id"], source_reference="Synthetic typo")
        for statement in (
            "UPDATE reta_base_elections SET monthly_base_minor = 100000",
            "DELETE FROM reta_base_elections",
            "UPDATE reta_base_election_voids SET source_reference = 'other'",
            "DELETE FROM reta_base_election_voids",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                db.connection.execute(statement)
            db.connection.rollback()
        # A base row needs a base and a tarifa plana row must not have one.
        for regime, base in (("base", None), ("tarifa_plana", 95098)):
            with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
                db.connection.execute(
                    "INSERT INTO reta_base_elections VALUES ('x', ?, '2026-03-01', ?, ?, "
                    "'individual', 'ref', ?, '2026-01-01')",
                    (election["taxpayer_profile_id"], regime, base, "0" * 64),
                )
            db.connection.rollback()
        assert set(db.table_counts()) >= {"reta_rate_tables", "reta_base_elections", "reta_base_election_voids"}
        for name in ("reta_base_election_voids", "reta_base_elections", "reta_rate_tables"):
            db.connection.execute(f"DROP TABLE {name}")
        db.connection.execute("PRAGMA user_version = 25")
        db.connection.commit()

    with LedgerDB.open(database, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION == 26
        assert db.list_reta_base_elections() == [] and db.reta_rate_table(2026) is None
        triggers = {
            row[0]
            for row in db.connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            if row[0].startswith("reta_")
        }
    assert triggers == {
        f"{table}_no_{action}"
        for table in ("reta_base_elections", "reta_base_election_voids")
        for action in ("update", "delete")
    }


def test_table_import_is_validated_idempotent_and_byte_exact(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        again = db.import_reta_table(TABLE_PATH.read_bytes())
        assert again["changed"] is False
        assert again["payload_json"].encode("utf-8") == TABLE_PATH.read_bytes()
        assert again["source_hash"] == hashlib.sha256(TABLE_PATH.read_bytes()).hexdigest()
        assert again["source_url"] == "https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-7296"
        broken = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
        broken["total_rate_bp"] = 3100
        with pytest.raises(ValueError, match="rate components sum"):
            db.import_reta_table(json.dumps(broken).encode("utf-8"))
        assert db.reta_rate_table(2026)["source_hash"] == again["source_hash"]


def test_base_elections_follow_rd_2064_1995_article_45_1(tmp_path: Path) -> None:
    database = _ledger(tmp_path, alta="2025-03-10")
    with LedgerDB.open(database) as db:
        with pytest.raises(ValueError, match="Import the 2025 RETA table"):
            _base(db, "2025-03-10")
        # The alta row may start on any day; a tarifa plana needs no base or table.
        first = _base(db, "2025-03-10", "tarifa_plana")
        assert first["monthly_base_minor"] is None
        assert _base(db, "2025-03-10", "tarifa_plana") == first
        with pytest.raises(ValueError, match="already starts on 2025-03-10"):
            _base(db, "2025-03-10", "tarifa_plana", kind="colaborador")
        with pytest.raises(ValueError, match="needs a monthly base"):
            _base(db, "2026-03-10", base=None)
        with pytest.raises(ValueError, match="tarifa plana row has none"):
            db.add_reta_base_election(
                effective_from="2026-03-10", regime="tarifa_plana", monthly_base_minor=95098,
                worker_kind="individual", source_reference="Synthetic TGSS resolution",
            )
        with pytest.raises(ValueError, match="exceeds the 2026 maximum base 510120"):
            _base(db, "2026-03-10", base=510121)
        # The row that ends a tarifa plana may start on any day, later ones may not.
        _base(db, "2026-03-10", base=510120)
        for day in ("2026-04-01", "2026-05-02"):
            with pytest.raises(ValueError, match="art. 45.1"):
                _base(db, day)
        with pytest.raises(ValueError, match="date order"):
            _base(db, "2026-03-01")
        assert _base(db, "2026-05-01")["effective_from"] == "2026-05-01"
        assert [row["effective_from"] for row in db.list_reta_base_elections()] == [
            "2025-03-10", "2026-03-10", "2026-05-01",
        ]


def test_void_corrects_a_typo_and_lets_a_missed_resolution_in(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        typo = _base(db, "2026-01-01", base=59098)
        with pytest.raises(ValueError, match="void it first"):
            _base(db, "2026-01-01")
        void = db.void_reta_base_election(election_id=typo["election_id"], source_reference="Typo in base")
        assert db.void_reta_base_election(election_id=typo["election_id"], source_reference="Typo in base") == void
        with pytest.raises(ValueError, match="already void"):
            db.void_reta_base_election(election_id=typo["election_id"], source_reference="Other")
        with pytest.raises(ValueError, match="Unknown RETA base election"):
            db.void_reta_base_election(election_id="missing", source_reference="Other")
        fixed = _base(db, "2026-01-01")
        may = _base(db, "2026-05-01", base=100000)
        # A resolution for March turns up after May was recorded.
        with pytest.raises(ValueError, match="date order"):
            _base(db, "2026-03-01", base=96000)
        db.void_reta_base_election(election_id=may["election_id"], source_reference="Re-entered after March")
        _base(db, "2026-03-01", base=96000)
        _base(db, "2026-05-01", base=100000)
        assert [
            (row["effective_from"], row["monthly_base_minor"]) for row in db.list_reta_base_elections()
        ] == [("2026-01-01", 95098), ("2026-03-01", 96000), ("2026-05-01", 100000)]
        assert db.list_reta_base_elections()[0]["election_id"] == fixed["election_id"]
        assert db.table_counts()["reta_base_elections"] == 5
        assert db.table_counts()["reta_base_election_voids"] == 2


def test_a_re_alta_row_may_start_mid_month(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        profile_id = db.list_business_activities()[0]["taxpayer_profile_id"]
        db.upsert_business_activity(
            taxpayer_profile_id=profile_id,
            activity_key="consulting",
            aeat_activity_code="A",
            aeat_activity_type="05",
            iae_section="2",
            iae_group_epigraph="763",
            description="Consulting",
            starts_on="2026-04-15",
            source_reference="Synthetic Modelo 036",
            source_hash="re-alta-source",
        )
        _base(db, "2026-01-01")
        with pytest.raises(ValueError, match="art. 45.1"):
            _base(db, "2026-04-16")
        assert _base(db, "2026-04-15")["effective_from"] == "2026-04-15"


def test_posted_income_with_contributions_added_back_sits_in_the_tramo(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
        _base(db, "2026-01-01")

    result = _check(database)

    # 9,000 - 1,000 expenses - 5 % difficult (400) = 7,600 IRPF net; + 600 G45.
    assert result["income"] == {
        "irpf_net_income_minor": 760000,
        "contributions_added_back_minor": 60000,
        "generic_deduction_bp": 700,
        "rendimiento_computable_minor": 762600,
        "regularisable_days": 181,
        "monthly_average_minor": 126398,
    }
    assert (result["status"], result["reasons"]) == ("ok", [])
    assert (result["bracket"]["table"], result["bracket"]["tramo"]) == ("general", 1)
    assert result["estimated_additional_minor"] == result["estimated_refund_minor"] == 0
    assert result["ledger"] == {"unposted_transaction_ids": [], "missing_periods": [], "blocked_detail": None}


def test_higher_posted_income_reads_below_the_bracket(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 2000000)
        _base(db, "2026-01-01")

    result = _check(database)

    assert result["status"] == "below_bracket"
    assert (result["bracket"]["table"], result["bracket"]["tramo"]) == ("general", 8)
    # (1,437.91 - 950.98) x 6 months x 31.5 %.
    assert result["estimated_additional_minor"] == 92030


def test_unposted_row_in_the_window_makes_the_check_unknown(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
        pending = _row(db, "pending", "2026-05-20", "income", 500000, status="needs_review")
        _row(db, "after-window", "2026-07-05", "income", 500000, status="needs_review")
        _base(db, "2026-01-01")

    result = _check(database)

    assert (result["status"], result["reasons"]) == ("unknown", ["unposted_rows_in_window"])
    assert result["ledger"]["unposted_transaction_ids"] == [pending["transaction_id"]]
    assert result["income"]["monthly_average_minor"] is None
    assert result["estimated_additional_minor"] is None


def test_tarifa_plana_days_leave_the_divisor_and_months_13_to_24_are_unverified(tmp_path: Path) -> None:
    database = _ledger(tmp_path, alta="2025-03-10")
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
        _base(db, "2025-03-10", "tarifa_plana")

    extended = _check(database)
    assert extended["status"] == "unknown"
    assert "tarifa_plana_extension_unverified" in extended["reasons"]

    with LedgerDB.open(database) as db:
        _base(db, "2026-03-10", base=130000)
    result = _check(database)

    assert (result["status"], result["reasons"]) == ("ok", [])
    assert result["income"]["regularisable_days"] == 113
    march = next(row for row in result["months"] if row["month"] == "2026-03")
    assert (march["alta_days"], march["tarifa_plana_days"], march["regularisable_days"]) == (31, 9, 22)
    assert [row["month"] for row in result["months"]] == [f"2026-0{month}" for month in range(1, 7)]


def test_a_tarifa_plana_extension_recorded_from_month_13_counts_as_verified(tmp_path: Path) -> None:
    database = _ledger(tmp_path, alta="2025-03-10")
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
        _base(db, "2025-03-10", "tarifa_plana")
        _base(db, "2026-03-10", "tarifa_plana")

    result = _check(database)

    # Months 11 to 16 are all tarifa plana, so nothing is regularisable yet.
    assert (result["status"], result["reasons"]) == ("unknown", ["window_empty"])
    assert result["income"]["regularisable_days"] == 0
    assert _plus_year(date(2024, 2, 29)) == date(2025, 3, 1)


def test_filed_history_rows_count_as_posted(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        _row(db, "social-security", "2026-02-10", "expense", 60000, concept="G45")
        _row(db, "software", "2026-04-10", "expense", 40000, concept="G24")
        batch = db.add_import_batch(source_name="xolo_source_book_rows", source_hash="synthetic-book-hash")
        document = db.upsert_document(
            external_key="synthetic-book-invoice",
            import_batch_id=batch["import_batch_id"],
            document_type="ingresos_book",
            document_number="SYN-1",
            issued_on="2026-03-15",
            period_key="2026-Q1",
            lifecycle_status="approved",
            source_hash="synthetic-book-document-hash",
        )
        db.add_document_source(
            document_id=document["document_id"],
            import_batch_id=batch["import_batch_id"],
            source_book_line_id="synthetic-row-1",
            source_file="Synthetic_book_2026.xlsx",
            source_row_number="7",
            source_hash="synthetic-row-hash",
        )
        _row(db, "income", "2026-03-15", "income", 900000, status="approved", document_id=document["document_id"])
        db.create_filing_snapshot(
            "2026-Q1", status="baseline", filed_on="2026-04-20", payload={"form": "130"}
        )
        db.add_transaction(
            external_key="verify-adjustment",
            period_key="2026-Q2",
            transaction_date="2026-05-01",
            booking_date="2026-05-01",
            entry_type="verify_history_adjustment",
            description="Synthetic replay adjustment",
            amount_minor=100,
            lifecycle_status="approved",
        )
        _base(db, "2026-01-01")

    result = _check(database)

    assert (result["status"], result["reasons"]) == ("ok", [])
    assert result["income"]["irpf_net_income_minor"] == 760000
    assert result["ledger"]["unposted_transaction_ids"] == []


def test_a_table_re_import_below_a_recorded_base_is_unknown(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    lowered = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    lowered["maximum_base_minor"] = 500000
    for tramo in lowered["tramos"]:
        tramo["max_base_minor"] = min(tramo["max_base_minor"], 500000)
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
        _base(db, "2026-01-01", base=510120)
        assert db.import_reta_table(json.dumps(lowered).encode("utf-8"))["changed"] is True

    result = _check(database)

    assert (result["status"], result["reasons"]) == ("unknown", ["base_above_table_maximum"])
    assert result["estimated_refund_minor"] is None


def test_an_empty_concept_treatment_does_not_hide_g45(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        row = _row(db, "social-security", "2026-02-10", "expense", 60000, concept="G45")
        db.add_detailed_tax_treatment(
            transaction_id=row["transaction_id"], treatment_type="accounting", tax_code="domestic_expense"
        )
        (loaded,) = load_tax_rows(db, 2026)
    assert loaded.aeat_expense_concept == "G45"


def test_ledger_side_blockers_are_reason_codes(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        _row(db, "income", "2026-03-15", "income", 900000)
        _base(db, "2026-01-01")
        _base(db, "2026-05-01", kind="colaborador")

    result = _check(database)

    assert result["status"] == "unknown"
    assert {"period_missing", "worker_kind_changed"} <= set(result["reasons"])
    assert result["ledger"]["missing_periods"] == ["2026-Q2"]
    assert result["worker_kind"] == "colaborador"


def test_conflicting_expense_concepts_block_the_ledger(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
        row = _row(db, "fee", "2026-05-10", "expense", 1000, concept="G45")
        db.add_detailed_tax_treatment(
            transaction_id=row["transaction_id"],
            treatment_type="accounting",
            tax_code="domestic_expense",
            aeat_expense_concept="G24",
        )
        _base(db, "2026-01-01")
        with pytest.raises(CalculationBlocked, match="aeat_expense_concept"):
            load_tax_rows(db, 2026)

    result = _check(database)

    assert (result["status"], result["reasons"]) == ("unknown", ["ledger_blocked"])
    assert "aeat_expense_concept" in result["ledger"]["blocked_detail"]


def test_through_defaults_to_the_last_month_end_before_as_of(tmp_path: Path) -> None:
    database = _ledger(tmp_path)
    with LedgerDB.open(database, read_only=True) as db:
        assert ledger_bracket_check(db, year=2026, as_of=AS_OF)["through"] == "2026-06-30"
        assert ledger_bracket_check(db, year=2026, as_of=date(2027, 2, 1))["through"] == "2026-12-31"
        with pytest.raises(ValueError, match="No month of 2026 has ended"):
            ledger_bracket_check(db, year=2026, as_of=date(2026, 1, 20))


def test_cli_imports_records_lists_and_checks(tmp_path: Path, capsys) -> None:
    database = _ledger(tmp_path)
    capsys.readouterr()
    assert main(["reta", "table", "import", "--db", str(database), "--input", str(TABLE_PATH)]) == 0
    imported = json.loads(capsys.readouterr().out)
    assert (imported["year"], imported["changed"]) == (2026, False)
    assert "payload_json" not in imported

    resolution = tmp_path / "synthetic-resolution.txt"
    resolution.write_bytes(b"synthetic TGSS resolution")
    command = [
        "reta", "base", "add", "--db", str(database), "--effective-from", "2026-01-01",
        "--regime", "base", "--worker-kind", "individual",
        "--source-reference", "Synthetic CSV 0001", "--source-file", str(resolution),
    ]
    for text in ("950,985", "1.166,70", "0"):
        with pytest.raises(ValueError, match="at most two decimals"):
            main([*command, "--monthly-base", text])
    assert main([*command, "--monthly-base", "950,99"]) == 0
    typo = json.loads(capsys.readouterr().out)
    assert main([
        "reta", "base", "void", "--db", str(database), "--election-id", typo["election_id"],
        "--source-reference", "Synthetic typo",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["election_id"] == typo["election_id"]
    assert main([*command, "--monthly-base", "950,98"]) == 0
    added = json.loads(capsys.readouterr().out)
    assert (added["monthly_base_minor"], added["source_hash"]) == (
        95098, hashlib.sha256(b"synthetic TGSS resolution").hexdigest(),
    )
    assert main(["reta", "base", "list", "--db", str(database)]) == 0
    assert [row["election_id"] for row in json.loads(capsys.readouterr().out)] == [added["election_id"]]

    check = ["reta", "check", "--db", str(database), "--year", "2026", "--as-of", "2026-07-15"]
    assert main(check) == 2
    assert json.loads(capsys.readouterr().out)["reasons"] == ["period_missing"]

    with LedgerDB.open(database) as db:
        _posted_half_year(db, 900000)
    out = tmp_path / "reta-check.json"
    assert main([*check, "--through", "2026-06-30", "--out", str(out)]) == 0
    capsys.readouterr()
    assert json.loads(out.read_text(encoding="utf-8"))["status"] == "ok"
