"""Validate and serve only explicitly built application resources."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

RESOURCE = re.compile(r"^(?:index\.html|ui-assets/[\w.-]+\.(?:js|css))$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UiAssets:
    def __init__(self, source_root: Path, *, allow_test: bool = False) -> None:
        self.root = (source_root if source_root.name == "dist" else source_root / "dist").resolve()
        try:
            self.manifest = json.loads((self.root / "build-manifest.json").read_text())
        except (OSError, ValueError) as exc:
            raise RuntimeError("Frontend build is missing or invalid. Run npm ci --include=dev and npm run build before installing or starting the web application.") from exc
        if not isinstance(self.manifest, dict) or self.manifest.get("contract") != 1:
            raise RuntimeError("Unsupported frontend build contract")
        if self.manifest.get("test_only") and not allow_test:
            raise RuntimeError("This is a test-only frontend. Run npm run build before starting the application.")
        files = self.manifest.get("files")
        if not isinstance(files, dict) or "index.html" not in files or not any(name.endswith(".js") for name in files):
            raise RuntimeError("Frontend build has no shell or JavaScript entry")
        for name, checksum in files.items():
            if not RESOURCE.fullmatch(name) or not isinstance(checksum, str) or not SHA256.fullmatch(checksum):
                raise RuntimeError("Invalid frontend resource manifest")
        self.files: dict[str, str] = files
        self.verify()

    def path(self, name: str) -> Path:
        if name not in self.files:
            raise FileNotFoundError(name)
        target = (self.root / name).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise FileNotFoundError("Invalid frontend resource path")
        return target

    def verify(self) -> None:
        for name, checksum in self.files.items():
            if hashlib.sha256(self.path(name).read_bytes()).hexdigest() != checksum:
                raise RuntimeError(f"Frontend resource does not match its build: {name}")
