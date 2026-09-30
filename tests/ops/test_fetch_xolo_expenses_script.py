from __future__ import annotations
from autonomo_test_support.paths import REPO_ROOT

import importlib.util
from pathlib import Path
import unittest


SCRIPT = REPO_ROOT / "scripts" / "imports" / "fetch_xolo_expenses.py"
SPEC = importlib.util.spec_from_file_location("fetch_xolo_expenses_script", SCRIPT)
assert SPEC and SPEC.loader
fetch_xolo_expenses = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fetch_xolo_expenses)


class FetchXoloExpensesScriptTests(unittest.TestCase):
    def test_extracts_supported_csrf_shapes(self):
        self.assertEqual(fetch_xolo_expenses._extract_csrf('<input name="_csrf" value="abc">'), "abc")
        self.assertEqual(fetch_xolo_expenses._extract_csrf('<input value="def" name="_csrf">'), "def")
        self.assertEqual(fetch_xolo_expenses._extract_csrf('<meta name="csrf-token" content="ghi">'), "ghi")
        self.assertEqual(fetch_xolo_expenses._extract_csrf('<meta content="jkl" name="csrf-token">'), "jkl")

    def test_extract_csrf_fails_clearly_when_missing(self):
        with self.assertRaisesRegex(SystemExit, "Could not find Xolo CSRF token"):
            fetch_xolo_expenses._extract_csrf("<html></html>")

    def test_payload_preserves_draw_and_pagination(self):
        payload = fetch_xolo_expenses._payload(draw=7, start=140, length=20)

        self.assertEqual(payload["draw"], 7)
        self.assertEqual(payload["start"], 140)
        self.assertEqual(payload["length"], 20)
        self.assertEqual([column["data"] for column in payload["columns"]], ["party", "categoryText", "number", "date", "paymentDate", "amount", "status"])


if __name__ == "__main__":
    unittest.main()
