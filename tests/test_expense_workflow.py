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
from test_review_confirm import _invoice_fixture, _approve_decision
from test_aeat_books import _profile_and_activity


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
    with open_db(fixture['database']) as db:
        result=commit(db,fixture,draft,tmp_path)
        native=native_year_summary(db.connection,result['asset_id'],date.today().year)
        annual=db.validate_asset_year(date.today().year)
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
        assert sum(row.deductible_irpf_eur for row in load_tax_rows(db,date.today().year))==Decimal(entry['amount_minor'])/100


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
