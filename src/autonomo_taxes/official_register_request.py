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
    "source-data",
    "raw table",
    "primary key",
    "internal audit",
    "backend",
    "calculation worksheets",
    "per-item calculation",
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
            "Subject: Request for my official accounting books and filing records",
            "",
            "Hello,",
            "",
            "I have downloaded the standard Xolo Data export for my account. It contains my invoice, expense, and tax-report PDFs, but it does not appear to include the official accounting/register books or the filing records I need to keep my own complete records for my autónomo professional activity after I stop using Xolo.",
            "",
            "Could you please provide any official or standard documents and exports that Xolo holds or prepared for my account and that are not included in the standard Data export? Where possible, please provide them in Excel/CSV or an AEAT-compatible electronic format.",
            "",
            f"For {years_text} up to and including {through_period}, please include the following where they exist or apply:",
            "",
            "1. IRPF registration books (direct estimation):",
            "- Libro registro de ingresos",
            "- Libro registro de gastos",
            "- Libro registro de bienes de inversión, including the amortization/depreciation schedule used for the returns",
            "- Libro registro de provisiones de fondos y suplidos (please confirm even if empty or not applicable)",
            "",
            "2. IVA registration books (if Xolo keeps or prepared them for me):",
            "- Libro registro de facturas expedidas",
            "- Libro registro de facturas recibidas",
            "- Libro registro de bienes de inversión",
            "- Libro registro de determinadas operaciones intracomunitarias, if applicable",
            "",
            "3. Filed tax forms and filing evidence not already in the Data export:",
            "- Returns prepared or filed on my behalf, e.g. Modelo 130, 303, 390, 100, 349, 216, 296, or any other model filed for my activity",
            "- The corresponding justificantes de presentación, NRC/payment references, and any payment or deferral receipts",
            "",
            "4. Standard bookkeeping exports and year-end summaries generated for my account, such as annual or quarterly ledgers, accounting exports, and the summaries used to prepare the returns.",
            "",
            "If any item does not exist, does not apply to my situation, or cannot be exported, please just confirm that explicitly. If Xolo uses different names for the same official books or records, the equivalent export is fine.",
            "",
            "Thank you.",
            "",
        ]
    )


def write_official_register_request(path: Path, markdown: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown, encoding="utf-8")
