from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autonomo_taxes.private_paths import configured_private_root  # noqa: E402
from autonomo_taxes.xolo_tax_calculations import (  # noqa: E402
    build_calculation_rows,
    parse_tax_report_links,
    write_xolo_tax_calculations_csv,
    write_xolo_tax_calculations_markdown,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xolo Modelo 130 calculation popups through Playwright")
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=configured_private_root() / "browser" / "xolo" / "storage-state.json",
    )
    parser.add_argument("--out-csv", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--out-json", type=Path, help="Optional raw calculation HTML bundle for debugging")
    args = parser.parse_args()

    if not args.storage_state.exists():
        raise SystemExit(f"Storage state not found: {args.storage_state}. Run scripts/xolo_playwright_login.py first.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(args.storage_state), viewport={"width": 1600, "height": 1200})
        page = context.new_page()
        page.goto("https://app.xolo.io/selfservice/tax-report", wait_until="networkidle", timeout=60_000)
        if "/hub/login" in page.url:
            raise SystemExit("Xolo session is not authenticated; rerun scripts/xolo_playwright_login.py")
        tax_report_html = page.content()
        reports = parse_tax_report_links(tax_report_html)
        calculation_html_by_report_id: dict[str, str] = {}
        for report in reports:
            report_id = report["report_id"]
            response = page.goto(
                f"https://app.xolo.io/selfservice/leap-esp/tax-report/calculation/130/{report_id}",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            if response is None or response.status >= 400:
                status = response.status if response else "no response"
                raise SystemExit(f"Failed to fetch calculation {report_id}: {status}")
            calculation_html_by_report_id[report_id] = page.content()
        browser.close()

    rows = build_calculation_rows(tax_report_html, calculation_html_by_report_id)
    write_xolo_tax_calculations_csv(args.out_csv, rows)
    write_xolo_tax_calculations_markdown(args.out_md, rows)
    if args.out_json:
        args.out_json.parent.mkdir(parents=True, exist_ok=True)
        args.out_json.write_text(
            json.dumps(
                {
                    "report_count": len(reports),
                    "reports": reports,
                    "calculation_html_by_report_id": calculation_html_by_report_id,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    print(f"Fetched {len(rows)} Xolo Modelo 130 calculation rows into {args.out_csv} and {args.out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
