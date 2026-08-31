#!/usr/bin/env python3
"""Fail closed when Git-bound content looks private.

The default scan covers the Git index plus modified tracked worktree files.
Use ``--history`` to scan all reachable blobs, historical paths, commit
messages, and annotated tags, or ``--commit-range`` for a bounded revision
set. Findings deliberately omit the matched value.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, Sequence


DEFAULT_MAX_BYTES = 2 * 1024 * 1024

PROHIBITED_TOP_LEVEL = frozenset(
    {
        ".local",
        ".omc",
        ".omx",
        "browser-sessions",
        "browser_sessions",
        "data",
        "evidence",
        "personal-exports",
        "personal_exports",
        "runs",
        "secrets",
        "source-docs",
        "source_docs",
        "tmp",
    }
)
PROHIBITED_COMPONENTS = frozenset(
    {
        ".ipynb_checkpoints",
        "browser-sessions",
        "browser_sessions",
        "personal-exports",
        "personal_exports",
        "secrets",
    }
)
PROHIBITED_SUFFIXES = (
    ".7z",
    ".db",
    ".gz",
    ".har",
    ".key",
    ".log",
    ".p12",
    ".pem",
    ".pfx",
    ".rar",
    ".sqlite",
    ".sqlite3",
    ".tar",
    ".tgz",
    ".zip",
)

# Exact SHA-256 digests of reviewed synthetic values. There is deliberately
# no path/directory allowlist: each permitted test value requires its own
# reviewable digest entry.
ALLOWED_SYNTHETIC_VALUE_SHA256 = frozenset(
    {
        # Reviewed reserved-domain emails and explicit synthetic identities.
        "1e32b53db8a32630f99ad097360287790b3ecfdc3cc472e4263624ced516e3a9",
        "19d06ddd31d129e5d8b0ed4e6edcdd767fa7440c3b6990754d4d8ddb06b5ee1f",
        "656f8b14b45a348577d4fe2806c5af6e5f272d764518379825643d3666cb814b",
        "8fea29e9f291485d8e45ba73c86cec5b31a9154b7acc21a93425660447ee3369",
        "a992ab11f27ef8a8880917fc27a095242c18c345a2a4532b61f285d2bea8fb6e",
        "b5ed50801b4a1d031a0f3fe9dbb65242aedbed33797cb3cf414eae19ffa45427",
        "b1ec61e341a488d3b65e3f59e4e6415c33354f2475dd9a80f05d8ce2a22c2977",
        "cd4d2a0e38a205e5cc3581e09f149ca1a30af3057e9dd6a6671ae351fcfdc24e",
        "faa296d58b7dcae9eec26d1991a5e3cc322ea91f0e668c0a720642817d7b0469",
    }
)
ALLOWED_BINARY_SHA256: frozenset[str] = frozenset()

# Approved Anthropic no-reply address used for commit co-author attribution.
# Exact match only, and only for email findings (never credentials/other fields).
ALLOWED_PUBLIC_BOT_EMAIL_SHA256 = frozenset(
    {"cd29c5ac348a026a3ec5286890908fffb5bf6ab77f20672171be323a70c95026"}
)


@dataclass(frozen=True, order=True)
class Finding:
    category: str
    location: str
    line: int
    fingerprint: str


@dataclass(frozen=True)
class PatternSpec:
    category: str
    pattern: re.Pattern[str]
    value_group: str | None = None


def _joined(*parts: str) -> str:
    """Keep scanner signatures from becoming scanner findings themselves."""

    return "".join(parts)


_CREDENTIAL_NAMES = _joined(
    "api[_-]?key|client[_-]?secret|password|passwd|access[_-]?token|",
    "refresh[_-]?token|auth[_-]?token|session[_-]?cookie",
)
PATTERNS: tuple[PatternSpec, ...] = (
    PatternSpec(
        "email-address",
        re.compile(r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"),
    ),
    PatternSpec(
        "spanish-tax-id",
        re.compile(r"(?<![A-Za-z0-9])(?:[XYZxyz][0-9]{7}[A-Za-z]|[0-9]{8}[A-Za-z])(?![A-Za-z0-9])"),
    ),
    PatternSpec(
        "iban",
        re.compile(r"(?<![A-Za-z0-9])ES(?:[ -]?[0-9]){22}(?![A-Za-z0-9])", re.IGNORECASE),
    ),
    PatternSpec(
        "spanish-phone",
        re.compile(r"(?<!\w)(?:\+34|0034)[\s().-]*(?:[0-9][\s().-]*){9}(?![0-9])"),
    ),
    PatternSpec(
        "google-drive-link",
        re.compile(r"https?://(?:drive|docs)\.google\.com/[^\s<>\"']+", re.IGNORECASE),
    ),
    PatternSpec(
        "private-key",
        re.compile(_joined("-----BEGIN ", r"(?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ),
    PatternSpec(
        "service-token",
        re.compile(
            _joined(
                r"(?<![A-Za-z0-9])(?:gh[pousr]_[A-Za-z0-9]{30,}|",
                r"github_pat_[A-Za-z0-9_]{50,}|AKIA[0-9A-Z]{16}|",
                r"xox[baprs]-[A-Za-z0-9-]{20,}|AIza[0-9A-Za-z_-]{30,})(?![A-Za-z0-9])",
            )
        ),
    ),
    PatternSpec(
        "jwt-token",
        re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"),
    ),
    PatternSpec(
        "credential-literal",
        re.compile(
            rf"(?i)\b(?:{_CREDENTIAL_NAMES})\b[\"']?\s*(?:=|:)\s*(?P<quote>[\"'])(?P<value>[^\"'\r\n]{{4,}})(?P=quote)"
        ),
        "value",
    ),
    PatternSpec(
        "personal-field",
        re.compile(
            r"(?i)\b(?:full_name|taxpayer_name|legal_name|postal_address|street_address)\b[\"']?\s*(?:=|:)\s*(?P<quote>[\"'])(?P<value>[^\"'\r\n]{3,})(?P=quote)"
        ),
        "value",
    ),
)


class GuardError(RuntimeError):
    """Operational failure that must make the guard fail closed."""


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8", "surrogateescape")
    return hashlib.sha256(value).hexdigest()


def _safe_location(value: str) -> str:
    escaped = value.encode("unicode_escape", "backslashreplace").decode("ascii")
    return escaped[:300] + ("..." if len(escaped) > 300 else "")


def prohibited_path_reason(path: str) -> str | None:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    pure = PurePosixPath(normalized)
    parts = tuple(part.lower() for part in pure.parts)
    if not parts:
        return None
    if parts[0] in PROHIBITED_TOP_LEVEL:
        return "private-directory"
    if any(part in PROHIBITED_COMPONENTS for part in parts):
        return "private-directory"
    name = parts[-1]
    if name.startswith(".env"):
        return "environment-file"
    if name.endswith((".db-shm", ".db-wal", ".sqlite-shm", ".sqlite-wal")):
        return "database-file"
    if name.endswith(PROHIBITED_SUFFIXES):
        return "private-file-type"
    return None


def scan_content(data: bytes, location: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> list[Finding]:
    safe_location = _safe_location(location)
    if len(data) > max_bytes:
        return [Finding("oversized-file", safe_location, 0, _sha256(data))]

    digest = _sha256(data)
    if digest in ALLOWED_BINARY_SHA256:
        return []
    if b"\x00" in data:
        return [Finding("binary-file", safe_location, 0, digest)]
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return [Finding("non-utf8-file", safe_location, 0, digest)]

    findings: set[Finding] = set()
    for line_number, line in enumerate(text.splitlines(), start=1):
        for spec in PATTERNS:
            for match in spec.pattern.finditer(line):
                value = match.group(spec.value_group) if spec.value_group else match.group(0)
                fingerprint = _sha256(value)
                if fingerprint in ALLOWED_SYNTHETIC_VALUE_SHA256:
                    continue
                if spec.category == "email-address" and fingerprint in ALLOWED_PUBLIC_BOT_EMAIL_SHA256:
                    continue
                findings.add(Finding(spec.category, safe_location, line_number, fingerprint))
    return sorted(findings)


def _git(repo: Path, args: Sequence[str], *, input_bytes: bytes | None = None) -> bytes:
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(repo), *args],
            input=input_bytes,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as exc:
        raise GuardError("unable to execute Git") from exc
    if completed.returncode != 0:
        raise GuardError("Git command failed")
    return completed.stdout


def _object_info(repo: Path, object_ids: Iterable[str]) -> dict[str, tuple[str, int]]:
    unique_ids = tuple(dict.fromkeys(object_ids))
    if not unique_ids:
        return {}
    payload = ("\n".join(unique_ids) + "\n").encode("ascii")
    output = _git(repo, ["cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"], input_bytes=payload)
    result: dict[str, tuple[str, int]] = {}
    for raw_line in output.splitlines():
        fields = raw_line.decode("ascii", "strict").split()
        if len(fields) != 3 or not fields[2].isdigit():
            raise GuardError("unexpected Git object metadata")
        result[fields[0]] = (fields[1], int(fields[2]))
    if len(result) != len(unique_ids):
        raise GuardError("Git object metadata was incomplete")
    return result


def _read_git_objects(repo: Path, object_ids: Iterable[str]) -> Iterator[tuple[str, bytes]]:
    ids = tuple(object_ids)
    if not ids:
        return
    try:
        process = subprocess.Popen(
            ["git", "-C", os.fspath(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise GuardError("unable to execute Git") from exc
    assert process.stdin is not None
    assert process.stdout is not None
    try:
        for expected_id in ids:
            process.stdin.write((expected_id + "\n").encode("ascii"))
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", "strict").strip().split()
            if len(header) != 3 or header[0] != expected_id or not header[2].isdigit():
                raise GuardError("unexpected Git object response")
            size = int(header[2])
            data = process.stdout.read(size)
            terminator = process.stdout.read(1)
            if len(data) != size or terminator != b"\n":
                raise GuardError("truncated Git object response")
            yield expected_id, data
        process.stdin.close()
        if process.wait() != 0:
            raise GuardError("Git object read failed")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def _index_entries(repo: Path) -> list[tuple[str, str, str]]:
    output = _git(repo, ["ls-files", "--stage", "-z"])
    entries: list[tuple[str, str, str]] = []
    for raw_entry in output.split(b"\x00"):
        if not raw_entry:
            continue
        try:
            metadata, raw_path = raw_entry.split(b"\t", 1)
            mode, object_id, stage = metadata.decode("ascii").split()
        except (ValueError, UnicodeDecodeError) as exc:
            raise GuardError("unexpected Git index entry") from exc
        if stage != "0":
            raise GuardError("unmerged Git index")
        entries.append((raw_path.decode("utf-8", "surrogateescape"), mode, object_id))
    return entries


def scan_index_and_worktree(repo: Path, *, max_bytes: int = DEFAULT_MAX_BYTES) -> list[Finding]:
    findings: list[Finding] = []
    entries = _index_entries(repo)
    regular_entries = [(path, mode, oid) for path, mode, oid in entries if mode != "160000"]
    info = _object_info(repo, (oid for _, _, oid in regular_entries))
    readable_ids = [oid for _, _, oid in regular_entries if info[oid][1] <= max_bytes]
    index_data = dict(_read_git_objects(repo, readable_ids))

    for path, mode, object_id in entries:
        reason = prohibited_path_reason(path)
        if reason:
            findings.append(Finding(reason, _safe_location(path), 0, _sha256(path)))
        if mode == "160000":
            findings.append(Finding("gitlink", _safe_location(path), 0, _sha256(path)))
            continue

        _, size = info[object_id]
        index_location = f"index:{path}"
        if size > max_bytes:
            findings.append(Finding("oversized-file", _safe_location(index_location), 0, _sha256(f"{object_id}:{size}")))
            staged_data: bytes | None = None
        else:
            staged_data = index_data[object_id]
            findings.extend(scan_content(staged_data, index_location, max_bytes=max_bytes))

        worktree_path = repo.joinpath(*PurePosixPath(path).parts)
        try:
            metadata = worktree_path.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise GuardError("unable to inspect a tracked worktree file") from exc
        if stat.S_ISLNK(metadata.st_mode):
            try:
                worktree_data = os.readlink(worktree_path).encode("utf-8", "surrogateescape")
            except OSError as exc:
                raise GuardError("unable to read a tracked symlink") from exc
        elif stat.S_ISREG(metadata.st_mode):
            try:
                with worktree_path.open("rb") as handle:
                    worktree_data = handle.read(max_bytes + 1)
            except OSError as exc:
                raise GuardError("unable to read a tracked worktree file") from exc
        else:
            findings.append(Finding("special-file", _safe_location(path), 0, _sha256(path)))
            continue
        if staged_data is None or worktree_data != staged_data:
            findings.extend(scan_content(worktree_data, f"worktree:{path}", max_bytes=max_bytes))
    return sorted(set(findings))


def _revision_args(revision: str) -> list[str]:
    return ["--all"] if revision == "--all" else [revision]


def scan_history(repo: Path, revision: str, *, max_bytes: int = DEFAULT_MAX_BYTES) -> list[Finding]:
    findings: list[Finding] = []
    revision_args = _revision_args(revision)
    objects_output = _git(repo, ["rev-list", "--objects", *revision_args])
    object_paths: dict[str, str] = {}
    object_ids: list[str] = []
    for raw_line in objects_output.splitlines():
        raw_id, separator, raw_path = raw_line.partition(b" ")
        try:
            object_id = raw_id.decode("ascii")
        except UnicodeDecodeError as exc:
            raise GuardError("unexpected Git object id") from exc
        object_ids.append(object_id)
        if separator:
            object_paths.setdefault(object_id, raw_path.decode("utf-8", "surrogateescape"))

    info = _object_info(repo, object_ids)
    blob_ids = [oid for oid in dict.fromkeys(object_ids) if info[oid][0] == "blob"]
    readable_blob_ids = [oid for oid in blob_ids if info[oid][1] <= max_bytes]
    for object_id in blob_ids:
        size = info[object_id][1]
        if size > max_bytes:
            location = f"history-blob:{object_id[:12]}:{object_paths.get(object_id, '<unknown>')}"
            findings.append(Finding("oversized-file", _safe_location(location), 0, _sha256(f"{object_id}:{size}")))
    for object_id, data in _read_git_objects(repo, readable_blob_ids):
        location = f"history-blob:{object_id[:12]}:{object_paths.get(object_id, '<unknown>')}"
        findings.extend(scan_content(data, location, max_bytes=max_bytes))

    names_output = _git(repo, ["log", "--format=tformat:", "--name-only", "-z", *revision_args])
    for raw_path in names_output.split(b"\x00"):
        path = raw_path.strip(b"\r\n").decode("utf-8", "surrogateescape")
        if not path:
            continue
        reason = prohibited_path_reason(path)
        if reason:
            findings.append(Finding(f"historical-{reason}", _safe_location(path), 0, _sha256(path)))

    commit_ids = _git(repo, ["rev-list", *revision_args]).decode("ascii", "strict").splitlines()
    commit_info = _object_info(repo, commit_ids)
    for commit_id, data in _read_git_objects(repo, commit_ids):
        if commit_info[commit_id][0] != "commit":
            raise GuardError("revision resolved to a non-commit object")
        _, separator, message = data.partition(b"\n\n")
        if not separator:
            raise GuardError("malformed Git commit object")
        findings.extend(scan_content(message, f"commit-message:{commit_id[:12]}", max_bytes=max_bytes))

    if revision == "--all":
        tag_output = _git(repo, ["for-each-ref", "--format=%(objecttype) %(objectname)", "refs/tags"])
        tag_ids = []
        for raw_line in tag_output.splitlines():
            object_type, _, raw_id = raw_line.partition(b" ")
            if object_type == b"tag":
                tag_ids.append(raw_id.decode("ascii", "strict"))
        for tag_id, data in _read_git_objects(repo, tag_ids):
            findings.extend(scan_content(data, f"annotated-tag:{tag_id[:12]}", max_bytes=max_bytes))

    return sorted(set(findings))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repository root (default: current directory)")
    history_group = parser.add_mutually_exclusive_group()
    history_group.add_argument("--history", action="store_true", help="also scan all reachable history and messages")
    history_group.add_argument("--commit-range", metavar="REVISION", help="also scan a Git revision or revision range")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES, help="maximum accepted file/blob size")
    return parser


def run(repo: Path, *, history_revision: str | None = None, max_bytes: int = DEFAULT_MAX_BYTES) -> list[Finding]:
    if max_bytes < 1:
        raise GuardError("max-bytes must be positive")
    try:
        root = Path(_git(repo, ["rev-parse", "--show-toplevel"]).decode("utf-8", "strict").strip())
    except UnicodeDecodeError as exc:
        raise GuardError("repository path is not UTF-8") from exc
    findings = scan_index_and_worktree(root, max_bytes=max_bytes)
    if history_revision:
        findings.extend(scan_history(root, history_revision, max_bytes=max_bytes))
    return sorted(set(findings))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    revision = "--all" if args.history else args.commit_range
    try:
        findings = run(args.repo, history_revision=revision, max_bytes=args.max_bytes)
    except GuardError as exc:
        print(f"privacy guard: ERROR ({exc})", file=sys.stderr)
        return 2

    if not findings:
        print("privacy guard: PASS")
        return 0
    print(f"privacy guard: FAILED ({len(findings)} finding(s))", file=sys.stderr)
    for finding in findings:
        print(
            f"- category={finding.category} location={finding.location} "
            f"line={finding.line} sha256={finding.fingerprint}",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
