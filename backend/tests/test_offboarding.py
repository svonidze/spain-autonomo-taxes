from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from pypdf import PdfWriter

from autonomo_taxes.cli import main
from autonomo_taxes.ledger_db import initialize
from autonomo_taxes.offboarding import (
    build_offboarding_manifest,
    build_offboarding_manifest_document,
    classify_offboarding_artifact,
    verify_offboarding_manifest,
)


def _write_minimal_pdf(path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    with path.open("wb") as handle:
        writer.write(handle)


def _record_filed_obligation(db, *, period: str, form: str, pdf: Path) -> None:
    db.add_obligation(
        period_key=period,
        obligation_code=form,
        filing_status="filed",
        filed_at="2026-07-20",
        blocking=False,
    )
    db.create_filing_snapshot(
        period,
        payload={"form": form},
        status="baseline",
        form_code=form,
        source_hash=hashlib.sha256(pdf.read_bytes()).hexdigest(),
        source_reference=str(pdf),
    )


class OffboardingTests(unittest.TestCase):
    def test_builds_manifest_with_required_categories_and_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = self._write_required_category_files(tmp_path)

            rows = build_offboarding_manifest(paths)
            verification = verify_offboarding_manifest(rows)

        categories = {row["category"] for row in rows}
        self.assertEqual(
            categories,
            {
                "exports",
                "books",
                "forms",
                "justificantes_csv_nrc",
                "presenter_role",
                "rectification_docs",
                "invoice_channel_evidence",
                "advisor_signoff",
            },
        )
        self.assertTrue(all(len(row["sha256"]) == 64 for row in rows))
        self.assertTrue(verification["ok"])
        self.assertFalse(verification["blocked"])

    def test_missing_required_category_blocks_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = self._write_required_category_files(tmp_path)
            incomplete = [path for path in paths if "libro_registro" not in path.name]

            rows = build_offboarding_manifest(incomplete)
            verification = verify_offboarding_manifest(rows)

        self.assertFalse(verification["ok"])
        self.assertTrue(verification["blocked"])
        self.assertIn("books", verification["missing_categories"])

    def test_v2_manifest_is_historical_only_even_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            manifest = build_offboarding_manifest_document(
                paths,
                generated_on=date(2026, 8, 31),
            )

            fresh = verify_offboarding_manifest(
                manifest,
                required_generated_on=date(2026, 8, 31),
            )
            stale = verify_offboarding_manifest(
                manifest,
                required_generated_on=date(2026, 9, 1),
            )
            paths[0].write_text("tampered historical artifact", encoding="utf-8")
            tampered = verify_offboarding_manifest(manifest)

        self.assertFalse(fresh["ok"])
        self.assertTrue(fresh["integrity_ok"])
        self.assertTrue(fresh["manifest_fresh"])
        self.assertFalse(fresh["strict_readiness"])
        self.assertEqual(
            fresh["strict_readiness_reasons"],
            ["schema_v2_historical_only"],
        )
        self.assertEqual(fresh["manifest_generated_on"], "2026-08-31")
        self.assertFalse(stale["ok"])
        self.assertTrue(stale["integrity_ok"])
        self.assertFalse(stale["manifest_fresh"])
        self.assertEqual(
            stale["strict_readiness_reasons"],
            ["schema_v2_historical_only", "manifest_date_mismatch"],
        )
        self.assertEqual(stale["freshness_reason"], "generated_on_mismatch")
        self.assertFalse(tampered["integrity_ok"])
        self.assertEqual(
            tampered["strict_readiness_reasons"],
            ["schema_v2_historical_only", "integrity_failed"],
        )

    def test_legacy_manifest_still_verifies_but_cannot_satisfy_dated_cutover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = build_offboarding_manifest(self._write_required_category_files(root))

            historical = verify_offboarding_manifest(rows)
            cutover = verify_offboarding_manifest(
                rows,
                required_generated_on=date(2026, 8, 31),
            )

        self.assertTrue(historical["ok"])
        self.assertTrue(historical["integrity_ok"])
        self.assertIsNone(historical["manifest_fresh"])
        self.assertFalse(cutover["ok"])
        self.assertTrue(cutover["integrity_ok"])
        self.assertEqual(cutover["freshness_reason"], "generated_on_missing")

    def test_cli_builds_versioned_manifest_and_can_require_its_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            form = next(path for path in paths if path.name == "modelo_303_2t_2026.pdf")
            manifest_path = root / "manifest.json"
            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=form,
                )

            self.assertEqual(
                main(
                    [
                        "offboarding",
                        "build",
                        "--db",
                        str(database_path),
                        str(root),
                        "--generated-on",
                        "2026-07-26",
                        "--out",
                        str(manifest_path),
                    ]
                ),
                0,
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 3)
            self.assertEqual(payload["generated_on"], "2026-07-26")
            self.assertEqual(len(payload["rows"]), 8)
            self.assertEqual(
                payload["expected_filings"],
                [{"form_code": "303", "period_key": "2026-Q2"}],
            )
            self.assertEqual(
                main(
                    [
                        "offboarding",
                        "verify",
                        "--manifest",
                        str(manifest_path),
                        "--required-generated-on",
                        "2026-07-26",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "offboarding",
                        "verify",
                        "--manifest",
                        str(manifest_path),
                        "--required-generated-on",
                        "2026-07-27",
                    ]
                ),
                2,
            )

    def test_v3_matches_expected_modelo347_for_2024_and_2025(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            for year in (2024, 2025):
                form = root / f"MOD 347 0A {year} exampleei example.pdf"
                _write_minimal_pdf(form)
                paths.append(form)

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=paths[2],
                )
                for year in (2024, 2025):
                    form = root / f"MOD 347 0A {year} exampleei example.pdf"
                    _record_filed_obligation(
                        db,
                        period=str(year),
                        form="347",
                        pdf=form,
                    )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertTrue(verification["ok"])
        self.assertTrue(verification["strict_readiness"])
        self.assertEqual(verification["missing_expected_filings"], [])
        self.assertEqual(
            {
                (row["form_code"], row["period_key"])
                for row in verification["matched_expected_filings"]
                if row["form_code"] == "347"
            },
            {("347", "2024"), ("347", "2025")},
        )

    def test_v3_zero_byte_modelo347_blocks_expected_2023_filing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            zero_byte_form = root / "M347 0A 2023 exampleei example.pdf"
            zero_byte_form.touch()
            paths.append(zero_byte_form)

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                db.add_obligation(
                    period_key="2023",
                    obligation_code="347",
                    filing_status="filed",
                    filed_at="2024-02-20",
                    blocking=False,
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertFalse(verification["integrity_ok"])
        self.assertEqual(
            verification["zero_byte_required_paths"],
            [str(zero_byte_form.resolve())],
        )
        self.assertEqual(
            verification["missing_expected_filings"],
            [{"form_code": "347", "period_key": "2023"}],
        )

    def test_v3_draft_form_cannot_satisfy_a_filed_obligation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            draft = root / "DRAFT_MOD 100 0A 2025 Taxpayer.pdf"
            draft.write_bytes(b"draft modelo 100")
            paths.append(draft)

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                db.add_obligation(
                    period_key="2025",
                    obligation_code="100",
                    filing_status="filed",
                    filed_at="2026-06-30",
                    blocking=False,
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertEqual(
            verification["missing_expected_filings"],
            [{"form_code": "100", "period_key": "2025"}],
        )
        self.assertIn(
            str(draft.resolve()),
            verification["uncategorized_paths"],
        )
        self.assertNotIn(str(draft.resolve()), verification["rejected_sensitive_paths"])

    def test_v3_nonzero_corrupt_pdf_cannot_satisfy_a_filed_obligation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            corrupt = root / "MOD 100 0A 2025 Taxpayer.pdf"
            corrupt.write_bytes(b"not actually a PDF")
            paths.append(corrupt)

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                db.add_obligation(
                    period_key="2025",
                    obligation_code="100",
                    filing_status="filed",
                    filed_at="2026-06-30",
                    blocking=False,
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertEqual(
            verification["missing_expected_filings"],
            [{"form_code": "100", "period_key": "2025"}],
        )
        self.assertIn(
            str(corrupt.resolve()),
            verification["rejected_sensitive_paths"],
        )

    def test_v3_filename_only_blank_pdf_cannot_satisfy_a_filed_obligation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                db.add_obligation(
                    period_key="2026-Q2",
                    obligation_code="303",
                    filing_status="filed",
                    filed_at="2026-07-20",
                    blocking=False,
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertEqual(
            verification["missing_expected_filings"],
            [{"form_code": "303", "period_key": "2026-Q2"}],
        )

    def test_unknown_historical_modelo347_blocks_until_not_due_is_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=paths[2],
                )
                obligation = db.add_obligation(
                    period_key="2023",
                    obligation_code="347",
                    determination="unknown",
                    filing_status="unknown",
                    blocking=True,
                )
                unresolved = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )
                db.add_obligation(
                    period_key="2023",
                    obligation_code="347",
                    determination="not_due",
                    filing_status="waived",
                    blocking=False,
                    expected_row_version=obligation["row_version"],
                )
                unsupported = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )
                account_check = root / "aeat-2023-347-account-check.pdf"
                calculation = root / "independent-calculation.json"
                _write_minimal_pdf(account_check)
                calculation.write_text('{"status":"not_due"}', encoding="utf-8")
                db.add_obligation_evidence(
                    obligation_id=obligation["obligation_id"],
                    evidence_kind="aeat_account_check",
                    source_reference=str(account_check),
                    source_hash=hashlib.sha256(account_check.read_bytes()).hexdigest(),
                )
                one_source = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )
                db.add_obligation_evidence(
                    obligation_id=obligation["obligation_id"],
                    evidence_kind="independent_calculation",
                    source_reference="Reviewed independent Modelo 347 calculation",
                    source_hash=hashlib.sha256(calculation.read_bytes()).hexdigest(),
                )
                decided = build_offboarding_manifest_document(
                    [*paths, calculation],
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            unresolved_check = verify_offboarding_manifest(unresolved)
            unsupported_check = verify_offboarding_manifest(unsupported)
            one_source_check = verify_offboarding_manifest(one_source)
            decided_check = verify_offboarding_manifest(decided)

        self.assertFalse(unresolved_check["ok"])
        self.assertEqual(
            unresolved_check["unresolved_required_obligations"],
            [
                {
                    "form_code": "347",
                    "period_key": "2023",
                    "determination": "unknown",
                    "filing_status": "unknown",
                    "missing_evidence_kinds": [
                        "aeat_account_check",
                        "independent_calculation",
                    ],
                }
            ],
        )
        self.assertIn(
            "required_obligation_status_unresolved",
            unresolved_check["strict_readiness_reasons"],
        )
        self.assertFalse(unsupported_check["ok"])
        self.assertEqual(
            unsupported_check["unresolved_required_obligations"][0][
                "missing_evidence_kinds"
            ],
            ["aeat_account_check", "independent_calculation"],
        )
        self.assertFalse(one_source_check["ok"])
        self.assertEqual(
            one_source_check["unresolved_required_obligations"][0][
                "missing_evidence_kinds"
            ],
            ["independent_calculation"],
        )
        self.assertTrue(decided_check["ok"], decided_check)
        self.assertEqual(decided_check["unresolved_required_obligations"], [])

    def test_current_year_unknown_modelo347_does_not_block_cutover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=paths[2],
                )
                db.add_obligation(
                    period_key="2026",
                    obligation_code="347",
                    determination="unknown",
                    filing_status="unknown",
                    blocking=True,
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertTrue(verification["ok"])
        self.assertEqual(verification["unresolved_required_obligations"], [])

    def test_v3_requires_final_export_dated_for_manifest_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            paths[0].unlink()
            old_export = (
                root
                / "raw_exports"
                / "2026-07-17"
                / "dataexport_old.zip"
            )
            old_export.parent.mkdir(parents=True)
            old_export.write_text("old export", encoding="utf-8")
            paths[0] = old_export
            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=paths[2],
                )
                old_manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )
                final_export = (
                    root
                    / "raw_exports"
                    / "2026-07-26"
                    / "dataexport_final.zip"
                )
                final_export.parent.mkdir(parents=True, exist_ok=True)
                final_export.write_text("final export", encoding="utf-8")
                current_manifest = build_offboarding_manifest_document(
                    [*paths, final_export],
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            old_check = verify_offboarding_manifest(old_manifest)
            current_check = verify_offboarding_manifest(current_manifest)

        self.assertFalse(old_check["ok"])
        self.assertEqual(old_check["final_export_paths"], [])
        self.assertIn(
            "final_export_for_manifest_date_missing",
            old_check["strict_readiness_reasons"],
        )
        self.assertTrue(current_check["ok"])
        self.assertEqual(
            current_check["final_export_paths"],
            [str(final_export.resolve())],
        )

    def test_v3_root_level_export_with_date_cannot_replace_raw_export_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            paths[0].unlink()
            root_export = root / "xolo_data_export_2026-07-26.zip"
            root_export.write_text("root-level export", encoding="utf-8")
            paths[0] = root_export
            with initialize(root / "ledger.sqlite") as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=paths[2],
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertEqual(verification["final_export_paths"], [])
        self.assertIn(
            "final_export_for_manifest_date_missing",
            verification["strict_readiness_reasons"],
        )

    def test_procedure_receipt_hash_cannot_replace_original_filed_return(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            with initialize(root / "ledger.sqlite") as db:
                db.add_obligation(
                    period_key="2026-Q2",
                    obligation_code="303",
                    filing_status="filed",
                    filed_at="2026-07-20",
                    blocking=False,
                )
                db.create_filing_snapshot(
                    "2026-Q2",
                    payload={"form": "303", "procedure": "payment_request"},
                    status="procedure_submitted",
                    form_code="303",
                    source_hash=hashlib.sha256(paths[2].read_bytes()).hexdigest(),
                    source_reference=str(paths[2]),
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertEqual(
            verification["missing_expected_filings"],
            [{"form_code": "303", "period_key": "2026-Q2"}],
        )

    def test_v3_vendor_receipt_and_supplier_factura_cannot_satisfy_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "raw_exports" / "2026-07-26" / "dataexport.zip"
            book = root / "libro_registro_gastos_2026.xlsx"
            form = root / "modelo_303_2t_2026.pdf"
            vendor_receipt = root / "vendor_receipt.pdf"
            supplier_factura = root / "supplier_expense_factura.pdf"
            export.parent.mkdir(parents=True)
            for index, path in enumerate(
                (export, book, form, vendor_receipt, supplier_factura),
                start=1,
            ):
                if path == form:
                    _write_minimal_pdf(path)
                else:
                    path.write_text(f"artifact-{index}", encoding="utf-8")

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=form,
                )
                manifest = build_offboarding_manifest_document(
                    (export, book, form, vendor_receipt, supplier_factura),
                    generated_on=date(2026, 7, 26),
                    database=db,
                    category_overrides={
                        str(vendor_receipt): "justificantes_csv_nrc",
                        str(supplier_factura): "invoice_channel_evidence",
                    },
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertIn(
            "justificantes_csv_nrc",
            verification["missing_categories"],
        )
        self.assertIn(
            "invoice_channel_evidence",
            verification["optional_missing_categories"],
        )
        self.assertEqual(
            verification["rejected_sensitive_paths"],
            sorted(
                (
                    str(supplier_factura.resolve()),
                    str(vendor_receipt.resolve()),
                )
            ),
        )

    def test_v3_rejects_false_sensitive_classification_even_with_real_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "raw_exports" / "2026-07-26" / "dataexport.zip"
            book = root / "libro_registro_gastos_2026.xlsx"
            form = root / "modelo_303_2t_2026.pdf"
            payment = root / "aeat_payment_nrc_303_2t_2026.pdf"
            vendor_receipt = root / "vendor_receipt.pdf"
            export.parent.mkdir(parents=True)
            export.write_text("export", encoding="utf-8")
            book.write_text("book", encoding="utf-8")
            _write_minimal_pdf(form)
            _write_minimal_pdf(payment)
            _write_minimal_pdf(vendor_receipt)

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=form,
                )
                manifest = build_offboarding_manifest_document(
                    (export, book, form, payment, vendor_receipt),
                    generated_on=date(2026, 7, 26),
                    database=db,
                    category_overrides={
                        str(vendor_receipt): "justificantes_csv_nrc",
                    },
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertTrue(verification["missing_categories"] == [])
        self.assertEqual(
            verification["rejected_sensitive_paths"],
            [str(vendor_receipt.resolve())],
        )
        self.assertIn("integrity_failed", verification["strict_readiness_reasons"])

    def test_v3_invoice_channel_evidence_is_optional_for_cutover(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = [
                root / "raw_exports" / "2026-07-26" / "dataexport.zip",
                root / "libro_registro_gastos_2026.xlsx",
                root / "modelo_303_2t_2026.pdf",
                root / "nrc_justificante_2t_2026.pdf",
            ]
            for index, path in enumerate(paths, start=1):
                path.parent.mkdir(parents=True, exist_ok=True)
                if path.suffix.casefold() == ".pdf":
                    _write_minimal_pdf(path)
                else:
                    path.write_text(f"artifact-{index}", encoding="utf-8")

            database_path = root / "ledger.sqlite"
            with initialize(database_path) as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=paths[2],
                )
                manifest = build_offboarding_manifest_document(
                    paths,
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertTrue(verification["ok"])
        self.assertIn(
            "invoice_channel_evidence",
            verification["optional_missing_categories"],
        )

    def test_provider_role_rectification_and_advisor_files_are_not_blanket_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = [
                "xolo_data_export_2026-07-26.zip",
                "libro_registro_gastos_2026.xlsx",
                "modelo_303_2t_2026.pdf",
                "nrc_justificante_csv_2t_2026.pdf",
                "invoice_channel_evidence_email.txt",
            ]
            paths = []
            for index, name in enumerate(names, start=1):
                path = root / name
                path.write_text(f"artifact-{index}", encoding="utf-8")
                paths.append(path)

            verification = verify_offboarding_manifest(build_offboarding_manifest(paths))

        self.assertTrue(verification["ok"])
        self.assertEqual(verification["missing_categories"], [])
        self.assertEqual(
            verification["optional_missing_categories"],
            ["presenter_role", "rectification_docs", "advisor_signoff"],
        )

    def test_verification_rehashes_files_and_detects_mutation_or_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = self._write_required_category_files(tmp_path)
            rows = build_offboarding_manifest(paths)
            paths[0].write_text("mutated", encoding="utf-8")
            paths[1].unlink()

            verification = verify_offboarding_manifest(rows)

        self.assertFalse(verification["ok"])
        self.assertIn(str(paths[0].resolve()), verification["hash_mismatch_paths"])
        self.assertIn(str(paths[1].resolve()), verification["missing_paths"])

    def test_tax_report_beats_export_parent_when_classifying_nested_files(self):
        path = Path("Xolo export") / "TAX_REPORT" / "MOD 130 2T 2026.pdf"

        self.assertEqual(classify_offboarding_artifact(path), "forms")

    def test_v3_rejects_mutable_extracted_xolo_export_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            paths = self._write_required_category_files(archive)
            mutable_export = archive / "Xolo export" / "INVOICE"
            mutable_export.mkdir(parents=True)
            mutable_invoice = mutable_export / "customer-invoice.pdf"
            _write_minimal_pdf(mutable_invoice)
            form = next(
                path for path in paths if path.name == "modelo_303_2t_2026.pdf"
            )
            with initialize(root / "ledger.sqlite") as db:
                _record_filed_obligation(
                    db,
                    period="2026-Q2",
                    form="303",
                    pdf=form,
                )
                manifest = build_offboarding_manifest_document(
                    [archive],
                    generated_on=date(2026, 7, 26),
                    database=db,
                )

            verification = verify_offboarding_manifest(manifest)

        self.assertFalse(verification["ok"])
        self.assertIn(
            "mutable_xolo_export_tree_included",
            verification["strict_readiness_reasons"],
        )
        self.assertEqual(
            verification["mutable_export_tree_paths"],
            [str(mutable_invoice.resolve())],
        )

    def test_audit_output_named_after_modelo_is_not_filing_evidence(self):
        path = (
            Path("Xolo evidence archive")
            / "audit_outputs"
            / "modelo130"
            / "modelo130_goal_status.csv"
        )

        self.assertEqual(classify_offboarding_artifact(path), "uncategorized")

    def test_unclassified_xolo_export_content_is_part_of_export_bundle(self):
        path = Path("Xolo export") / "INVOICE" / "customer-invoice.pdf"

        self.assertEqual(classify_offboarding_artifact(path), "exports")

    def test_supplier_invoice_identifier_cannot_look_like_a_modelo(self):
        path = (
            Path("Xolo export")
            / "EXPENSE"
            / "Invoice-00002.pdf"
        )

        self.assertEqual(classify_offboarding_artifact(path), "exports")

    def test_build_accepts_directories_and_deduplicates_overlapping_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "TAX_REPORT"
            nested.mkdir()
            form = nested / "MOD 130 2T 2026.pdf"
            form.write_text("filed form", encoding="utf-8")

            rows = build_offboarding_manifest([root, form])

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["category"], "forms")

    def test_build_can_exclude_manifest_output_inside_scanned_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._write_required_category_files(root)
            manifest = root / "offboarding_manifest.json"
            manifest.write_text("stale manifest", encoding="utf-8")

            rows = build_offboarding_manifest([root], excluded_paths=[manifest])
            manifest.write_text("replacement manifest", encoding="utf-8")
            verification = verify_offboarding_manifest(rows)

        self.assertNotIn(manifest.name, {row["name"] for row in rows})
        self.assertTrue(verification["ok"])
        self.assertEqual(verification["hash_mismatch_paths"], [])

    def test_system_metadata_is_ignored_by_build_and_legacy_manifest_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            paths = self._write_required_category_files(root)
            metadata = root / "TAX_REPORT" / "desktop.ini"
            metadata.parent.mkdir()
            metadata.write_text("first system version", encoding="utf-8")

            rows = build_offboarding_manifest([root])
            self.assertNotIn(metadata.name, {row["name"] for row in rows})

            legacy_row = {
                "path": str(metadata.resolve()),
                "name": metadata.name,
                "category": "forms",
                "size_bytes": str(metadata.stat().st_size),
                "sha256": "0" * 64,
            }
            metadata.write_text("changed by the operating system", encoding="utf-8")
            verification = verify_offboarding_manifest([*rows, legacy_row])

        self.assertTrue(verification["ok"])
        self.assertEqual(verification["ignored_system_paths"], [str(metadata.resolve())])
        self.assertEqual(verification["hash_mismatch_paths"], [])

    def test_system_metadata_cannot_satisfy_a_required_category(self):
        with tempfile.TemporaryDirectory() as tmp:
            metadata = Path(tmp) / "TAX_REPORT" / "desktop.ini"
            metadata.parent.mkdir()
            metadata.write_text("system metadata", encoding="utf-8")
            row = {
                "path": str(metadata.resolve()),
                "name": metadata.name,
                "category": "forms",
                "size_bytes": str(metadata.stat().st_size),
                "sha256": "0" * 64,
            }

            verification = verify_offboarding_manifest([row])

        self.assertIn("forms", verification["missing_categories"])
        self.assertTrue(verification["blocked"])

    def test_relative_override_applies_when_scanning_absolute_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "archive"
            archive.mkdir()
            artifact = archive / "ambiguous.bin"
            artifact.write_text("evidence", encoding="utf-8")
            original_cwd = Path.cwd()
            try:
                os.chdir(root)
                rows = build_offboarding_manifest(
                    [archive.resolve()],
                    category_overrides={Path("archive") / artifact.name: "books"},
                )
            finally:
                os.chdir(original_cwd)

        self.assertEqual(rows[0]["category"], "books")

    def _write_required_category_files(self, root: Path) -> list[Path]:
        names = [
            Path("raw_exports") / "2026-07-26" / "dataexport_test.zip",
            Path("libro_registro_gastos_2026.xlsx"),
            Path("modelo_303_2t_2026.pdf"),
            Path("nrc_justificante_csv_2t_2026.pdf"),
            Path("presenter_role_authorization.pdf"),
            Path("rectification_supporting_letter.pdf"),
            Path("invoice_channel_evidence_email.txt"),
            Path("asesor_fiscal_signoff.pdf"),
        ]
        paths: list[Path] = []
        for index, name in enumerate(names, start=1):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.suffix.casefold() == ".pdf":
                _write_minimal_pdf(path)
            else:
                path.write_text(f"artifact-{index}", encoding="utf-8")
            paths.append(path)
        return paths


if __name__ == "__main__":
    unittest.main()
