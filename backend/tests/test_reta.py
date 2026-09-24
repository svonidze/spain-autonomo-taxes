from __future__ import annotations

import json
from datetime import date
from fractions import Fraction
from pathlib import Path

import pytest
from autonomo_taxes.reta import (
    BaseElection,
    bracket_check,
    find_tramo,
    load_reta_table,
    next_base_change_date,
)
from autonomo_test_support.paths import REPO_ROOT

TABLE_PATH = REPO_ROOT / "reference" / "reta" / "2026.json"

# Orden PJC/297/2026 art. 18.1 (BOE-A-2026-7296), copied from the BOE text.
BOE_2026_TRAMOS = [
    ("reduced", 1, "≤ 670", "653,59", "718,94"),
    ("reduced", 2, "> 670 y ≤ 900", "718,95", "900,00"),
    ("reduced", 3, "> 900 y < 1.166,70", "849,67", "1.166,70"),
    ("general", 1, "≥ 1.166,70 y ≤ 1.300", "950,98", "1.300,00"),
    ("general", 2, "> 1.300 y ≤ 1.500", "960,78", "1.500,00"),
    ("general", 3, "> 1.500 y ≤ 1.700", "960,78", "1.700,00"),
    ("general", 4, "> 1.700 y ≤ 1.850", "1.143,79", "1.850,00"),
    ("general", 5, "> 1.850 y ≤ 2.030", "1.209,15", "2.030,00"),
    ("general", 6, "> 2.030 y ≤ 2.330", "1.274,51", "2.330,00"),
    ("general", 7, "> 2.330 y ≤ 2.760", "1.356,21", "2.760,00"),
    ("general", 8, "> 2.760 y ≤ 3.190", "1.437,91", "3.190,00"),
    ("general", 9, "> 3.190 y ≤ 3.620", "1.519,61", "3.620,00"),
    ("general", 10, "> 3.620 y ≤ 4.050", "1.601,31", "4.050,00"),
    ("general", 11, "> 4.050 y ≤ 6.000", "1.732,03", "5.101,20"),
    ("general", 12, "> 6.000", "1.928,10", "5.101,20"),
]
# Jan-Aug 2026: the regularisable days of a full-year alta through 31 August.
JAN_AUG_DAYS = 243


def _minor(text: str) -> int:
    euros, _, cents = text.replace(".", "").partition(",")
    return int(euros) * 100 + int(cents or 0)


def _bounds(boe_range: str) -> dict[str, object]:
    bounds: dict[str, object] = {
        "lower_minor": None, "lower_inclusive": None, "upper_minor": None, "upper_inclusive": None,
    }
    for part in boe_range.split(" y "):
        operator, value = part.split(" ")
        side = "lower" if operator in {">", "≥"} else "upper"
        bounds[f"{side}_minor"] = _minor(value)
        bounds[f"{side}_inclusive"] = operator in {"≥", "≤"}
    return bounds


def test_2026_table_matches_orden_pjc_297_2026_article_18() -> None:
    payload = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    table = load_reta_table(TABLE_PATH)

    assert len(table.tramos) == 15
    assert len(table.source_file_hash) == 64
    assert table.source_checked_on == date(2026, 9, 23)
    assert table.maximum_base_minor == 510120
    for boe, raw, row in zip(BOE_2026_TRAMOS, payload["tramos"], table.tramos, strict=True):
        name, number, boe_range, min_base, max_base = boe
        assert (row.table, row.tramo, raw["boe_range"]) == (name, number, boe_range)
        assert {key: getattr(row, key) for key in _bounds(boe_range)} == _bounds(boe_range)
        assert (row.min_base_minor, row.max_base_minor) == (_minor(min_base), _minor(max_base))

    rates = {row["component"]: (row["rate_bp"], row["source_kind"], row["source_reference"]) for row in payload["rate_components"]}
    assert rates == {
        "contingencias_comunes": (2830, "primary", "Orden PJC/297/2026 art. 18.2.a)"),
        "contingencias_profesionales": (130, "primary", "Orden PJC/297/2026 art. 18.2.b)"),
        "mecanismo_equidad_intergeneracional": (90, "primary", "Orden PJC/297/2026 art. 18.2.c)"),
        "cese_actividad": (90, "primary", "Orden PJC/297/2026 art. 37.5.a)"),
        "formacion_profesional": (10, "primary", "Orden PJC/297/2026 art. 37.6"),
    }
    assert table.total_rate_bp == 3150
    assert table.generic_deduction_bp == {"individual": 700, "societario": 300, "colaborador": 700}


