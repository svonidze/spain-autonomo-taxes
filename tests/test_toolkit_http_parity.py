"""Both adapters expose the same decision, refusal and committed accounting result."""

from pathlib import Path
import json
from uuid import uuid4

import pytest
from autonomo_taxes.local_web import LocalWebConfig
from autonomo_taxes.toolkit_cli import public_value
from test_expense_workflow import setup_draft
from test_local_web_guided import _Server
from test_toolkit_cli import invoke

pytestmark = pytest.mark.web


def test_expense_http_cli_preview_errors_and_replay_share_one_action(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setenv("AUTONOMO_PRIVATE_ROOT", str(tmp_path / "private"))
    fixture, draft = setup_draft(tmp_path)
    root = Path(__file__).resolve().parents[1]
    config = LocalWebConfig(
        project_root=root,
        database=fixture["database"],
        inbox_root=tmp_path / "private/inbox",
        archive_root=tmp_path / "private/evidence",
        cache_root=tmp_path / "private/cache",
        static_root=root / "packages/ui/src/autonomo_taxes_ui/dist",
        read_only_document_roots=(tmp_path,),
    )
    server = _Server(config, monkeypatch, ecb=None)
    origin = f"http://127.0.0.1:{server.port}"
    base = "/api/expense-workflows/" + fixture["transaction_id"]

    def post(suffix, payload):
        status, _, body = server.request(
            "POST",
            base + suffix,
            body=json.dumps(payload).encode(),
            origin=origin,
            content_type="application/json",
        )
        return status, json.loads(body)

    try:
        for version in (draft["draft_version"] - 1, draft["draft_version"]):
            payload = {"expected_version": version}
            code, cli = invoke(
                capsys,
                fixture,
                ["expense", "preview", fixture["transaction_id"]],
                payload=payload,
            )
            status, http = post("/preview", payload)
            assert (code == 0) == (status == 200)
            if code:
                assert cli["code"] == http["code"]
            else:
                assert cli == public_value(http)
        request = {
            "expected_version": draft["draft_version"],
            "preview_token": http["preview_token"],
            "request_id": str(uuid4()),
        }
        status, http = post("/confirm", request)
        assert status == 200 and http["posted"]
        code, cli = invoke(
            capsys,
            fixture,
            ["expense", "confirm", fixture["transaction_id"]],
            payload=request,
        )
        assert (
            code == 0
            and cli["transaction_id"] == http["transaction_id"]
            and cli["posted"]
        )
        code, detail = invoke(
            capsys, fixture, ["transactions", "show", fixture["transaction_id"]]
        )
        assert code == 0
        assert detail == public_value(
            server.server.app.transaction_detail(fixture["transaction_id"])
        )
    finally:
        server.close()
