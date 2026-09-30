"""Deduction rule catalog: proposes IRPF/IVA ceilings for one expense draft.

A proposal is a ceiling, not advice: the reviewer may enter less, never more.
Amounts are integer EUR cents rounded half-up once per product. Any edit to a
rule needs a new catalog source_checked_on: posting records rule versions by
that date and refuses a rule whose content changed under the same date.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from datetime import date
from decimal import Decimal
from functools import cache
from importlib import resources
from typing import Any

from .depreciation import minor

FACT_NAMES = ("persons", "persons_disabled", "days", "abroad", "overnight", "electronic_payment", "evidence_confirmed")
IRPF_PARAMETERS = {
    "full": (), "proportional_area": (), "coefficient_times_area": ("coefficient_basis_points",),
    "per_person_cap": ("cap_minor_per_person", "cap_minor_per_disabled_person"),
    "per_day_cap": ("cap_minor_per_day",), "never": (),
}
IVA_TYPES = {"full", "proportional_area", "none", "manual"}
EVIDENCE = {"factura_completa_nif", "bank_statement", "any"}
RISKS = {"low", "medium", "high"}
DAY_CAPS = {"spain", "spain_overnight", "abroad", "abroad_overnight"}
RULE_FIELDS = {"id", "irpf", "iva", "evidence", "risk", "risk_reason", "valid_from", "valid_to", "parser_categories", "sources"}
SOURCE_FIELDS = {"title", "url", "checked_on", "source_kind"}
AREA_TYPES = {"proportional_area", "coefficient_times_area"}
# Complete invoices and their rectifications (R5 corrects simplified invoices).
COMPLETE_INVOICES = {"F1", "F3", "R1", "R2", "R3", "R4"}
POSTED = ("posted", "included_in_snapshot")


class ExpenseRuleCatalogError(ValueError):
    pass


def _check(condition: Any, message: str) -> None:
    if not condition:
        raise ExpenseRuleCatalogError(message)


def _iso(value: Any, name: str) -> str:
    try:
        date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ExpenseRuleCatalogError(f"{name} must be an ISO date") from None
    return value


def _positive(value: Any) -> bool:
    return type(value) is int and value > 0


def validate_catalog(data: Any) -> dict[str, Any]:
    _check(isinstance(data, dict) and set(data) == {"catalog_version", "source_checked_on", "rules"}, "Invalid catalog fields")
    _check(type(data["catalog_version"]) is int and data["catalog_version"] == 1, "Unsupported catalog version")
    checked_on = _iso(data["source_checked_on"], "source_checked_on")
    _check(isinstance(data["rules"], list) and data["rules"], "The catalog has no rules")
    rules: dict[str, dict[str, Any]] = {}
    categories: dict[str, str] = {}
    for rule in data["rules"]:
        _check(isinstance(rule, dict) and set(rule) == RULE_FIELDS, "Invalid rule fields")
        rule_id = rule["id"]
        _check(isinstance(rule_id, str) and rule_id.isidentifier(), "Invalid rule id")
        _check(rule_id not in rules, f"Duplicate rule id {rule_id}")
        irpf = rule["irpf"]
        parameters = IRPF_PARAMETERS.get(irpf.get("type")) if isinstance(irpf, dict) else None
        _check(parameters is not None and set(irpf) == {"type", *parameters}, f"{rule_id}: unknown IRPF rule type")
        if irpf["type"] == "coefficient_times_area":
            _check(_positive(irpf["coefficient_basis_points"]) and irpf["coefficient_basis_points"] <= 10000, f"{rule_id}: invalid coefficient")
        elif irpf["type"] == "per_person_cap":
            _check(_positive(irpf["cap_minor_per_person"]) and _positive(irpf["cap_minor_per_disabled_person"])
                   and irpf["cap_minor_per_disabled_person"] >= irpf["cap_minor_per_person"], f"{rule_id}: invalid caps")
        elif irpf["type"] == "per_day_cap":
            caps = irpf["cap_minor_per_day"]
            _check(isinstance(caps, dict) and set(caps) == DAY_CAPS and all(map(_positive, caps.values())), f"{rule_id}: invalid daily caps")
        _check(isinstance(rule["iva"], dict) and set(rule["iva"]) == {"type"} and rule["iva"]["type"] in IVA_TYPES, f"{rule_id}: unknown IVA rule type")
        _check(rule["evidence"] in EVIDENCE, f"{rule_id}: unknown evidence requirement")
        _check(rule["risk"] in RISKS and isinstance(rule["risk_reason"], str) and rule["risk_reason"].strip(), f"{rule_id}: risk and reason are required")
        _iso(rule["valid_from"], f"{rule_id}.valid_from")
        _check(rule["valid_to"] is None or _iso(rule["valid_to"], f"{rule_id}.valid_to") >= rule["valid_from"], f"{rule_id}: invalid validity")
        _check(isinstance(rule["parser_categories"], list), f"{rule_id}: parser_categories must be a list")
        for category in rule["parser_categories"]:
            _check(isinstance(category, str) and category.isidentifier(), f"{rule_id}: invalid parser category")
            _check(category not in categories, f"Parser category {category} maps to several rules")
            categories[category] = rule_id
        _check(isinstance(rule["sources"], list) and rule["sources"], f"{rule_id}: sources are required")
        for source in rule["sources"]:
            _check(isinstance(source, dict) and SOURCE_FIELDS <= set(source) <= SOURCE_FIELDS | {"note"}, f"{rule_id}: invalid source fields")
            _check(isinstance(source["title"], str) and source["title"].strip(), f"{rule_id}: source title is required")
            _check(isinstance(source["url"], str) and source["url"].startswith("https://"), f"{rule_id}: source URL must use https")
            _check(_iso(source["checked_on"], f"{rule_id}.checked_on") <= checked_on, f"{rule_id}: source checked after the catalog date")
            _check("note" not in source or isinstance(source["note"], str) and source["note"].strip(), f"{rule_id}: source note must be text")
            _check(source["source_kind"] in {"primary", "secondary"}, f"{rule_id}: unknown source kind")
        rules[rule_id] = rule
    return {"version": data["source_checked_on"], "rules": rules, "categories": categories}


@cache
def load_catalog() -> dict[str, Any]:
    text = resources.files("autonomo_taxes").joinpath("expense_rules.json").read_text(encoding="utf-8")
    return validate_catalog(json.loads(text))


def rule_hash(rule: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(rule, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def rule_for_parser_category(category: str) -> str | None:
    return load_catalog()["categories"].get(category)


def uses_area(rule_id: str) -> bool:
    return "area_share" in _facts(load_catalog()["rules"][rule_id])


def _facts(rule: Mapping[str, Any]) -> list[str]:
    irpf = rule["irpf"]["type"]
    facts = ["area_share"] if irpf in AREA_TYPES or rule["iva"]["type"] == "proportional_area" else []
    if irpf == "per_person_cap":
        facts += ["persons", "persons_disabled"]
    if irpf == "per_day_cap":
        facts += ["days", "abroad", "overnight", "electronic_payment"]
    if rule["evidence"] == "bank_statement":
        facts.append("evidence_confirmed")
    return facts


def rule_list() -> list[dict[str, Any]]:
    """What a rule selector needs; area_share is the draft's deductible_ratio field."""
    return [{"id": rule["id"], "irpf_type": rule["irpf"]["type"], "iva_type": rule["iva"]["type"], "evidence": rule["evidence"],
             "risk": rule["risk"], "facts": _facts(rule), "parser_categories": list(rule["parser_categories"]),
             "valid_from": rule["valid_from"], "valid_to": rule["valid_to"], "sources": deepcopy(rule["sources"])}
            for rule in load_catalog()["rules"].values()]


