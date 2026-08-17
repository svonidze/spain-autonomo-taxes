from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
from typing import Iterable


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def restore_backup(*, archive: Path, manifest: Path, target_root: Path) -> Path:
    source = archive.expanduser().resolve(strict=True)
    manifest_path = manifest.expanduser().resolve(strict=True)
    target = target_root.expanduser().resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if payload.get("archive") != source.name:
        raise ValueError("Manifest archive name does not match requested archive")
    if payload.get("archive_sha256") != _sha256(source):
        raise ValueError("Archive SHA-256 does not match manifest")
    if target.exists() and any(target.iterdir()):
        raise ValueError("Restore target must not already contain files")
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    expected = {
        str(row["path"]): (str(row["sha256"]), int(row["size"]))
        for row in payload.get("files", [])
    }
    with tarfile.open(source, "r:gz") as tar:
        members = tar.getmembers()
        for member in members:
            relative = PurePosixPath(member.name)
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or not member.isfile()
                or member.name not in expected
            ):
                raise ValueError("Archive contains an unsafe or unexpected member")
        if {member.name for member in members} != set(expected):
            raise ValueError("Archive members do not match manifest")
        for member in members:
            destination = target.joinpath(*PurePosixPath(member.name).parts)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            extracted = tar.extractfile(member)
            if extracted is None:
                raise ValueError("Archive member could not be read")
            with destination.open("wb") as handle:
                shutil.copyfileobj(extracted, handle)
            destination.chmod(0o600)
            digest, size = expected[member.name]
            if destination.stat().st_size != size or _sha256(destination) != digest:
                raise ValueError(f"Restored file did not match manifest: {member.name}")
    return target


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Restore a verified private-root archive into an empty directory")
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = restore_backup(
        archive=args.archive,
        manifest=args.manifest,
        target_root=args.target_root,
    )
    print(f"restored_root={target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
