from dataclasses import replace
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from datetime import date, timedelta
from copy import deepcopy

import pytest

from autonomo_taxes.ledger_db import open as open_db
from autonomo_taxes.local_web import LocalWebConfig
from test_expense_workflow import setup_draft
from test_local_web_guided import _Server


def test_authenticated_http_expense_workflow_and_follow_up_failure(tmp_path,monkeypatch):
    fixture,draft=setup_draft(tmp_path)
    root=Path(__file__).resolve().parents[1]
    config=LocalWebConfig(project_root=root,database=fixture['database'],inbox_root=tmp_path/'inbox',archive_root=tmp_path/'archive',cache_root=tmp_path/'cache',static_root=root/'src/autonomo_taxes/web_ui',read_only_document_roots=(tmp_path,))
    server=_Server(config,monkeypatch,ecb=None)
    base='/api/expense-workflows/'+fixture['transaction_id']
    origin=f'http://127.0.0.1:{server.port}'
    try:
        assert server.request('GET',base,cookie=False)[0]==400
        payload=json.dumps({'expected_version':draft['draft_version']}).encode()
        assert server.request('POST',base+'/preview',body=payload,origin='https://example.invalid',content_type='application/json')[0]==400
        status,_,body=server.request('POST',base+'/preview',body=payload,origin=origin,content_type='application/json')
        assert status==200,body
        proposed=json.loads(body)
        data=json.dumps({'expected_version':draft['draft_version'],'preview_token':proposed['preview_token'],'request_id':str(uuid4())}).encode()
        monkeypatch.setattr(server.server.app,'refresh_dashboard',lambda *args,**kwargs:(_ for _ in ()).throw(RuntimeError('refresh unavailable')))
        status,_,body=server.request('POST',base+'/confirm',body=data,origin=origin,content_type='application/json')
        assert status==200,body
        result=json.loads(body);assert result['posted'] and result['follow_up_pending'] and 'calculation_refresh_pending' in result['warnings']
        detail=server.server.app.transaction_detail(fixture['transaction_id'])
        assert detail['workflow_follow_up']['follow_up_pending']
        monkeypatch.setattr(server.server.app,'refresh_dashboard',lambda *args,**kwargs:{})
        status,_,body=server.request('POST',base+'/follow-up',body=b'{}',origin=origin,content_type='application/json')
        assert status==200 and not json.loads(body)['follow_up_pending']
        assert not server.server.app.transaction_detail(fixture['transaction_id'])['workflow_follow_up']['follow_up_pending']
        status,_,body=server.request('POST',base+'/confirm',body=data,origin=origin,content_type='application/json')
        assert status==200 and json.loads(body)['transaction_id']==result['transaction_id']
        with open_db(config.database) as db:assert db.connection.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]==1
    finally:server.close()


def test_preview_embedding_is_restricted_to_verified_binary_types(tmp_path,monkeypatch):
    fixture,draft=setup_draft(tmp_path)
    root=Path(__file__).resolve().parents[1]
    config=LocalWebConfig(project_root=root,database=fixture['database'],inbox_root=tmp_path/'inbox',archive_root=tmp_path/'archive',cache_root=tmp_path/'cache',static_root=root/'src/autonomo_taxes/web_ui',read_only_document_roots=(tmp_path,))
    with open_db(config.database) as db:
        source=db.connection.execute('SELECT source_path FROM documents').fetchone()[0]
        Path(source).write_bytes(b'%PDF-1.4\nSynthetic PDF fixture')
        digest=hashlib.sha256(Path(source).read_bytes()).hexdigest()
        db.connection.execute('UPDATE documents SET source_hash=?,external_key=?',(digest,'sha256:'+digest));db.connection.commit()
    server=_Server(config,monkeypatch,ecb=None)
    endpoint='/api/document/'+fixture['document_id']
    try:
        assert server.request('GET',endpoint+'/preview',cookie=False)[0]==400
        status,headers,body=server.request('GET',endpoint+'/preview')
        assert status==200 and headers['X-Frame-Options']=='SAMEORIGIN'
        assert "frame-ancestors 'self'" in headers['Content-Security-Policy']
        assert server.request('GET',endpoint+'/content')[1]['X-Frame-Options']=='DENY'
        assert server.request('GET','/')[1]['X-Frame-Options']=='DENY'
        Path(source).write_bytes(b'<script>untrusted</script>')
        monkeypatch.setattr(server.server.app,'document_file',lambda _: (Path(source),'text/html'))
        assert server.request('GET',endpoint+'/preview')[0]==415
    finally:server.close()


