"""Serve the real application with ephemeral, synthetic data only."""
from pathlib import Path
import os
import signal
import tempfile

from autonomo_taxes import local_web
from autonomo_taxes.ledger_db import LedgerDB
from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer, LocalWebConfig


def terminate(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, terminate)
    with tempfile.TemporaryDirectory(prefix="autonomo-browser-test-") as directory:
        root = Path(directory)
        package = Path(local_web.__file__).resolve().parent
        if os.environ.get("AUTONOMO_BROWSER_INSTALLED") == "1" and "site-packages" not in package.parts:
            raise RuntimeError("Installed browser check imported the source checkout")
        config = LocalWebConfig(project_root=root, database=root / "autonomo.sqlite",
            inbox_root=root / "Inbox", archive_root=root / "Evidence", cache_root=root / "cache",
            static_root=package / "web_ui", private_root=root, allow_test_ui=os.environ.get("AUTONOMO_PSEUDO") == "1")
        with LedgerDB.initialize(config.database) as db:
            db.add_transaction(external_key="web-income", period_key="2026-Q3",
                transaction_date="2026-07-01", booking_date="2026-07-01", entry_type="income",
                description="Example income", amount_minor=125000, amount_eur_minor=125000,
                direction="credit", lifecycle_status="posted")
            expense = db.add_transaction(external_key="web-expense", period_key="2026-Q3",
                transaction_date="2026-07-02", booking_date="2026-07-02", entry_type="expense",
                description="Example expense", amount_minor=12100, amount_eur_minor=12100,
                direction="debit", lifecycle_status="approved")
            db.add_detailed_tax_treatment(transaction_id=expense["transaction_id"], treatment_type="expense",
                tax_code="G03", taxable_base_minor=10000, vat_minor=2100, deductible_irpf_minor=10000,
                deductible_vat_minor=2100, include_modelo130=True, include_modelo303=True)
        app = LocalAccountingApp(config, session_token="synthetic-browser-session")
        with LocalAccountingServer(("127.0.0.1", 8765), app) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
