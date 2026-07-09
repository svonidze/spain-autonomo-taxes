from __future__ import annotations


SOURCE_BOOK_WORDING_REPLACEMENTS = [
    ("submitted Modelo 130 expense registers", "source-book exports"),
    ("submitted Modelo 130 expense register", "source-book export"),
    ("submitted Modelo 130 registers", "source books"),
    ("submitted Modelo 130 register", "source books"),
    ("submitted-register", "source-book"),
    ("submitted registers", "source books"),
    ("submitted register", "source books"),
    ("Xolo submitted-register", "Xolo source-book"),
    ("Xolo submitted registers", "Xolo source books"),
    ("Xolo submitted register", "Xolo source books"),
    ("filed expense register", "source-book export"),
    ("the submitted registers", "the source books"),
    ("the submitted register", "the source books"),
]


def source_book_wording(value: str) -> str:
    text = value
    for old, new in SOURCE_BOOK_WORDING_REPLACEMENTS:
        text = text.replace(old, new)
    return text
