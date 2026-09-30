from autonomo_test_support.expense_workflow import setup_draft
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




def commit(db, fixture, draft, tmp_path, request=None):
    proposed=preview(db,fixture['transaction_id'],expected_version=draft['draft_version'])
    return confirm_and_post(db,fixture['transaction_id'],expected_version=draft['draft_version'],
        preview_token=proposed['preview_token'],request_id=request or str(uuid4()),actor='synthetic-user',archive_root=tmp_path/'archive')


@pytest.mark.parametrize('method',[None,'immediate','linear'])
def test_complete_workflow(tmp_path,method):
    fixture,draft=setup_draft(tmp_path,method=method)
    with open_db(fixture['database']) as db:
        before='\n'.join(db.connection.iterdump())
        proposed=preview(db,fixture['transaction_id'],expected_version=draft['draft_version'])
        assert '\n'.join(db.connection.iterdump())==before
        request=str(uuid4())
        result=confirm_and_post(db,fixture['transaction_id'],expected_version=draft['draft_version'],
            preview_token=proposed['preview_token'],request_id=request,actor='synthetic-user',archive_root=tmp_path/'archive')
        assert result['posted']
        assert confirm_and_post(db,fixture['transaction_id'],expected_version=draft['draft_version'],
            preview_token=proposed['preview_token'],request_id=request,actor='synthetic-user',archive_root=tmp_path/'archive')==result
        rows=load_tax_rows(db,date.today().year)
        assert sum(r.deductible_vat_eur for r in rows)==Decimal('21.00')
        assert sum(r.deductible_irpf_eur for r in rows)==(0 if method=='linear' else Decimal('100.00'))
        assert db.connection.execute('SELECT COUNT(*) FROM payments').fetchone()[0]==0
        assert db.connection.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==bool(method)
        if method:
            entries=db.connection.execute('SELECT * FROM amortization_entries').fetchall()
            assert sum(row['amount_minor'] for row in entries)==10000
            if method=='immediate':
                assert len(entries)==1 and entries[0]['recognition_transaction_id'] and entries[0]['include_in_books']==1
            else:
                assert all(row['recognition_transaction_id'] is None and row['include_in_books']==0 for row in entries)
        vat=calculate_modelo303_rows(rows,year=date.today().year,quarter=(date.today().month-1)//3+1)
        assert vat.values['29']==Decimal('21.00') and vat.values['31']==0


def test_rollback_after_journal_failure(tmp_path,monkeypatch):
    import autonomo_taxes.expense_workflow as workflow
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        before='\n'.join(db.connection.iterdump())
        original=workflow._recognize
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('after journal write')
        monkeypatch.setattr(workflow,'_recognize',fail)
        with pytest.raises(RuntimeError):commit(db,fixture,draft,tmp_path)
        assert '\n'.join(db.connection.iterdump())==before


def test_changed_approved_draft_invalidates_approval(tmp_path):
    from autonomo_taxes.review_packet import prepare_review_packet,confirm_review_packet
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        packet=prepare_review_packet(db,'transaction:'+fixture['transaction_id']);_approve_decision(packet);confirm_review_packet(db,packet)
        fresh=get_draft(db,fixture['transaction_id'])
        payload=deepcopy(fresh['payload']);payload['facts']['document_number']='CORRECTED-SYNTHETIC'
        payload['change_reason']='Corrected against the source document'
        saved=save_draft(db,fixture['transaction_id'],payload=payload,expected_version=fresh['draft_version'],
                         source_snapshot_hash=fresh['current_snapshot_hash'],actor='synthetic-user')
        assert saved['source']['transaction']['lifecycle_status']=='needs_review'
        assert saved['payload']['facts']['document_number']=='CORRECTED-SYNTHETIC'
        assert saved['source']['document']['document_number']!='CORRECTED-SYNTHETIC'


def test_stale_draft_and_preview_rejected(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        with pytest.raises(ExpenseWorkflowError,match='changed'):
            preview(db,fixture['transaction_id'],expected_version=0)
        with pytest.raises(ExpenseWorkflowError,match='Preview is stale'):
            confirm_and_post(db,fixture['transaction_id'],expected_version=draft['draft_version'],preview_token='old',
                request_id=str(uuid4()),actor='synthetic-user',archive_root=tmp_path/'archive')


@pytest.mark.parametrize('start',['2024-01-01','2024-02-29','2025-11-27','2025-12-31'])
def test_schedule_is_bounded_and_uses_one_share(start):
    rows=schedule(basis_minor=10101,business_use_ratio=0.5,annual_rate_basis_points=2500,placed_in_service_on=start,method='linear')
    assert sum(row['amount_minor'] for row in rows)==5051
    assert all(row['amount_minor']>0 and row['recognition_on']>=start for row in rows)
    assert len({row['period_key'] for row in rows})==len(rows)


def test_whole_leap_year_equals_annual_rate():
    rows=schedule(basis_minor=100000,business_use_ratio=1,annual_rate_basis_points=2500,placed_in_service_on='2024-01-01',method='linear')
    assert sum(row['amount_minor'] for row in rows if row['period_key'].startswith('2024'))==25000


@pytest.mark.parametrize('method',['immediate','linear'])
def test_native_annual_totals_are_actual_and_not_external_evidence(tmp_path,method):
    from autonomo_taxes.depreciation import native_year_summary
    fixture,draft=setup_draft(tmp_path,method=method)
    asset_year = date.fromisoformat(draft['payload']['asset']['placed_in_service_on']).year
    with open_db(fixture['database']) as db:
        result=commit(db,fixture,draft,tmp_path)
        native=native_year_summary(db.connection,result['asset_id'],asset_year)
        annual=db.validate_asset_year(asset_year)
        if method=='immediate':
            assert native['current_minor']==10000 and not native['pending']
            assert annual['ready']
            assert annual['assets'][0]['quarter_minor']==annual['assets'][0]['annual_minor']==10000
        else:
            assert native['current_minor']==0 and native['pending']
            assert not annual['ready']
            assert {i['issue_code'] for i in annual['issues']}=={'pending_native_depreciation'}
        assert db.connection.execute("SELECT COUNT(*) FROM amortization_entries WHERE entry_kind='annual_evidence'").fetchone()[0]==0


def test_future_schedule_cannot_post_or_duplicate(tmp_path):
    fixture,draft=setup_draft(tmp_path,method='linear')
    with open_db(fixture['database']) as db:
        result=commit(db,fixture,draft,tmp_path)
        entry=db.connection.execute('SELECT * FROM amortization_entries WHERE asset_id=? ORDER BY recognition_on DESC',(result['asset_id'],)).fetchone()
        before='\n'.join(db.connection.iterdump())
        with pytest.raises(ExpenseWorkflowError,match='date has not arrived'):
            recognize_period(db,entry['amortization_entry_id'],expected_version=entry['row_version'],request_id=str(uuid4()),actor='test',archive_root=tmp_path/'archive')
        assert '\n'.join(db.connection.iterdump())==before


def test_duplicate_supplier_is_not_created(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        db.connection.execute('UPDATE counterparties SET tax_id=?',('TEST-TAX-ID-001',));db.connection.commit()
        fresh=get_draft(db,fixture['transaction_id']);p=deepcopy(fresh['payload']);p['facts']['counterparty_id']=None
        p['supplier']={'display_name':'Another shop name','tax_id':'TEST-TAX-ID-001','vat_id':'','country_code':'ES'}
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')
        with pytest.raises(ExpenseWorkflowError,match='supplier with this identifier exists'):
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert db.connection.execute('SELECT COUNT(*) FROM counterparties').fetchone()[0]==1


def test_new_supplier_with_vat_id_is_created_atomically(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        p=deepcopy(draft['payload']);p['facts']['counterparty_id']=None
        p['supplier']={'display_name':'New Synthetic Supplier','tax_id':'TEST-SUPPLIER-NEW','vat_id':'ES-TEST-SUPPLIER-NEW','country_code':'ES'}
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')
        before='\n'.join(db.connection.iterdump())
        preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert '\n'.join(db.connection.iterdump())==before
        commit(db,fixture,saved,tmp_path)
        party=db.connection.execute("SELECT * FROM counterparties WHERE tax_id='TEST-SUPPLIER-NEW'").fetchone()
        assert party['vat_id']=='ES-TEST-SUPPLIER-NEW'
        assert db.connection.execute('SELECT COUNT(*) FROM counterparties').fetchone()[0]==2


def test_asset_basis_cannot_exceed_real_invoice_gross(tmp_path):
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        p=deepcopy(draft['payload']);p['asset']['basis_minor']=30000
        p['decision']['tax_treatment'].update(taxable_base_minor=30000,vat_minor=6300,deductible_vat_minor=6300)
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')
        before='\n'.join(db.connection.iterdump())
        with pytest.raises(ExpenseWorkflowError,match='reconcile'):
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert '\n'.join(db.connection.iterdump())==before


@pytest.mark.parametrize('category,concept',[('21','G29'),('23','G31'),('24','G28'),('25','G28'),('28','G32'),('29','G28')])
def test_journal_concept_comes_from_reviewed_equipment_category(tmp_path,category,concept):
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        p=deepcopy(draft['payload']);p['asset']['aeat_asset_type']=category
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')
        result=commit(db,fixture,saved,tmp_path)
        journal=result['recognitions'][0]['transaction_id']
        assert db.connection.execute('SELECT aeat_expense_concept FROM tax_treatments WHERE transaction_id=?',(journal,)).fetchone()[0]==concept


def test_preview_token_changes_when_recognition_date_arrives(tmp_path,monkeypatch):
    from datetime import timedelta
    import autonomo_taxes.expense_workflow as workflow
    fixture,draft=setup_draft(tmp_path,method='immediate')
    today=date.today()
    class Tomorrow(date):
        @classmethod
        def today(cls):return today+timedelta(days=1)
    with open_db(fixture['database']) as db:
        p=deepcopy(draft['payload']);p['asset']['placed_in_service_on']=(today+timedelta(days=1)).isoformat()
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')
        first=preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert first['deductible_irpf_minor']==0
        monkeypatch.setattr(workflow,'date',Tomorrow)
        with pytest.raises(ExpenseWorkflowError,match='Preview is stale'):
            confirm_and_post(db,fixture['transaction_id'],expected_version=saved['draft_version'],preview_token=first['preview_token'],request_id=str(uuid4()),actor='test',archive_root=tmp_path/'archive')
        assert db.connection.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==0


@pytest.mark.parametrize('stage',['_facts','_asset','confirm_review_packet','_remember'])
def test_every_accounting_stage_rolls_back(tmp_path,monkeypatch,stage):
    import autonomo_taxes.expense_workflow as workflow
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        proposed=preview(db,fixture['transaction_id'],expected_version=draft['draft_version'])
        before='\n'.join(db.connection.iterdump())
        original=getattr(workflow,stage)
        def fail(*args,**kwargs):
            original(*args,**kwargs)
            raise RuntimeError('injected failure')
        monkeypatch.setattr(workflow,stage,fail)
        with pytest.raises(RuntimeError,match='injected failure'):
            confirm_and_post(db,fixture['transaction_id'],expected_version=draft['draft_version'],preview_token=proposed['preview_token'],request_id=str(uuid4()),actor='test',archive_root=tmp_path/'archive')
        assert '\n'.join(db.connection.iterdump())==before


def test_supplier_identifier_collision_cannot_rewrite_another_country(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        db.connection.execute("UPDATE counterparties SET tax_id='TEST-SHARED-ID'");db.connection.commit()
        party=dict(db.connection.execute('SELECT * FROM counterparties').fetchone())
        fresh=get_draft(db,fixture['transaction_id']);p=deepcopy(fresh['payload']);p['facts']['counterparty_id']=None
        p['supplier']={'display_name':'Different Synthetic US Supplier','tax_id':'TEST-SHARED-ID','vat_id':'','country_code':'US'}
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')
        with pytest.raises(ExpenseWorkflowError,match='supplier with this identifier exists'):
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert dict(db.connection.execute('SELECT * FROM counterparties').fetchone())==party


def test_manual_ocr_review_only_closes_document_issues(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        issue=db.add_validation_issue(period_key=fixture['period'],issue_code='document_structural_review',severity='warning',message='Manual original review needed',subject_table='documents',subject_id=fixture['document_id'],blocking=True)
        fresh=get_draft(db,fixture['transaction_id']);p=deepcopy(fresh['payload'])
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')
        with pytest.raises(ExpenseWorkflowError,match='manual review'):
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        p['manual_review_reason']='Readable source manually checked; extraction missed its text'
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=saved['draft_version'],source_snapshot_hash=saved['source_snapshot_hash'],actor='test')
        unrelated=db.add_validation_issue(period_key=fixture['period'],issue_code='other_period_problem',severity='warning',message='Unrelated issue must remain open',subject_table='periods',subject_id='synthetic-other-subject',blocking=True)
        commit(db,fixture,saved,tmp_path)
        assert db.connection.execute('SELECT issue_status FROM validation_issues WHERE validation_issue_id=?',(issue['validation_issue_id'],)).fetchone()[0]=='resolved'
        assert db.connection.execute('SELECT issue_status FROM validation_issues WHERE validation_issue_id=?',(unrelated['validation_issue_id'],)).fetchone()[0]=='open'


def test_later_quarter_recognition_is_versioned_and_idempotent(tmp_path,monkeypatch):
    import autonomo_taxes.expense_workflow as workflow
    import autonomo_taxes.posting as posting
    import autonomo_taxes.review_packet as review
    fixture,draft=setup_draft(tmp_path,method='linear')
    with open_db(fixture['database']) as db:
        result=commit(db,fixture,draft,tmp_path)
        entry=dict(db.connection.execute('SELECT * FROM amortization_entries WHERE asset_id=? ORDER BY recognition_on',(result['asset_id'],)).fetchone())
        future=date.fromisoformat(entry['recognition_on'])
        class DueDate(date):
            @classmethod
            def today(cls):return future
        monkeypatch.setattr(workflow,'date',DueDate);monkeypatch.setattr(posting,'date',DueDate);monkeypatch.setattr(review,'date',DueDate)
        with pytest.raises(ExpenseWorkflowError,match='Schedule changed'):
            recognize_period(db,entry['amortization_entry_id'],expected_version=entry['row_version']+1,request_id=str(uuid4()),actor='test',archive_root=tmp_path/'archive')
        request=str(uuid4())
        args=dict(expected_version=entry['row_version'],request_id=request,actor='test',archive_root=tmp_path/'archive')
        recognized=recognize_period(db,entry['amortization_entry_id'],**args)
        assert recognize_period(db,entry['amortization_entry_id'],**args)==recognized
        assert recognize_period(db,entry['amortization_entry_id'],**{**args,'request_id':str(uuid4())})['transaction_id']==recognized['transaction_id']
        assert db.connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==2
        assert sum(row.deductible_irpf_eur for row in load_tax_rows(db,future.year))==Decimal(entry['amount_minor'])/100


def test_changed_native_schedule_cannot_create_recognition(tmp_path,monkeypatch):
    import autonomo_taxes.expense_workflow as workflow
    fixture,draft=setup_draft(tmp_path,method='linear')
    with open_db(fixture['database']) as db:
        result=commit(db,fixture,draft,tmp_path)
        entry=dict(db.connection.execute('SELECT * FROM amortization_entries WHERE asset_id=? ORDER BY recognition_on',(result['asset_id'],)).fetchone())
        db.connection.execute('UPDATE amortization_entries SET amount_minor=amount_minor+1 WHERE amortization_entry_id=?',(entry['amortization_entry_id'],));db.connection.commit()
        with pytest.raises(ExpenseWorkflowError,match='reviewed calculation'):
            recognize_period(db,entry['amortization_entry_id'],expected_version=entry['row_version'],request_id=str(uuid4()),actor='test',archive_root=tmp_path/'archive')
        assert db.connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==1


def test_web_intake_defers_supplier_creation(tmp_path,capsys):
    from autonomo_taxes.cli import main
    from autonomo_taxes.ledger_db import initialize
    database=tmp_path/'ledger.sqlite';invoice=tmp_path/'synthetic-invoice.txt'
    invoice.write_text('Synthetic supplier invoice for EUR 121.00')
    with initialize(database):pass
    assert main(['ingest','--db',str(database),str(invoice),'--kind','expense_invoice','--issued-on',date.today().isoformat(),'--gross','121.00','--taxable-base','100.00','--vat','21.00','--currency','EUR','--counterparty-name','Synthetic Shop Candidate','--defer-counterparty'])==0
    capsys.readouterr()
    with open_db(database) as db:
        assert db.connection.execute('SELECT COUNT(*) FROM counterparties').fetchone()[0]==0
        assert db.connection.execute('SELECT counterparty_id FROM transactions').fetchone()[0] is None


@pytest.mark.parametrize('tax_code,reverse', [('eu_service_expense',False),('eu_goods_expense',False),('non_eu_service_expense',False),('domestic_reverse_charge_expense',False),('domestic_input',True)])
def test_reverse_charge_tax_code_must_agree_with_book_classification(tmp_path,tax_code,reverse):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        p=deepcopy(draft['payload']);p['decision']['tax_treatment'].update(tax_code=tax_code,aeat_reverse_charge=reverse)
        if tax_code!='domestic_input':p['facts']['gross_minor']=10000
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')
        with pytest.raises(ExpenseWorkflowError,match='Reverse-charge classification'):
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert db.connection.execute("SELECT COUNT(*) FROM transactions WHERE lifecycle_status='posted'").fetchone()[0]==0


def test_low_value_limit_checks_full_cost_before_share(tmp_path):
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        p=deepcopy(draft['payload']);p['facts']['gross_minor']=48400
        p['decision']['tax_treatment'].update(taxable_base_minor=40000,vat_minor=8400,deductible_vat_minor=8400)
        p['asset'].update(basis_minor=40000,business_use_ratio=0.5)
        saved=save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')
        with pytest.raises(ExpenseWorkflowError,match='before business share'):
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])


def test_low_value_annual_limit_includes_existing_register(tmp_path):
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        for index in range(84):
            db.add_asset(asset_code=f'synthetic-existing-{index}',cost_minor=30000,currency='EUR',depreciation_method='immediate',placed_in_service_on=date.today().isoformat(),source_hash=f'synthetic-existing-source-{index}')
        with pytest.raises(ExpenseWorkflowError,match='Annual low-value asset limit'):
            preview(db,fixture['transaction_id'],expected_version=draft['draft_version'])
        assert db.connection.execute('SELECT COUNT(*) FROM assets').fetchone()[0]==84


def test_partial_leap_quarter_uses_cumulative_rounding():
    rows=schedule(basis_minor=100000,business_use_ratio=1,annual_rate_basis_points=2500,placed_in_service_on='2024-02-29',method='linear')
    assert rows[0]=={'period_key':'2024-Q1','recognition_on':'2024-03-31','amount_minor':2186}
    assert rows[1]['amount_minor']==6216
    assert sum(row['amount_minor'] for row in rows)==100000


def test_native_acquisition_and_recognition_agree_in_books_and_forms(tmp_path):
    from autonomo_taxes.aeat_books import build_aeat_book_projection
    from autonomo_taxes.books import write_accounting_books
    from autonomo_taxes.tax_engine import calculate_modelo130_rows,calculate_modelo390
    import csv
    fixture,draft=setup_draft(tmp_path,method='immediate')
    with open_db(fixture['database']) as db:
        db.connection.execute("UPDATE counterparties SET tax_id='TEST-TAX-ID-001'");db.connection.commit()
        fresh=get_draft(db,fixture['transaction_id'])
        draft=save_draft(db,fixture['transaction_id'],payload=fresh['payload'],expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')
        commit(db,fixture,draft,tmp_path)
        projection=build_aeat_book_projection(db,period_key=fixture['period'])
        assert not projection['blockers'],projection['blockers']
        assert len(projection['expense_rows'])==2 and len(projection['asset_rows'])==1
        assert projection['asset_rows'][0]['amortizacion_cuota_resultante_eur']=='100.00'
        assert projection['asset_rows'][0]['annual_evidence_source_book_line_id'] is None
        books=write_accounting_books(db,tmp_path/'books',period_key=fixture['period'])
        with books.files['expense'].open() as stream:expenses=list(csv.DictReader(stream))
        assert sum(int(row['recomputed_deductible_irpf_minor']) for row in expenses)==10000
        rows=load_tax_rows(db,date.today().year)
        _,irpf=calculate_modelo130_rows(rows,year=date.today().year,quarter=(date.today().month-1)//3+1,difficult_expenses_rate=Decimal(0))
        assert irpf.values['02']==Decimal('100.00')
        vat=calculate_modelo303_rows(rows,year=date.today().year,quarter=(date.today().month-1)//3+1)
        annual=calculate_modelo390([vat],year=date.today().year)
        assert vat.values['29']==Decimal('21.00')
        assert annual.values['49']==annual.values['64']==Decimal('21.00')


@pytest.mark.parametrize('case',['exact','different_amount','ambiguous'])
def test_migration_adopts_only_unambiguous_matching_legacy_links(tmp_path,case):
    from unittest.mock import patch
    from autonomo_taxes.ledger_db import initialize
    database=tmp_path/'legacy.sqlite'
    with patch('autonomo_taxes.ledger_db.LATEST_SCHEMA_VERSION',21),initialize(database) as db:
        supplier=db.upsert_counterparty(external_key='legacy-supplier',display_name='Synthetic legacy supplier',country_code='ES')
        invoice=db.upsert_document(external_key='legacy-invoice',document_type='expense_invoice',document_number='SYN-LEGACY',issued_on='2026-07-01',period_key='2026-Q3',source_hash='synthetic-legacy-invoice')
        purchase=db.add_transaction(external_key='legacy-purchase',period_key='2026-Q3',transaction_date='2026-07-01',booking_date='2026-07-01',entry_type='expense',description='Synthetic legacy purchase',amount_minor=12100,currency='EUR',document_id=invoice['document_id'],counterparty_id=supplier['counterparty_id'],lifecycle_status='posted')
        internal=db.upsert_document(external_key='legacy-internal',document_type='other',document_number='SYN-AMORT',issued_on='2026-07-01',period_key='2026-Q3',source_hash='synthetic-legacy-internal')
        journal=db.add_transaction(external_key='legacy-journal',period_key='2026-Q3',transaction_date='2026-07-01',booking_date='2026-07-01',entry_type='expense',description='Unrelated title; links are authoritative',amount_minor=10000,currency='EUR',document_id=internal['document_id'],counterparty_id=supplier['counterparty_id'],lifecycle_status='posted')
        db.add_detailed_tax_treatment(transaction_id=journal['transaction_id'],treatment_type='invoice_review',tax_code='domestic_input',aeat_expense_concept='G31',deductible_irpf_minor=10000,vat_minor=0,deductible_vat_minor=0,include_modelo130=True,include_modelo303=False)
        for index in range(2 if case=='ambiguous' else 1):
            asset=db.add_asset(asset_code=f'synthetic-legacy-{index}',cost_minor=10000,currency='EUR',depreciation_method='immediate',placed_in_service_on='2026-07-01',source_hash='synthetic-legacy-source',document_id=invoice['document_id'],acquisition_transaction_id=purchase['transaction_id'])
            db.add_amortization_entry(asset_id=asset['asset_id'],period_key='2026-Q3',amount_minor=9900 if case=='different_amount' else 10000,source_hash='synthetic-legacy-row',source_book_line_id='transaction:'+journal['transaction_id'])
        before={table:[dict(row) for row in db.connection.execute('SELECT * FROM '+table)] for table in ('transactions','tax_treatments','assets')}
    with open_db(database,apply_migrations=True) as db:
        assert db.connection.execute('PRAGMA user_version').fetchone()[0]==LATEST_SCHEMA_VERSION
        for table,rows in before.items():assert [dict(row) for row in db.connection.execute('SELECT * FROM '+table)]==rows
        links=[row[0] for row in db.connection.execute('SELECT recognition_transaction_id FROM amortization_entries')]
        assert links==([journal['transaction_id']] if case=='exact' else [None]*len(links))
        assert not db.connection.execute('PRAGMA foreign_key_check').fetchall()


def with_rule(db, fixture, draft, rule_id, tax=None, **facts):
    from autonomo_taxes.expense_rules import FACT_NAMES
    p=deepcopy(draft['payload']);p['deduction']={'rule_id':rule_id,'facts':{**dict.fromkeys(FACT_NAMES),**facts}}
    p['decision']['tax_treatment'].update(tax or {})
    return save_draft(db,fixture['transaction_id'],payload=p,expected_version=draft['draft_version'],source_snapshot_hash=draft['source_snapshot_hash'],actor='test')


def test_rule_ceiling_is_recorded_and_preview_leaves_no_trace(tmp_path):
    from autonomo_taxes.expense_rules import load_catalog
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        saved=with_rule(db,fixture,draft,'general_business',{'notes':''})
        assert (saved['deduction_proposal']['status'],saved['deduction_proposal']['irpf_minor'],saved['deduction_proposal']['vat_minor'])==('ready',10000,2100)
        assert {rule['id'] for rule in saved['deduction_rules']}==set(load_catalog()['rules'])
        before='\n'.join(db.connection.iterdump())
        proposed=preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert '\n'.join(db.connection.iterdump())==before
        assert proposed['deduction']['rule_id']=='general_business'
        commit(db,fixture,saved,tmp_path)
        row=db.connection.execute('''SELECT rv.rule_name,rv.version,tt.notes FROM tax_treatments tt
            JOIN rule_versions rv ON rv.rule_version_id=tt.rule_version_id WHERE tt.transaction_id=?''',(fixture['transaction_id'],)).fetchone()
        assert (row['rule_name'],row['version'])==('expense_rule:general_business',load_catalog()['version'])
        assert 'Deduction rule general_business' in row['notes'] and 'ceiling IRPF 10000, IVA 2100' in row['notes']


@pytest.mark.parametrize('irpf,vat,allowed',[(4000,1000,True),(10000,2100,True),(10001,2100,False)])
def test_rule_proposal_is_a_ceiling_not_a_target(tmp_path,irpf,vat,allowed):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        saved=with_rule(db,fixture,draft,'general_business',{'deductible_irpf_minor':irpf,'deductible_vat_minor':vat})
        if allowed:
            assert commit(db,fixture,saved,tmp_path)['posted']
        else:
            with pytest.raises(ExpenseWorkflowError) as error:
                preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
            assert error.value.code=='deduction_exceeds_rule'


def test_home_supplies_leave_the_reviewed_iva_amount_manual(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        saved=with_rule(db,fixture,draft,'home_utility_partial_dwelling',{
            'deductible_ratio':0.25,'deductible_irpf_minor':750,'deductible_vat_minor':600})
        assert saved['deduction_proposal']['vat_minor'] is None
        assert commit(db,fixture,saved,tmp_path)['posted']
        row=db.connection.execute('SELECT * FROM tax_treatments WHERE transaction_id=?',
                                  (fixture['transaction_id'],)).fetchone()
        assert row['deductible_vat_minor']==600
        assert 'IVA manual' in row['notes']


@pytest.mark.parametrize('rule_id,method,code',[('own_meals',None,'deduction_facts_missing'),('no_such_rule',None,'deduction_rule_unknown'),
    ('general_business','immediate','deduction_out_of_scope'),('fine_or_surcharge',None,'deduction_exceeds_rule')])
def test_selected_rule_must_apply_before_preview(tmp_path,rule_id,method,code):
    fixture,draft=setup_draft(tmp_path,method=method)
    with open_db(fixture['database']) as db:
        saved=with_rule(db,fixture,draft,rule_id)
        before='\n'.join(db.connection.iterdump())
        with pytest.raises(ExpenseWorkflowError) as error:
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert error.value.code==code
        assert '\n'.join(db.connection.iterdump())==before


def test_changed_rule_under_the_same_catalog_version_blocks(tmp_path):
    from autonomo_taxes.expense_rules import load_catalog
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        db.add_rule_version(rule_name='expense_rule:general_business',version=load_catalog()['version'],source_hash='0'*64)
        saved=with_rule(db,fixture,draft,'general_business')
        with pytest.raises(ExpenseWorkflowError) as error:
            preview(db,fixture['transaction_id'],expected_version=saved['draft_version'])
        assert (error.value.code,error.value.status)==('deduction_rule_conflict',409)


def test_cap_subtracts_only_postings_linked_to_the_rule(tmp_path):
    from autonomo_taxes.expense_rules import propose
    fixture,draft=setup_draft(tmp_path)
    tax={'taxable_base_minor':12100,'vat_minor':0,'rate_basis_points':0,'deductible_vat_minor':0,'deductible_irpf_minor':12100}
    with open_db(fixture['database']) as db:
        saved=with_rule(db,fixture,draft,'health_insurance',tax,persons=1,persons_disabled=0)
        assert saved['deduction_proposal']['irpf_minor']==12100
        commit(db,fixture,saved,tmp_path)
        later=deepcopy(saved['payload']);later['decision']['tax_treatment'].update(taxable_base_minor=50000)
        other={'transaction':{'transaction_id':'synthetic-other-transaction'}}
        result=propose(db,other,later)
        assert result['irpf_minor']==50000-12100
        assert {'code':'irpf_person_cap','params':{'persons':1,'persons_disabled':0,'cap_minor':50000,'used_minor':12100,'year':saved['payload']['facts']['transaction_date'][:4]}} in result['explanation']
        later['deduction']={'rule_id':'own_meals','facts':{'persons':None,'persons_disabled':None,'days':1,'abroad':False,'overnight':False,'electronic_payment':True}}
        meals=propose(db,other,later)
        assert meals['irpf_minor']==2667 and any(note['params'].get('used_minor')==0 for note in meals['explanation'])


def test_exhausted_daily_meal_cap_leaves_no_iva(tmp_path):
    from autonomo_taxes.expense_rules import propose
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        saved=with_rule(db,fixture,draft,'own_meals',{'deductible_irpf_minor':2667,'deductible_vat_minor':0},days=1,abroad=False,overnight=False,electronic_payment=True)
        assert (saved['deduction_proposal']['irpf_minor'],saved['deduction_proposal']['vat_minor'])==(2667,None)
        commit(db,fixture,saved,tmp_path)
        second=propose(db,{'transaction':{'transaction_id':'synthetic-second-meal'}},saved['payload'])
        assert (second['status'],second['irpf_minor'],second['vat_minor'])==('ready',0,0)
        assert 'cap_exhausted' in [note['code'] for note in second['explanation']]


def test_older_fact_sets_and_repeated_rule_lines_are_normalized(tmp_path):
    from autonomo_taxes.expense_workflow import _current,_decision
    older={'deduction':{'rule_id':'social_security_reta','facts':{'persons':None}}}
    assert _current(older)['deduction']['facts']['evidence_confirmed'] is None and 'evidence_confirmed' not in older['deduction']['facts']
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        with pytest.raises(ExpenseWorkflowError,match='evidence_confirmed'):
            with_rule(db,fixture,draft,'social_security_reta',evidence_confirmed='yes')
        payload=deepcopy(draft['payload']);payload['decision']['tax_treatment']['notes']='Reviewed\nDeduction rule general_business (catalog old): ceiling IRPF 1, IVA 1 EUR cents'
        packet={'state':{'issues':[]}}
        proposal={'rule_id':'general_business','version':'2026-09-24','source_hash':'synthetic-hash','status':'ready','risk':'low','irpf_minor':10000,'vat_minor':2100}
        _decision(db,packet,payload,None,proposal)
        notes=packet['decision']['tax_treatment']['notes']
        assert notes.count('Deduction rule ')==1 and notes.startswith('Reviewed\n') and 'ceiling IRPF 10000' in notes


def test_draft_saved_before_rules_stays_on_manual_path(tmp_path):
    fixture,draft=setup_draft(tmp_path)
    with open_db(fixture['database']) as db:
        legacy=deepcopy(draft['payload']);del legacy['deduction']
        db.connection.execute('UPDATE expense_drafts SET payload_json=?',(json.dumps(legacy),));db.connection.commit()
        fresh=get_draft(db,fixture['transaction_id'])
        assert fresh['payload']['deduction'] is None and fresh['deduction_proposal'] is None
        saved=save_draft(db,fixture['transaction_id'],payload=legacy,expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')
        assert saved['draft_version']==fresh['draft_version']
        with pytest.raises(ExpenseWorkflowError,match='deduction rule fields'):
            save_draft(db,fixture['transaction_id'],payload={**legacy,'deduction':{'rule_id':'general_business'}},expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')


@pytest.mark.parametrize('percent,share',[('25',0.25),('100',None)])
def test_intake_category_seeds_rule_and_only_a_partial_business_share(tmp_path,monkeypatch,capsys,percent,share):
    import hashlib
    from autonomo_taxes.cli import main
    from autonomo_taxes.intake import IntakeResult
    from autonomo_taxes.ledger_db import initialize
    from autonomo_taxes.parsers import LedgerEntry
    database=tmp_path/'ledger.sqlite';invoice=tmp_path/'utility.pdf';invoice.write_bytes(b'synthetic utility fixture')
    digest=hashlib.sha256(invoice.read_bytes()).hexdigest()
    suggestion=LedgerEntry(kind='expense',date=date.today(),document=str(invoice),counterparty='Synthetic Utility',description='SYNTH-UTILITY-1',
        amount_original=Decimal('60.50'),currency='EUR',amount_eur=Decimal('60.50'),deductible_eur=None,category='home_utility_review',
        confidence='high',review_required=True,notes='Business-use allocation requires review')
    monkeypatch.setattr('autonomo_taxes.operational_cli.inspect_document',lambda *args,**kwargs:IntakeResult(source_path=str(invoice),sha256=digest,
        kind='expense_invoice',mime_type='application/pdf',extraction_method='pdf_text',extracted_text='parsed fixture',status='extracted',structural_errors=()))
    monkeypatch.setattr('autonomo_taxes.operational_cli._document_suggestion',lambda *args,**kwargs:suggestion)
    with initialize(database):pass
    assert main(['ingest','--db',str(database),str(invoice),'--kind','expense_invoice'])==0
    capsys.readouterr()
    with open_db(database) as db:
        treatment=db.connection.execute('SELECT * FROM tax_treatments').fetchone()
        assert treatment['notes'].endswith('; parser_category=home_utility_review')
        db.add_intake_receipt(intake_tab='expense_intake',source_row_number=2,row_fingerprint='synthetic-row',evidence_sha256=digest,
            document_id=db.connection.execute('SELECT document_id FROM documents').fetchone()[0],transaction_id=treatment['transaction_id'],
            treatment_id=treatment['treatment_id'],input_payload={'business_use_percent':percent})
        seeded=get_draft(db,treatment['transaction_id'])
    assert seeded['payload']['deduction']['rule_id']=='home_utility_partial_dwelling'
    assert seeded['payload']['decision']['tax_treatment']['deductible_ratio']==share
    assert seeded['deduction_proposal']['status']=='needs_facts'
    assert ('area_share' in seeded['deduction_proposal']['missing'])==(share is None)
