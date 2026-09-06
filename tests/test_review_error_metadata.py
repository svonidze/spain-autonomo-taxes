from http import HTTPStatus
from types import SimpleNamespace

from autonomo_taxes.local_web import LocalAccountingApp, LocalAccountingHandler
from autonomo_taxes.review_packet import ReviewPacketError


def test_review_metadata_keeps_original_error_and_status():
    failure = LocalAccountingApp._review_api_error(None, ReviewPacketError("decision.business_purpose is required"))
    assert str(failure) == "decision.business_purpose is required"
    assert failure.status == HTTPStatus.BAD_REQUEST
    assert failure.message_code == "review.validationBusinessPurpose"
    assert failure.field == "business_purpose"
    observed = {}
    handler = SimpleNamespace(wfile=SimpleNamespace(closed=False),
        _send_json=lambda payload, status: observed.update(payload=payload, status=status))
    LocalAccountingHandler._send_error_json(handler, failure.status, str(failure),
        code=failure.code, current=failure.current, message_code=failure.message_code,
        params=failure.params, field=failure.field)
    assert observed["payload"]["error"] == str(failure)
    assert observed["payload"]["code"] == failure.code
    assert observed["payload"]["message_code"] == failure.message_code
    assert observed["payload"]["field"] == failure.field
    assert "current" not in observed["payload"]


def test_unrecognized_review_diagnostic_is_not_reclassified_for_translation():
    failure = LocalAccountingApp._review_api_error(None, ReviewPacketError("Synthetic unknown validation"))
    assert str(failure) == "Synthetic unknown validation"
    assert failure.message_code is None
    assert failure.field is None


# Requires the separately built optional UI assets.
import pytest
pytestmark = pytest.mark.web
