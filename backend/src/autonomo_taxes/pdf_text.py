from __future__ import annotations

from pathlib import Path


def extract_pdf_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def readable_text(path: Path) -> tuple[str, str | None]:
    try:
        return extract_pdf_text(path), None
    except Exception as exc:  # PDF extraction failures are audit data.
        return "", f"{type(exc).__name__}: {exc}"
