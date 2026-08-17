from __future__ import annotations

from http.cookiejar import CookieJar
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, build_opener


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: healthcheck.py <https-url>")
    base = sys.argv[1].rstrip("/")
    deadline = time.monotonic() + 30
    last_error = "health check did not run"
    while time.monotonic() < deadline:
        try:
            opener = build_opener(HTTPCookieProcessor(CookieJar()))
            with opener.open(base + "/", timeout=5) as root:
                if root.status != 200:
                    raise RuntimeError(f"root returned {root.status}")
            with opener.open(base + "/api/bootstrap", timeout=5) as response:
                payload = json.load(response)
                if response.status == 200 and isinstance(payload, dict):
                    return 0
                raise RuntimeError("bootstrap did not return JSON")
        except (HTTPError, URLError, OSError, RuntimeError, json.JSONDecodeError) as exc:
            last_error = str(exc)
            time.sleep(1)
    raise SystemExit(f"health check did not become ready within 30 seconds: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
