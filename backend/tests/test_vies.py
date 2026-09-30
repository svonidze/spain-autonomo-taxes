from __future__ import annotations

import base64
import hashlib
from http.client import IncompleteRead
import json
from pathlib import Path
import ssl
import sqlite3
from urllib.error import URLError

import pytest

from autonomo_taxes import vies
from autonomo_taxes.cli import main
from autonomo_taxes.history_migration import _prune_unreferenced_migration_counterparty
from autonomo_taxes.ledger_db import LATEST_SCHEMA_VERSION, LedgerDB
from autonomo_taxes.vies import ViesConsentError, check_vat, normalize_vat_number


# Shapes recorded from the official check-vat-test-service; values are synthetic.
VALID = {
    "countryCode": "DE", "vatNumber": "100", "requestDate": "2032-01-02T10:00:00.000Z",
    "valid": True, "requestIdentifier": "", "name": "John Doe",
    "address": "123 Main St, Anytown, UK",
}
INVALID = {**VALID, "vatNumber": "200", "valid": False, "name": "---", "address": "---"}


def _error(code: str) -> dict:
    return {"actionSucceed": False, "errorWrappers": [{"error": code}]}


class FakePost:
    def __init__(self, status: int = 200, payload: object = VALID) -> None:
        self.status = status
        self.raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.calls: list[dict] = []

    def __call__(self, url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        self.calls.append({"url": url, "body": json.loads(body), "timeout": timeout})
        return self.status, self.raw


def _check(number: str, post: FakePost, **kwargs):
    return check_vat("DE", number, confirm_network=True, http_post=post, **kwargs)


@pytest.mark.parametrize(
    ("country", "number", "expected"),
    [
        ("DE", "DE 123.456-789", ("DE", "123456789")),
        ("de", "123456789", ("DE", "123456789")),
        ("AT", "atu12345678", ("AT", "U12345678")),
        ("GR", "EL 123456789", ("EL", "123456789")),
        ("EL", "GR123456789", ("EL", "123456789")),
    ],
)
def test_normalizes_prefix_spaces_and_punctuation(country, number, expected) -> None:
    assert normalize_vat_number(country, number) == expected


@pytest.mark.parametrize(
    ("country", "number"),
    [("GB", "123456789"), ("ZZ", "123"), ("DE", "FR12345678901"), ("DE", " - "), ("DE", "")],
)
def test_rejects_non_eu_mismatched_or_empty_numbers(country, number) -> None:
    with pytest.raises(ValueError):
        normalize_vat_number(country, number)


def test_northern_ireland_numbers_point_to_gb_vat_for_services() -> None:
    with pytest.raises(ValueError, match="GB VAT number"):
        normalize_vat_number("XI", "XI123456789")


def test_requires_consent_before_any_request() -> None:
    post = FakePost()
    with pytest.raises(ViesConsentError, match="European Commission"):
        check_vat("DE", "100", confirm_network=False, http_post=post)
    assert post.calls == []


def test_valid_and_invalid_answers_keep_raw_evidence() -> None:
    post = FakePost(payload=VALID)
    result = _check("DE100", post)
    assert post.calls[0]["body"] == {"countryCode": "DE", "vatNumber": "100"}
    assert result["outcome"] == "valid"
    assert result["trader_name"] == "John Doe"
    assert result["request_identifier"] is None and result["requester_used"] is False
    assert result["response_sha256"] == hashlib.sha256(post.raw).hexdigest()
    assert result["response_body"] == post.raw.decode()
    assert result["response_encoding"] == "utf-8" and result["request_sent"] is True
    assert _check("200", FakePost(payload=INVALID))["outcome"] == "invalid"


def test_requester_is_sent_only_when_given() -> None:
    post = FakePost(payload={**VALID, "requestIdentifier": "WAPIAAAAW1234567"})
    result = _check("100", post, requester="ES B-12345678")
    assert post.calls[0]["body"] == {
        "countryCode": "DE", "vatNumber": "100",
        "requesterMemberStateCode": "ES", "requesterNumber": "B12345678",
    }
    assert result["requester_used"] is True
    assert result["request_identifier"] == "WAPIAAAAW1234567"


@pytest.mark.parametrize(
    ("post", "outcome", "error_code"),
    [
        (FakePost(payload=_error("INVALID_INPUT")), "invalid_input", "INVALID_INPUT"),
        (FakePost(payload=_error("INVALID_REQUESTER_INFO")), "invalid_input", "INVALID_REQUESTER_INFO"),
        (FakePost(payload=_error("MS_UNAVAILABLE")), "unavailable", "MS_UNAVAILABLE"),
        (FakePost(payload=_error("MS_MAX_CONCURRENT_REQ")), "unavailable", "MS_MAX_CONCURRENT_REQ"),
        (FakePost(payload=_error("VAT_BLOCKED")), "unavailable", "VAT_BLOCKED"),
        (FakePost(500, _error("VOW-ERR-1")), "unavailable", "VOW-ERR-1"),
        (FakePost(400, _error("INVALID_INPUT")), "invalid_input", "INVALID_INPUT"),
        (FakePost(payload=b"<html>maintenance</html>"), "unavailable", "MALFORMED_RESPONSE"),
        (FakePost(payload={"valid": "yes"}), "unavailable", "MALFORMED_RESPONSE"),
        (FakePost(payload={**VALID, "vatNumber": "999"}), "unavailable", "RESPONSE_MISMATCH"),
    ],
)
def test_errors_never_become_invalid(post, outcome, error_code) -> None:
    result = _check("100", post)
    assert (result["outcome"], result["error_code"]) == (outcome, error_code)
    assert result["trader_name"] is None


@pytest.mark.parametrize(
    ("error", "error_code", "sent"),
    [
        (TimeoutError("synthetic timeout"), "TRANSPORT_ERROR:TimeoutError", True),
        (IncompleteRead(b"{"), "TRANSPORT_ERROR:IncompleteRead", True),
        (URLError(ssl.SSLCertVerificationError("synthetic")),
         "CONNECTION_FAILED:SSLCertVerificationError", False),
        (URLError("synthetic"), "CONNECTION_FAILED:URLError", False),
    ],
)
def test_transport_failure_is_unavailable_without_response(error, error_code, sent) -> None:
    def broken(url: str, body: bytes, timeout: float):
        raise error

    result = check_vat("DE", "100", confirm_network=True, http_post=broken)
    assert result["outcome"] == "unavailable"
    assert (result["error_code"], result["request_sent"]) == (error_code, sent)
    assert result["response_sha256"] is None and result["http_status"] is None


def test_non_utf8_response_is_stored_losslessly(tmp_path: Path) -> None:
    raw = b"\xff\xfe synthetic gateway page"
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = _party(db)
        check = db.record_vies_check(party["counterparty_id"], _check("100", FakePost(502, raw)))
    assert (check["outcome"], check["response_encoding"]) == ("unavailable", "base64")
    assert hashlib.sha256(base64.b64decode(check["response_body"])).hexdigest() == (
        check["response_sha256"]
    )


def _party(db: LedgerDB, vat_id: str | None = "DE100", country: str = "DE") -> dict:
    party = db.upsert_counterparty(display_name="Synthetic EU Customer", country_code=country)
    return db.set_counterparty_tax_profile(party["counterparty_id"], vat_id=vat_id)


@pytest.mark.parametrize(
    ("payload", "apply", "status", "applied"),
    [
        (VALID, True, "registered", "registered"),
        (INVALID, True, "not_registered", "not_registered"),
        (VALID, False, "unknown", None),
        (_error("MS_UNAVAILABLE"), True, "unknown", None),
        (_error("INVALID_INPUT"), True, "unknown", None),
    ],
)
def test_only_applied_answers_change_roi_status(tmp_path: Path, payload, apply, status, applied) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = _party(db)
        number = payload.get("vatNumber", "100")
        check = db.record_vies_check(
            party["counterparty_id"], _check(number, FakePost(payload=payload)),
            apply=apply, expected_row_version=party["row_version"],
        )
        after = db.connection.execute(
            "SELECT roi_status, row_version, source_hash FROM counterparties"
        ).fetchone()
    assert after["roi_status"] == status
    assert check["applied_roi_status"] == applied
    if applied:
        assert check["applied_row_version"] == after["row_version"] == party["row_version"] + 1
        assert after["source_hash"] == check["response_sha256"]
    else:
        assert check["applied_row_version"] is None
        assert after["row_version"] == party["row_version"]


def test_stale_counterparty_keeps_evidence_without_applying(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = _party(db)
        check = db.record_vies_check(
            party["counterparty_id"], _check("100", FakePost()),
            apply=True, expected_row_version=party["row_version"] - 1,
        )
        assert check["applied_roi_status"] is None
        assert db.connection.execute("SELECT roi_status FROM counterparties").fetchone()[0] == "unknown"
        assert len(db.list_vies_checks(counterparty_id=party["counterparty_id"])) == 1


def test_checks_are_immutable_and_protect_the_counterparty(tmp_path: Path) -> None:
    with LedgerDB.initialize(tmp_path / "ledger.sqlite") as db:
        party = _party(db)
        check = db.record_vies_check(party["counterparty_id"], _check("100", FakePost()))
        assert hashlib.sha256(check["response_body"].encode()).hexdigest() == (
            check["response_sha256"]
        )
        assert db.table_counts()["vies_checks"] == 1
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.connection.execute("UPDATE vies_checks SET outcome = 'invalid'")
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            db.connection.execute("DELETE FROM vies_checks")
        assert _prune_unreferenced_migration_counterparty(db, party["counterparty_id"]) is False


def test_migrates_schema_25_to_27_preserves_vies_migration_26(tmp_path: Path) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        for name in ("reta_base_election_voids", "reta_base_elections", "reta_rate_tables", "vies_checks"):
            db.connection.execute(f"DROP TABLE {name}")
        db.connection.execute("PRAGMA user_version = 25")
        db.connection.commit()
    with LedgerDB.open(database, apply_migrations=True) as db:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == LATEST_SCHEMA_VERSION
        triggers = {
            row[0] for row in db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='vies_checks'"
            )
        }
    assert triggers == {"vies_checks_no_update", "vies_checks_no_delete"}


def test_cli_requires_consent_then_records_and_lists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        party_id = _party(db)["counterparty_id"]
    post = FakePost()
    monkeypatch.setattr(vies, "_default_http_post", post)
    base = ["counterparties", "vies-check", "--db", str(database), "--counterparty-id", party_id]

    assert main(base + ["--apply"]) == 2
    refused = json.loads(capsys.readouterr().out)
    assert refused["sent"] is False and "European Commission" in refused["error"]
    assert post.calls == []

    assert main(base + ["--confirm-network-to-vies", "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["applied"] is True and applied["roi_status"] == "registered"
    assert post.calls[0]["url"] == vies.VIES_CHECK_URL

    monkeypatch.setattr(vies, "_default_http_post", FakePost(payload=_error("MS_UNAVAILABLE")))
    assert main(base + ["--confirm-network-to-vies", "--apply"]) == 2
    unavailable = json.loads(capsys.readouterr().out)
    assert unavailable["applied"] is False and unavailable["roi_status"] == "registered"

    assert main(["counterparties", "vies-list", "--db", str(database)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row["outcome"] for row in listed] == ["unavailable", "valid"]


def test_cli_help_discloses_what_is_sent(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        main(["counterparties", "vies-check", "--help"])
    help_text = " ".join(capsys.readouterr().out.split())
    assert "sent to the European Commission VIES service" in help_text
    assert "--requester-vat" in help_text


def test_cli_keeps_evidence_when_counterparty_changes_during_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        party = _party(db)
    post = FakePost()

    def post_while_editing(url: str, body: bytes, timeout: float) -> tuple[int, bytes]:
        with LedgerDB.open(database) as other:
            other.set_counterparty_tax_profile(party["counterparty_id"], vat_id="DE100")
        return post(url, body, timeout)

    monkeypatch.setattr(vies, "_default_http_post", post_while_editing)
    assert main([
        "counterparties", "vies-check", "--db", str(database),
        "--counterparty-id", party["counterparty_id"], "--confirm-network-to-vies", "--apply",
    ]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["applied"] is False and output["reason"] == "counterparty_changed"
    assert output["roi_status"] == "unknown"
    assert output["row_version"] == party["row_version"] + 1
    with LedgerDB.open(database, read_only=True) as db:
        assert [row["outcome"] for row in db.list_vies_checks()] == ["valid"]


def test_cli_reports_unsent_request_when_connection_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "ledger.sqlite"
    with LedgerDB.initialize(database) as db:
        party_id = _party(db)["counterparty_id"]

    def refused(url: str, body: bytes, timeout: float):
        raise URLError(ConnectionRefusedError("synthetic"))

    monkeypatch.setattr(vies, "_default_http_post", refused)
    assert main([
        "counterparties", "vies-check", "--db", str(database),
        "--counterparty-id", party_id, "--confirm-network-to-vies",
    ]) == 2
    output = json.loads(capsys.readouterr().out)
    assert output["sent"] is False
    assert output["check"]["error_code"] == "CONNECTION_FAILED:ConnectionRefusedError"
