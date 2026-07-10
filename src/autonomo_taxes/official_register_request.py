from __future__ import annotations

from pathlib import Path


BANNED_FIRST_CONTACT_PHRASES = [
    "asset id",
    "stable row id",
    "inclusion status",
    "catch-up",
    "github row",
    "quarterly tie-outs",
    "xolo expense id",
]


def build_official_register_request(
    *,
    years: list[str] | None = None,
    through_period: str = "2T 2026",
) -> str:
    requested_years = years or ["2023", "2024", "2025", "2026"]
    years_text = ", ".join(requested_years[:-1]) + f", and {requested_years[-1]}" if len(requested_years) > 1 else requested_years[0]
    return "\n".join(
        [
            "# Message To Send To Xolo",
            "",
            "Draft only. Do not send automatically.",
            "",
            "Hello,",
            "",
            "I am closing my own records after using Xolo and need a copy of the official IRPF books/registers for my autónomo professional activity.",
            "",
            f"Could you please provide, preferably in Excel/CSV or AEAT-compatible electronic format, for {years_text} up to {through_period}:",
            "",
            "1. Libro registro de ingresos",
            "2. Libro registro de gastos",
            "3. Libro registro de bienes de inversión",
            "4. Libro registro de provisiones de fondos y suplidos, even if empty or not applicable",
            "",
            "I need these registers to keep my own tax records and reconcile the Modelo 130 declarations prepared by Xolo. If Xolo uses different names for the same official books, please provide the equivalent export.",
            "",
            "If the normal Xolo data export does not include these official books, please let me know how to obtain the accounting/bookkeeping export.",
            "",
            "Thank you.",
            "",
        ]
    )


def write_official_register_request(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
