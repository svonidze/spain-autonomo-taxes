from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable


DRIVE_FILE_SCOPE = "https://www.googleapis.com/auth/drive.file"


def authorize(*, client_secret: Path, token_out: Path) -> Path:
    """Run the one-time user OAuth consent flow for the private Drive root."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:  # pragma: no cover - dependency guard
        raise RuntimeError("google-auth-oauthlib is required for Google Drive authorization") from exc
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret), [DRIVE_FILE_SCOPE])
    credentials = flow.run_local_server(port=0, open_browser=True)
    token_out.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    token_out.write_text(credentials.to_json(), encoding="utf-8")
    token_out.chmod(0o600)
    return token_out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authorize the private Google Drive storage backend")
    parser.add_argument("--client-secret", type=Path, required=True)
    parser.add_argument("--token-out", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    token = authorize(client_secret=args.client_secret, token_out=args.token_out)
    print(f"oauth_token={token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
