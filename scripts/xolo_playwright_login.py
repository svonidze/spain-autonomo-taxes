from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Xolo in Playwright and save an authenticated storage state")
    parser.add_argument("--profile-dir", type=Path, default=Path(".omx/xolo-playwright-profile"))
    parser.add_argument("--state-path", type=Path, default=Path(".omx/xolo-storage-state.json"))
    parser.add_argument("--log-path", type=Path, default=Path(".omx/xolo-playwright-login.log"))
    parser.add_argument("--url", default="https://app.xolo.io/selfservice/expense")
    parser.add_argument("--timeout-minutes", type=int, default=30)
    args = parser.parse_args()

    args.profile_dir.mkdir(parents=True, exist_ok=True)
    args.state_path.parent.mkdir(parents=True, exist_ok=True)
    args.log_path.parent.mkdir(parents=True, exist_ok=True)

    def log(message: str) -> None:
        timestamp = datetime.now().isoformat(timespec="seconds")
        with args.log_path.open("a", encoding="utf-8") as handle:
            handle.write(f"[{timestamp}] {message}\n")

    log(f"opening {args.url}")
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(args.profile_dir),
            headless=False,
            viewport={"width": 1400, "height": 950},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded", timeout=60_000)
        log(f"initial url={page.url!r} title={page.title()!r}")
        deadline_ms = args.timeout_minutes * 60 * 1000
        elapsed_ms = 0
        while elapsed_ms < deadline_ms:
            try:
                context.storage_state(path=str(args.state_path))
                pages = list(context.pages)
                active = [_page_snapshot(candidate) for candidate in pages]
                if any(_is_authenticated_xolo_page(candidate) for candidate in pages):
                    log(f"authenticated state saved to {args.state_path}; pages={active!r}")
                    context.close()
                    return 0
                if elapsed_ms % 10_000 == 0:
                    log(f"waiting for login; pages={active!r}")
                page.wait_for_timeout(2_000)
                elapsed_ms += 2_000
            except PlaywrightError as exc:
                log(f"browser closed while waiting for login: {exc}")
                return 3

        log(f"timeout waiting for login; last pages={[_page_snapshot(candidate) for candidate in context.pages]!r}")
        context.close()
        return 2
    return 0


def _page_snapshot(page) -> str:
    try:
        return f"{page.url} | {page.title()}"
    except PlaywrightError:
        return "<closed page>"


def _is_authenticated_xolo_page(page) -> bool:
    try:
        return page.url.startswith("https://app.xolo.io/selfservice") and "/hub/login" not in page.url
    except PlaywrightError:
        return False


if __name__ == "__main__":
    raise SystemExit(main())
