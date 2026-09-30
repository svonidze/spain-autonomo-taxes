from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from autonomo_taxes.zenmoney import inspect_zenmoney_csv, load_zenmoney_payments_csv


def test_inspection_lists_exact_accounts_and_coverage_without_transaction_details(
    tmp_path: Path,
) -> None:
    path = tmp_path / "zenmoney.csv"
    path.write_text(
        "Дата;Счёт;Сумма (расход);Валюта (расход);Счёт-получатель;"
        "Сумма (доход);Валюта (доход);Плательщик;Комментарий;deleted\n"
        "02.07.2026;Revolut Pro;10,00;EUR;;;;OpenAI;private note;\n"
        "03.07.2026;;;;Wise Business;100,00;USD;Customer;invoice number;\n"
        "2026-02-31;Personal;5,00;EUR;;;;Cafe;lunch;\n"
        "04.07.2026;Old account;5,00;EUR;;;;Old supplier;deleted;true\n",
        encoding="utf-8",
    )

    result = inspect_zenmoney_csv(path)

    assert result.row_count == 4
    assert result.dated_row_count == 2
    assert result.invalid_date_row_count == 1
    assert result.deleted_row_count == 1
    assert result.starts_on == date(2026, 7, 2)
    assert result.ends_on == date(2026, 7, 3)
    assert [account.account_name for account in result.accounts] == [
        "Personal",
        "Revolut Pro",
        "Wise Business",
    ]
    assert result.accounts[1].currencies == ("EUR",)
    assert result.accounts[2].income_rows == 1
    assert all(account.account_name != "Old account" for account in result.accounts)
    assert "private note" not in repr(result)


def test_loads_selected_business_accounts_and_skips_transfers_and_personal_rows(
    tmp_path: Path,
) -> None:
    path = tmp_path / "zenmoney.csv"
    path.write_text(
        "Дата;Категория;Плательщик;Комментарий;Счёт;Сумма (расход);Валюта (расход);"
        "Счёт-получатель;Сумма (доход);Валюта (доход);id;deleted\n"
        "02.07.2026;AI;OpenAI;2QFSQPLO-0004;Revolut Pro;103,00;EUR;;;;zm-1;\n"
        "03.07.2026;Income;IPG;FACT-2026-SYNTH-DOCUMENT-035;;;;Revolut Pro;1200,00;USD;zm-2;\n"
        "04.07.2026;Transfer;;Move cash;Revolut Pro;500,00;EUR;Wise Business;500,00;EUR;zm-3;\n"
        "05.07.2026;Food;Cafe;Lunch;Personal;20,00;EUR;;;;zm-4;\n"
        "30.06.2026;AI;Anthropic;Old quarter;Revolut Pro;20,00;EUR;;;;zm-5;\n"
        "06.07.2026;AI;Cursor;Deleted;Revolut Pro;30,00;EUR;;;;zm-6;true\n",
        encoding="utf-8",
    )

    result = load_zenmoney_payments_csv(
        path,
        business_accounts={"Revolut Pro", "Wise Business"},
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 9, 30),
    )

    assert len(result.payments) == 2
    expense, income = result.payments
    assert expense.external_id == "zm-1"
    assert expense.amount_original == Decimal("-103.00")
    assert expense.account_name == "Revolut Pro"
    assert expense.reference == "2QFSQPLO-0004"
    assert income.external_id == "zm-2"
    assert income.amount_original == Decimal("1200.00")
    assert income.currency == "USD"
    assert income.amount_eur is None
    assert {row.reason for row in result.skipped} == {
        "internal_business_transfer",
        "non_business_account",
        "outside_period",
        "deleted",
    }


def test_semantic_ids_are_stable_and_keep_identical_rows_distinct(tmp_path: Path) -> None:
    path = tmp_path / "zenmoney.csv"
    path.write_text(
        "Date,Payee,Comment,Account,Outcome,Currency\n"
        "2026-07-02,OpenAI,Monthly invoice,Business,10.00,EUR\n"
        "2026-07-02,OpenAI,Monthly invoice,Business,10.00,EUR\n",
        encoding="utf-8",
    )

    first = load_zenmoney_payments_csv(
        path,
        business_accounts={"Business"},
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 9, 30),
    )
    second = load_zenmoney_payments_csv(
        path,
        business_accounts={"Business"},
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 9, 30),
    )

    first_ids = [row.external_id for row in first.payments]
    assert first_ids == [row.external_id for row in second.payments]
    assert first_ids[0] != first_ids[1]

    path.write_text(
        "Date,Payee,Comment,Account,Outcome,Currency\n"
        "2026-07-02,OpenAI,Edited comment,Business,10.00,EUR\n"
        "2026-07-02,OpenAI,Edited comment,Business,10.00,EUR\n",
        encoding="utf-8",
    )
    edited = load_zenmoney_payments_csv(
        path,
        business_accounts={"Business"},
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 9, 30),
    )
    assert [row.external_id for row in edited.payments] == first_ids


def test_cp1251_tab_export_preserves_quoted_multiline_comment(tmp_path: Path) -> None:
    path = tmp_path / "zenmoney.tsv"
    content = (
        "Дата\tКатегория\tПлательщик\tКомментарий\tСчёт\tСумма (расход)\tВалюта\n"
        '02.07.2026\tСервисы\tПоставщик\t"Первая строка\nВторая строка"\tБизнес\t10,00\tEUR\n'
    )
    path.write_bytes(content.encode("cp1251"))

    result = load_zenmoney_payments_csv(
        path,
        business_accounts={"Бизнес"},
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 9, 30),
    )

    assert len(result.payments) == 1
    assert result.payments[0].comment == "Первая строка\nВторая строка"
    assert result.payments[0].amount_original == Decimal("-10.00")


def test_signed_single_account_amount_keeps_its_direction(tmp_path: Path) -> None:
    path = tmp_path / "zenmoney.csv"
    path.write_text(
        "Date,Account,Signed amount,Currency\n"
        "2026-07-02,Business,100.00,EUR\n"
        "2026-07-03,Business,-20.00,EUR\n",
        encoding="utf-8",
    )

    result = load_zenmoney_payments_csv(
        path,
        business_accounts={"Business"},
        starts_on=date(2026, 7, 1),
        ends_on=date(2026, 9, 30),
    )

    assert [row.amount_original for row in result.payments] == [
        Decimal("100.00"),
        Decimal("-20.00"),
    ]


def test_currencyless_business_row_requires_explicit_default(tmp_path: Path) -> None:
    path = tmp_path / "zenmoney.csv"
    path.write_text(
        "Date,Account,Outcome\n2026-07-02,Business,10.00\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="no currency"):
        load_zenmoney_payments_csv(
            path,
            business_accounts={"Business"},
            starts_on=date(2026, 7, 1),
            ends_on=date(2026, 9, 30),
        )