def test_corrected_period_cleans_only_the_original_inbox_file(tmp_path,monkeypatch):
    from autonomo_taxes.expense_workflow import get_draft,save_draft
    first=date(date.today().year,1,1)
    fixture,_=setup_draft(tmp_path,transaction_date=first.isoformat())
    root=Path(__file__).resolve().parents[1]
    config=LocalWebConfig(project_root=root,database=fixture['database'],inbox_root=tmp_path/'inbox',archive_root=tmp_path/'archive',cache_root=tmp_path/'cache',static_root=root/'src/autonomo_taxes/web_ui')
    with open_db(config.database) as db:
        document=dict(db.connection.execute('SELECT * FROM documents').fetchone())
        original=Path(document['source_path']).read_bytes()
        source=config.inbox_root/fixture['period']/'expense_invoice'/'synthetic.txt'
        archive=config.archive_root/fixture['period']/'expense_invoice'/'synthetic.txt'
        for path in (source,archive):path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(original)
        batch=db.add_import_batch(source_name=str(source),source_hash=document['source_hash'],batch_key='synthetic-intake')
        db.connection.execute('UPDATE documents SET import_batch_id=?,source_path=? WHERE document_id=?',(batch['import_batch_id'],str(archive),fixture['document_id']));db.connection.commit()
        treatment=db.connection.execute('SELECT treatment_id FROM tax_treatments').fetchone()[0]
        db.add_intake_receipt(intake_tab='expense_intake',source_row_number=2,row_fingerprint='synthetic-row',evidence_sha256=document['source_hash'],document_id=fixture['document_id'],transaction_id=fixture['transaction_id'],treatment_id=treatment,input_payload={'file_name':source.name})
        target=(first-timedelta(days=1)).isoformat();db.ensure_period(f'{first.year-1}-Q4')
        fresh=get_draft(db,fixture['transaction_id']);p=deepcopy(fresh['payload'])
        p['facts'].update(issued_on=target,transaction_date=target,booking_date=target)
        draft=save_draft(db,fixture['transaction_id'],payload=p,expected_version=fresh['draft_version'],source_snapshot_hash=fresh['current_snapshot_hash'],actor='test')
    server=_Server(config,monkeypatch,ecb=None)
    try:
        monkeypatch.setattr(server.server.app,'refresh_dashboard',lambda *args,**kwargs:{})
        proposed=server.server.app.expense_preview(fixture['transaction_id'],{'expected_version':draft['draft_version']})
        result=server.server.app.expense_confirm(fixture['transaction_id'],{'expected_version':draft['draft_version'],'preview_token':proposed['preview_token'],'request_id':str(uuid4())},'test')
        assert result['posted'] and not result['follow_up_pending']
        assert result['period']==f'{first.year-1}-Q4' and result['cleanup_period_key']==fixture['period']
        assert not source.exists() and archive.read_bytes()==original
        assert not server.server.app.expense_follow_up(fixture['transaction_id'])['follow_up_pending']
    finally:server.close()


def test_expense_ui_keeps_retry_identity_and_legacy_asset_route(tmp_path):
    import subprocess
    _,draft=setup_draft(tmp_path)
    root=Path(__file__).resolve().parents[1]
    subprocess.run(['node',str(root/'tests/test_expense_workflow_ui.js')],input=json.dumps(draft),text=True,cwd=root,check=True)
