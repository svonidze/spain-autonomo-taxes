from __future__ import annotations

from http.cookiejar import CookieJar
from html.parser import HTMLParser
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, build_opener
from urllib.parse import urljoin, urlsplit


class Resources(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script" and attrs.get("src"):
            self.urls.append((attrs["src"], "javascript"))
        if tag == "link" and "stylesheet" in attrs.get("rel", "").split() and attrs.get("href"):
            self.urls.append((attrs["href"], "text/css"))


def check_resources(opener, base: str, html: str) -> None:
    resources = Resources()
    resources.feed(html)
    if not any(kind == "javascript" for _, kind in resources.urls):
        raise RuntimeError("application shell has no JavaScript resource")
    origin = urlsplit(base)
    for reference, kind in resources.urls:
        target = urljoin(base + "/", reference)
        parsed = urlsplit(target)
        if (parsed.scheme, parsed.netloc) != (origin.scheme, origin.netloc):
            raise RuntimeError("shell references an unexpected external resource")
        with opener.open(target, timeout=5) as response:
            if response.status != 200 or kind not in response.headers.get("Content-Type", "") or not response.read():
                raise RuntimeError("application resource is missing or has the wrong content type")


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
                html = root.read().decode("utf-8")
            check_resources(opener, base, html)
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