@pytest.mark.parametrize(
    ("monthly_minor", "expected"),
    [
        (-100, ("reduced", 1)),
        (67000, ("reduced", 1)),
        (Fraction(1340001, 20), ("reduced", 2)),  # 670.0005 EUR is already > 670
        (67001, ("reduced", 2)),
        (116669, ("reduced", 3)),
        (Fraction(233339, 2), ("reduced", 3)),  # 1,166.695 EUR is still < 1,166.70
        (116670, ("general", 1)),
        (600000, ("general", 11)),
        (Fraction(1200001, 2), ("general", 12)),
        (600001, ("general", 12)),
    ],
)
def test_monthly_income_maps_to_boe_tramo_at_each_boundary(monthly_minor, expected: tuple[str, int]) -> None:
    row = find_tramo(load_reta_table(TABLE_PATH), monthly_minor)

    assert (row.table, row.tramo) == expected


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda p: p["tramos"][4].update(lower_minor=130001), "not contiguous"),
        (lambda p: p["tramos"][4].update(lower_inclusive=True), "not contiguous"),
        (lambda p: p["tramos"].pop(3), "RETA tramo 4 must be"),
        (lambda p: p["tramos"][14].update(upper_minor=900000, upper_inclusive=True), "open above"),
        (lambda p: p["tramos"][2].update(upper_inclusive=None), "must be a boolean"),
        (lambda p: p["rate_components"][3].update(rate_bp=100), "sum to 3160 bp"),
        (lambda p: p["rate_components"].append(dict(p["rate_components"][4])), "Duplicate RETA rate component"),
        (lambda p: p["rate_components"][4].pop("source_url"), "formacion_profesional needs source_kind"),
        (lambda p: p["rate_components"][0].update(source_url="https://example.com/cc"), "not on https://www.boe.es/"),
        (lambda p: p.pop("source_reference"), "RETA table needs source_kind"),
        (lambda p: p.pop("generic_deduction_source"), "generic_deduction_source must carry"),
        (lambda p: p["generic_deduction_bp"].pop("colaborador"), "must list exactly"),
        (lambda p: p["generic_deduction_bp"].update(individual=-1), "between 0 and 9999"),
    ],
)
def test_loader_rejects_gaps_rate_mismatch_and_missing_sources(tmp_path: Path, mutate, message: str) -> None:
    payload = json.loads(TABLE_PATH.read_text(encoding="utf-8"))
    mutate(payload)
    path = tmp_path / "reta.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_reta_table(path)


def _check(**overrides):
    arguments = {
        "table": load_reta_table(TABLE_PATH),
        "year": 2026,
        "as_of": date(2026, 9, 23),
        "through": date(2026, 8, 31),
        # 14,000.00 EUR x 93 % x 30 / 243 days = 1,607.41 EUR/month -> general tramo 3.
        "irpf_net_income_minor": 1_300_000,
        "contributions_added_back_minor": 100_000,
        "worker_kind": "individual",
        "alta_periods": [(date(2025, 6, 1), None)],
        "elections": [BaseElection(date(2026, 1, 1), "base", 100_000)],
    }
    arguments.update(overrides)
    return bracket_check(arguments.pop("table"), **arguments)


@pytest.mark.parametrize(
    ("worker_kind", "net", "expected"),
    [
        ("individual", 100_005, 93_005),  # 93,004.65 cents rounds half up for display
        ("societario", 100_005, 97_005),  # 97,004.85 cents
        ("colaborador", 100_005, 93_005),
        ("individual", -100_005, -93_005),
    ],
)
def test_generic_deduction_per_worker_kind(worker_kind: str, net: int, expected: int) -> None:
    result = _check(worker_kind=worker_kind, irpf_net_income_minor=net, contributions_added_back_minor=0)

    assert result["income"]["rendimiento_computable_minor"] == expected
    if worker_kind != "individual":
        assert (result["status"], result["reasons"]) == ("unknown", ["worker_kind_floor_unverified"])
        assert result["estimated_additional_minor"] is None
    # Societario income also needs entity income the ledger does not hold.
    assert (result["bracket"] is None) is (worker_kind == "societario")
    assert (result["income"]["monthly_average_minor"] is None) is (worker_kind == "societario")
    assert (result["boundary_sensitive"] is None) is (worker_kind == "societario")


