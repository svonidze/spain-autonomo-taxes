"""A narrow, versioned frontend build gate for the existing release scripts.

Never switches services, repairs partial releases, or loads application secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib


def run(args: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> str:
    return subprocess.check_output(args, cwd=cwd, env=env, text=True).strip()


def git(source: Path, *args: str) -> str:
    return run(["git", "-C", str(source), *args], cwd=source)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contract(source: Path, sha: str) -> int:
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RuntimeError("Expected a full Git SHA")
    config = tomllib.loads(git(source, "show", f"{sha}:pyproject.toml"))
    value = config.get("tool", {}).get("autonomo", {}).get("web-ui", {}).get("build-contract", 0)
    if type(value) is not int or value not in (0, 1):
        raise RuntimeError("Unknown frontend build contract")
    return value


def expected_tools(source: Path, sha: str) -> dict[str, str]:
    package = json.loads(git(source, "show", f"{sha}:package.json"))
    engines = package["engines"]
    for key in ("node", "npm"):
        if not re.fullmatch(r"\d+\.\d+\.\d+", engines[key]):
            raise RuntimeError("Frontend tools must have exact versions")
    if git(source, "show", f"{sha}:.nvmrc") != engines["node"]:
        raise RuntimeError("Node pins disagree")
    return {key: engines[key] for key in ("node", "npm")}


def tools(source: Path, sha: str) -> tuple[str, str, dict[str, str]]:
    expected = expected_tools(source, sha)
    node = shutil.which("node")
    npm = shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("Install the pinned Node/npm build tools before preparing this release")
    npm = str(Path(npm).resolve())
    env = build_environment(node)
    actual_node = run([node, "--version"], cwd=source, env=env).removeprefix("v")
    actual_npm = run([node, npm, "--version"], cwd=source, env=env)
    if actual_node != expected["node"] or actual_npm != expected["npm"]:
        raise RuntimeError(f"Expected Node {expected['node']} / npm {expected['npm']}; found {actual_node} / {actual_npm}")
    return node, npm, env


def build_environment(node: str) -> dict[str, str]:
    # Keep package-manager cache and temporary-file locations, never service secrets
    # or ambient NODE_OPTIONS / VITE_* / AUTONOMO_* values.
    allowed = ("HOME", "USER", "LOGNAME", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env["PATH"] = str(Path(node).parent) + os.pathsep + os.environ.get("PATH", os.defpath)
    env["CI"] = "true"
    return env


def verify_tree(source: Path, sha: str, target: Path) -> None:
    if git(target, "rev-parse", "HEAD") != sha or git(target, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Release source must be the clean reviewed Git object")
    expected_lock = subprocess.check_output(["git", "-C", str(source), "show", f"{sha}:package-lock.json"])
    if digest((target / "package-lock.json").read_bytes()) != digest(expected_lock):
        raise RuntimeError("Release lockfile differs from its reviewed Git object")


def installed_manifest(target: Path) -> dict:
    code = """
import json, sys
from pathlib import Path
import autonomo_taxes
from autonomo_taxes.ui_assets import UiAssets
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION
package = Path(autonomo_taxes.__file__).resolve().parent
if not package.is_relative_to(Path(sys.argv[1]).resolve() / '.venv'):
    raise RuntimeError('Frontend validation imported the checkout instead of the installed package')
assets = UiAssets(package / 'web_ui')
print(json.dumps({'build': assets.manifest, 'schema': str(LATEST_SCHEMA_VERSION)}))
"""
    return json.loads(run([str(target / ".venv/bin/python"), "-I", "-c", code, str(target)], cwd=target.parent))


def installation(source: Path, sha: str, target: Path) -> dict:
    verify_tree(source, sha, target)
    result = installed_manifest(target)
    build = result["build"]
    if build["source_sha"] != sha or build.get("source_dirty") is not False:
        raise RuntimeError("Installed frontend does not identify the clean reviewed SHA")
    if (target / ".release-sha").read_text().strip() != sha:
        raise RuntimeError("Release marker mismatch")
    if (target / ".schema-version").read_text().strip() != result["schema"]:
        raise RuntimeError("Installed schema marker mismatch")
    if build["lock_sha256"] != digest((target / "package-lock.json").read_bytes()):
        raise RuntimeError("Installed frontend lockfile mismatch")
    expected = expected_tools(source, sha)
    if any(build[key] != expected[key] for key in ("node", "npm")):
        raise RuntimeError("Installed frontend tool version mismatch")
    if not (target / ".venv/bin/autonomo-web").is_file():
        raise RuntimeError("Installed web entry point is missing")
    return result


def verify(source: Path, sha: str, target: Path) -> None:
    try:
        receipt = json.loads((target / ".ui-install-complete.json").read_text())
    except (OSError, ValueError) as exc:
        raise RuntimeError("Incomplete frontend release: preserve this target and follow scoped recovery; do not retry in place") from exc
    if receipt != installation(source, sha, target):
        raise RuntimeError("Frontend completion receipt disagrees with installed resources")


def write_receipt(source: Path, sha: str, target: Path) -> None:
    receipt = target / ".ui-install-complete.json"
    if receipt.exists():
        raise RuntimeError("Refusing to replace an existing completion receipt")
    data = installation(source, sha, target)
    temporary = target / ".ui-install-complete.tmp"
    with temporary.open("x", encoding="utf-8") as output:
        os.chmod(temporary, 0o600)
        json.dump(data, output, sort_keys=True)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, receipt)
    fd = os.open(target, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def main(argv: list[str]) -> None:
    if len(argv) != 4 or argv[0] not in ("preflight", "build", "receipt", "verify"):
        raise RuntimeError("usage: prepare_ui_release.py <preflight|build|receipt|verify> <source-repo> <sha> <target>")
    phase, source_text, sha, target_text = argv
    source, target = Path(source_text).resolve(), Path(target_text).resolve()
    if contract(source, sha) == 0:
        return  # Existing legacy installation and rollback contracts are unchanged.
    if phase == "preflight":
        if target.exists():
            verify(source, sha, target)
        else:
            tools(source, sha)
    elif phase == "build":
        verify_tree(source, sha, target)
        node, npm, env = tools(source, sha)
        if (target / ".ui-install-complete.json").exists():
            raise RuntimeError("Refusing to rebuild a completed immutable release")
        try:
            with (target / ".ui-build-started").open("x") as marker:
                marker.write(sha + "\n")
        except FileExistsError as exc:
            raise RuntimeError("Frontend build already started: preserve the partial target for scoped recovery") from exc
        subprocess.run([node, npm, "ci", "--include=dev", "--no-audit", "--no-fund"], cwd=target, env=env, check=True)
        subprocess.run([node, npm, "run", "build"], cwd=target, env=env, check=True)
        verify_tree(source, sha, target)
    elif phase == "receipt":
        write_receipt(source, sha, target)
    else:
        verify(source, sha, target)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(f"Frontend release gate: {exc}") from exc
