"""Agent-facing adapters: private JSON input, structured output, shared services."""

from __future__ import annotations
from datetime import date
from decimal import Decimal
from pathlib import Path
import json
import getpass
import os
import re
import sqlite3
import sys
from typing import Any
from .private_paths import configured_private_root
from .services.application import AccountingService, RuntimeConfig
from .services.common import (
    ServiceApiError,
    ServiceError,
    _exact_object_fields,
    _validated_intake_fields,
    private_output,
)


def register_commands(groups):
    def command(group, name, help, *, identifier=False, input=False, period=False):
        parser = groups[group].add_parser(name, help=help)
        parser.add_argument("--db", type=Path)
        parser.add_argument("--inbox-root", type=Path)
        parser.add_argument("--archive-root", type=Path)
        parser.add_argument("--cache-root", type=Path)
        parser.add_argument("--read-only-document-root", type=Path, action="append")
        if identifier:
            parser.add_argument("id")
        if input:
            parser.add_argument(
                "--input",
                type=Path,
                required=True,
                help="Private JSON file, or - for stdin",
            )
        if period:
            parser.add_argument("--period", required=True)
        parser.set_defaults(_toolkit_action=f"{group}.{name}", _operational_handler=run)
        return parser

    local = command(
        "intake", "local", "Accept one local original for review", input=True
    )
    local.add_argument("file", type=Path)
    local.add_argument(
        "--google-folder-id", help="Explicitly authorized cloud destination"
    )
    drive = command(
        "intake",
        "google-drive",
        "Accept one existing Drive original for review",
        input=True,
    )
    drive.add_argument("url")
    tx = command(
        "transactions", "list", "Read transactions and accounting status", period=True
    )
    tx.add_argument("--entry-type", choices=["income", "expense"])
    tx.add_argument("--status")
    tx.add_argument("--query")
    tx.add_argument("--limit", type=int, default=250)
    command(
        "transactions",
        "show",
        "Read one transaction, its evidence and treatments",
        identifier=True,
    )
    docs = command("documents", "list", "Read document inventory", period=True)
    docs.add_argument("--kind")
    docs.add_argument("--limit", type=int, default=250)
    original = command(
        "documents",
        "original",
        "Copy the original into a new private output file",
        identifier=True,
    )
    original.add_argument("--out", type=Path, required=True)
    expenses = command(
        "expense",
        "list",
        "Read purchase/depreciation rows with stable paging",
        period=True,
    )
    expenses.add_argument("--query")
    expenses.add_argument("--offset", type=int, default=0)
    expenses.add_argument("--limit", type=int, default=100)
    work = command(
        "review",
        "work-item",
        "Read a fresh decision packet, readiness and verified FX suggestion",
        identifier=True,
    )
    work.add_argument(
        "--out",
        type=Path,
        required=True,
        help="New file inside the private root; preserves the immutable packet",
    )
    command(
        "review",
        "posting-preview",
        "Read explicit posting rows, versions and blockers",
        period=True,
    )
    command(
        "review",
        "confirm-packet",
        "Atomically apply one decision and verified FX without posting",
        input=True,
    )
    command("counterparties", "list", "Read counterparties and their current versions")
    command("counterparties", "show", "Read one counterparty", identifier=True)
    rows = command(
        "counterparties",
        "transactions",
        "Read one counterparty operation history",
        identifier=True,
    )
    rows.add_argument("--period")
    rows.add_argument("--offset", type=int, default=0)
    rows.add_argument("--limit", type=int, default=100)

    draft = command(
        "expense",
        "draft",
        "Read the exact expense draft into a private file",
        identifier=True,
    )
    draft.add_argument("--out", type=Path, required=True)
    for name, description in [
        ("save", "Save one draft with its source hash and version"),
        ("preview", "Validate one saved draft without posting"),
        ("confirm", "Post exactly the accepted expense preview"),
    ]:
        command("expense", name, description, identifier=True, input=True)
    command(
        "expense",
        "follow-up",
        "Retry cleanup and calculation only for a posted expense",
        identifier=True,
    )
    command(
        "assets",
        "schedule",
        "Read the native asset schedule and posting eligibility",
        identifier=True,
    )
    command(
        "assets",
        "post-depreciation",
        "Post one due schedule entry with a durable request ID",
        identifier=True,
        input=True,
    )


def context(args):
    private = args._private_paths
    values = getattr(args, "_runtime_values", {})
    base = private.config_path.parent if private.config_path else private.root

    def configured(name, default=None):
        value = values.get(name)
        if value in (None, ""):
            return default
        path = Path(value)
        return path if path.is_absolute() else base / path

    roots = args.read_only_document_root
    if roots is None:
        raw = values.get("read_only_document_roots", [])
        if not isinstance(raw, list) or not all(
            isinstance(value, str) for value in raw
        ):
            raise ValueError("read_only_document_roots must be a list of paths")
        roots = [
            Path(value) if Path(value).is_absolute() else base / value for value in raw
        ]
    return RuntimeConfig(
        project_root=Path.cwd(),
        database=args.db.resolve(),
        inbox_root=args.inbox_root or private.inbox,
        archive_root=args.archive_root or private.evidence,
        cache_root=args.cache_root
        or configured("cache_root", private.cache / "web" / "dashboard"),
        read_only_document_roots=tuple(roots),
        legacy_path_map_file=configured("legacy_path_map_file"),
        private_root=private.root,
    )


