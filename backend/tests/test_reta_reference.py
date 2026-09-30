"""Independent examples checked against primary texts on 2026-10-01.

Amounts below are EUR cents, transcribed from separate arithmetic, not produced
by reta.py. Orden PJC/297/2026 arts. 18 and 37 supplies the tramos and 31.50%
rate; LGSS art. 308.1.c supplies the 7% deduction and netting; RD 2064/1995
arts. 44, 46 and 47 supplies natural alta days, exclusions and partial months.

Sources:
https://www.boe.es/diario_boe/txt.php?id=BOE-A-2026-7296
https://www.boe.es/buscar/act.php?id=BOE-A-2015-11724#a308
https://www.boe.es/buscar/act.php?id=BOE-A-1996-1579#a44
https://portal.seg-social.gob.es/wps/portal/importass/importass/otras_secciones/Regularizacion
"""

from datetime import date, timedelta

import pytest

from autonomo_taxes.reta import BaseElection, bracket_check, load_reta_table
from autonomo_test_support.paths import REPO_ROOT


@pytest.mark.parametrize("net,contributions,start,through,elections,expected", [
    # (22,000 + 4,000) x .93 x 30 / 365 = 1,987.39726 EUR/month.
    # (1,209.15 - 950.98) x 12 x .315 = 975.8826 EUR additional.
    (2200000, 400000, "2026-01-01", "2026-12-31", [("2026-01-01", "base", 95098)],
     ("below_bracket", "general", 5, 365, 198740, 95098, 97588, 0)),
    # 12,000 x .93 x 30 / 365 = 917.26027 EUR/month.
    # (1,500 - 1,166.70) x 12 x .315 = 1,259.874 EUR refund.
    (1200000, 0, "2026-01-01", "2026-12-31", [("2026-01-01", "base", 150000)],
     ("above_bracket", "reduced", 3, 365, 91726, 150000, 0, 125987)),
    # 1,000 x .93 x 30 / 16 = 1,743.75 EUR/month; 16 January..31 January.
    # (1,143.79 - 950.98) x 16/30 x .315 = 32.39208 EUR additional.
    (100000, 0, "2026-01-16", "2026-01-31", [("2026-01-16", "base", 95098)],
     ("below_bracket", "general", 4, 16, 174375, 95098, 3239, 0)),
    # 6,000 x .93 x 30 / 90 = 1,860 EUR/month.
    # ((1,209.15 - 950.98) x 2 + (1,209.15 - 1,300)) x .315 = 134.02935.
    # Net the positive and negative differences before rounding.
    (600000, 0, "2026-01-01", "2026-03-31",
     [("2026-01-01", "base", 95098), ("2026-03-01", "base", 130000)],
     ("below_bracket", "general", 5, 90, 186000, 106732, 13403, 0)),
    # Jan-Jun has 181 natural days; Jan-Mar tarifa plana excludes 90 days.
    # 6,000 x .93 x 30 / 91 = 1,839.56044 EUR/month.
    # (1,143.79 - 950.98) x 3 x .315 = 182.20545 EUR additional.
    (600000, 0, "2026-01-01", "2026-06-30",
     [("2026-01-01", "tarifa_plana", None), ("2026-04-01", "base", 95098)],
     ("below_bracket", "general", 4, 91, 183956, 95098, 18221, 0)),
    # -1,000 x .93 x 30 / 365 = -76.43836 EUR/month; reduced tramo 1.
    # The elected 653.59 EUR base is within its 653.59..718.94 EUR range.
    (-100000, 0, "2026-01-01", "2026-12-31", [("2026-01-01", "base", 65359)],
     ("ok", "reduced", 1, 365, -7644, 65359, 0, 0)),
], ids=["annual-add-back", "annual-refund", "partial-month", "netted-base-change",
        "tarifa-plana-days", "negative-net"])
def test_independently_calculated_reta_examples(net, contributions, start, through, elections, expected):
    end = date.fromisoformat(through)
    result = bracket_check(
        load_reta_table(REPO_ROOT / "reference/reta/2026.json"),
        year=2026,
        as_of=end + timedelta(days=1),
        through=end,
        irpf_net_income_minor=net,
        contributions_added_back_minor=contributions,
        worker_kind="individual",
        alta_periods=[(date.fromisoformat(start), end)],
        elections=[BaseElection(date.fromisoformat(day), regime, base) for day, regime, base in elections],
    )
    assert result["reasons"] == []
    assert (
        result["status"], result["bracket"]["table"], result["bracket"]["tramo"],
        result["income"]["regularisable_days"], result["income"]["monthly_average_minor"],
        result["average_provisional_base_minor"], result["estimated_additional_minor"],
        result["estimated_refund_minor"],
    ) == expected
