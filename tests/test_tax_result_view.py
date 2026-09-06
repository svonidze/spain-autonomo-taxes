from __future__ import annotations

from datetime import date
from pathlib import Path

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.tax_result_view import build_tax_summary


_PERIOD = {
    "period_key": "2031-Q2",
    "period_type": "quarter",
    "status": "open",
    "starts_on": "2031-04-01",
    "ends_on": "2031-06-30",
}


def _summary(tmp_path: Path, obligations: list[dict]) -> dict:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite3") as db:
        _, summary = build_tax_summary(
            db.connection,
            period=_PERIOD,
            obligations=obligations,
            tax_forms={},
            cached={},
            as_of=date(2031, 5, 15),
        )
    return summary


def test_quarter_without_obligation_rows_is_undecided_not_exempt(tmp_path: Path) -> None:
    summary = _summary(tmp_path, [])

    assert summary["unresolved_obligations"] == ["130", "303"]
    assert summary["total_payable_minor"] is None
    assert summary["settlement_status"] == "undetermined"
    assert summary["calculation_source"] == "unavailable"
    assert summary["forms"]["130"]["determination"] == "unknown"
    assert summary["forms"]["303"]["determination"] == "unknown"


def test_missing_core_return_stays_unresolved_next_to_a_reviewed_one(tmp_path: Path) -> None:
    summary = _summary(
        tmp_path,
        [
            {
                "obligation_id": "obligation-303",
                "obligation_code": "303",
                "determination": "not_due",
                "filing_status": "waived",
            }
        ],
    )

    assert summary["unresolved_obligations"] == ["130"]
    assert summary["total_payable_minor"] is None
    assert summary["forms"]["303"]["calculation_source"] == "not_required"
