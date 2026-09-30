"""Exercise HTTP -> CLI without a repository cwd or inherited source path.

The installed acceptance runner executes this file with its wheel's Python -I.
All data is synthetic and confined to the temporary directory.
"""
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import tempfile
import threading

from autonomo_taxes import local_web
from autonomo_taxes.ledger_db import LedgerDB


def main() -> None:
    os.environ.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="autonomo-cli-bridge-") as directory:
        root = Path(directory)
        config = local_web.LocalWebConfig(
            project_root=root, database=root / "ledger.sqlite",
            inbox_root=None, archive_root=None, cache_root=root / "cache",
            static_root=Path(local_web.__file__).resolve().parent / "web_ui",
            private_root=root,
        )
        with LedgerDB.initialize(config.database) as db:
            db.add_transaction(
                external_key="synthetic-bridge-income", period_key="2026-Q3",
                transaction_date="2026-07-01", booking_date="2026-07-01",
                entry_type="income", description="Synthetic bridge income",
                amount_minor=10000, amount_eur_minor=10000,
                direction="credit", lifecycle_status="posted",
            )
        app = local_web.LocalAccountingApp(config, session_token="synthetic-bridge-session")
        with local_web.LocalAccountingServer(("127.0.0.1", 0), app) as server:
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            host, port = server.server_address
            connection = HTTPConnection(host, port, timeout=180)
            try:
                connection.request(
                    "POST", "/api/dashboard/refresh",
                    body=json.dumps({"period": "2026-Q3", "as_of": "2026-07-02"}),
                    headers={"Content-Type": "application/json", "Origin": f"http://{host}:{port}",
                             "Cookie": f"{app.cookie_name}={app.session_token}"},
                )
                response = connection.getresponse()
                payload = json.loads(response.read())
                assert response.status == 200, payload
                assert payload["status"] == "refreshed", payload
                assert payload["period"] == "2026-Q3", payload
                dashboard = json.loads((config.cache_root / "2026-Q3/dashboard.json").read_text())
                assert dashboard["period"] == "2026-Q3"
                assert dashboard["as_of"] == "2026-07-02"
            finally:
                connection.close()
                server.shutdown()
                worker.join(timeout=10)
                assert not worker.is_alive()
    print("HTTP -> CLI refresh verified outside the repository")


if __name__ == "__main__":
    main()
