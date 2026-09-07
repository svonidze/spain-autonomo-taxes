from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import os
from pathlib import Path
import warnings


PRIVATE_ROOT_ENV = "AUTONOMO_PRIVATE_ROOT"
APP_DIRECTORY = "spain-autonomo-taxes"


class PrivatePathError(ValueError):
    pass


class LegacyPrivateConfigWarning(UserWarning):
    pass


@dataclass(frozen=True)
class PrivatePaths:
    root: Path
    config_path: Path | None
    config_source: str

    @property
    def database(self) -> Path:
        return self.root / "autonomo.sqlite"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def evidence(self) -> Path:
        return self.root / "evidence"

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def browser(self) -> Path:
        return self.root / "browser"


def default_private_root(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    local_app_data = values.get("LOCALAPPDATA", "").strip()
    if local_app_data:
        base = Path(local_app_data).expanduser()
        if not base.is_absolute():
            base = Path.home() / "AppData" / "Local"
    elif os.name == "nt":
        base = Path.home() / "AppData" / "Local"
    else:
        xdg_data_home = values.get("XDG_DATA_HOME", "").strip()
        base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
        if not base.is_absolute():
            base = Path.home() / ".local" / "share"
    return (base / APP_DIRECTORY).resolve()


def configured_private_root(environ: Mapping[str, str] | None = None) -> Path:
    values = os.environ if environ is None else environ
    configured_root = values.get(PRIVATE_ROOT_ENV, "").strip()
    if not configured_root:
        return default_private_root(values)
    root = Path(configured_root)
    if not root.is_absolute():
        raise PrivatePathError(f"{PRIVATE_ROOT_ENV} must be an absolute path")
    return root.resolve()


def resolve_private_paths(
    *,
    project_root: Path,
    explicit_config: Path | None = None,
    environ: Mapping[str, str] | None = None,
    warning_emitter: Callable[[str], object] | None = None,
) -> PrivatePaths:
    values = os.environ if environ is None else environ
    project = project_root.resolve()

    if explicit_config is not None:
        config_path = cli_path(explicit_config)
        return PrivatePaths(
            root=config_path.parent,
            config_path=config_path,
            config_source="explicit",
        )

    if values.get(PRIVATE_ROOT_ENV, "").strip():
        root = configured_private_root(values)
        config_path = root / "config.yaml"
        return PrivatePaths(
            root=root,
            config_path=config_path if config_path.is_file() else None,
            config_source="environment",
        )

    root = default_private_root(values)
    config_path = root / "config.yaml"
    if config_path.is_file():
        return PrivatePaths(root=root, config_path=config_path, config_source="default")

    legacy_config = project / ".local" / "config.yaml"
    if legacy_config.is_file():
        message = (
            f"Using legacy private config {legacy_config}. Move it to {config_path} or set "
            f"{PRIVATE_ROOT_ENV} to an absolute directory; no files are moved automatically."
        )
        if warning_emitter is None:
            warnings.warn(message, LegacyPrivateConfigWarning, stacklevel=2)
        else:
            warning_emitter(message)
        return PrivatePaths(root=root, config_path=legacy_config, config_source="legacy")

    return PrivatePaths(root=root, config_path=None, config_source="default")


def cli_path(value: Path) -> Path:
    """Resolve a CLI path with the process working directory as its base."""
    return value.expanduser().resolve()


def config_path(value: object, *, config_file: Path) -> Path:
    """Resolve a canonical YAML path relative to the YAML file that defines it."""
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = config_file.parent / path
    return path.resolve()
