from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


SCRIPT_PATH = REPO_ROOT / "scripts" / "probe_xolo_storage_state.py"
SPEC = importlib.util.spec_from_file_location("probe_xolo_storage_state", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


class ProbeXoloStorageStateTests(unittest.TestCase):
    def test_sanitizes_query_values_but_keeps_keys(self):
        url, keys = probe._sanitize_url_with_query_keys(
            "https://app.xolo.io/selfservice/expense/data?_csrf=secret&draw=1&start=20"
        )

        self.assertEqual(url, "https://app.xolo.io/selfservice/expense/data")
        self.assertEqual(keys, ["_csrf", "draw", "start"])

    def test_only_relevant_selfservice_urls_are_captured(self):
        self.assertTrue(probe._is_relevant_url("https://app.xolo.io/selfservice/tax-report"))
        self.assertFalse(probe._is_relevant_url("https://static.xolo.io/r/app.js"))

    def test_same_origin_non_selfservice_network_urls_are_captured(self):
        self.assertTrue(probe._is_network_capture_url("https://app.xolo.io/api/register/data"))
        self.assertTrue(probe._is_network_capture_url("https://app.xolo.io/leap-esp/reporting/example"))
        self.assertFalse(probe._is_network_capture_url("https://static.xolo.io/r/app.js"))

    def test_detail_pages_are_not_queued_for_broad_probe(self):
        self.assertFalse(
            probe._should_queue_link("https://app.xolo.io/selfservice/expense/invoice/1751445/details")
        )
        self.assertTrue(probe._should_queue_link("https://app.xolo.io/selfservice/tax-report"))

    def test_company_document_downloads_are_not_queued(self):
        self.assertFalse(
            probe._should_queue_link("https://app.xolo.io/selfservice/settings/company-documents/file/13094654")
        )

    def test_safe_links_adds_modelo130_calculations_only_from_income_tax_page(self):
        class FakePage:
            def __init__(self) -> None:
                self.args: list[bool] = []

            def eval_on_selector_all(self, selector, expression, include_modelo130_calculations):
                self.args.append(include_modelo130_calculations)
                links = ["/selfservice/tax-report"]
                if include_modelo130_calculations:
                    links.append("/selfservice/leap-esp/tax-report/calculation/130/8246")
                return links

        tax_page = FakePage()
        tax_links = probe._safe_links(tax_page, "https://app.xolo.io/selfservice/tax-report")
        vat_page = FakePage()
        vat_links = probe._safe_links(vat_page, "https://app.xolo.io/selfservice/leap-esp/tax-report/vat")

        self.assertTrue(tax_page.args[0])
        self.assertIn("https://app.xolo.io/selfservice/leap-esp/tax-report/calculation/130/8246", tax_links)
        self.assertFalse(vat_page.args[0])
        self.assertNotIn("https://app.xolo.io/selfservice/leap-esp/tax-report/calculation/130/8246", vat_links)

    def test_network_summary_groups_without_query_values(self):
        items = [
            probe.NetworkObservation(
                source_page="https://app.xolo.io/selfservice/expense",
                method="POST",
                status=200,
                resource_type="xhr",
                content_type="application/json; charset=utf-8",
                url="https://app.xolo.io/selfservice/expense/data",
                query_keys=["_csrf", "draw"],
            ),
            probe.NetworkObservation(
                source_page="https://app.xolo.io/selfservice/expense",
                method="POST",
                status=200,
                resource_type="xhr",
                content_type="application/json",
                url="https://app.xolo.io/selfservice/expense/data",
                query_keys=["_csrf", "start"],
            ),
        ]

        rows = probe._network_summary_rows(items)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 2)
        self.assertEqual(rows[0]["query_keys"], ["_csrf", "draw", "start"])

    def test_markdown_records_auth_source_without_secret_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "probe.md"
            probe.write_markdown(
                path,
                pages=[],
                network=[],
                auth_source="profile-dir:.omx/xolo-playwright-profile",
            )

            markdown = path.read_text(encoding="utf-8")

        self.assertIn("# Xolo Auth Endpoint Probe", markdown)
        self.assertIn("Auth source: `profile-dir:.omx/xolo-playwright-profile`", markdown)
        self.assertNotIn("__Host-SESSION", markdown)


if __name__ == "__main__":
    unittest.main()
