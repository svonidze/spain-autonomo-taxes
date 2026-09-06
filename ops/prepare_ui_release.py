"""A narrow, versioned frontend build gate for the existing release scripts.

Never switches services, repairs partial releases, or loads application secrets.
"""
from __future__ import annotations

import hashlib
from email.parser import Parser
import zipfile
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
    if type(value) is not int or value not in (0, 1, 2):
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
    if contract(source, sha) == 2:
        return paired_installation(source, sha, target)
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


def wheel_artifacts(target: Path) -> dict:
    paths = sorted((target / ".ui-wheels").glob("*.whl"))
    if len(paths) != 2:
        raise RuntimeError("Paired release requires exactly two retained wheels")
    packages = {}
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            metadata_paths = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
            if len(metadata_paths) != 1 or len(archive.namelist()) != len(set(archive.namelist())):
                raise RuntimeError("Invalid wheel inventory")
            metadata = Parser().parsestr(archive.read(metadata_paths[0]).decode())
            name = metadata["Name"]
            if name not in {"spain-autonomo-taxes", "spain-autonomo-taxes-ui"} or name in packages:
                raise RuntimeError("Unexpected paired wheel")
            files = {}
            for member in archive.namelist():
                if member.endswith("/") or member.endswith(".dist-info/RECORD"):
                    continue
                if Path(member).is_absolute() or ".." in Path(member).parts or ".data" in member:
                    raise RuntimeError("Unsafe wheel path")
                files[member] = digest(archive.read(member))
            packages[name] = {"filename": path.name, "sha256": digest(path.read_bytes()),
                "version": metadata["Version"], "requires": metadata.get_all("Requires-Dist", []), "files": files}
    core, ui = packages["spain-autonomo-taxes"], packages["spain-autonomo-taxes-ui"]
    version = tomllib.loads((target / "pyproject.toml").read_text())["project"]["version"]
    ui_version = tomllib.loads((target / "packages/ui/pyproject.toml").read_text())["project"]["version"]
    if not version == ui_version == core["version"] == ui["version"]:
        raise RuntimeError("Paired package versions differ")
    if f"spain-autonomo-taxes=={version}" not in [value.replace(" ", "") for value in ui["requires"]]:
        raise RuntimeError("UI must require the exact core version")
    if any("web_ui/" in name or name.endswith((".js", ".css", ".html")) for name in core["files"]):
        raise RuntimeError("Core wheel contains optional UI assets")
    return packages


def install_release(source: Path, sha: str, target: Path) -> None:
    pip = [str(target / ".venv/bin/python"), "-m", "pip"]
    if contract(source, sha) != 2:
        subprocess.run([*pip, "install", "--disable-pip-version-check", "--no-input", str(target)], check=True)
        return
    verify_tree(source, sha, target)
    directory = target / ".ui-wheels"
    directory.mkdir()  # Never repair an interrupted preparation in place.
    for package in (target, target / "packages/ui"):
        subprocess.run([*pip, "wheel", "--disable-pip-version-check", "--no-input", "--no-deps", "--wheel-dir", str(directory), str(package)], cwd=target, check=True)
    wheels = wheel_artifacts(target)
    for name in ("spain-autonomo-taxes", "spain-autonomo-taxes-ui"):
        extra = ["--no-deps"] if name.endswith("-ui") else []
        subprocess.run([*pip, "install", "--disable-pip-version-check", "--no-input", *extra, str(directory / wheels[name]["filename"])], cwd=target, check=True)


def paired_installation(source: Path, sha: str, target: Path) -> dict:
    verify_tree(source, sha, target)
    wheels = wheel_artifacts(target)
    code = r"""
import hashlib, json, sys
from importlib.metadata import distribution
from pathlib import Path
import autonomo_taxes, autonomo_taxes_ui
from autonomo_taxes.ui_assets import UiAssets
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION
venv = Path(sys.argv[1]).resolve() / '.venv'
for module in (autonomo_taxes, autonomo_taxes_ui):
    if not Path(module.__file__).resolve().is_relative_to(venv):
        raise RuntimeError('Import escaped installed release')
artifacts = json.loads(sys.stdin.read())
installed = {}
for name, wheel in artifacts.items():
    dist = distribution(name)
    if dist.version != wheel['version']:
        raise RuntimeError('Installed package version mismatch')
    hashes = {}
    for member, expected in wheel['files'].items():
        path = Path(dist.locate_file(member)).resolve()
        if not path.is_relative_to(venv) or not path.is_file():
            raise RuntimeError('Installed package path mismatch')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError('Installed package differs from retained wheel')
        hashes[member] = actual
    installed[name] = hashes
assets = UiAssets(Path(autonomo_taxes_ui.__file__).resolve().parent / 'dist')
print(json.dumps({'installed': installed, 'build': assets.manifest, 'schema': str(LATEST_SCHEMA_VERSION)}))
"""
    result = json.loads(subprocess.check_output([str(target / ".venv/bin/python"), "-I", "-c", code, str(target)], input=json.dumps(wheels), text=True, cwd=target.parent))
    build = result["build"]
    if build.get("source_sha") != sha or build.get("source_dirty") is not False:
        raise RuntimeError("UI was not built from the selected clean SHA")
    if build.get("lock_sha256") != digest((target / "package-lock.json").read_bytes()):
        raise RuntimeError("Installed frontend lockfile mismatch")
    if any(build.get(key) != value for key, value in expected_tools(source, sha).items()):
        raise RuntimeError("Installed frontend tool version mismatch")
    if (target / ".release-sha").read_text().strip() != sha or (target / ".schema-version").read_text().strip() != result["schema"]:
        raise RuntimeError("Paired release markers mismatch")
    if not all((target / ".venv/bin" / name).is_file() for name in ("autonomo-tax", "autonomo-web")):
        raise RuntimeError("Paired release entry point missing")
    return {"release_contract": 2, "source_sha": sha, "wheels": wheels, "launchers": {name: digest((target / ".venv/bin" / name).read_bytes()) for name in ("autonomo-tax", "autonomo-web")}, **result}


def main(argv: list[str]) -> None:
    if len(argv) != 4 or argv[0] not in ("preflight", "build", "install", "receipt", "verify"):
        raise RuntimeError("usage: prepare_ui_release.py <preflight|build|install|receipt|verify> <source-repo> <sha> <target>")
    phase, source_text, sha, target_text = argv
    source, target = Path(source_text).resolve(), Path(target_text).resolve()
    if phase == "install":
        install_release(source, sha, target)
        return
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
