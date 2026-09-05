"""Serve the real application with ephemeral, synthetic data only."""
from dataclasses import replace
from pathlib import Path
import runpy
import signal
import tempfile

from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingServer


def terminate(_signum: int, _frame: object) -> None:
    raise SystemExit(0)


def main() -> None:
    signal.signal(signal.SIGTERM, terminate)
    fixture = runpy.run_path(str(Path(__file__).resolve().parents[1] / "test_local_web.py"))
    with tempfile.TemporaryDirectory(prefix="autonomo-browser-test-") as directory:
        root = Path(directory)
        config = replace(fixture["_config"](root), private_root=root)
        fixture["_database"](config)
        app = LocalAccountingApp(config, session_token="synthetic-browser-session")
        with LocalAccountingServer(("127.0.0.1", 8765), app) as server:
            server.serve_forever()


if __name__ == "__main__":
    main()
