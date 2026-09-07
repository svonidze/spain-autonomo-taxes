from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import unicodedata


def _normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value.strip())


def _looks_like_windows_path(value: str) -> bool:
    normalized = _normalize_text(value)
    return normalized.startswith("\\\\") or (
        len(normalized) >= 3 and normalized[1:3] in {":\\", ":/"}
    )


@dataclass(frozen=True)
class LegacyPathMapping:
    source: str
    destination: str
    source_is_windows: bool

    @property
    def source_path(self) -> PureWindowsPath | PurePosixPath:
        return PureWindowsPath(self.source) if self.source_is_windows else PurePosixPath(self.source)

    @property
    def destination_path(self) -> PureWindowsPath | PurePosixPath:
        if _looks_like_windows_path(self.destination):
            return PureWindowsPath(self.destination)
        return PurePosixPath(self.destination)

    def resolve(self, candidate: str) -> Path | None:
        normalized = _normalize_text(candidate)
        candidate_path: PureWindowsPath | PurePosixPath
        if self.source_is_windows:
            candidate_path = PureWindowsPath(normalized)
            source_parts = tuple(part.casefold() for part in self.source_path.parts)
            candidate_parts = tuple(part.casefold() for part in candidate_path.parts)
        else:
            candidate_path = PurePosixPath(normalized)
            source_parts = self.source_path.parts
            candidate_parts = candidate_path.parts
        if len(candidate_parts) < len(source_parts) or candidate_parts[: len(source_parts)] != source_parts:
            return None
        relative = candidate_path.parts[len(source_parts) :]
        destination = self.destination_path
        if destination.suffix and not relative:
            return Path(str(destination))
        return Path(str(destination.joinpath(*relative)))


class LegacyPathResolver:
    def __init__(self, mappings: tuple[LegacyPathMapping, ...] = ()) -> None:
        self._mappings = tuple(
            sorted(mappings, key=lambda item: len(item.source_path.parts), reverse=True)
        )

    @classmethod
    def from_json_file(cls, path: Path | None) -> "LegacyPathResolver":
        if path is None:
            return cls()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError(f"{path} must contain a JSON object")
        mappings: list[LegacyPathMapping] = []
        normalized_sources: set[tuple[bool, str]] = set()
        for source, destination in loaded.items():
            if not isinstance(source, str) or not isinstance(destination, str):
                raise ValueError(f"{path} must map strings to strings")
            mapping = LegacyPathMapping(
                source=_normalize_text(source),
                destination=_normalize_text(destination),
                source_is_windows=_looks_like_windows_path(source),
            )
            if not mapping.source_path.is_absolute() or not mapping.destination_path.is_absolute():
                raise ValueError(f"{path} must contain only absolute path mappings")
            source_key = (
                mapping.source_is_windows,
                mapping.source.casefold() if mapping.source_is_windows else mapping.source,
            )
            if source_key in normalized_sources:
                raise ValueError(f"{path} contains duplicate normalized source roots")
            normalized_sources.add(source_key)
            mappings.append(mapping)
        return cls(tuple(mappings))

    def resolve(self, value: str | None) -> Path | None:
        if value is None:
            return None
        normalized = _normalize_text(value)
        for mapping in self._mappings:
            resolved = mapping.resolve(normalized)
            if resolved is not None:
                return resolved
        return None
