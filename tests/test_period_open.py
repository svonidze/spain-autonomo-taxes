from __future__ import annotations

from pathlib import Path

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import UNREVIEWED_OBLIGATION_EXPLANATION, LedgerDB
from autonomo_taxes.tax_rules import ANNUAL_FORM_CODES, FORM_RULES, QUARTERLY_FORM_CODES


def _obligations(db: LedgerDB, period_key: str) -> dict[str, dict]:
    rows = db.connection.execute(
        """
        SELECT o.* FROM obligations o
        JOIN periods p ON p.period_id = o.period_id
        WHERE p.period_key = ?
        ORDER BY o.obligation_code
        """,
        (period_key,),
    ).fetchall()
    return {row["obligation_code"]: dict(row) for row in rows}


def test_period_open_creates_the_period_and_seeds_undecided_obligations(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite3"
    LedgerDB.initialize(database).close()

    assert main(["period", "open", "--db", str(database), "2031-Q2"]) == 0
    capsys.readouterr()

    with LedgerDB.initialize(database) as db:
        period = db.connection.execute(
            "SELECT * FROM periods WHERE period_key = '2031-Q2'"
        ).fetchone()
        rows = _obligations(db, "2031-Q2")

    assert period["period_type"] == "quarter"
    assert period["status"] == "open"
    assert set(rows) == set(QUARTERLY_FORM_CODES)
    for code, row in rows.items():
        assert row["determination"] == "unknown"
        assert row["filing_status"] == "unknown"
        assert row["blocking"] == 1
        assert row["explanation"] == UNREVIEWED_OBLIGATION_EXPLANATION
        assert row["source_citation"] == FORM_RULES[code].source_citation


def test_period_open_is_idempotent_and_keeps_reviewed_decisions(tmp_path: Path, capsys) -> None:
    database = tmp_path / "ledger.sqlite3"
    with LedgerDB.initialize(database) as db:
        db.ensure_period("2031-Q3")
        db.add_obligation(
            period_key="2031-Q3",
            obligation_code="303",
            filing_status="waived",
            determination="not_due",
            explanation="Reviewed: no VAT activity in the quarter",
        )

    assert main(["period", "open", "--db", str(database), "2031-Q3"]) == 0
    capsys.readouterr()
    assert main(["period", "open", "--db", str(database), "2031-Q3"]) == 0
    capsys.readouterr()

    with LedgerDB.initialize(database) as db:
        rows = _obligations(db, "2031-Q3")
        count = db.connection.execute(
            """
            SELECT COUNT(*) FROM obligations o
            JOIN periods p ON p.period_id = o.period_id
            WHERE p.period_key = '2031-Q3'
            """
        ).fetchone()[0]

    assert count == len(QUARTERLY_FORM_CODES)
    assert rows["303"]["determination"] == "not_due"
    assert rows["303"]["explanation"] == "Reviewed: no VAT activity in the quarter"
    assert rows["130"]["determination"] == "unknown"


def test_seed_unreviewed_obligations_uses_the_annual_set_for_a_year(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        created = db.seed_unreviewed_obligations("2031")
        rows = _obligations(db, "2031")

    expected = {
        code
        for code in ANNUAL_FORM_CODES
        if FORM_RULES[code].introduced_year is None or FORM_RULES[code].introduced_year <= 2031
    }
    assert {row["obligation_code"] for row in created} == expected
    assert set(rows) == expected
