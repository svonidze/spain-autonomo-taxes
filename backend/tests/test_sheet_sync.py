from __future__ import annotations

import unittest

from autonomo_taxes.sheet_sync import (
    apply_sheet_pull,
    apply_sheet_push,
    diff_sheet_rows,
    stable_row_uuid,
)


class SheetSyncTests(unittest.TestCase):
    def test_stable_row_uuid_is_deterministic(self):
        left = stable_row_uuid("invoice", "2026-06", "INV-1")
        right = stable_row_uuid("invoice", "2026-06", "INV-1")
        other = stable_row_uuid("invoice", "2026-06", "INV-2")

        self.assertEqual(left, right)
        self.assertNotEqual(left, other)

    def test_push_pull_diff_is_idempotent_after_apply(self):
        invoice_uuid = stable_row_uuid("invoice", "INV-1")
        tax_uuid = stable_row_uuid("tax", "Q2-2026")
        notes_uuid = stable_row_uuid("note", "2026-07")

        local_rows = [
            {"uuid": invoice_uuid, "row_version": 1, "status": "open", "label": "invoice"},
            {"uuid": tax_uuid, "row_version": 2, "status": "open", "label": "vat", "amount": "450.00"},
        ]
        remote_rows = [
            {"uuid": tax_uuid, "row_version": 1, "status": "open", "label": "vat", "amount": "400.00"},
            {"uuid": notes_uuid, "row_version": 1, "status": "open", "label": "note"},
        ]

        plan = diff_sheet_rows(local_rows, remote_rows)

        self.assertEqual([row.uuid for row in plan.push_create], [invoice_uuid])
        self.assertEqual([(update.row.uuid, update.expected_row_version) for update in plan.push_update], [(tax_uuid, 1)])
        self.assertEqual([row.uuid for row in plan.pull_create], [notes_uuid])
        self.assertEqual(plan.pull_update, ())
        self.assertEqual(plan.conflicts, ())

        remote_after_push = apply_sheet_push(remote_rows, plan)
        local_after_pull = apply_sheet_pull(local_rows, plan)
        local_after_sync = apply_sheet_pull(local_after_pull, diff_sheet_rows(local_after_pull, remote_after_push.rows))
        final_plan = diff_sheet_rows(local_after_sync, remote_after_push.rows)

        self.assertEqual(remote_after_push.conflicts, ())
        self.assertEqual(final_plan.push_create, ())
        self.assertEqual(final_plan.push_update, ())
        self.assertEqual(final_plan.pull_create, ())
        self.assertEqual(final_plan.pull_update, ())
        self.assertEqual(final_plan.conflicts, ())

    def test_closed_rows_are_immutable(self):
        row_uuid = stable_row_uuid("closed", "Q1-2026")
        local_rows = [
            {"uuid": row_uuid, "row_version": 2, "status": "closed", "label": "filed", "amount": "450.00"},
        ]
        remote_rows = [
            {"uuid": row_uuid, "row_version": 2, "status": "closed", "label": "filed", "amount": "400.00"},
        ]

        plan = diff_sheet_rows(local_rows, remote_rows)

        self.assertEqual(plan.push_create, ())
        self.assertEqual(plan.push_update, ())
        self.assertEqual(plan.pull_create, ())
        self.assertEqual(plan.pull_update, ())
        self.assertEqual(len(plan.conflicts), 1)
        self.assertEqual(plan.conflicts[0].reason, "immutable_row")

    def test_push_apply_detects_stale_row_version_conflicts(self):
        row_uuid = stable_row_uuid("invoice", "INV-1")
        local_rows = [
            {"uuid": row_uuid, "row_version": 2, "status": "open", "label": "invoice", "amount": "120.00"},
        ]
        remote_rows = [
            {"uuid": row_uuid, "row_version": 1, "status": "open", "label": "invoice", "amount": "100.00"},
        ]
        remote_advanced = [
            {"uuid": row_uuid, "row_version": 2, "status": "open", "label": "invoice", "amount": "110.00"},
        ]

        plan = diff_sheet_rows(local_rows, remote_rows)
        result = apply_sheet_push(remote_advanced, plan)

        self.assertEqual(len(plan.push_update), 1)
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.conflicts[0].reason, "stale_row_version")


if __name__ == "__main__":
    unittest.main()
