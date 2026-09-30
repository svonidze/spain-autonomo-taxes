from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from autonomo_taxes.cli import main
from autonomo_taxes.source_book_availability import build_source_book_availability


class SourceBookAvailabilityTests(unittest.TestCase):
    def test_consolidates_negative_source_book_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_fixture_files(tmp_path)

            rows = build_source_book_availability(**paths)

        by_source = {row["source"]: row for row in rows}
        self.assertEqual(by_source["first_chronological_gate"]["status"], "blocked_material_unexplained_adjustment")
        self.assertIn("2023-Q2", by_source["first_chronological_gate"]["evidence"])
        self.assertEqual(by_source["google_drive_xolo_export_archive"]["status"], "missing_required_evidence")
        self.assertEqual(by_source["latest_xolo_dataexport_zip"]["status"], "missing_required_evidence")
        self.assertEqual(by_source["historical_xolo_dataexport_archives"]["evidence_count"], "0")
        self.assertEqual(by_source["authenticated_xolo_ui_probe"]["status"], "available_but_insufficient")
        self.assertIn("login-like pages=0", by_source["authenticated_xolo_ui_probe"]["evidence"])
        self.assertEqual(by_source["xolo_support_request"]["status"], "ready_to_send")

    def test_includes_safe_request_package_when_manifest_is_provided(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_fixture_files(tmp_path, request_package=True)

            rows = build_source_book_availability(**paths)

        by_source = {row["source"]: row for row in rows}
        self.assertEqual(by_source["xolo_source_book_request_package"]["status"], "ready_to_send_package")
        self.assertIn("included=2", by_source["xolo_source_book_request_package"]["evidence"])
        self.assertIn("missing_or_broken=0", by_source["xolo_source_book_request_package"]["evidence"])

    def test_does_not_overclaim_when_candidates_or_login_wall_appear(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_fixture_files(
                tmp_path,
                local_candidate_count=2,
                dataexport_candidate_count=1,
                historical_candidate_count=3,
                support_exists=False,
                probe_login_wall=True,
            )

            rows = build_source_book_availability(**paths)

        by_source = {row["source"]: row for row in rows}
        self.assertEqual(by_source["google_drive_xolo_export_archive"]["status"], "candidate_found")
        self.assertIn("has candidate", by_source["google_drive_xolo_export_archive"]["conclusion"])
        self.assertEqual(by_source["latest_xolo_dataexport_zip"]["status"], "candidate_found")
        self.assertIn("contains source-book", by_source["latest_xolo_dataexport_zip"]["conclusion"])
        self.assertEqual(by_source["historical_xolo_dataexport_archives"]["status"], "candidate_found")
        self.assertIn("At least one historical", by_source["historical_xolo_dataexport_archives"]["conclusion"])
        self.assertEqual(by_source["authenticated_xolo_ui_probe"]["status"], "auth_probe_not_authenticated")
        self.assertIn("cannot prove", by_source["authenticated_xolo_ui_probe"]["conclusion"])
        self.assertEqual(by_source["xolo_support_request"]["status"], "missing")

    def test_cli_writes_source_book_availability_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = _write_fixture_files(tmp_path)
            out_csv = tmp_path / "availability.csv"
            out_md = tmp_path / "availability.md"

            exit_code = main(
                [
                    "audit-source-book-availability",
                    "--first-gate",
                    str(paths["first_gate_csv"]),
                    "--evidence-inventory",
                    str(paths["evidence_inventory_csv"]),
                    "--dataexport-inventory",
                    str(paths["dataexport_inventory_csv"]),
                    "--dataexport-archives",
                    str(paths["dataexport_archives_csv"]),
                    "--storage-probe",
                    str(paths["storage_probe_json"]),
                    "--support-request",
                    str(paths["support_request_md"]),
                    "--request-package-manifest",
                    str(paths["request_package_manifest_csv"]),
                    "--out-csv",
                    str(out_csv),
                    "--out-md",
                    str(out_md),
                ]
            )

            self.assertEqual(exit_code, 0)
            self.assertTrue(out_csv.exists())
            markdown = out_md.read_text(encoding="utf-8")
            self.assertIn("Xolo Source-Book Availability", markdown)
            self.assertIn("Do not tune downstream amortization", markdown)
            self.assertIn("Xolo records request package is ready", markdown)
            self.assertNotIn("internal books", markdown.lower())


def _write_fixture_files(
    tmp_path: Path,
    *,
    local_candidate_count: int = 0,
    dataexport_candidate_count: int = 0,
    historical_candidate_count: int = 0,
    support_exists: bool = True,
    probe_login_wall: bool = False,
    request_package: bool = True,
) -> dict[str, Path]:
    first_gate = tmp_path / "first_gate.csv"
    _write_csv(
        first_gate,
        [
            "section",
            "period",
            "key",
            "status",
            "amount_eur",
            "fit_signal",
            "evidence_status",
            "finding",
            "source_ref",
            "xolo_question",
        ],
        [
            {
                "section": "gate",
                "period": "2023-Q2",
                "status": "blocked_material_unexplained_adjustment",
                "amount_eur": "115.54",
            },
            {
                "section": "raw_context",
                "period": "2023-Q2",
                "amount_eur": "94.06",
                "fit_signal": "21.48",
            },
        ],
    )
    evidence = tmp_path / "evidence.csv"
    _write_csv(
        evidence,
        ["category", "period", "count", "paths"],
        [
            {"category": "candidate_source_book_row_evidence", "count": str(local_candidate_count)},
            {"category": "candidate_asset_schedule", "count": "0"},
            {"category": "scan_local_files", "count": "366"},
            {"category": "scan_zip_members", "count": "54"},
        ],
    )
    dataexport = tmp_path / "dataexport.csv"
    _write_csv(
        dataexport,
        ["kind", "key", "count", "paths"],
        [
            {"kind": "top_level", "key": "EXPENSE", "count": "173"},
            {"kind": "top_level", "key": "INVOICE", "count": "49"},
            {"kind": "top_level", "key": "TAX_REPORT", "count": "30"},
            {
                "kind": "conclusion",
                "key": "candidate_source_book_or_asset_schedule",
                "count": str(dataexport_candidate_count),
            },
        ],
    )
    archives = tmp_path / "archives.csv"
    _write_csv(
        archives,
        ["archive", "candidate_source_book_or_asset_files"],
        [
            {
                "archive": "dataexport_old.zip",
                "candidate_source_book_or_asset_files": str(historical_candidate_count),
            },
            {"archive": "dataexport_new.zip", "candidate_source_book_or_asset_files": "0"},
        ],
    )
    storage_probe = tmp_path / "storage_probe.json"
    storage_probe.write_text(
        json.dumps(
            {
                "pages": _probe_pages(probe_login_wall)
            }
        ),
        encoding="utf-8",
    )
    support = tmp_path / "support.md"
    if support_exists:
        support.write_text("# support request\n", encoding="utf-8")
    package_manifest = tmp_path / "package_manifest.csv"
    if request_package:
        _write_csv(
            package_manifest,
            ["package_path", "source_path", "role", "status", "size", "sha256", "notes"],
            [
                {
                    "package_path": "package/message_to_xolo.md",
                    "source_path": "runs/xolo_support_message_to_send.md",
                    "role": "message",
                    "status": "included",
                },
                {
                    "package_path": "package/attachments/xolo_source_book_response_check.md",
                    "source_path": "runs/xolo_source_book_response_check.md",
                    "role": "optional_attachment",
                    "status": "included",
                },
            ],
        )
    paths = {
        "first_gate_csv": first_gate,
        "evidence_inventory_csv": evidence,
        "dataexport_inventory_csv": dataexport,
        "dataexport_archives_csv": archives,
        "storage_probe_json": storage_probe,
        "support_request_md": support,
    }
    if request_package:
        paths["request_package_manifest_csv"] = package_manifest
    return paths


def _probe_pages(login_wall: bool) -> list[dict[str, str | int]]:
    if login_wall:
        return [
            {
                "source_url": "https://app.xolo.io/selfservice/tax-report",
                "final_url": "https://app.xolo.io/hub/login",
                "status": 200,
                "title": "Log in to your account | Xolo",
            }
        ]
    return [
        {"source_url": "https://app.xolo.io/selfservice", "final_url": "", "status": 200, "title": "Dashboard"},
        {
            "source_url": "https://app.xolo.io/selfservice/assets",
            "final_url": "",
            "status": 403,
            "title": "Forbidden",
        },
        {
            "source_url": "https://app.xolo.io/selfservice/leap-esp/tax-report/calculation/130/8246",
            "final_url": "",
            "status": 200,
            "title": "",
        },
    ]


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


if __name__ == "__main__":
    unittest.main()