def test_tramo_lookup_uses_the_exact_average_not_the_rounded_one() -> None:
    # 1,416.85 x 93 % x 30 / 59 days = 670.0019 EUR: rounds to 670.00 but is > 670.
    above_670 = _check(
        as_of=date(2026, 2, 28), through=date(2026, 2, 28),
        irpf_net_income_minor=141_685, contributions_added_back_minor=0,
    )
    # 1,170.88 x 93 % x 30 / 28 days = 1,166.6983 EUR: rounds to 1,166.70 but is < 1,166.70.
    below_1166_70 = _check(
        as_of=date(2026, 2, 28), through=date(2026, 2, 28), alta_periods=[(date(2026, 2, 1), None)],
        irpf_net_income_minor=117_088, contributions_added_back_minor=0,
    )

    assert above_670["income"]["monthly_average_minor"] == 67_000
    assert (above_670["bracket"]["table"], above_670["bracket"]["tramo"]) == ("reduced", 2)
    assert below_1166_70["income"]["monthly_average_minor"] == 116_670
    assert (below_1166_70["bracket"]["table"], below_1166_70["bracket"]["tramo"]) == ("reduced", 3)


def test_average_base_inside_tramo_is_ok_with_known_zero_estimates() -> None:
    result = _check()

    assert (result["status"], result["reasons"]) == ("ok", [])
    assert result["income"]["regularisable_days"] == JAN_AUG_DAYS
    assert result["income"]["monthly_average_minor"] == 160_741
    assert (result["bracket"]["table"], result["bracket"]["tramo"]) == ("general", 3)
    assert result["boundary_sensitive"] is False  # 92.59 EUR from 1,700; margin is 20.00
    assert result["average_provisional_base_minor"] == 100_000  # full months weigh 1 each
    assert [row["difference_to_min_minor"] for row in result["months"]] == [3_922] * 8
    assert (result["estimated_additional_minor"], result["estimated_refund_minor"]) == (0, 0)
    assert result["additional_locked_in_minor"] == 0
    assert result["next_base_change"] == {"effective_on": "2026-11-01", "request_by": "2026-10-31"}


def test_below_tramo_charges_the_netted_shortfall_and_locks_in_months_until_the_next_change() -> None:
    # 48,000 x 93 % x 30 / 243 = 5,511.11 EUR/month -> general tramo 11 (min 1,732.03).
    result = _check(irpf_net_income_minor=4_800_000, contributions_added_back_minor=0)

    assert result["status"] == "below_bracket"
    assert (result["bracket"]["tramo"], result["boundary_sensitive"]) == (11, False)
    # (1,732.03 x 8 - 8,000) x 31.50 % = 1,844.7156 -> 1,844.72, rounded once.
    assert result["estimated_additional_minor"] == 184_472
    assert result["estimated_refund_minor"] == 0
    # Sep and Oct keep the old base: 732.03 x 10 x 31.50 % = 2,305.8945.
    assert result["additional_locked_in_minor"] == 230_589


def test_above_tramo_reports_the_netted_refund() -> None:
    result = _check(elections=[BaseElection(date(2025, 3, 1), "base", 200_000)])

    assert result["status"] == "above_bracket"
    assert result["estimated_refund_minor"] == 75_600  # (8 x 2,000 - 8 x 1,700) x 31.50 %
    assert result["estimated_additional_minor"] == 0
    assert result["additional_locked_in_minor"] == 0


