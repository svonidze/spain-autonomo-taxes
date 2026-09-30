from decimal import Decimal
from pathlib import Path

import pytest

from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.modelo390 import FILED_CASILLAS, modelo390_values_from_layout_pages
from autonomo_taxes.operational_cli import _filed_baseline


def test_extracts_filed_casillas_and_treats_blank_boxes_as_zero() -> None:
    values = modelo390_values_from_layout_pages(
        _valid_layout(),
        positioned_pages=_valid_positioned_pages(),
    )

    assert values.values["48"] == Decimal("2329.47")
    assert values.values["49"] == Decimal("489.19")
    assert values.values["84"] == Decimal("-489.19")
    assert values.values["85"] == Decimal("433.08")
    assert values.values["86"] == Decimal("-922.27")
    assert values.values["110"] == Decimal("80926.34")
    assert values.values["523"] == Decimal("6096.19")
    assert values.values["95"] == Decimal("0.00")
    assert "95" in values.blank_casillas
    assert dict(values.value_sources)["95"] == "box_present_no_value_captured"


def test_rejects_ambiguous_casilla_values() -> None:
    pages = list(_valid_layout())
    pages.append("84 -500,00")

    with pytest.raises(ValueError, match="casilla 84 has ambiguous values"):
        modelo390_values_from_layout_pages(
            pages,
            positioned_pages=[*_valid_positioned_pages(), []],
        )


def test_rejects_inconsistent_settlement_arithmetic() -> None:
    pages = [page.replace("65 -489,19", "65 -400,00") for page in _valid_layout()]

    with pytest.raises(ValueError, match="casilla 65 does not reconcile"):
        modelo390_values_from_layout_pages(
            pages,
            positioned_pages=_valid_positioned_pages(include_values=False),
        )


def test_rejects_incomplete_form_layout() -> None:
    pages = [page.replace("523 6.096,19", "") for page in _valid_layout()]
    positioned = [
        [fragment for fragment in page if fragment[0] != "523"]
        for page in _valid_positioned_pages()
    ]

    with pytest.raises(ValueError, match="missing expected casillas: 523"):
        modelo390_values_from_layout_pages(pages, positioned_pages=positioned)


def test_uses_positioned_row_when_layout_wraps_value_to_another_line() -> None:
    pages = [page.replace("97 45,36", "97\n45,36") for page in _valid_layout()]

    values = modelo390_values_from_layout_pages(
        pages,
        positioned_pages=_valid_positioned_pages(),
    )

    assert values.values["97"] == Decimal("45.36")
    assert dict(values.value_sources)["97"] == "positioned_row"


def test_supports_parenthesized_and_trailing_minus_amounts() -> None:
    pages = [
        page.replace("65 -489,19", "65 (489,19)").replace(
            "84 -489,19",
            "84 489,19-",
        )
        for page in _valid_layout()
    ]
    positioned = _valid_positioned_pages(include_values=False)

    values = modelo390_values_from_layout_pages(pages, positioned_pages=positioned)

    assert values.values["65"] == Decimal("-489.19")
    assert values.values["84"] == Decimal("-489.19")


def test_filed_baseline_prefers_snapshot_with_values_over_newer_empty_snapshot(
    tmp_path: Path,
) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        db.ensure_period("2025")
        db.create_filing_snapshot(
            "2025",
            status="baseline",
            filed_on="2026-01-30",
            payload={
                "form": "390",
                "filed_values": {"86": "-922.27"},
                "value_extraction_schema": "modelo390_v2",
            },
            snapshot_hash="enriched",
        )
        db.create_filing_snapshot(
            "2025",
            status="baseline",
            filed_on="2026-01-30",
            payload={"form": "390"},
            snapshot_hash="newer-empty",
        )

        baseline = _filed_baseline(db, "2025", "390")

    assert baseline is not None
    assert baseline["filed_values"] == {"86": "-922.27"}
    assert baseline["value_extraction_schema"] == "modelo390_v2"


def _valid_layout() -> list[str]:
    populated = {
        "48": "2.329,47",
        "49": "489,19",
        "64": "489,19",
        "65": "-489,19",
        "84": "-489,19",
        "85": "433,08",
        "86": "-922,27",
        "97": "45,36",
        "662": "876,91",
        "110": "80.926,34",
        "108": "80.926,34",
        "523": "6.096,19",
    }
    lines = [
        f"{key} {populated[key]}" if key in populated else key
        for key in FILED_CASILLAS
    ]
    return ["\n".join(lines)]


def _valid_positioned_pages(*, include_values: bool = True):
    populated = {
        "48": "2.329,47",
        "49": "489,19",
        "64": "489,19",
        "65": "-489,19",
        "84": "-489,19",
        "85": "433,08",
        "86": "-922,27",
        "97": "45,36",
        "662": "876,91",
        "110": "80.926,34",
        "108": "80.926,34",
        "523": "6.096,19",
    }
    left = {"48", "50", "52", "54", "56", "58", "597"}
    fragments = []
    for index, key in enumerate(FILED_CASILLAS):
        y = Decimal("760") - Decimal(index * 10)
        label_x = Decimal("222") if key in left else Decimal("425")
        fragments.append((key, label_x, y, Decimal("1")))
        if include_values and key in populated:
            value_x = Decimal("333") if key in left else Decimal("530")
            fragments.append((populated[key], value_x, y - Decimal("1"), Decimal("9")))
    return [fragments]
