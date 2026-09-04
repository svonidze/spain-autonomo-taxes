from __future__ import annotations

import json
import re
from typing import Any, Mapping


def tax_settlement_payment_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return the evidence state used by both close checks and the tax UI."""
    issues: list[str] = []
    try:
        payload = json.loads(str(row["source_row_json"] or ""))
    except json.JSONDecodeError:
        payload = None
    if not isinstance(payload, Mapping):
        payload = {}
        issues.append("source_payload_missing")

    evidence_sha256 = str(payload.get("evidence_sha256") or "").strip()
    evidence_locator = str(payload.get("evidence_locator") or "").strip()
    source_system = str(
        row["source_system"] or payload.get("source_system") or ""
    ).strip()
    external_id = str(row["external_id"] or payload.get("external_id") or "").strip()
    if re.fullmatch(r"[0-9a-fA-F]{64}", evidence_sha256) is None:
        issues.append("evidence_sha256_missing_or_invalid")
    if not evidence_locator:
        issues.append("evidence_locator_missing")
    if not source_system or not external_id:
        issues.append("source_identity_missing")
    if int(row["amount_minor"]) <= 0:
        issues.append("payment_amount_not_positive")
    if str(row["currency"]).upper() != "EUR":
        issues.append("payment_currency_not_eur")
    if row["match_status"] not in {"exact", "manual"}:
        issues.append("payment_match_not_confirmed")

    return {
        "payment_id": row["payment_id"],
        "paid_on": row["paid_on"],
        "amount_minor": row["amount_minor"],
        "currency": row["currency"],
        "source_system": source_system,
        "external_id": external_id,
        "evidence_sha256": evidence_sha256 or None,
        "evidence_locator": evidence_locator or None,
        "evidence_ready": not issues,
        "issues": issues,
    }
