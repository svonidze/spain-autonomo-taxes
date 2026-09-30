import json
from copy import deepcopy
from importlib import resources

import pytest
from autonomo_taxes.expense_rules import (
    FACT_NAMES,
    ExpenseRuleCatalogError,
    load_catalog,
    propose,
    validate_catalog,
)
from autonomo_taxes.ledger_db import initialize

STEP_ONE = {"general_business", "social_security_reta", "home_utility_partial_dwelling",
            "health_insurance", "own_meals", "fine_or_surcharge"}


def raw():
    return json.loads(resources.files("autonomo_taxes").joinpath("expense_rules.json").read_text(encoding="utf-8"))


def draft(rule_id, *, base=10000, vat=2100, deductible_vat=None, share=None, invoice="F1", on="2026-07-01", asset=None,
          currency="EUR", fx=None, **facts):
    tax = dict(taxable_base_minor=base, vat_minor=vat, deductible_vat_minor=deductible_vat, deductible_ratio=share,
               aeat_invoice_type=invoice, include_modelo303=True)
    return {"facts": {"transaction_date": on, "currency": currency}, "asset": asset, "fx": fx, "decision": {"tax_treatment": tax},
            "deduction": None if rule_id is None else {"rule_id": rule_id, "facts": {**dict.fromkeys(FACT_NAMES), **facts}}}


STATE = {"transaction": {"transaction_id": "synthetic-transaction"}}


def codes(result):
    return [note["code"] for note in result["explanation"]]


def test_catalog_has_step_one_rules_with_pinned_legal_values():
    catalog = load_catalog()
    rules = catalog["rules"]
    assert set(rules) == STEP_ONE and catalog["version"] == "2026-10-01"
    assert rules["home_utility_partial_dwelling"]["irpf"] == {"type": "coefficient_times_area", "coefficient_basis_points": 3000}
    assert rules["health_insurance"]["irpf"]["cap_minor_per_person"] == 50000
    assert rules["health_insurance"]["irpf"]["cap_minor_per_disabled_person"] == 150000
    assert rules["own_meals"]["irpf"]["cap_minor_per_day"] == {"spain": 2667, "spain_overnight": 5334, "abroad": 4808, "abroad_overnight": 9135}
    assert rules["fine_or_surcharge"]["irpf"]["type"] == "never"
    assert catalog["categories"]["home_utility_review"] == "home_utility_partial_dwelling"
    assert catalog["categories"]["reta"] == "social_security_reta"
    secondary = {rule_id for rule_id, rule in rules.items() for source in rule["sources"] if source["source_kind"] == "secondary"}
    assert secondary == set()
    assert rules["home_utility_partial_dwelling"]["iva"] == {"type": "manual"}
    assert all(source["url"].startswith("https://") and source["checked_on"] for rule in rules.values() for source in rule["sources"])


@pytest.mark.parametrize("mutate,message", [
    (lambda data: data["rules"].append(deepcopy(data["rules"][0])), "Duplicate rule id"),
    (lambda data: data["rules"][1]["parser_categories"].append(data["rules"][0]["parser_categories"][0]), "maps to several rules"),
    (lambda data: data["rules"][0]["irpf"].update(type="generous"), "unknown IRPF rule type"),
    (lambda data: data["rules"][0]["iva"].update(type="all"), "unknown IVA rule type"),
    (lambda data: data["rules"][0].update(sources=[]), "sources are required"),
    (lambda data: data["rules"][0]["sources"][0].update(url="http://example.invalid"), "https"),
    (lambda data: data["rules"][0]["sources"][0].pop("checked_on"), "invalid source fields"),
    (lambda data: data["rules"][0].update(risk="unknown"), "risk and reason"),
    (lambda data: data["rules"][0].update(evidence="photo"), "evidence"),
    (lambda data: data.update(catalog_version=True), "catalog version"),
    (lambda data: data["rules"][0]["sources"][0].update(note=7), "note must be text"),
    (lambda data: data["rules"][0]["sources"][0].update(checked_on="2099-01-01"), "after the catalog date"),
    (lambda data: next(rule for rule in data["rules"] if rule["id"] == "health_insurance")["irpf"].update(cap_minor_per_disabled_person=100), "invalid caps"),
])
def test_invalid_catalog_is_rejected(mutate, message):
    data = raw()
    mutate(data)
    with pytest.raises(ExpenseRuleCatalogError, match=message):
        validate_catalog(data)


def test_manual_path_has_no_proposal():
    assert propose(None, STATE, draft(None)) is None


