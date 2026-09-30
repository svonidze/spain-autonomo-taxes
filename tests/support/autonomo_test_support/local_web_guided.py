"""Reusable synthetic fixtures shared by backend test suites."""

from __future__ import annotations
import hashlib
import json
from datetime import date
from decimal import Decimal
from http.client import HTTPConnection
from pathlib import Path
import threading
import pytest
import autonomo_taxes.local_web as local_web
from autonomo_taxes.fx_reference import (
    ECBRateObservation,
    ECBRateResult,
    FXRateUnavailableError,
    clear_cache,
    fetch_eur_rate,
)
from autonomo_taxes.ledger_db import LedgerDB, open as open_ledger_db
from autonomo_taxes.local_web import (
    LocalAccountingApp,
    LocalAccountingServer,
    LocalWebConfig,
)
from autonomo_taxes.review_packet import prepare_review_work_item
from autonomo_test_support.paths import REPO_ROOT
SESSION_TOKEN = "guided-web-token"

def _config(tmp_path: Path) -> LocalWebConfig:
    return LocalWebConfig(
        project_root=tmp_path,
        database=tmp_path / "autonomo.sqlite",
        inbox_root=tmp_path / "Inbox",
        archive_root=tmp_path / "Evidence",
        cache_root=tmp_path / "cache",
        static_root=(
            REPO_ROOT
            / "backend/src"
            / "autonomo_taxes"
            / "web_ui"
        ),
    )

def _fake_ecb_result(*, status: str = "exact", units: str = "1.2500") -> ECBRateResult:
    day = date.today()
    units_per_eur = Decimal(units)
    raw = json.dumps(
        {"currency": "USD", "date": day.isoformat(), "value": units},
        sort_keys=True,
        separators=(",", ":"),
    )
    observation = ECBRateObservation(
        currency="USD",
        rate_date=day,
        units_per_eur=units_per_eur,
        eur_per_unit=Decimal(1) / units_per_eur,
        source_url="https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A",
        raw_observation=raw,
        raw_observation_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
    )
    return ECBRateResult(status=status, observation=observation)

class _Server:
    def __init__(self, config: LocalWebConfig, monkeypatch: pytest.MonkeyPatch, ecb) -> None:
        self.config = config
        app = LocalAccountingApp(config, session_token=SESSION_TOKEN)
        self.server = LocalAccountingServer(("127.0.0.1", 0), app)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_address[1]
        self.connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        if ecb is not None:
            monkeypatch.setattr(local_web, "fetch_eur_rate", ecb)

    def close(self) -> None:
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        origin: str | None = None,
        cookie: bool = True,
        content_type: str | None = None,
    ) -> tuple[int, dict[str, str], bytes]:
        headers = {"Host": f"127.0.0.1:{self.port}"}
        if cookie:
            headers["Cookie"] = f"autonomo_session={SESSION_TOKEN}"
        if origin is not None:
            headers["Origin"] = origin
        if content_type is not None:
            headers["Content-Type"] = content_type
        self.connection.request(method, path, body=body, headers=headers)
        response = self.connection.getresponse()
        payload = response.read()
        return response.status, dict(response.getheaders()), payload