def test_mixed_months_are_judged_on_the_total_against_the_tramo() -> None:
    # 4 x 900 + 4 x 2,000 = 11,600 lies inside 8 x 960.78 .. 8 x 1,700 (average 1,450).
    result = _check(elections=[
        BaseElection(date(2026, 1, 1), "base", 90_000),
        BaseElection(date(2026, 5, 1), "base", 200_000),
    ])

    assert result["status"] == "ok"
    assert result["average_provisional_base_minor"] == 145_000
    assert (result["estimated_additional_minor"], result["estimated_refund_minor"]) == (0, 0)
    assert [row["difference_to_min_minor"] for row in result["months"][:4]] == [-6_078] * 4
    assert [row["difference_to_max_minor"] for row in result["months"][4:]] == [30_000] * 4


def test_monthly_differences_are_netted_positive_and_negative() -> None:
    result = _check(elections=[
        BaseElection(date(2026, 1, 1), "base", 90_000),
        BaseElection(date(2026, 5, 1), "base", 97_000),
    ])

    assert result["status"] == "below_bracket"
    # 4 x -60.78 + 4 x +9.22 against the 960.78 minimum = 206.24 x 31.50 %.
    assert result["estimated_additional_minor"] == 6_497
    # Sep and Oct stay at 970.00: (4 x 60.78 - 6 x 9.22) x 31.50 %.
    assert result["additional_locked_in_minor"] == 5_916


@pytest.mark.parametrize("base", [96_078, 96_500])
def test_base_at_or_just_above_the_tramo_minimum_over_full_months_is_ok(base: int) -> None:
    # LGSS art. 308.1.c regla 3.ª compares totals: 8 x base against 8 x 960.78.
    result = _check(elections=[BaseElection(date(2026, 1, 1), "base", base)])

    assert (result["status"], result["bracket"]["tramo"]) == ("ok", 3)
    assert result["average_provisional_base_minor"] == base
    assert (result["estimated_additional_minor"], result["estimated_refund_minor"]) == (0, 0)


def test_tarifa_plana_days_leave_the_divisor_but_not_the_income() -> None:
    result = _check(
        irpf_net_income_minor=400_000,
        contributions_added_back_minor=0,
        alta_periods=[(date(2025, 7, 1), None)],
        elections=[
            BaseElection(date(2025, 7, 1), "tarifa_plana", None),
            BaseElection(date(2026, 7, 1), "base", 90_000),
        ],
    )

    # 4,000 x 93 % x 30 / 62 days (Jul-Aug) = 1,800.00 EUR/month -> general tramo 4.
    assert result["income"]["regularisable_days"] == 62
    assert result["income"]["monthly_average_minor"] == 180_000
    assert result["bracket"]["tramo"] == 4
    assert [row["tarifa_plana_days"] for row in result["months"]] == [31, 28, 31, 30, 31, 30, 0, 0]
    assert [row["difference_to_min_minor"] for row in result["months"]] == [None] * 6 + [-24_379] * 2
    assert result["status"] == "below_bracket"
    assert result["estimated_additional_minor"] == 15_359  # 2 x 243.79 x 31.50 %
    assert result["additional_locked_in_minor"] == 30_718  # Jul-Oct

    only_tarifa_plana = _check(elections=[BaseElection(date(2025, 7, 1), "tarifa_plana", None)])
    assert (only_tarifa_plana["status"], only_tarifa_plana["reasons"]) == ("unknown", ["window_empty"])


def test_partial_month_counts_thirtieths_of_the_base_and_the_tramo_limits() -> None:
    result = _check(
        alta_periods=[(date(2026, 3, 16), None)],
        elections=[BaseElection(date(2026, 3, 16), "base", 100_000)],
    )

    # 13,020 EUR x 30 / 169 days = 2,311.24 EUR/month -> general tramo 6, 18.76 below 2,330.
    assert result["income"]["regularisable_days"] == 169
    assert (result["bracket"]["tramo"], result["boundary_sensitive"]) == (6, True)
    march = result["months"][0]
    # RD 2064/1995 art. 47.1: 16 days pay 16/30 of the month, limits likewise.
    assert (march["month"], march["alta_days"], march["provisional_base_minor"]) == ("2026-03", 16, 53_333)
    assert march["difference_to_min_minor"] == -14_641  # (1,000 - 1,274.51) x 16 / 30
    assert result["status"] == "below_bracket"
    assert result["estimated_additional_minor"] == 47_847
    assert result["additional_locked_in_minor"] == 65_141


