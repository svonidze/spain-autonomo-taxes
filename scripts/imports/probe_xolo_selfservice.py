from __future__ import annotations

import argparse
import html
import json
import os
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
import re
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


BASE_URL = "https://app.xolo.io"

DEFAULT_ENDPOINTS = [
    "/selfservice",
    "/selfservice/expense",
    "/selfservice/report",
    "/selfservice/reports",
    "/selfservice/tax",
    "/selfservice/taxes",
    "/selfservice/tax-report",
    "/selfservice/tax-reports",
    "/selfservice/declaration",
    "/selfservice/declarations",
    "/selfservice/accounting",
    "/selfservice/assets",
    "/selfservice/fixed-assets",
    "/selfservice/amortization",
]

INTERESTING_PATTERNS = [
    "tax",
    "report",
    "declaration",
    "modelo",
    "mod 130",
    "130",
    "asset",
    "amort",
    "depreciat",
    "register",
    "ledger",
    "expense",
    "accounting",
]


@dataclass
class ProbeResult:
    url: str
    status: int
    content_type: str
    body_bytes: int
    title: str
    interesting_terms: list[str]
    interesting_links: list[str]
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe authenticated Xolo self-service pages without storing secrets")
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--endpoint", action="append", default=[], help="Additional endpoint or URL to probe")
    parser.add_argument("--max-pages", type=int, default=40)
    args = parser.parse_args()

    cookie = os.environ.get("XOLO_COOKIE")
    if not cookie:
        raise SystemExit("Set XOLO_COOKIE in the environment; do not commit it")

    endpoints = DEFAULT_ENDPOINTS + args.endpoint
    results = probe(cookie, endpoints, args.max_pages)
    write_json(args.out_json, results)
    write_markdown(args.out_md, results)
    print(f"Probed {len(results)} Xolo self-service URLs into {args.out_json} and {args.out_md}")
    return 0


def probe(cookie: str, endpoints: Iterable[str], max_pages: int) -> list[ProbeResult]:
    seen: set[str] = set()
    queue: deque[str] = deque(_normalize_url(endpoint) for endpoint in endpoints)
    results: list[ProbeResult] = []

    while queue and len(results) < max_pages:
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        result, body = _fetch(cookie, url)
        results.append(result)
        if result.status != 200 or not body:
            continue
        for link in _interesting_links(body, url):
            if link not in seen and len(seen) + len(queue) < max_pages * 3:
                queue.append(link)
    return results


def write_json(path: Path, results: list[ProbeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([asdict(result) for result in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def write_markdown(path: Path, results: list[ProbeResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Xolo Self-Service Endpoint Probe",
        "",
        "Read-only authenticated probe. Cookie and CSRF values are never written to this artifact.",
        "",
        "| Status | URL | Title | Terms | Links | Error |",
        "|---:|---|---|---|---:|---|",
    ]
    for result in results:
        terms = ", ".join(result.interesting_terms)
        lines.append(
            "| "
            + " | ".join(
                [
                    str(result.status),
                    _cell(result.url),
                    _cell(result.title),
                    _cell(terms),
                    str(len(result.interesting_links)),
                    _cell(result.error),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Interesting Links", ""])
    for result in results:
        if not result.interesting_links:
            continue
        lines.extend([f"### {result.url}", ""])
        for link in result.interesting_links:
            lines.append(f"- `{link}`")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _fetch(cookie: str, url: str) -> tuple[ProbeResult, str]:
    request = Request(
        url,
        headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "cookie": cookie,
            "referer": BASE_URL + "/selfservice",
            "user-agent": "Mozilla/5.0",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
            body = raw.decode("utf-8", errors="replace")
            content_type = response.headers.get("content-type", "")
            return _result(url, response.status, content_type, len(raw), body), body
    except HTTPError as exc:
        raw = exc.read()
        body = raw.decode("utf-8", errors="replace")
        content_type = exc.headers.get("content-type", "") if exc.headers else ""
        result = _result(url, exc.code, content_type, len(raw), body)
        result.error = _short_error(body) or exc.reason
        return result, body
    except URLError as exc:
        return (
            ProbeResult(
                url=url,
                status=0,
                content_type="",
                body_bytes=0,
                title="",
                interesting_terms=[],
                interesting_links=[],
                error=str(exc.reason),
            ),
            "",
        )


def _result(url: str, status: int, content_type: str, body_bytes: int, body: str) -> ProbeResult:
    text = _plain_text(body)
    return ProbeResult(
        url=url,
        status=status,
        content_type=content_type,
        body_bytes=body_bytes,
        title=_title(body),
        interesting_terms=_interesting_terms(text),
        interesting_links=_interesting_links(body, url),
    )


def _normalize_url(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return _strip_query(endpoint)
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return BASE_URL + endpoint


def _strip_query(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))


def _interesting_terms(text: str) -> list[str]:
    lower = text.lower()
    return [term for term in INTERESTING_PATTERNS if term in lower]


def _interesting_links(body: str, current_url: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"""href=["']([^"']+)["']""", body):
        href = html.unescape(match.group(1))
        if href.startswith("#") or href.startswith("javascript:"):
            continue
        url = _strip_query(urljoin(current_url, href))
        parsed = urlparse(url)
        if parsed.netloc != "app.xolo.io" or not parsed.path.startswith("/selfservice"):
            continue
        if _is_interesting_url(url):
            links.append(url)
    return sorted(set(links))


def _is_interesting_url(url: str) -> bool:
    lower = url.lower()
    return any(term in lower for term in INTERESTING_PATTERNS)


def _title(body: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", body, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()


def _plain_text(body: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", html.unescape(body))).strip()


def _short_error(body: str) -> str:
    text = _plain_text(body)
    return text[:200]


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