def _count(value: Any) -> bool:
    return type(value) is int and value >= 0


def _note(code: str, **params: Any) -> dict[str, Any]:
    return {"code": code, "params": params}


def _linked_irpf(db: Any, rule_id: str, transaction_id: str, clause: str, value: str) -> int:
    """IRPF already posted under this rule; postings made without the rule are not counted."""
    return db.connection.execute(f"""SELECT COALESCE(SUM(tt.deductible_irpf_minor),0) FROM tax_treatments tt
        JOIN transactions t ON t.transaction_id=tt.transaction_id JOIN rule_versions rv ON rv.rule_version_id=tt.rule_version_id
        WHERE rv.rule_name=? AND t.transaction_id<>? AND t.lifecycle_status IN (?,?) AND tt.include_modelo130=1 AND {clause}""",
        ("expense_rule:" + rule_id, transaction_id, *POSTED, value)).fetchone()[0]


def _eur_confirmed(state: Mapping[str, Any], payload: Mapping[str, Any]) -> bool:
    """Tax amounts are EUR only for EUR documents or after an FX decision."""
    facts, tx = payload["facts"], state["transaction"]
    if str(facts.get("currency") or "").upper() == "EUR" or payload.get("fx"):
        return True
    return (type(tx.get("amount_eur_minor")) is int and bool(tx.get("fx_rate_id"))
            and facts.get("currency") == (tx.get("original_currency") or tx.get("currency"))
            and facts.get("gross_minor") == tx.get("amount_original_minor") and facts.get("transaction_date") == tx.get("transaction_date"))


