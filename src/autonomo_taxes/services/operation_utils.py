"""Shared accounting operations; no HTTP, CLI dispatch or UI dependency."""

from __future__ import annotations


def _parse_review_id(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise ValueError(
            "Review IDs must be typed as document:<uuid> or transaction:<uuid>"
        )
    review_kind, subject_id = value.split(":", 1)
    if review_kind not in {"document", "transaction"} or not subject_id:
        raise ValueError(
            "Review IDs must be typed as document:<uuid> or transaction:<uuid>"
        )
    return review_kind, subject_id