def read_input(path):
    if str(path) == "-":
        text = sys.stdin.read(65537)
    else:
        with path.open() as source:
            text = source.read(65537)
    if len(text.encode("utf-8")) > 65536:
        raise ValueError("JSON input exceeds 64 KiB")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def public_value(value):
    if isinstance(value, dict):
        return {
            key: public_value(item)
            for key, item in value.items()
            if key
            not in {
                "source_path",
                "credential_ref",
                "config_json",
                "access_token",
                "refresh_token",
                "database",
                "archive_root",
                "inbox_root",
                "cache_root",
            }
        }
    if isinstance(value, (list, tuple)):
        return [public_value(item) for item in value]
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str) and (
        value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value)
    ):
        return Path(value.replace("\\", "/")).name
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def dispatch(app, args):
    action = args._toolkit_action
    if action == "intake.local":
        facts = _validated_intake_fields(read_input(args.input))
        if args.google_folder_id and not re.fullmatch(
            r"[A-Za-z0-9_-]{10,256}", args.google_folder_id
        ):
            raise ValueError("Invalid Google folder ID")
        with args.file.open("rb") as source:
            content = source.read(30 * 1024 * 1024 + 1)
        return app.ingest_upload(
            fields=facts,
            filename=args.file.name,
            content=content,
            google_folder_id=args.google_folder_id,
        )
    if action == "intake.google-drive":
        return app.ingest_google_drive_url(
            fields=read_input(args.input), drive_url=args.url
        )
    if action == "transactions.list":
        return app.transactions(
            args.period,
            entry_type=args.entry_type,
            lifecycle_status=args.status,
            query=args.query,
            limit=args.limit,
        )
    if action == "transactions.show":
        return app.transaction_detail(args.id)
    if action == "documents.list":
        return app.documents(args.period, document_type=args.kind, limit=args.limit)
    if action == "documents.original":
        return app.export_original(args.id, args.out)
    if action == "expense.list":
        return app.expenses(
            args.period, query=args.query, offset=args.offset, limit=args.limit
        )
    if action == "review.work-item":
        return app.review_work_item(args.id)
    if action == "review.posting-preview":
        return app.posting_preview(args.period)
    if action == "review.confirm-packet":
        payload = read_input(args.input)
        payload.setdefault("fx", None)
        _exact_object_fields(payload, {"packet", "fx"}, "review confirmation")
        return app.review_confirm(payload["packet"], payload["fx"])
    if action == "counterparties.list":
        return app.counterparties()
    if action == "counterparties.show":
        return app.counterparty_detail(args.id)
    if action == "counterparties.transactions":
        return app.counterparty_transactions(
            args.id, period_key=args.period, offset=args.offset, limit=args.limit
        )
    if action == "expense.draft":
        return app.expense_draft(args.id)
    if action == "expense.save":
        draft = app.expense_save(
            args.id, read_input(args.input), "cli:" + getpass.getuser()
        )
        return {
            "saved": True,
            **{
                key: draft[key]
                for key in (
                    "transaction_id",
                    "draft_version",
                    "source_snapshot_hash",
                    "current_snapshot_hash",
                    "editable",
                    "conflict",
                )
            },
        }
    if action == "expense.preview":
        return app.expense_preview(args.id, read_input(args.input))
    if action == "expense.confirm":
        return app.expense_confirm(
            args.id, read_input(args.input), "cli:" + getpass.getuser()
        )
    if action == "expense.follow-up":
        return app.expense_follow_up(args.id)
    if action == "assets.schedule":
        return app.depreciation_schedule(args.id)
    if action == "assets.post-depreciation":
        return app.depreciation_post(
            args.id, read_input(args.input), "cli:" + getpass.getuser()
        )
    raise ValueError("Unsupported toolkit command")


def run(args):
    try:
        config = context(args)
        if getattr(args, "out", None) is not None:
            args.out = private_output(config, args.out)
        raw = dispatch(AccountingService(config), args)
        result = public_value(raw)
        output = getattr(args, "out", None)
        if output is not None and args._toolkit_action != "documents.original":
            output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(raw, file, ensure_ascii=False, indent=2)
                file.write("\n")
            result = {
                "written": output.name,
                "review_id": raw.get("review_id"),
                "snapshot_hash": raw.get("packet", {}).get("snapshot_hash"),
                "supported": raw.get("supported"),
                "review_allowed": raw.get("review_allowed"),
            }
            if args._toolkit_action == "expense.draft":
                result = {
                    "written": output.name,
                    **{
                        key: raw[key]
                        for key in (
                            "transaction_id",
                            "draft_version",
                            "source_snapshot_hash",
                            "current_snapshot_hash",
                            "editable",
                            "conflict",
                        )
                    },
                }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        code = getattr(error, "code", None) or (
            "not_found"
            if isinstance(error, FileNotFoundError)
            else (
                "io_error"
                if isinstance(error, OSError)
                else (
                    "invalid_input"
                    if isinstance(error, (ValueError, TypeError))
                    else "internal_error"
                )
            )
        )
        message = str(error)
        if isinstance(error, OSError) or re.search(
            r"(?:https?://|(?:^|\s)/|[A-Za-z]:[\\/])", message
        ):
            message = "Operation failed; private diagnostic withheld."
        if code == "internal_error":
            message = "Unexpected operation failure."
        payload = {"error": message, "code": code}
        current = getattr(error, "current", None)
        if current is not None:
            payload["current"] = public_value(current)
        print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
        return 1