def propose(db: Any, state: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Return the ceiling for the selected rule, or None on the manual path."""
    deduction = payload.get("deduction")
    if not deduction:
        return None
    catalog = load_catalog()
    rule_id = deduction["rule_id"]
    result: dict[str, Any] = {"rule_id": rule_id, "status": "unavailable", "irpf_minor": None, "vat_minor": None,
                              "suggested": None, "missing": [], "explanation": []}
    explain = result["explanation"].append
    rule = catalog["rules"].get(rule_id)
    if rule is None:
        explain(_note("rule_unknown"))
        return result
    tax, facts = payload["decision"]["tax_treatment"], deduction["facts"]
    full_invoice = tax.get("aeat_invoice_type") in COMPLETE_INVOICES
    evidence = {"any": True, "bank_statement": None, "factura_completa_nif": full_invoice}[rule["evidence"]]
    result.update(version=catalog["version"], source_hash=rule_hash(rule), risk=rule["risk"], risk_reason=rule["risk_reason"],
                  sources=deepcopy(rule["sources"]), evidence={"required": rule["evidence"], "satisfied": evidence})
    irpf_rule, iva_type = rule["irpf"], rule["iva"]["type"]
    on = payload["facts"].get("transaction_date")
    try:
        on = date.fromisoformat(on).isoformat()
    except (TypeError, ValueError):
        result.update(status="needs_facts", missing=["transaction_date"])
        return result
    if on < rule["valid_from"] or rule["valid_to"] is not None and on > rule["valid_to"]:
        explain(_note("rule_not_valid_on_date", transaction_date=on, valid_from=rule["valid_from"], valid_to=rule["valid_to"]))
        result["status"] = "out_of_scope"
        return result
    if payload.get("asset") is not None:
        explain(_note("equipment_not_covered"))
        result["status"] = "out_of_scope"
        return result
    if irpf_rule["type"] == "per_day_cap" and _count(facts["days"]) and facts["days"] > 1:
        explain(_note("one_day_per_draft", days=facts["days"]))
        result["status"] = "out_of_scope"
        return result
    if irpf_rule["type"] == "never":
        explain(_note("irpf_never"))
        result.update(status="not_deductible", irpf_minor=0, vat_minor=0, suggested={"irpf_minor": 0, "vat_minor": 0})
        return result

    base, vat, share = tax.get("taxable_base_minor"), tax.get("vat_minor"), tax.get("deductible_ratio")
    missing = [] if _eur_confirmed(state, payload) else ["eur_amount"]
    missing += [name for name in ("taxable_base_minor", "vat_minor") if not _count(tax.get(name))]
    needs_area = irpf_rule["type"] in AREA_TYPES or iva_type == "proportional_area"
    if needs_area and not (type(share) in (int, float) and 0 <= share <= 1):
        missing.append("area_share")
    if irpf_rule["type"] == "per_person_cap":
        persons, disabled = facts["persons"], facts["persons_disabled"]
        if not (_count(persons) and persons >= 1):
            missing.append("persons")
        if not (_count(disabled) and (not _count(persons) or disabled <= persons)):
            missing.append("persons_disabled")
    if irpf_rule["type"] == "per_day_cap":
        if facts["days"] != 1:
            missing.append("days")
        missing += [name for name in ("abroad", "overnight", "electronic_payment") if not isinstance(facts[name], bool)]
    # Evidence the application cannot check needs the reviewer's explicit confirmation.
    if evidence is None and facts["evidence_confirmed"] is not True:
        missing.append("evidence_confirmed")
    if missing:
        result.update(status="needs_facts", missing=missing)
        return result
    if irpf_rule["type"] == "per_day_cap" and not facts["electronic_payment"]:
        explain(_note("electronic_payment_required"))
        result.update(status="not_deductible", irpf_minor=0, vat_minor=0, suggested={"irpf_minor": 0, "vat_minor": 0})
        return result

    area = Decimal(str(share)) if needs_area else Decimal(1)
    area_points = minor(area * 10000)
    if iva_type == "full":
        proposed_vat = vat
    elif iva_type == "proportional_area":
        proposed_vat = minor(Decimal(vat) * area)
    else:
        proposed_vat = 0 if iva_type == "none" else None
    explain(_note("vat_" + iva_type, **({"share_basis_points": area_points} if iva_type == "proportional_area" else {})))
    # LIVA art. 97.Uno: IVA is deductible only with a complete invoice, whatever the rule's own evidence is.
    if not full_invoice and (iva_type != "none" or evidence is False):
        if iva_type != "none":
            proposed_vat = 0
        explain(_note("evidence_missing", required="factura_completa_nif"))
    elif evidence is None:
        explain(_note("evidence_confirmed_by_reviewer", required=rule["evidence"]))

    if irpf_rule["type"] == "coefficient_times_area":
        share_irpf = Decimal(irpf_rule["coefficient_basis_points"]) / 10000 * area
        explain(_note("irpf_coefficient_times_area", coefficient_basis_points=irpf_rule["coefficient_basis_points"], share_basis_points=area_points))
    elif irpf_rule["type"] == "proportional_area":
        share_irpf = area
        explain(_note("irpf_area_share", share_basis_points=area_points))
    else:
        share_irpf = Decimal(1)
        explain(_note("irpf_full"))
    transaction_id = state["transaction"]["transaction_id"]
    if irpf_rule["type"] == "per_person_cap":
        cap = (persons - disabled) * irpf_rule["cap_minor_per_person"] + disabled * irpf_rule["cap_minor_per_disabled_person"]
        used = _linked_irpf(db, rule_id, transaction_id, "substr(t.transaction_date,1,4)=?", on[:4])
        explain(_note("irpf_person_cap", persons=persons, persons_disabled=disabled, cap_minor=cap, used_minor=used, year=on[:4]))
    elif irpf_rule["type"] == "per_day_cap":
        cap = irpf_rule["cap_minor_per_day"][("abroad" if facts["abroad"] else "spain") + ("_overnight" if facts["overnight"] else "")]
        used = _linked_irpf(db, rule_id, transaction_id, "t.transaction_date=?", on)
        explain(_note("irpf_day_cap", per_day_minor=cap, used_minor=used, transaction_date=on))
    else:
        cap = used = None
    if cap is not None:
        explain(_note("cap_counts_rule_postings_only"))
        # LIVA art. 96.Uno.6.º: no IVA on hospitality that is not IRPF-deductible.
        if cap <= used and proposed_vat != 0:
            proposed_vat = 0
            explain(_note("cap_exhausted"))

    def ceiling(recovered: int) -> int:
        # IVA that is not recovered is part of the IRPF cost.
        irpf = minor(Decimal(base) * share_irpf) + max(0, minor(Decimal(vat) * share_irpf) - recovered)
        return irpf if cap is None else min(irpf, max(0, cap - used))

    entered_vat = tax.get("deductible_vat_minor") if _count(tax.get("deductible_vat_minor")) else 0
    if proposed_vat is None:
        recovered = 0 if tax.get("include_modelo303") is False else entered_vat
    else:
        # Recoverable IVA is never an IRPF cost, even when it is not claimed.
        recovered = max(entered_vat, proposed_vat)
    irpf = ceiling(recovered)
    unrecovered = max(0, minor(Decimal(vat) * share_irpf) - recovered)
    if unrecovered:
        explain(_note("unrecovered_vat_added", amount_minor=unrecovered))
    # The pair a reviewer can copy: the IRPF ceiling if the proposed IVA is recovered.
    suggested = {"irpf_minor": irpf if proposed_vat is None else ceiling(proposed_vat), "vat_minor": proposed_vat}
    result.update(status="ready", irpf_minor=irpf, vat_minor=proposed_vat, suggested=suggested)
    return result
