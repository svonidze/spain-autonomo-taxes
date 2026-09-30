from __future__ import annotations

import argparse
import html
import json
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys
from typing import Any
from urllib.parse import parse_qsl, urljoin, urlparse, urlunparse


BASE_URL = "https://app.xolo.io"

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from autonomo_taxes.private_paths import configured_private_root  # noqa: E402

DEFAULT_ENDPOINTS = [
    "/selfservice",
    "/selfservice/expense",
    "/selfservice/tax-report",
    "/selfservice/leap-esp/tax-report",
    "/selfservice/leap-esp/tax-report/irpf",
    "/selfservice/leap-esp/tax-report/vat",
    "/selfservice/leap-esp/tax-report/others",
    "/selfservice/accounting",
    "/selfservice/reports",
    "/selfservice/report",
    "/selfservice/assets",
    "/selfservice/fixed-assets",
    "/selfservice/depreciation",
    "/selfservice/amortization",
    "/selfservice/data-export",
    "/selfservice/dataexport",
    "/selfservice/export",
]

INTERESTING_PATTERNS = [
    "130",
    "accounting",
    "amort",
    "amortiz",
    "asset",
    "calculation",
    "dataexport",
    "declaration",
    "deduct",
    "depreci",
    "download",
    "expense",
    "fixed",
    "gastos",
    "bienes",
    "irpf",
    "ledger",
    "libro",
    "modelo",
    "register",
    "registro",
    "report",
    "tax",
    "inversion",
    "inversión",
]


@dataclass
class PageProbe:
    source_url: str
    final_url: str
    status: int
    title: str
    body_chars: int
    interesting_terms: list[str]
    interesting_links: list[str]
    error: str = ""


@dataclass
class NetworkObservation:
    source_page: str
    method: str
    status: int
    resource_type: str
    content_type: str
    url: str
    query_keys: list[str]
    error: str = ""


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Probe authenticated Xolo self-service pages through Playwright storage state or a persistent profile. "
            "The output intentionally strips query values and never serializes cookies or CSRF tokens."
        )
    )
    parser.add_argument(
        "--storage-state",
        type=Path,
        default=configured_private_root() / "browser" / "xolo" / "storage-state.json",
    )
    parser.add_argument(
        "--profile-dir",
        type=Path,
        help="Use a persistent Playwright profile instead of a saved storage-state JSON.",
    )
    parser.add_argument("--out-json", type=Path, required=True)
    parser.add_argument("--out-md", type=Path, required=True)
    parser.add_argument("--endpoint", action="append", default=[], help="Additional endpoint or URL to probe")
    parser.add_argument("--max-pages", type=int, default=60)
    parser.add_argument("--network-idle-ms", type=int, default=10_000)
    parser.add_argument("--settle-ms", type=int, default=250)
    parser.add_argument(
        "--no-discover-links",
        action="store_true",
        help="Probe only the explicit/default endpoint list without queueing discovered self-service links.",
    )
    args = parser.parse_args()

    if args.profile_dir is not None:
        if not args.profile_dir.exists():
            raise SystemExit(f"Profile directory not found: {args.profile_dir}. Run scripts/imports/xolo_playwright_login.py first.")
    elif not args.storage_state.exists():
        raise SystemExit(f"Storage state not found: {args.storage_state}. Run scripts/imports/xolo_playwright_login.py first.")

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - environment guard.
        raise SystemExit("Playwright is required for storage-state probing") from exc

    pages, network = probe(
        sync_playwright=sync_playwright,
        timeout_error=PlaywrightTimeoutError,
        storage_state=None if args.profile_dir is not None else args.storage_state,
        profile_dir=args.profile_dir,
        endpoints=DEFAULT_ENDPOINTS + args.endpoint,
        max_pages=args.max_pages,
        discover_links=not args.no_discover_links,
        network_idle_ms=args.network_idle_ms,
        settle_ms=args.settle_ms,
    )
    auth_source = f"profile-dir:{args.profile_dir}" if args.profile_dir is not None else f"storage-state:{args.storage_state}"
    write_json(args.out_json, pages, network, auth_source=auth_source)
    write_markdown(args.out_md, pages, network, auth_source=auth_source)
    print(f"Probed {len(pages)} Xolo pages and {len(network)} network responses into {args.out_json} and {args.out_md}")
    return 0