@pytest.mark.parametrize(
    ("overrides", "reasons", "income_known"),
    [
        ({"elections": [BaseElection(date(2026, 3, 1), "base", 100_000)]}, ["base_missing"], True),
        ({"elections": [BaseElection(date(2026, 1, 1), "base", 50_000)]}, ["base_below_table_minimum"], True),
        ({"table": None}, ["table_unavailable"], False),
        ({"irpf_net_income_minor": None}, ["ledger_blocked"], False),
        ({"reasons": ["unposted_rows_in_window", "period_missing"]}, ["unposted_rows_in_window", "period_missing"], False),
        ({"alta_periods": []}, ["window_empty"], False),
    ],
)
def test_missing_inputs_give_unknown_with_null_estimates(overrides, reasons: list[str], income_known: bool) -> None:
    result = _check(**overrides)

    assert (result["status"], result["reasons"]) == ("unknown", reasons)
    assert result["estimated_additional_minor"] is None
    assert result["estimated_refund_minor"] is None
    assert result["additional_locked_in_minor"] is None
    assert result["average_provisional_base_minor"] is None
    assert all(row["difference_to_min_minor"] is None for row in result["months"])
    assert (result["bracket"] is not None) is income_known
    assert (result["boundary_sensitive"] is not None) is income_known
    assert (result["income"]["monthly_average_minor"] is not None) is income_known


def test_table_for_another_year_is_unavailable() -> None:
    result = _check(year=2027, as_of=date(2027, 3, 2), through=date(2027, 2, 28))

    assert (result["status"], result["reasons"], result["table"]) == ("unknown", ["table_unavailable"], None)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"through": date(2026, 8, 30)}, "month end"),
        ({"through": date(2026, 9, 30)}, "before through"),
        ({"alta_periods": [(date(2026, 3, 1), date(2026, 2, 1))]}, "alta period"),
        ({"reasons": ["looks_fine"]}, "Unsupported caller reasons"),
        ({"worker_kind": "employee"}, "worker_kind"),
        ({"elections": [BaseElection(date(2026, 1, 1), "base", None)]}, "positive monthly_base_minor"),
        ({"elections": [BaseElection(date(2026, 1, 1), "base", 510_121)]}, "exceeds the 2026 maximum base"),
    ],
)
def test_bracket_check_rejects_malformed_inputs(overrides, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _check(**overrides)


@pytest.mark.parametrize(
    ("as_of", "effective_on"),
    [
        (date(2026, 1, 1), date(2026, 3, 1)),
        (date(2026, 2, 28), date(2026, 3, 1)),
        (date(2026, 3, 1), date(2026, 5, 1)),
        (date(2026, 9, 23), date(2026, 11, 1)),
        (date(2026, 10, 31), date(2026, 11, 1)),
        (date(2026, 11, 15), date(2027, 1, 1)),
        (date(2026, 12, 31), date(2027, 1, 1)),
    ],
)
def test_next_base_change_follows_the_two_month_request_windows(as_of: date, effective_on: date) -> None:
    assert next_base_change_date(as_of) == effective_on


def test_check_run_years_later_locks_in_the_whole_year() -> None:
    result = _check(as_of=date(2029, 5, 10), irpf_net_income_minor=4_800_000, contributions_added_back_minor=0)

    assert result["status"] == "below_bracket"
    assert result["additional_locked_in_minor"] == 276_707  # 732.03 x 12 x 31.50 %


def test_request_in_november_locks_in_the_rest_of_the_year() -> None:
    result = _check(
        as_of=date(2026, 11, 15),
        through=date(2026, 10, 31),
        irpf_net_income_minor=6_000_000,
        contributions_added_back_minor=0,
    )

    # 55,800 x 30 / 304 days = 5,506.58 EUR/month -> tramo 11; Nov and Dec cannot change.
    assert result["next_base_change"] == {"effective_on": "2027-01-01", "request_by": "2026-12-31"}
    assert result["estimated_additional_minor"] == 230_589
    assert result["additional_locked_in_minor"] == 276_707
