from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
from uuid import NAMESPACE_URL, uuid5


IMMUTABLE_ROW_STATUSES = {"closed", "historical"}


@dataclass(frozen=True)
class SheetRow:
    uuid: str
    row_version: int
    status: str
    values: dict[str, Any]


@dataclass(frozen=True)
class SheetUpdate:
    row: SheetRow
    expected_row_version: int


@dataclass(frozen=True)
class SheetConflict:
    uuid: str
    reason: str
    local_row_version: int | None
    remote_row_version: int | None


@dataclass(frozen=True)
class SheetSyncPlan:
    push_create: tuple[SheetRow, ...]
    push_update: tuple[SheetUpdate, ...]
    pull_create: tuple[SheetRow, ...]
    pull_update: tuple[SheetRow, ...]
    conflicts: tuple[SheetConflict, ...]
    unchanged: tuple[str, ...]


@dataclass(frozen=True)
class SheetPushResult:
    rows: tuple[SheetRow, ...]
    conflicts: tuple[SheetConflict, ...]


def stable_row_uuid(*parts: object) -> str:
    token = "::".join(str(part).strip() for part in parts)
    return str(uuid5(NAMESPACE_URL, token))


def normalize_sheet_row(row: SheetRow | Mapping[str, Any]) -> SheetRow:
    if isinstance(row, SheetRow):
        return row
    if "uuid" not in row or not str(row["uuid"]).strip():
        raise ValueError(f"Sheet row is missing uuid: {row!r}")
    if "row_version" not in row:
        raise ValueError(f"Sheet row is missing row_version: {row!r}")
    row_version = int(row["row_version"])
    if row_version < 1:
        raise ValueError(f"Sheet row_version must be >= 1: {row!r}")
    status = str(row.get("status", "open")).strip().lower() or "open"
    values = {str(key): value for key, value in row.items() if key not in {"uuid", "row_version", "status"}}
    return SheetRow(uuid=str(row["uuid"]).strip(), row_version=row_version, status=status, values=values)


def diff_sheet_rows(
    local_rows: Sequence[SheetRow | Mapping[str, Any]],
    remote_rows: Sequence[SheetRow | Mapping[str, Any]],
) -> SheetSyncPlan:
    local_by_uuid = _index_rows(local_rows)
    remote_by_uuid = _index_rows(remote_rows)

    push_create: list[SheetRow] = []
    push_update: list[SheetUpdate] = []
    pull_create: list[SheetRow] = []
    pull_update: list[SheetRow] = []
    conflicts: list[SheetConflict] = []
    unchanged: list[str] = []

    for row_uuid in sorted(set(local_by_uuid) | set(remote_by_uuid)):
        local = local_by_uuid.get(row_uuid)
        remote = remote_by_uuid.get(row_uuid)
        if local is None:
            pull_create.append(remote)
            continue
        if remote is None:
            push_create.append(local)
            continue
        if _is_immutable(local) or _is_immutable(remote):
            if _rows_equal(local, remote):
                unchanged.append(row_uuid)
            else:
                conflicts.append(
                    SheetConflict(
                        uuid=row_uuid,
                        reason="immutable_row",
                        local_row_version=local.row_version,
                        remote_row_version=remote.row_version,
                    )
                )
            continue
        if _rows_equal(local, remote):
            if local.row_version == remote.row_version:
                unchanged.append(row_uuid)
            elif local.row_version > remote.row_version:
                push_update.append(SheetUpdate(row=local, expected_row_version=remote.row_version))
            else:
                pull_update.append(remote)
            continue
        if local.row_version == remote.row_version:
            conflicts.append(
                SheetConflict(
                    uuid=row_uuid,
                    reason="divergent_same_version",
                    local_row_version=local.row_version,
                    remote_row_version=remote.row_version,
                )
            )
            continue
        if local.row_version > remote.row_version:
            push_update.append(SheetUpdate(row=local, expected_row_version=remote.row_version))
        else:
            pull_update.append(remote)

    return SheetSyncPlan(
        push_create=tuple(push_create),
        push_update=tuple(push_update),
        pull_create=tuple(pull_create),
        pull_update=tuple(pull_update),
        conflicts=tuple(conflicts),
        unchanged=tuple(unchanged),
    )


def apply_sheet_pull(
    local_rows: Sequence[SheetRow | Mapping[str, Any]],
    plan: SheetSyncPlan,
) -> tuple[SheetRow, ...]:
    rows = _index_rows(local_rows)
    for row in plan.pull_create:
        rows[row.uuid] = row
    for row in plan.pull_update:
        rows[row.uuid] = row
    return tuple(rows[row_uuid] for row_uuid in sorted(rows))


def apply_sheet_push(
    remote_rows: Sequence[SheetRow | Mapping[str, Any]],
    plan: SheetSyncPlan,
) -> SheetPushResult:
    rows = _index_rows(remote_rows)
    conflicts: list[SheetConflict] = []

    for row in plan.push_create:
        existing = rows.get(row.uuid)
        if existing is not None:
            conflicts.append(
                SheetConflict(
                    uuid=row.uuid,
                    reason="duplicate_uuid_on_create",
                    local_row_version=row.row_version,
                    remote_row_version=existing.row_version,
                )
            )
            continue
        rows[row.uuid] = row

    for update in plan.push_update:
        existing = rows.get(update.row.uuid)
        if existing is None:
            conflicts.append(
                SheetConflict(
                    uuid=update.row.uuid,
                    reason="missing_remote_row",
                    local_row_version=update.row.row_version,
                    remote_row_version=None,
                )
            )
            continue
        if _is_immutable(existing) and not _rows_equal(existing, update.row):
            conflicts.append(
                SheetConflict(
                    uuid=update.row.uuid,
                    reason="immutable_row",
                    local_row_version=update.row.row_version,
                    remote_row_version=existing.row_version,
                )
            )
            continue
        if existing.row_version != update.expected_row_version:
            conflicts.append(
                SheetConflict(
                    uuid=update.row.uuid,
                    reason="stale_row_version",
                    local_row_version=update.row.row_version,
                    remote_row_version=existing.row_version,
                )
            )
            continue
        rows[update.row.uuid] = update.row

    return SheetPushResult(rows=tuple(rows[row_uuid] for row_uuid in sorted(rows)), conflicts=tuple(conflicts))


def _index_rows(rows: Sequence[SheetRow | Mapping[str, Any]]) -> dict[str, SheetRow]:
    indexed: dict[str, SheetRow] = {}
    for raw_row in rows:
        row = normalize_sheet_row(raw_row)
        if row.uuid in indexed:
            raise ValueError(f"Duplicate row uuid: {row.uuid}")
        indexed[row.uuid] = row
    return indexed


def _is_immutable(row: SheetRow) -> bool:
    return row.status in IMMUTABLE_ROW_STATUSES


def _rows_equal(left: SheetRow, right: SheetRow) -> bool:
    return left.status == right.status and left.values == right.values