@pytest.mark.parametrize("payload,status,irpf,vat", [
    (draft("general_business", deductible_vat=2100), "ready", 10000, 2100),
    # LIVA art. 97.Uno: no IVA without a complete invoice; unrecovered IVA is an IRPF cost.
    (draft("general_business", invoice="F2"), "ready", 12100, 0),
    (draft("social_security_reta", vat=0, evidence_confirmed=True), "ready", 10000, 0),
    # IRPF uses 30% x 25%; the manually entered IVA stays outside the proposal.
    (draft("home_utility_partial_dwelling", share=0.25, deductible_vat=525), "ready", 750, None),
    # With no manually recovered IVA, the corresponding IVA share is part of IRPF cost.
    (draft("home_utility_partial_dwelling", share=0.25, deductible_vat=0), "ready", 908, None),
    (draft("general_business", invoice="R1", deductible_vat=2100), "ready", 10000, 2100),
    (draft("general_business", invoice="R5"), "ready", 12100, 0),
    (draft("own_meals", base=3000, vat=300, deductible_vat=0, days=1, abroad=False, overnight=False, electronic_payment=True), "ready", 2667, None),
    (draft("own_meals", base=12000, vat=0, days=1, abroad=True, overnight=True, electronic_payment=True), "ready", 9135, None),
    (draft("own_meals", base=3000, vat=300, invoice="F2", days=1, abroad=False, overnight=False, electronic_payment=True), "ready", 2667, 0),
    (draft("general_business", currency="USD", fx={"rate_source": "ecb"}, deductible_vat=2100), "ready", 10000, 2100),
    (draft("own_meals", base=3000, vat=300, days=1, abroad=False, overnight=False, electronic_payment=False), "not_deductible", 0, 0),
    (draft("fine_or_surcharge"), "not_deductible", 0, 0),
])
def test_proposal_amounts(tmp_path, payload, status, irpf, vat):
    with initialize(tmp_path / "ledger.sqlite") as db:
        result = propose(db, STATE, payload)
    assert (result["status"], result["irpf_minor"], result["vat_minor"]) == (status, irpf, vat)
    assert result["sources"] and result["risk"] and result["version"] == "2026-10-01"


def test_health_insurance_cap_counts_people(tmp_path):
    with initialize(tmp_path / "ledger.sqlite") as db:
        result = propose(db, STATE, draft("health_insurance", base=300000, vat=0, persons=3, persons_disabled=1))
    assert result["irpf_minor"] == 250000 and result["vat_minor"] == 0
    assert {"code": "irpf_person_cap", "params": {"persons": 3, "persons_disabled": 1, "cap_minor": 250000, "used_minor": 0, "year": "2026"}} in result["explanation"]
    assert "cap_counts_rule_postings_only" in codes(result)


@pytest.mark.parametrize("payload,missing", [
    (draft("home_utility_partial_dwelling"), ["area_share"]),
    (draft("home_utility_partial_dwelling", base=None, share=True), ["taxable_base_minor", "area_share"]),
    (draft("health_insurance", persons=1, persons_disabled=2), ["persons_disabled"]),
    (draft("own_meals", days=0), ["days", "abroad", "overnight", "electronic_payment"]),
    (draft("general_business", on=None), ["transaction_date"]),
    # Document-currency cents must not be shown as EUR before an FX decision.
    (draft("general_business", currency="USD"), ["eur_amount"]),
])
def test_missing_facts_block_the_proposal(payload, missing):
    result = propose(None, STATE, payload)
    assert (result["status"], result["missing"], result["irpf_minor"]) == ("needs_facts", missing, None)


@pytest.mark.parametrize("payload,status,code", [
    (draft("own_meals", on="2017-12-31", days=1, abroad=False, overnight=False, electronic_payment=True), "out_of_scope", "rule_not_valid_on_date"),
    (draft("general_business", asset={"method": "linear"}), "out_of_scope", "equipment_not_covered"),
    (draft("no_such_rule"), "unavailable", "rule_unknown"),
    (draft("home_rent_partial_dwelling"), "unavailable", "rule_unknown"),
    (draft("own_meals", days=3, abroad=False, overnight=False, electronic_payment=True), "out_of_scope", "one_day_per_draft"),
])
def test_rule_that_does_not_apply_has_no_amounts(payload, status, code):
    result = propose(None, STATE, payload)
    assert (result["status"], result["irpf_minor"], result["vat_minor"]) == (status, None, None)
    assert code in codes(result)


def test_evidence_is_reported():
    missing_invoice = propose(None, STATE, draft("general_business", invoice="F2"))
    assert missing_invoice["evidence"] == {"required": "factura_completa_nif", "satisfied": False} and "evidence_missing" in codes(missing_invoice)
    reta = propose(None, STATE, draft("social_security_reta", vat=0))
    assert reta["evidence"] == {"required": "bank_statement", "satisfied": None} and reta["missing"] == ["evidence_confirmed"]
    confirmed = propose(None, STATE, draft("social_security_reta", vat=0, evidence_confirmed=True))
    assert confirmed["status"] == "ready" and "evidence_confirmed_by_reviewer" in codes(confirmed)


def test_rule_list_names_the_facts_each_rule_needs():
    from autonomo_taxes.expense_rules import rule_list
    facts = {rule["id"]: rule["facts"] for rule in rule_list()}
    assert set(facts) == STEP_ONE
    assert facts["home_utility_partial_dwelling"] == ["area_share"]
    assert facts["health_insurance"] == ["persons", "persons_disabled"]
    assert facts["own_meals"] == ["days", "abroad", "overnight", "electronic_payment"]
    assert facts["social_security_reta"] == ["evidence_confirmed"]
    assert facts["general_business"] == []


def test_unclaimed_recoverable_iva_does_not_raise_the_irpf_ceiling(tmp_path):
    for payload in (draft("general_business", deductible_vat=0), draft("general_business", deductible_vat=2100)):
        payload["decision"]["tax_treatment"]["include_modelo303"] = False
        result = propose(None, STATE, payload)
        assert (result["irpf_minor"], result["suggested"]) == (10000, {"irpf_minor": 10000, "vat_minor": 2100})
        assert "unrecovered_vat_added" not in codes(result)
    with initialize(tmp_path / "ledger.sqlite") as db:
        meals = propose(db, STATE, draft("own_meals", base=3000, vat=300, deductible_vat=100, days=1, abroad=False, overnight=False, electronic_payment=True))
    assert meals["suggested"] == {"irpf_minor": 2667, "vat_minor": None}
