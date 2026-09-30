from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re
from typing import Iterable, Sequence

from pypdf import PdfReader

from .money import cents, parse_amount


FILED_CASILLAS = (
    "47",
    "48",
    "49",
    "50",
    "51",
    "52",
    "53",
    "54",
    "55",
    "56",
    "57",
    "58",
    "59",
    "597",
    "598",
    "64",
    "65",
    "84",
    "85",
    "659",
    "86",
    "95",
    "97",
    "98",
    "662",
    "99",
    "103",
    "104",
    "110",
    "108",
    "523",
)

_NUMBER_PATTERN = r"(?:\d{1,3}(?:\.\d{3})*|\d+),\d{2}"
_AMOUNT_PATTERN = rf"(?:-{_NUMBER_PATTERN}|{_NUMBER_PATTERN}-|\({_NUMBER_PATTERN}\)|{_NUMBER_PATTERN})"
_LEFT_VALUE_CASILLAS = {"48", "50", "52", "54", "56", "58", "597"}
_RIGHT_VALUE_CASILLAS = {"49", "51", "53", "55", "57", "59", "598"}
_POSITION_TOLERANCE = Decimal("90")

PositionedFragment = tuple[str, Decimal, Decimal, Decimal]


@dataclass(frozen=True)
class Modelo390Values:
    casillas: tuple[tuple[str, Decimal], ...]
    blank_casillas: tuple[str, ...]
    value_sources: tuple[tuple[str, str], ...] = ()

    @property
    def values(self) -> dict[str, Decimal]:
        return dict(self.casillas)


def extract_modelo390_values(path: Path) -> Modelo390Values:
    reader = PdfReader(str(path))
    if not reader.pages:
        raise ValueError(f"Modelo 390 report has no form pages: {path}")
    layouts: list[str] = []
    positioned_pages: list[list[PositionedFragment]] = []
    for page in reader.pages:
        fragments: list[PositionedFragment] = []

        def visitor(fragment: str, _cm, tm, _font_dict, font_size: float) -> None:
            value = " ".join(fragment.split())
            if not value:
                return
            fragments.append(
                (
                    value,
                    Decimal(str(tm[4])),
                    Decimal(str(tm[5])),
                    Decimal(str(font_size)),
                )
            )

        page.extract_text(visitor_text=visitor)
        layouts.append(page.extract_text(extraction_mode="layout") or "")
        positioned_pages.append(fragments)
    return modelo390_values_from_layout_pages(
        layouts,
        positioned_pages=positioned_pages,
    )


def modelo390_values_from_layout_pages(
    layout_pages: Iterable[str],
    *,
    positioned_pages: Sequence[Sequence[PositionedFragment]] | None = None,
) -> Modelo390Values:
    pages = tuple(layout_pages)
    if not pages or not any(page.strip() for page in pages):
        raise ValueError("Modelo 390 layout text is empty")
    if positioned_pages is None or len(positioned_pages) != len(pages):
        raise ValueError("Modelo 390 positioned text is required for blank-box validation")

    box_labels = _box_labels(positioned_pages)
    missing = [
        key
        for key in FILED_CASILLAS
        if key not in box_labels
    ]
    if missing:
        raise ValueError(
            "Modelo 390 layout is missing expected casillas: " + ", ".join(missing)
        )

    extracted: dict[str, Decimal] = {}
    blank: list[str] = []
    value_sources: list[tuple[str, str]] = []
    for key in FILED_CASILLAS:
        pattern = re.compile(
            rf"(?<!\d){re.escape(key)}(?!\d)[ \t]+({_AMOUNT_PATTERN})(?!\d)"
        )
        layout_candidates = {
            _parse_printed_amount(match.group(1))
            for page in pages
            for line in page.splitlines()
            for match in pattern.finditer(line)
        }
        positioned_candidates = _positioned_candidates(
            key,
            positioned_pages,
        )
        candidates = layout_candidates | positioned_candidates
        if len(candidates) > 1:
            rendered = ", ".join(f"{value:.2f}" for value in sorted(candidates))
            raise ValueError(f"Modelo 390 casilla {key} has ambiguous values: {rendered}")
        if candidates:
            extracted[key] = candidates.pop()
            source_parts: list[str] = []
            if layout_candidates:
                source_parts.append("layout_line")
            if positioned_candidates:
                source_parts.append("positioned_row")
            value_sources.append((key, "+".join(source_parts)))
        else:
            extracted[key] = Decimal("0.00")
            blank.append(key)
            value_sources.append((key, "box_present_no_value_captured"))

    _validate_arithmetic(extracted)
    return Modelo390Values(
        casillas=tuple((key, cents(extracted[key])) for key in FILED_CASILLAS),
        blank_casillas=tuple(blank),
        value_sources=tuple(value_sources),
    )


def _box_labels(
    positioned_pages: Sequence[Sequence[PositionedFragment]],
) -> set[str]:
    labels: set[str] = set()
    for fragments in positioned_pages:
        for text, _x, _y, font_size in fragments:
            if font_size > Decimal("2.5") or re.fullmatch(r"\d+(?:\s+\d+)*", text) is None:
                continue
            labels.update(token for token in text.split() if token in FILED_CASILLAS)
    return labels


def _positioned_candidates(
    key: str,
    positioned_pages: Sequence[Sequence[PositionedFragment]],
) -> set[Decimal]:
    candidates: set[Decimal] = set()
    expected_x = Decimal("333") if key in _LEFT_VALUE_CASILLAS else Decimal("530")
    for fragments in positioned_pages:
        label_rows: list[Decimal] = []
        for text, _x, y, font_size in fragments:
            if font_size > Decimal("2.5") or re.fullmatch(r"\d+(?:\s+\d+)*", text) is None:
                continue
            if key in text.split():
                label_rows.append(y)
        for raw, x, y, font_size in fragments:
            if font_size < Decimal("5") or _printed_amount_match(raw) is None:
                continue
            if x == 0 and y == 0:
                continue
            if abs(x - expected_x) > _POSITION_TOLERANCE:
                continue
            if any(abs(y - label_y) <= Decimal("6") for label_y in label_rows):
                candidates.add(_parse_printed_amount(raw))
    return candidates


def _printed_amount_match(raw: str) -> re.Match[str] | None:
    return re.fullmatch(_AMOUNT_PATTERN, raw)


def _parse_printed_amount(raw: str) -> Decimal:
    value = raw.strip()
    negative = value.startswith("(") or value.endswith("-")
    if value.startswith("(") and value.endswith(")"):
        value = value[1:-1]
    if value.endswith("-"):
        value = value[:-1]
    amount = parse_amount(value)
    return cents(-amount if negative else amount)


def _validate_arithmetic(values: dict[str, Decimal]) -> None:
    expected_general_result = cents(values["47"] - values["64"])
    if abs(values["65"] - expected_general_result) > Decimal("0.02"):
        raise ValueError(
            "Modelo 390 casilla 65 does not reconcile with 47 - 64: "
            f"{values['65']:.2f} != {expected_general_result:.2f}"
        )

    expected_liquidation = cents(values["84"] + values["659"] - values["85"])
    if abs(values["86"] - expected_liquidation) > Decimal("0.02"):
        raise ValueError(
            "Modelo 390 casilla 86 does not reconcile with 84 + 659 - 85: "
            f"{values['86']:.2f} != {expected_liquidation:.2f}"
        )
