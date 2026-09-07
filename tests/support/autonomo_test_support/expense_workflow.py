"""Reusable synthetic fixtures shared by backend test suites."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
from uuid import uuid4
import pytest
from autonomo_taxes.depreciation import schedule
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION, open as open_db
from autonomo_taxes.expense_workflow import get_draft, save_draft, preview, confirm_and_post, recognize_period, ExpenseWorkflowError
from autonomo_taxes.tax_row_loader import load_tax_rows
from autonomo_taxes.tax_engine import calculate_modelo303_rows
from autonomo_test_support.review_confirm import _invoice_fixture, _approve_decision
from autonomo_test_support.aeat_books import _profile_and_activity

def setup_draft(tmp_path, *, method=None, transaction_date=None):
    fixture = _invoice_fixture(tmp_path, transaction_date=transaction_date)
    with open_db(fixture['database']) as db:
        _profile_and_activity(db)
        draft = get_draft(db, fixture['transaction_id'])
        _approve_decision({'decision': draft['payload']['decision']})
        draft['payload']['change_reason'] = 'Reviewed facts against immutable original'
        if method:
            draft['payload']['asset'] = dict(description='Synthetic test equipment',basis_minor=10000,business_use_ratio=1.0,
                annual_rate_basis_points=10000 if method=='immediate' else 2500,placed_in_service_on=transaction_date or date.today().isoformat(),
                method=method,new_equipment=True,aeat_asset_type='23')
        saved = save_draft(db, fixture['transaction_id'], payload=draft['payload'], expected_version=0,
                           source_snapshot_hash=draft['source_snapshot_hash'],actor='synthetic-user')
    return fixture,saved
