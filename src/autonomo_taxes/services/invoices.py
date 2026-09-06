"""Shared application operations for CLI and HTTP adapters."""

from __future__ import annotations
from datetime import date
import json
from typing import Any, Mapping
from .. import expense_workflow
from ..expense_workflow import ExpenseWorkflowError
from ..fx_reference import ECBRateObservation, FXReferenceError, fetch_eur_rate
from ..ledger_db import LedgerDbError, open as open_ledger_db
from ..review_packet import (
    ReviewPacketError,
    build_fx_suggestion,
    classify_review_packet_failure,
    confirm_review_packet,
    prepare_review_packet,
    prepare_review_work_item,
)
from .common import (
    PostingOperationError,
    ServiceApiError,
    ServiceError,
    _POSTING_BATCH_LOCK,
    _exact_object_fields,
    _validate_period,
)
from .posting_operations import post_batch
from .calculation_operations import dashboard
from .review_operations import apply_fx
from ..review_packet import apply_review_packet


class InvoiceService:
    def posting_preview(self, period_key: str) -> dict[str, Any]:
        period = _validate_period(period_key)
        try:
            return self._build_posting_preview(period)
        except LedgerDbError as exc:
            if str(exc).startswith("Unknown period: "):
                raise FileNotFoundError(str(exc)) from exc
            raise

    def post_ready(
        self,
        *,
        period_key: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        period = _validate_period(period_key)
        if not items:
            raise ServiceError("Posting preview has no ready items")
        if not _POSTING_BATCH_LOCK.acquire(blocking=False):
            return {
                "error": "posting_in_progress",
                "message": "Another posting batch is already running",
                "status": "busy",
            }
        try:
            result, code = post_batch(
                {"period": period, "items": items},
                database=self.config.database,
                period=period,
                inbox_root=self.config.inbox_root,
                archive_root=self.config.archive_root,
            )
            if code:
                raise PostingOperationError(result)
            return result
        finally:
            _POSTING_BATCH_LOCK.release()

    def expense_draft(self, transaction_id: str) -> dict[str, Any]:
        with open_ledger_db(self.config.database, read_only=True) as db:
            result = expense_workflow.get_draft(db, transaction_id)
            state = json.loads(json.dumps(result["source"]))
            facts = result["payload"]["facts"]
            tx = state["transaction"]
            if (
                facts["currency"] != (tx.get("original_currency") or tx["currency"])
                or facts["transaction_date"] != tx["transaction_date"]
                or facts["gross_minor"] != tx.get("amount_original_minor")
            ):
                tx["fx_rate_id"] = None
                state["fx"] = None
            tx.update(
                original_currency=facts["currency"],
                currency=facts["currency"],
                transaction_date=facts["transaction_date"],
            )
            result["fx_suggestion"] = self._fx_suggestion_for(db, {"state": state})
            return result

    def expense_save(
        self, transaction_id: str, payload: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        _exact_object_fields(
            payload,
            {"payload", "expected_version", "source_snapshot_hash"},
            "expense draft request",
        )
        with open_ledger_db(self.config.database) as db:
            expense_workflow.save_draft(db, transaction_id, actor=actor, **payload)
        return self.expense_draft(transaction_id)

    def _expense_fx_verifier(self, transaction_id: str):
        # External validation happens before the short accounting write transaction.
        with open_ledger_db(self.config.database, read_only=True) as db:
            draft = expense_workflow.get_draft(db, transaction_id)["payload"]
        fx = draft["fx"]
        if not fx or fx.get("rate_source") != "ecb":
            return None
        currency, rate_date = draft["facts"]["currency"], date.fromisoformat(
            fx["rate_date"]
        )
        observation = self._ecb_verify(currency, rate_date)

        def verified(request_currency, request_date):
            if (request_currency, request_date) != (currency, rate_date):
                raise ExpenseWorkflowError(
                    "FX request changed during validation",
                    code="expense_stale",
                    status=409,
                )
            return observation

        return verified

    def expense_preview(
        self, transaction_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        _exact_object_fields(payload, {"expected_version"}, "expense preview request")
        verifier = self._expense_fx_verifier(transaction_id)
        with open_ledger_db(self.config.database) as db:
            return expense_workflow.preview(
                db, transaction_id, ecb_verify=verifier, **payload
            )

    def expense_confirm(
        self, transaction_id: str, payload: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        _exact_object_fields(
            payload,
            {"expected_version", "preview_token", "request_id"},
            "expense confirm request",
        )
        if self.config.archive_root is None:
            raise ServiceError("Original archive is not configured")
        expense_workflow._request_id(payload["request_id"])
        # A successful action can be retried even though its source is now posted.
        with open_ledger_db(self.config.database, read_only=True) as db:
            existing = db.connection.execute(
                "SELECT 1 FROM expense_actions WHERE request_id=?",
                (payload["request_id"],),
            ).fetchone()
        verifier = None if existing else self._expense_fx_verifier(transaction_id)
        with open_ledger_db(self.config.database) as db:
            result = expense_workflow.confirm_and_post(
                db,
                transaction_id,
                actor=actor,
                archive_root=self.config.archive_root,
                inbox_root=self.config.inbox_root,
                ecb_verify=verifier,
                **payload,
            )
        return {**result, **self.expense_follow_up(transaction_id)}

    def expense_follow_up(self, transaction_id: str) -> dict[str, Any]:
        from ..intake import cleanup_expense_inbox_source
        from ..posting import (
            prevalidate_expense_inbox_cleanup,
            expense_inbox_cleanup_row,
            build_expense_inbox_cleanup_candidate,
        )

        warnings = []
        with open_ledger_db(self.config.database, read_only=True) as db:
            row = db.connection.execute(
                "SELECT t.lifecycle_status,p.period_key FROM transactions t JOIN periods p ON p.period_id=t.period_id WHERE t.transaction_id=?",
                (transaction_id,),
            ).fetchone()
            if row is None or row["lifecycle_status"] not in {
                "posted",
                "included_in_snapshot",
            }:
                raise ExpenseWorkflowError(
                    "Follow-up requires a posted transaction", status=409
                )
            period = row["period_key"]
            try:
                action = db.connection.execute(
                    "SELECT result_json FROM expense_actions WHERE json_extract(result_json,'$.transaction_id')=? ORDER BY created_at DESC LIMIT 1",
                    (transaction_id,),
                ).fetchone()
                original_period = (
                    json.loads(action[0]).get("cleanup_period_key") if action else None
                )
                if original_period and original_period != period:
                    cleanup_row = expense_inbox_cleanup_row(db, transaction_id)
                    if cleanup_row:
                        cleanup_row["period_key"] = original_period
                        candidate = build_expense_inbox_cleanup_candidate(
                            cleanup_row,
                            inbox_root=self.config.inbox_root,
                            archive_root=self.config.archive_root,
                        )
                        cleanup_expense_inbox_source(candidate)
                else:
                    cleanup = prevalidate_expense_inbox_cleanup(
                        db,
                        transaction_id,
                        inbox_root=self.config.inbox_root,
                        archive_root=self.config.archive_root,
                    )
                    if cleanup.blocking:
                        warnings.append("cleanup_pending")
                    elif cleanup.candidate is not None:
                        cleanup_expense_inbox_source(cleanup.candidate)
            except Exception:
                warnings.append("cleanup_pending")
        try:
            self.refresh_dashboard(period)
        except Exception:
            warnings.append("calculation_refresh_pending")
        outcome = {
            "posted": True,
            "follow_up_pending": bool(warnings),
            "warnings": warnings,
        }
        with open_ledger_db(self.config.database) as db:
            with db.transaction():
                actions = db.connection.execute(
                    "SELECT request_id,result_json FROM expense_actions WHERE json_extract(result_json,'$.transaction_id')=?",
                    (transaction_id,),
                ).fetchall()
                for action in actions:
                    result = {**json.loads(action["result_json"]), **outcome}
                    db.connection.execute(
                        "UPDATE expense_actions SET result_json=? WHERE request_id=?",
                        (json.dumps(result), action["request_id"]),
                    )
        return outcome

    def depreciation_schedule(self, asset_id: str) -> dict[str, Any]:
        with open_ledger_db(self.config.database, read_only=True) as db:
            native = db.connection.execute(
                "SELECT * FROM asset_depreciation_plans WHERE asset_id=?", (asset_id,)
            ).fetchone()
            rows = expense_workflow.asset_schedule(db, asset_id)
            for row in rows:
                row["can_post"] = bool(
                    native
                    and not row["recognition_transaction_id"]
                    and row["amount_minor"] > 0
                    and row["period_status"] == "open"
                    and row["recognition_on"]
                    and row["recognition_on"] <= date.today().isoformat()
                )
            return {"asset_id": asset_id, "native": bool(native), "rows": rows}

    def depreciation_post(
        self, entry_id: str, payload: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        _exact_object_fields(
            payload, {"expected_version", "request_id"}, "depreciation request"
        )
        if self.config.archive_root is None:
            raise ServiceError("Original archive is not configured")
        with open_ledger_db(self.config.database) as db:
            result = expense_workflow.recognize_period(
                db,
                entry_id,
                actor=actor,
                archive_root=self.config.archive_root,
                **payload,
            )
        return {**result, **self.expense_follow_up(result["transaction_id"])}

    def refresh_dashboard(
        self, period_key: str, *, as_of: str | None = None
    ) -> dict[str, Any]:
        period = _validate_period(period_key)
        effective_as_of = date.fromisoformat(as_of) if as_of else date.today()
        output_dir = self.config.cache_root / period
        dashboard(
            database=self.config.database,
            period=period,
            as_of=effective_as_of,
            out_dir=output_dir,
        )
        dashboard_path = output_dir / "dashboard.json"
        if not dashboard_path.is_file():
            raise ServiceError("Dashboard command did not create dashboard.json")
        return json.loads(dashboard_path.read_text(encoding="utf-8"))

    def review_work_item(self, review_id: str) -> dict[str, Any]:
        try:
            with open_ledger_db(self.config.database, read_only=True) as db:
                packet = prepare_review_packet(db, review_id)
                fx_suggestion = self._fx_suggestion_for(db, packet)
                result = prepare_review_work_item(
                    db, review_id, fx_suggestion=fx_suggestion
                )
                context = self._status_context(db.connection).transaction(
                    packet["state"]["transaction"]["transaction_id"]
                )
                result["ui_context"] = context
                result["posting_context"] = context.get("posting")
                result["review_allowed"] = (
                    result["supported"]
                    and packet["state"]["period"]["status"] == "open"
                    and not any(
                        r["code"] == "future_dated"
                        for r in (context.get("posting") or {}).get("blockers", [])
                    )
                )
                return result
        except ReviewPacketError as exc:
            raise self._review_api_error(exc) from exc

    def review_confirm(
        self,
        packet: Mapping[str, Any],
        fx: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        self._ensure_supported_review_packet(packet)
        try:
            with open_ledger_db(self.config.database) as db:
                return confirm_review_packet(
                    db, packet, fx, ecb_verify=self._ecb_verify
                )
        except ReviewPacketError as exc:
            raise self._review_api_error(exc) from exc

    def _fx_suggestion_for(
        self, db: Any, packet: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        state = packet["state"]
        transaction = state["transaction"]
        original_currency = str(
            transaction["original_currency"] or transaction["currency"] or "EUR"
        ).upper()
        if original_currency == "EUR":
            return None
        if transaction["fx_rate_id"]:
            provenance = db.fx_provenance_for_rate(str(transaction["fx_rate_id"]))
            verification = db.fx_verification_for_transaction(
                str(transaction["transaction_id"]),
                str(transaction["fx_rate_id"]),
            )
            return build_fx_suggestion(
                state, provenance=provenance, verification=verification
            )
        transaction_date = date.fromisoformat(str(transaction["transaction_date"])[:10])
        try:
            ecb_result = fetch_eur_rate(original_currency, transaction_date)
            ecb_error: str | None = None
        except FXReferenceError as exc:
            ecb_result = None
            ecb_error = str(exc)
        return build_fx_suggestion(state, ecb_result=ecb_result, ecb_error=ecb_error)

    def _ecb_verify(self, currency: str, rate_date: date) -> ECBRateObservation | None:
        """Re-verify a submitted official rate against a fresh ECB lookup.

        The confirmation flow stores this observation's provenance rather than
        accepting client-provided audit data.
        """

        result = fetch_eur_rate(currency, rate_date)
        if result.status != "exact":
            return None
        return result.observation

    def review_validate(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_supported_review_packet(packet)
        return self._run_review_apply(packet, dry_run=True)

    def review_apply(self, packet: Mapping[str, Any]) -> dict[str, Any]:
        self._ensure_supported_review_packet(packet)
        return self._run_review_apply(packet, dry_run=False)

    def review_apply_fx(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        review_id = str(payload["review_id"]).strip()
        self.review_work_item(review_id)
        try:
            apply_fx(self.config.database, payload)
        except (ValueError, LedgerDbError) as exc:
            message = str(exc)
            if message.startswith("Expected row_version"):
                raise ServiceApiError(409, "stale_snapshot", message) from exc
            if message.startswith("Conflicting FX"):
                raise ServiceApiError(409, "fx_rate_conflict", message) from exc
            if "immutable after close" in message or "lifecycle_status=" in message:
                raise ServiceApiError(409, "review_conflict", message) from exc
            raise ServiceApiError(400, "fx_review_invalid", message) from exc
        return self.review_work_item(review_id)

    def _run_review_apply(
        self,
        packet: Mapping[str, Any],
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        try:
            with open_ledger_db(self.config.database) as db:
                return apply_review_packet(db, packet, dry_run=dry_run)
        except (ReviewPacketError, ValueError, LedgerDbError) as exc:
            code, status = classify_review_packet_failure(str(exc))
            raise ServiceApiError(status, code, str(exc)) from exc

    def _ensure_supported_review_packet(self, packet: Mapping[str, Any]) -> None:
        review_id = str(packet.get("review_id", "")).strip()
        if not review_id:
            return
        work_item = self.review_work_item(review_id)
        if work_item["supported"]:
            return
        reasons = ", ".join(str(value) for value in work_item["unsupported_reasons"])
        raise ServiceApiError(
            409,
            "unsupported_work_item",
            "Review work item is not supported by the local web workflow: " + reasons,
        )

    def _review_api_error(self, exc: ReviewPacketError) -> ServiceApiError:
        code, status = classify_review_packet_failure(str(exc))
        metadata = {
            "decision.business_purpose is required": (
                "review.validationBusinessPurpose",
                "business_purpose",
            ),
            "decision.reason is required": ("review.validationReason", "reason"),
            "Approved review requires document_valid=true": (
                "review.validationDocumentConfirmation",
                "document_valid",
            ),
        }.get(str(exc))
        return ServiceApiError(
            status,
            code,
            str(exc),
            message_code=metadata[0] if metadata else None,
            field=metadata[1] if metadata else None,
        )