def probe(
    *,
    sync_playwright: Any,
    timeout_error: type[Exception],
    storage_state: Path | None,
    profile_dir: Path | None,
    endpoints: list[str],
    max_pages: int,
    discover_links: bool,
    network_idle_ms: int,
    settle_ms: int,
) -> tuple[list[PageProbe], list[NetworkObservation]]:
    pages: list[PageProbe] = []
    network: list[NetworkObservation] = []
    current_page = {"url": ""}

    with sync_playwright() as playwright:
        browser = None
        if profile_dir is not None:
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                headless=True,
                viewport={"width": 1600, "height": 1200},
            )
        else:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(storage_state), viewport={"width": 1600, "height": 1200})
        page = context.pages[0] if context.pages else context.new_page()

        def on_response(response: Any) -> None:
            observation = _network_observation(response, current_page.get("url", ""))
            if observation is not None:
                network.append(observation)

        page.on("response", on_response)

        seen: set[str] = set()
        queue: deque[str] = deque(_normalize_url(endpoint) for endpoint in endpoints)
        while queue and len(pages) < max_pages:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            current_page["url"] = url
            result = _probe_page(page, url, timeout_error, network_idle_ms, settle_ms)
            pages.append(result)
            if not discover_links or result.status >= 400:
                continue
            for link in result.interesting_links:
                if link not in seen and _should_queue_link(link) and len(seen) + len(queue) < max_pages * 4:
                    queue.append(link)

        context.close()
        if browser is not None:
            browser.close()

    return pages, network


def write_json(path: Path, pages: list[PageProbe], network: list[NetworkObservation], *, auth_source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "page_count": len(pages),
        "auth_source": auth_source,
        "network_observation_count": len(network),
        "pages": [asdict(page) for page in pages],
        "network_observations": [asdict(item) for item in network],
        "network_summary": _network_summary_rows(network),
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def write_markdown(path: Path, pages: list[PageProbe], network: list[NetworkObservation], *, auth_source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    login_like_pages = [page for page in pages if "log in" in page.title.lower() or "/hub/login" in page.final_url]
    candidate_rows = [
        row
        for row in _network_summary_rows(network)
        if _interesting_terms(row["url"]) or any(key.lower() in {"_csrf", "draw", "start", "length"} for key in row["query_keys"])
    ]

    lines = [
        "# Xolo Auth Endpoint Probe",
        "",
        "Read-only authenticated probe through Playwright storage state or a persistent profile. Query values, cookies, and CSRF token values are not written to this artifact.",
        "",
        "## Summary",
        "",
        f"- Auth source: `{auth_source}`.",
        f"- Pages probed: `{len(pages)}`.",
        f"- Network responses captured: `{len(network)}`.",
        f"- Distinct same-origin Xolo network endpoints: `{len(_network_summary_rows(network))}`.",
        f"- Login-like pages: `{len(login_like_pages)}`.",
        "",
        "## Pages",
        "",
        "| Status | Source URL | Final URL | Title | Terms | Links | Error |",
        "|---:|---|---|---|---|---:|---|",
    ]
    for page in pages:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(page.status),
                    _cell(page.source_url),
                    _cell(page.final_url),
                    _cell(page.title),
                    _cell(", ".join(page.interesting_terms)),
                    str(len(page.interesting_links)),
                    _cell(page.error),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Candidate Network Endpoints", ""])
    if candidate_rows:
        lines.extend(["| Count | Statuses | Method | URL | Content types | Query keys | Sources |", "|---:|---|---|---|---|---|---:|"])
        for row in candidate_rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(row["count"]),
                        _cell(", ".join(row["statuses"])),
                        _cell(row["method"]),
                        _cell(row["url"]),
                        _cell(", ".join(row["content_types"])),
                        _cell(", ".join(row["query_keys"])),
                        str(row["source_count"]),
                    ]
                )
                + " |"
            )
    else:
        lines.append("No candidate network endpoints matched the audit terms.")

    lines.extend(["", "## Interesting Links", ""])
    for page in pages:
        if not page.interesting_links:
            continue
        lines.extend([f"### `{page.source_url}`", ""])
        for link in page.interesting_links[:40]:
            lines.append(f"- `{link}`")
        if len(page.interesting_links) > 40:
            lines.append(f"- ... {len(page.interesting_links) - 40} more")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")


def _probe_page(page: Any, url: str, timeout_error: type[Exception], network_idle_ms: int, settle_ms: int) -> PageProbe:
    status = 0
    error = ""
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=60_000)
        status = int(response.status) if response is not None else 0
        try:
            page.wait_for_load_state("networkidle", timeout=network_idle_ms)
        except timeout_error:
            error = f"networkidle timeout after {network_idle_ms}ms"
        if settle_ms:
            page.wait_for_timeout(settle_ms)
    except Exception as exc:  # pragma: no cover - live browser failure guard.
        return PageProbe(
            source_url=url,
            final_url="",
            status=status,
            title="",
            body_chars=0,
            interesting_terms=[],
            interesting_links=[],
            error=str(exc)[:300],
        )

    title = _safe_call(lambda: page.title(), "")
    final_url = _sanitize_url(_safe_call(lambda: page.url, ""))
    text = _safe_body_text(page)
    links = _safe_links(page, final_url or url)
    return PageProbe(
        source_url=url,
        final_url=final_url,
        status=status,
        title=title,
        body_chars=len(text),
        interesting_terms=_interesting_terms(text),
        interesting_links=links,
        error=error,
    )


