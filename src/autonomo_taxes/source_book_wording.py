from __future__ import annotations

import re


SOURCE_BOOK_WORDING_REPLACEMENTS = [
    ("submitted row-level expense register", "source-book row treatment"),
    ("submitted row-level register", "source-book row treatment"),
    ("submitted per-row register", "source-book row detail"),
    ("submitted register row inclusion", "source-book row inclusion"),
    ("submitted-register confirmation", "source-book confirmation"),
    ("submitted-register adjustment", "source-book adjustment"),
    ("submitted-register entries", "source-book entries"),
    ("Submitted register row inclusion", "Source-book row inclusion"),
    ("Submitted-register confirmation", "Source-book confirmation"),
    ("Submitted-register adjustment", "Source-book adjustment"),
    ("Submitted-register entries", "Source-book entries"),
    ("Which submitted row", "Which source-book row"),
    ("Xolo's submitted register", "Xolo's source books"),
    ("Submitted expense registers", "Source-book exports"),
    ("Submitted expense register", "Source-book export"),
    ("submitted expense registers", "source-book exports"),
    ("submitted expense register", "source-book export"),
    ("submitted Modelo 130 expense registers", "source-book exports"),
    ("submitted Modelo 130 expense register", "source-book export"),
    ("submitted Modelo 130 registers", "source books"),
    ("submitted Modelo 130 register", "source books"),
    ("Submitted registers", "Source books"),
    ("Submitted register", "Source books"),
    ("Submitted-register", "Source-book"),
    ("submitted-register", "source-book"),
    ("submitted registers", "source books"),
    ("submitted register", "source books"),
    ("Xolo submitted-register", "Xolo source-book"),
    ("Xolo submitted registers", "Xolo source books"),
    ("Xolo submitted register", "Xolo source books"),
    ("filed register", "source books"),
    ("filed expense register", "source-book export"),
    ("row register", "source-book row set"),
    ("the submitted registers", "the source books"),
    ("the submitted register", "the source books"),
]


def source_book_wording(value: str) -> str:
    text = value
    text = re.sub(
        r"\bsubmitted ([1-4]T \d{4}) register\b",
        r"source books for \1",
        text,
    )
    text = re.sub(
        r"\bfiled Modelo 130 ([1-4]T \d{4}) register\b",
        r"source books for filed Modelo 130 \1",
        text,
    )
    for old, new in SOURCE_BOOK_WORDING_REPLACEMENTS:
        text = re.sub(_phrase_pattern(old), new, text)
    return text


def _phrase_pattern(phrase: str) -> str:
    return r"(?<!\w)" + re.escape(phrase) + r"(?!\w)"
