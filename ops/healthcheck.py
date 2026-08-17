from __future__ import annotations

from http.cookiejar import CookieJar
import json
import sys
from urllib.request import HTTPCookieProcessor, build_opener


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: healthcheck.py <https-url>")
    base = sys.argv[1].rstrip("/")
    opener = build_opener(HTTPCookieProcessor(CookieJar()))
    with opener.open(base + "/", timeout=20) as root:
        if root.status != 200:
            raise SystemExit(f"root health check returned {root.status}")
    with opener.open(base + "/api/bootstrap", timeout=20) as response:
        payload = json.load(response)
        if response.status != 200 or not isinstance(payload, dict):
            raise SystemExit("bootstrap health check failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