def _network_observation(response: Any, source_page: str) -> NetworkObservation | None:
    try:
        url, query_keys = _sanitize_url_with_query_keys(response.url)
        if not _is_network_capture_url(url):
            return None
        request = response.request
        return NetworkObservation(
            source_page=source_page,
            method=request.method,
            status=int(response.status),
            resource_type=request.resource_type,
            content_type=response.headers.get("content-type", ""),
            url=url,
            query_keys=query_keys,
        )
    except Exception as exc:  # pragma: no cover - response metadata can vanish during navigation.
        return NetworkObservation(
            source_page=source_page,
            method="",
            status=0,
            resource_type="",
            content_type="",
            url="",
            query_keys=[],
            error=str(exc)[:300],
        )


def _network_summary_rows(network: list[NetworkObservation]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[NetworkObservation]] = {}
    for observation in network:
        if not observation.url:
            continue
        grouped.setdefault((observation.method, observation.url), []).append(observation)

    rows: list[dict[str, Any]] = []
    for (method, url), items in grouped.items():
        statuses = sorted({str(item.status) for item in items})
        content_types = sorted({item.content_type.split(";")[0] for item in items if item.content_type})
        query_keys = sorted({key for item in items for key in item.query_keys})
        source_pages = sorted({item.source_page for item in items if item.source_page})
        rows.append(
            {
                "count": len(items),
                "method": method,
                "url": url,
                "statuses": statuses,
                "content_types": content_types,
                "query_keys": query_keys,
                "source_count": len(source_pages),
            }
        )
    rows.sort(key=lambda row: (-int(row["count"]), row["url"], row["method"]))
    return rows


def _normalize_url(endpoint: str) -> str:
    endpoint = endpoint.strip()
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        return _sanitize_url(endpoint)
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return BASE_URL + endpoint


def _sanitize_url(url: str) -> str:
    sanitized, _ = _sanitize_url_with_query_keys(url)
    return sanitized


def _sanitize_url_with_query_keys(url: str) -> tuple[str, list[str]]:
    if not url:
        return "", []
    parsed = urlparse(url)
    query_keys = sorted({key for key, _ in parse_qsl(parsed.query, keep_blank_values=True)})
    sanitized = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return sanitized, query_keys


def _safe_body_text(page: Any) -> str:
    try:
        text = page.locator("body").inner_text(timeout=3_000)
    except Exception:
        text = _plain_text(_safe_call(lambda: page.content(), ""))
    return re.sub(r"\s+", " ", text).strip()


def _safe_links(page: Any, current_url: str) -> list[str]:
    include_modelo130_calculations = current_url.rstrip("/") in {
        f"{BASE_URL}/selfservice/tax-report",
        f"{BASE_URL}/selfservice/leap-esp/tax-report",
    }
    try:
        hrefs = page.eval_on_selector_all(
            "a",
            """(els, includeModelo130Calculations) => els.flatMap(a => {
              const out = [];
              const href = a.getAttribute('href');
              if (href) out.push(href);
              const reportId = a.getAttribute('data-report-id');
              if (includeModelo130Calculations && reportId && /^\\d+$/.test(reportId)) {
                out.push('/selfservice/leap-esp/tax-report/calculation/130/' + reportId);
              }
              return out;
            })""",
            include_modelo130_calculations,
        )
    except Exception:
        hrefs = []
    links: list[str] = []
    for href in hrefs:
        if not href or href.startswith("#") or href.startswith("javascript:"):
            continue
        url = _sanitize_url(urljoin(current_url, html.unescape(href)))
        if _is_relevant_url(url) and (_interesting_terms(url) or _is_selfservice_url(url)):
            links.append(url)
    return sorted(set(links))


def _should_queue_link(url: str) -> bool:
    parsed = urlparse(url)
    if "/selfservice/expense/invoice/" in parsed.path:
        return False
    if "/selfservice/settings/company-documents/file/" in parsed.path:
        return False
    if "/download" in parsed.path:
        return False
    return _is_relevant_url(url) and bool(_interesting_terms(url))


def _is_relevant_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == "app.xolo.io" and parsed.path.startswith("/selfservice")


def _is_selfservice_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == "app.xolo.io" and parsed.path.startswith("/selfservice")


def _is_network_capture_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == "app.xolo.io" and parsed.path != "/hub/logout"


def _interesting_terms(value: str) -> list[str]:
    lower = value.lower()
    return [term for term in INTERESTING_PATTERNS if term in lower]


def _plain_text(value: str) -> str:
    text = re.sub(r"<[^>]*>", " ", value)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _safe_call(func: Any, default: Any) -> Any:
    try:
        return func()
    except Exception:
        return default


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
