from decimal import Decimal

from autonomo_taxes.cash_check import build_cash_check


def test_cash_check_sums_positive_due_forms_and_treats_negative_vat_as_no_payment() -> None:
    result = build_cash_check(
        {
            "130": {"blocked": False, "values": {"19": Decimal("777.29")}},
            "303": {"blocked": False, "values": {"71": Decimal("-21.00")}},
        },
        due_forms={"130", "303"},
        available_eur=Decimal("800.00"),
    )

    assert result["required_tax_eur"] == Decimal("777.29")
    assert result["buffer_eur"] == Decimal("100.00")
    assert result["status"] == "tax_covered_buffer_low"
    assert result["tax_payment_ready"] is True
    assert result["recommended_buffer_ready"] is False


def test_cash_check_reports_actual_tax_shortfall() -> None:
    result = build_cash_check(
        {"130": {"blocked": False, "values": {"19": "1200.00"}}},
        due_forms={"130"},
        available_eur=Decimal("1000.00"),
    )

    assert result["status"] == "shortfall"
    assert result["tax_shortfall_eur"] == Decimal("200.00")
    assert result["recommended_reserve_eur"] == Decimal("1320.00")


def test_cash_check_treats_modelo349_as_information_return() -> None:
    result = build_cash_check(
        {"349": {"blocked": False, "values": {}}},
        due_forms={"349"},
        available_eur=None,
    )

    assert result["required_tax_eur"] == Decimal("0.00")
    assert result["tax_payment_ready"] is True
    assert result["status"] == "not_required"
    assert result["forms"] == [
        {
            "form": "349",
            "payable_eur": Decimal("0.00"),
            "status": "information_return_no_payment",
        }
    ]


def test_cash_check_never_marks_blocked_calculation_ready() -> None:
    result = build_cash_check(
        {"130": {"blocked": True, "reason": "missing treatment"}},
        due_forms={"130"},
        available_eur=Decimal("1000.00"),
    )

    assert result["tax_payment_ready"] is False
    assert result["status"] == "calculation_blocked"
    assert result["calculation_blocked_forms"] == ["130"]
