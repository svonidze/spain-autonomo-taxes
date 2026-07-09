from __future__ import annotations

import argparse
from pathlib import Path
import sys

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autonomo_taxes.xolo_expense_details import (  # noqa: E402
    enrich_raw_expense_rows,
    parse_detail_facts,
    read_csv_rows,
    write_detail_facts_csv,
    write_detail_facts_markdown,
    write_enriched_expenses_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch Xolo expense detail-page exchange rates through Playwright")
    parser.add_argument("--storage-state", type=Path, default=Path(".omx/xolo-storage-state.json"))
    parser.add_argument("--raw-csv", type=Path, required=True)
    parser.add_argument("--out-facts-csv", type=Path, required=True)
    parser.add_argument("--out-facts-md", type=Path, required=True)
    parser.add_argument("--out-enriched-csv", type=Path, required=True)
    parser.add_argument("--limit", type=int, help="Optional development limit")
    args = parser.parse_args()

    if not args.storage_state.exists():
        raise SystemExit(f"Storage state not found: {args.storage_state}. Run scripts/xolo_playwright_login.py first.")

    raw_rows = read_csv_rows(args.raw_csv)
    if args.limit:
        raw_rows = raw_rows[: args.limit]

    facts = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(storage_state=str(args.storage_state), viewport={"width": 1600, "height": 1200})
        page = context.new_page()
        for index, row in enumerate(raw_rows, start=1):
            url = row.get("xolo_url")
            if not url:
                continue
            page.goto(url, wait_until="networkidle", timeout=60_000)
            if "/hub/login" in page.url:
                raise SystemExit("Xolo session is not authenticated; rerun scripts/xolo_playwright_login.py")
            text = page.locator("body").inner_text(timeout=15_000)
            facts.append(parse_detail_facts(row, text))
            if index % 25 == 0:
                print(f"Fetched {index}/{len(raw_rows)} expense details")
        browser.close()

    fact_rows = [fact.to_row() for fact in facts]
    enriched_rows = enrich_raw_expense_rows(raw_rows, fact_rows)
    write_detail_facts_csv(args.out_facts_csv, facts)
    write_detail_facts_markdown(args.out_facts_md, fact_rows)
    write_enriched_expenses_csv(args.out_enriched_csv, enriched_rows)
    print(
        f"Fetched {len(facts)} Xolo expense detail rows into {args.out_facts_csv}, "
        f"{args.out_facts_md}, and {args.out_enriched_csv}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
