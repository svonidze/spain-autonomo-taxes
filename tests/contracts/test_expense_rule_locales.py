"""Every catalog rule, fact and explanation code has RU and EN interface text."""
import json
import re

from autonomo_taxes.expense_rules import FACT_NAMES, load_catalog
from autonomo_test_support.paths import REPO_ROOT

LOCALES = REPO_ROOT / "frontend/src/locales"
SOURCE = (REPO_ROOT / "backend/src/autonomo_taxes/expense_rules.py").read_text()


def keys(code):
    return set(json.loads((LOCALES / code / "expenseRules.json").read_text())) | set(json.loads((LOCALES / code / "errors.json").read_text()))


def test_rule_ids_facts_codes_and_errors_have_ru_and_en_text():
    rules = load_catalog()["rules"].values()
    codes = set(re.findall(r'_note\("([a-z_]+)"[,)]', SOURCE)) | {"vat_" + rule["iva"]["type"] for rule in rules}
    missing = {name for line in re.findall(r'(?<!")\bmissing\b[^\n]*', SOURCE) for name in re.findall(r'"([a-z_]+)"', line)}
    errors = set(re.findall(r'code="(deduction_[a-z_]+)"', (REPO_ROOT / "backend/src/autonomo_taxes/expense_workflow.py").read_text()))
    expected = {f"expenseRules.rule.{rule['id']}" for rule in rules} | {f"expenseRules.riskReason.{rule['id']}" for rule in rules}
    expected |= {f"expenseRules.risk.{rule['risk']}" for rule in rules}
    expected |= {f"expenseRules.fact.{name}" for name in (*FACT_NAMES, "area_share", *missing)}
    expected |= {f"expenseRules.code.{code}" for code in codes} | {f"errors.{code}" for code in errors}
    expected |= {f"expenseRules.status.{status}" for status in ("ready", "needs_facts", "not_deductible", "out_of_scope", "unavailable")}
    assert {"eur_amount", "evidence_confirmed", "transaction_date"} <= missing and len(errors) == 5 and "cap_exhausted" in codes
    for code in ("ru", "en"):
        assert expected - keys(code) == set(), code
