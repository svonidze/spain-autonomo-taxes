"""Build a reviewed JSON mapping from catalogue digests to original Drive file IDs.

This is deliberately a read-only preparation step.  Review the generated JSON,
run ``storage adopt-google-archive --dry-run`` with it, then run the same
command without ``--dry-run``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import subprocess
from typing import Iterable


class RecordBuildError(RuntimeError):
    pass


def build_records(
    *,
    database: Path,
    rclone_binary: str,
    rclone_config: Path,
    remote: str,
    mount_backend_key: str,
    source_books_prefix: str,
) -> list[dict[str, str]]:
    """Resolve each catalogue file to an existing path under a Drive remote."""
    if not remote.strip() or remote.endswith(":"):
        raise RecordBuildError("rclone remote must be a non-empty remote name")
    db_path = database.resolve(strict=True)
    uri = f"file:{db_path.as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT f.content_sha256,
                   MIN(d.source_path) AS source_path,
                   MAX(CASE WHEN sb.backend_key = ? THEN fr.provider_locator END) AS drive_path
            FROM files f
            JOIN document_attachments da ON da.file_id = f.file_id
            JOIN documents d ON d.document_id = da.document_id
            LEFT JOIN file_replicas fr ON fr.file_id = f.file_id
            LEFT JOIN storage_backends sb ON sb.storage_backend_id = fr.storage_backend_id
            WHERE da.attachment_role = 'source'
            GROUP BY f.file_id, f.content_sha256
            ORDER BY f.content_sha256
            """,
            (mount_backend_key,),
        ).fetchall()
    records: list[dict[str, str]] = []
    for row in rows:
        source_path = str(row["source_path"] or "")
        archive_path = str(row["drive_path"] or "")
        if not archive_path:
            name = Path(source_path).name
            if not name:
                raise RecordBuildError("Catalogue file has no legacy source filename")
            archive_path = f"{source_books_prefix.rstrip('/')}/{name}"
        metadata = _rclone_stat(
            rclone_binary=rclone_binary,
            rclone_config=rclone_config,
            remote=remote,
            path=archive_path,
        )
        drive_file_id = str(metadata.get("ID") or "")
        if not drive_file_id:
            raise RecordBuildError(f"Drive object did not expose an ID: {archive_path}")
        display_name = str(metadata.get("Name") or Path(archive_path).name)
        records.append(
            {
                "content_sha256": str(row["content_sha256"]),
                "drive_file_id": drive_file_id,
                "archive_path": archive_path,
                "display_name": display_name,
                "web_url": "https://" + "drive.google.com/open?id=" + drive_file_id,
            }
        )
    return records


def _rclone_stat(
    *,
    rclone_binary: str,
    rclone_config: Path,
    remote: str,
    path: str,
) -> dict[str, object]:
    target = f"{remote}:{path}"
    try:
        run = subprocess.run(
            [rclone_binary, "--config", str(rclone_config), "lsjson", "--stat", target],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
        )
    except FileNotFoundError as exc:
        raise RecordBuildError("rclone is not installed") from exc
    except subprocess.CalledProcessError as exc:
        raise RecordBuildError(f"Unable to inspect Drive archive path: {path}") from exc
    try:
        payload = json.loads(run.stdout)
    except json.JSONDecodeError as exc:
        raise RecordBuildError(f"rclone returned invalid metadata for: {path}") from exc
    if not isinstance(payload, dict) or payload.get("IsDir"):
        raise RecordBuildError(f"Drive archive path is not a file: {path}")
    return dict(payload)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build reviewed Google Drive adoption records without changing the database"
    )
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--rclone-binary", default="rclone")
    parser.add_argument("--rclone-config", type=Path, required=True)
    parser.add_argument("--remote", required=True)
    parser.add_argument("--mount-backend", default="drive_mount_ro")
    parser.add_argument(
        "--source-books-prefix",
        default="Налоги/Autonomo. Налоги/Xolo evidence archive/source_books",
    )
    parser.add_argument("--out", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records = build_records(
        database=args.db,
        rclone_binary=args.rclone_binary,
        rclone_config=args.rclone_config,
        remote=args.remote,
        mount_backend_key=args.mount_backend,
        source_books_prefix=args.source_books_prefix,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"records={len(records)}")
    print(f"out={args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
