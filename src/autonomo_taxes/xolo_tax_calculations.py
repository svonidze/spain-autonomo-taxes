from __future__ import annotations

import csv
import html
from html.parser import HTMLParser
from pathlib import Path
import re

from .money import parse_amount


XOLO_TAX_CALCULATION_FIELDS = [
    "year",
    "quarter",
    "period",
    "report_id",
    "file_id",
    "filename",
    "submitted_date",
    "xolo_status",
    "amount_due",
    "total_compounded_sales_ytd",
    "total_compounded_deductible_expenses_ytd",
    "net_results_ytd",
    "net_results_20_percent",
    "previous_quarters_compensation",
    "withholding_taxes",
    "article_110_3_reduction",
    "payable_irpf_for_quarter",
]


def parse_tax_report_links(html_text: str) -> list[dict[str, str]]:
    parser = _AnchorParser()
    parser.feed(html_text)
    reports: dict[tuple[str, str], dict[str, str]] = {}
    for anchor in parser.anchors:
        attrs = anchor["attrs"]
        element_id = attrs.get("id", "")
        match = re.match(r"row-dots-(\d{4})-(\d)-calculation", element_id)
        if match:
            key = (match.group(1), match.group(2))
            reports.setdefault(key, {"year": key[0], "quarter": key[1]})
            reports[key]["report_id"] = attrs.get("data-report-id", "")
        match = re.match(r"row-dots-(\d{4})-(\d)-view", element_id)
        if match:
            key = (match.group(1), match.group(2))
            reports.setdefault(key, {"year": key[0], "quarter": key[1]})
            reports[key].update(
                {
                    "filename": attrs.get("data-report-filename", ""),
                    "submitted_date": attrs.get("data-report-submitted-date", ""),
                    "file_id": attrs.get("data-report-file-id", ""),
                    "period": _period_label(attrs.get("data-report-period", ""), key[0], key[1]),
                }
            )
    return [
        row
        for row in sorted(reports.values(), key=lambda item: (int(item["year"]), int(item["quarter"])))
        if row.get("report_id")
    ]


def parse_calculation_html(html_text: str) -> dict[str, str]:
    text = _plain_text(html_text)
    values: dict[str, str] = {}
    period_match = re.search(r"Model 130\s+(\d{4}-\d{2}-\d{2}\s+-\s+\d{4}-\d{2}-\d{2})", text)
    if period_match:
        values["period_range"] = period_match.group(1)
    money = r"(€\s*-?[\d\s\xa0.]+,\d{2})"
    patterns = {
        "total_compounded_sales_ytd": rf"Total compounded sales YTD\s+{money}",
        "total_compounded_deductible_expenses_ytd": rf"Total compounded deductible expenses YTD\s+{money}",
        "net_results_ytd": rf"Net results YTD\s+{money}",
        "net_results_20_percent": rf"20% of net results\s+{money}",
        "previous_quarters_compensation": rf"Subtract the amount to be compensated from previous quarters\s+{money}",
        "withholding_taxes": rf"Substract withholding taxes applied in sales invoices\s+{money}",
        "article_110_3_reduction": rf"Reduction by application of the deduction of article 110\.3\s+{money}",
        "payable_irpf_for_quarter": rf"Payable IRPF for the quarter\s+{money}",
    }
    for field, pattern in patterns.items():
        match = re.search(pattern, text)
        values[field] = _normalize_money(match.group(1)) if match else ""
    return values


def build_calculation_rows(tax_report_html: str, calculation_html_by_report_id: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for report in parse_tax_report_links(tax_report_html):
        calculation = parse_calculation_html(calculation_html_by_report_id.get(report["report_id"], ""))
        amount_due = calculation.get("payable_irpf_for_quarter", "")
        rows.append(
            {
                "year": report.get("year", ""),
                "quarter": report.get("quarter", ""),
                "period": report.get("period", ""),
                "report_id": report.get("report_id", ""),
                "file_id": report.get("file_id", ""),
                "filename": report.get("filename", ""),
                "submitted_date": report.get("submitted_date", ""),
                "xolo_status": _status(report),
                "amount_due": amount_due,
                "total_compounded_sales_ytd": calculation.get("total_compounded_sales_ytd", ""),
                "total_compounded_deductible_expenses_ytd": calculation.get(
                    "total_compounded_deductible_expenses_ytd", ""
                ),
                "net_results_ytd": calculation.get("net_results_ytd", ""),
                "net_results_20_percent": calculation.get("net_results_20_percent", ""),
                "previous_quarters_compensation": calculation.get("previous_quarters_compensation", ""),
                "withholding_taxes": calculation.get("withholding_taxes", ""),
                "article_110_3_reduction": calculation.get("article_110_3_reduction", ""),
                "payable_irpf_for_quarter": calculation.get("payable_irpf_for_quarter", ""),
            }
        )
    return rows


def write_xolo_tax_calculations_csv(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=XOLO_TAX_CALCULATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_xolo_tax_calculations_markdown(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Xolo Modelo 130 Calculations",
        "",
        "Fetched from Xolo's authenticated tax-report calculation endpoint.",
        "These rows confirm Xolo's quarter-level YTD calculation, but they are not the submitted row-level expense register.",
        "",
        "| Period | Status | Report id | File id | Sales YTD | Expenses YTD | Payable | Submitted |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row["period"],
                    row["xolo_status"],
                    row["report_id"],
                    row["file_id"],
                    _fmt(row["total_compounded_sales_ytd"]),
                    _fmt(row["total_compounded_deductible_expenses_ytd"]),
                    _fmt(row["payable_irpf_for_quarter"]),
                    row["submitted_date"],
                ]
            )
            + " |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _period_label(period_range: str, year: str, quarter: str) -> str:
    if period_range:
        return f"{year}-Q{quarter}"
    return f"{year}-Q{quarter}"


def _status(report: dict[str, str]) -> str:
    if report.get("file_id") and report.get("submitted_date"):
        return "submitted"
    return "forecast_or_unsubmitted"


def _normalize_money(value: str) -> str:
    return f"{parse_amount(value):.2f}"


def _plain_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html.unescape(value))).strip()


def _fmt(value: str) -> str:
    return f"{parse_amount(value):,.2f}" if value else ""


class _AnchorParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.anchors: list[dict[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self.anchors.append({"attrs": {key: value or "" for key, value in attrs}})
