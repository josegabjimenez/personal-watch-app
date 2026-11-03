#!/usr/bin/env python3
from __future__ import annotations

import concurrent.futures
import hashlib
import hmac
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from unittest import mock
from urllib import error, request
from zoneinfo import ZoneInfo

import mobile_capture_staging_server as srv
from fixtures.grocery_test_support import grocery_environment


class MobileCaptureStagingServerTests(unittest.TestCase):
    def setUp(self):
        clock = mock.patch.object(srv, "reminder_dispatch_now", return_value=datetime.fromisoformat("2026-08-30T16:00:00-05:00"))
        clock.start()
        self.addCleanup(clock.stop)

    @staticmethod
    def grocery_payload(*, live: bool = False, text: str = "leche"):
        return {
            "event_type": "mobile_capture.v1",
            "schema_version": 1,
            "request_id": "unit-grocery-write",
            "route": {"agent": "aura", "intent": "grocery", "domain": "aura.grocery_capture"},
            "capture": {"text": text},
            "context": {"dry_run": not live, "allow_write": live, "allow_firefly_write": False},
        }

    @staticmethod
    def reminder_payload(
        *,
        live: bool = False,
        text: str = "recuérdame llamar a mamá mañana a las dos pm",
        request_id: str = "unit-reminder-write",
    ):
        return {
            "event_type": "mobile_capture.v1",
            "schema_version": 1,
            "request_id": request_id,
            "created_at": "2026-08-30T21:00:00Z",
            "route": {"agent": "aura", "intent": "reminder", "domain": "aura.reminder_capture"},
            "capture": {"text": text},
            "context": {"dry_run": not live, "allow_write": live, "allow_firefly_write": False},
        }

    @staticmethod
    def expense_payload(
        *,
        live: bool = False,
        text: str = "45 mil almuerzo",
        request_id: str = "unit-expense-write",
        confirmed: bool = False,
        cancelling: bool = False,
        confirmation_token: str | None = None,
        operation_id: str | None = None,
    ):
        return {
            "event_type": "mobile_capture.v1",
            "schema_version": 1,
            "request_id": request_id,
            "created_at": "2026-08-30T21:00:00Z",
            "source": {"device_id": "unit-watch"},
            "route": {"agent": "megan", "intent": "expense", "domain": "megan.expense_capture"},
            "capture": {"text": text},
            "context": {
                "user_confirmation": confirmed,
                "user_cancellation": cancelling,
                "confirmation_token": confirmation_token,
                "operation_id": operation_id,
                "dry_run": not live,
                "allow_write": live,
                "allow_firefly_write": live and not cancelling,
            },
        }

    @staticmethod
    def general_payload(
        *,
        live: bool = False,
        text: str = "investiga el estado del proyecto Hermes",
        request_id: str = "unit-general-run",
    ):
        return {
            "event_type": "mobile_capture.v1",
            "schema_version": 1,
            "request_id": request_id,
            "created_at": "2026-08-30T21:00:00Z",
            "source": {"device_id": "unit-watch"},
            "route": {"agent": "argos", "intent": "general_capture", "domain": "argos.general_capture"},
            "capture": {"text": text},
            "context": {
                "dry_run": not live,
                "allow_write": live,
                "allow_firefly_write": False,
            },
        }

    @staticmethod
    def write_config(*domains: str, enabled: bool = True, ledger_path: Path = Path("unused.sqlite")):
        if ledger_path == Path("unused.sqlite"):
            ledger_path = Path(tempfile.mkdtemp(prefix="mobile-capture-test-")) / "ledger.sqlite"
        return srv.Config(
            secret=b"test",
            ledger_path=ledger_path,
            host="127.0.0.1",
            port=0,
            write_enabled=enabled,
            write_domains=frozenset(domains),
            aura_helper=Path(__file__),
            expense_helper=Path(__file__),
        )

    @staticmethod
    def expense_plan(operation_id: str) -> dict:
        return {
            "adapter_version": 2,
            "operation_id": operation_id,
            "transaction_kind": "cash_expense",
            "currency_code": "COP",
            "amount_cop": 45000,
            "date": "2026-08-30",
            "description": "Almuerzos",
            "source_id": "1",
            "source_name": "Cuenta Ahorros Banco Bogotá",
            "destination_id": "7",
            "destination_name": "Food",
            "category_id": "1",
            "category_name": "Food",
            "budget_id": "12",
            "budget_name": "Comidas afuera",
            "tags": [],
            "external_id": f"hermes-watch:{operation_id}",
            "evidence": "history",
            "similar_journal_id": "801",
        }

    @classmethod
    def expense_result(
        cls,
        operation_id: str,
        *,
        plan_hash: str = "a" * 64,
        decision: str = "auto_write",
    ) -> dict:
        result = {
            "ok": True,
            "intent": "expense",
            "decision": decision,
            "transaction_kind": "cash_expense",
            "write_committed": decision in {"committed", "duplicate"},
            "operation_id": operation_id,
            "external_id": f"hermes-watch:{operation_id}",
            "plan_hash": plan_hash,
            "plan": cls.expense_plan(operation_id),
            "post_attempted": decision == "committed",
        }
        if decision in {"committed", "duplicate"}:
            result.update(journal_ids=["journal-9001"], group_ids=["group-8001"])
        return result

    @staticmethod
    def signed_posts(payload: dict, cfg: srv.Config, count: int = 2):
        return MobileCaptureStagingServerTests.signed_payloads([payload] * count, cfg)

    @staticmethod
    def signed_payloads(payloads: list[dict], cfg: srv.Config):

        class QuietHandler(srv.MobileCaptureHandler):
            def log_message(self, fmt, *args):
                pass

        srv.init_db(cfg.ledger_path)
        server = srv.ThreadingHTTPServer((cfg.host, 0), QuietHandler)
        server.cfg = cfg  # type: ignore[attr-defined]
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        endpoint = f"http://127.0.0.1:{server.server_address[1]}/webhooks/mobile-capture-v1"
        responses = []
        try:
            for payload in payloads:
                raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                timestamp = str(int(time.time()))
                signature = hmac.new(
                    cfg.secret,
                    timestamp.encode("utf-8") + b"." + raw,
                    hashlib.sha256,
                ).hexdigest()
                req = request.Request(
                    endpoint,
                    data=raw,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Timestamp": timestamp,
                        "X-Webhook-Signature-V2": signature,
                        "X-Request-ID": payload["request_id"],
                    },
                )
                with request.urlopen(req, timeout=5) as http_response:
                    responses.append((http_response.status, json.loads(http_response.read())))
        finally:
            server.shutdown()
            server_thread.join()
            server.server_close()
        return responses

    def test_parse_amount_cop(self):
        self.assertEqual(srv.parse_amount_cop("45 mil en Uber"), 45000)
        self.assertEqual(srv.parse_amount_cop("33.500 sandwiches"), 33500)
        self.assertEqual(srv.parse_amount_cop("fp 364.900 ChatGPT tc1"), 364900)

    def test_hmac_v2_accepts_exact_body(self):
        secret = b"test-secret"
        raw = b'{"event_type":"mobile_capture.v1"}'
        ts = str(int(time.time()))
        sig = hmac.new(secret, ts.encode() + b"." + raw, hashlib.sha256).hexdigest()

        class Headers(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        ok, reason = srv.verify_hmac_v2(
            Headers({"X-Webhook-Timestamp": ts, "X-Webhook-Signature-V2": sig}),
            raw,
            secret,
        )
        self.assertTrue(ok, reason)

    def test_hmac_v2_rejects_changed_body(self):
        secret = b"test-secret"
        raw = b'{"event_type":"mobile_capture.v1"}'
        changed = b'{"event_type":"mobile_capture.v1","x":1}'
        ts = str(int(time.time()))
        sig = hmac.new(secret, ts.encode() + b"." + raw, hashlib.sha256).hexdigest()

        class Headers(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        ok, reason = srv.verify_hmac_v2(
            Headers({"X-Webhook-Timestamp": ts, "X-Webhook-Signature-V2": sig}),
            changed,
            secret,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "invalid signature")

    def test_megan_dry_run_plan(self):
        payload = {
            "event_type": "mobile_capture.v1",
            "schema_version": 1,
            "request_id": "unit-megan-1",
            "created_at": "2026-07-06T20:55:00Z",
            "route": {"agent": "megan", "intent": "expense", "domain": "megan.expense_capture"},
            "capture": {"text": "45 mil en Uber"},
            "context": {"dry_run": True, "allow_write": False},
        }
        plan = srv.plan_capture(payload)
        self.assertEqual(plan["status"], "accepted")
        self.assertTrue(plan["dry_run"])
        self.assertFalse(plan["plan"]["would_write"])
        self.assertEqual(plan["plan"]["amount_cop"], 45000)

    def test_expense_and_general_require_exact_independent_gates(self):
        cfg = self.write_config("megan.expense_capture", "argos.general_capture")
        self.assertTrue(srv.write_is_authorized(self.expense_payload(live=True), cfg))
        self.assertFalse(srv.write_is_authorized(self.general_payload(live=True), cfg))
        self.assertFalse(srv.write_is_authorized(self.expense_payload(live=False), cfg))
        self.assertFalse(srv.write_is_authorized(self.general_payload(live=False), cfg))

        expense_without_firefly = self.expense_payload(live=True)
        expense_without_firefly["context"]["allow_firefly_write"] = False
        self.assertFalse(srv.write_is_authorized(expense_without_firefly, cfg))

        general_with_firefly = self.general_payload(live=True)
        general_with_firefly["context"]["allow_firefly_write"] = True
        self.assertFalse(srv.write_is_authorized(general_with_firefly, cfg))

        general_with_expense_proof = self.general_payload(live=True)
        general_with_expense_proof["context"].update(
            user_confirmation=True,
            confirmation_token="v2.not-valid-for-general",
            operation_id="exp_0123456789abcdef0123456789abcdef",
        )
        self.assertFalse(srv.write_is_authorized(general_with_expense_proof, cfg))

        confirmed_expense = self.expense_payload(
            live=True,
            confirmed=True,
            confirmation_token="v2.server-token",
            operation_id="exp_0123456789abcdef0123456789abcdef",
        )
        self.assertTrue(srv.write_is_authorized(confirmed_expense, cfg))

        cancellation = self.expense_payload(
            live=True,
            request_id="authorized-expense-cancellation",
            cancelling=True,
            operation_id="exp_0123456789abcdef0123456789abcdef",
        )
        self.assertTrue(srv.write_is_authorized(cancellation, cfg))
        for key, value in (
            ("user_confirmation", True),
            ("confirmation_token", "v2.must-not-authorize-cancellation"),
            ("allow_firefly_write", True),
            ("operation_id", "not-an-expense-operation"),
        ):
            with self.subTest(invalid_cancellation_field=key):
                invalid = json.loads(json.dumps(cancellation))
                invalid["context"][key] = value
                self.assertFalse(srv.write_is_authorized(invalid, cfg))
        cancellation_without_explicit_firefly_denial = json.loads(json.dumps(cancellation))
        cancellation_without_explicit_firefly_denial["context"].pop("allow_firefly_write")
        self.assertFalse(
            srv.write_is_authorized(cancellation_without_explicit_firefly_denial, cfg)
        )
        cancellation_with_nonboolean_firefly_gate = json.loads(json.dumps(cancellation))
        cancellation_with_nonboolean_firefly_gate["context"]["allow_firefly_write"] = "false"
        self.assertFalse(
            srv.write_is_authorized(cancellation_with_nonboolean_firefly_gate, cfg)
        )
        for field, value in (
            ("user_confirmation", None),
            ("user_confirmation", "false"),
            ("confirmation_token", ""),
            ("confirmation_token", 42),
        ):
            with self.subTest(malformed_cancellation_field=field, value=value):
                malformed = json.loads(json.dumps(cancellation))
                malformed["context"][field] = value
                self.assertFalse(srv.write_is_authorized(malformed, cfg))

        expense_route_mismatch = self.expense_payload(live=True)
        expense_route_mismatch["route"]["intent"] = "general_capture"
        self.assertFalse(srv.write_is_authorized(expense_route_mismatch, cfg))

    def test_fp_tc_and_non_expense_flows_never_invoke_expense_helper(self):
        cfg = self.write_config("megan.expense_capture")
        for index, text in enumerate(
            [
                "tc1 45 mil almuerzo",
                "fp 45 mil ropa",
                "futuro pago 80 mil mercado",
                "transferencia 45 mil a Nequi",
            ]
        ):
            with self.subTest(text=text):
                payload = self.expense_payload(live=True, text=text, request_id=f"blocked-expense-{index}")
                with mock.patch.object(srv, "run_expense_helper") as helper:
                    response = srv.execute_capture(payload, cfg)
                self.assertEqual(response["status"], "rejected")
                self.assertFalse(response["write_committed"])
                self.assertFalse(response["plan"]["would_write"])
                helper.assert_not_called()

    def test_simple_expense_requires_signed_confirmation_before_commit(self):
        payload = self.expense_payload(live=True)
        cfg = self.write_config("megan.expense_capture")
        operation_id = srv.make_expense_operation_id(payload, cfg.secret)
        preflight = self.expense_result(operation_id)
        with mock.patch.object(srv, "run_expense_helper", return_value=preflight) as helper:
            response = srv.execute_capture(payload, cfg)

        self.assertEqual(response["status"], "needs_confirmation")
        self.assertFalse(response["write_committed"])
        self.assertFalse(response["plan"]["would_write"])
        self.assertRegex(response["operation_id"], r"^exp_[0-9a-f]{32}$")
        self.assertRegex(
            response["confirmation_token"],
            r"^v2\.\d+\.exp_[0-9a-f]{32}\.[0-9a-f]{64}\.[0-9a-f]{64}$",
        )
        self.assertEqual(response["confirmation"]["amount_cop"], 45000)
        self.assertEqual(response["confirmation"]["source_name"], "Cuenta Ahorros Banco Bogotá")
        helper.assert_called_once()
        self.assertTrue(helper.call_args.kwargs["dry_run"])
        self.assertEqual(helper.call_args.kwargs["mode"], "plan")

    def test_simple_expense_commits_only_after_valid_signed_confirmation(self):
        cfg = self.write_config("megan.expense_capture")
        initial_payload = self.expense_payload(live=True)
        operation_id = srv.make_expense_operation_id(initial_payload, cfg.secret)
        preflight = self.expense_result(operation_id)
        absent = self.expense_result(operation_id, decision="absent")
        committed = self.expense_result(operation_id, decision="committed")

        with mock.patch.object(
            srv,
            "run_expense_helper",
            side_effect=[preflight, absent, committed],
        ) as helper:
            preview = srv.execute_capture(initial_payload, cfg)
            payload = self.expense_payload(
                live=True,
                request_id="unit-expense-confirmed",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=operation_id,
            )
            response = srv.execute_capture(payload, cfg)

        self.assertEqual(response["status"], "committed")
        self.assertTrue(response["write_committed"])
        self.assertTrue(response["plan"]["would_write"])
        self.assertEqual(response["external_ids"]["firefly_journal_ids"], ["journal-9001"])
        self.assertEqual(response["external_ids"]["firefly_external_id"], f"hermes-watch:{operation_id}")
        self.assertEqual(helper.call_count, 3)
        self.assertEqual([call.kwargs["mode"] for call in helper.call_args_list], ["plan", "reconcile", "commit"])
        self.assertEqual(helper.call_args_list[2].kwargs["expected_plan_hash"], "a" * 64)
        self.assertEqual(helper.call_args_list[2].kwargs["confirmed_plan"], preflight["plan"])
        self.assertTrue(all(call.kwargs["operation_id"] == operation_id for call in helper.call_args_list))
        operation = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["status"], "committed")

    def test_expense_confirmation_token_expires_and_is_bound_to_text_device_and_plan(self):
        payload = self.expense_payload(live=True, text="45 mil almuerzo")
        payload["source"] = {"device_id": "watch-a"}
        operation_id = "exp_cccccccccccccccccccccccccccccccc"
        token = srv.make_expense_confirmation_token(
            payload,
            operation_id,
            "c" * 64,
            b"secret",
            now=1000,
        )
        confirmed = json.loads(json.dumps(payload))
        confirmed["context"]["user_confirmation"] = True
        confirmed["context"]["confirmation_token"] = token
        confirmed["context"]["operation_id"] = operation_id
        self.assertTrue(srv.expense_confirmation_is_valid(confirmed, operation_id, "c" * 64, b"secret", now=1200))
        self.assertFalse(
            srv.expense_confirmation_is_valid(
                confirmed,
                operation_id,
                "c" * 64,
                b"secret",
                now=1000 + srv.EXPENSE_CONFIRMATION_TTL_SECONDS + 1,
            )
        )
        self.assertFalse(srv.expense_confirmation_is_valid(confirmed, operation_id, "d" * 64, b"secret", now=1200))

        changed_text = json.loads(json.dumps(confirmed))
        changed_text["capture"]["text"] = "80 mil mercado"
        self.assertFalse(srv.expense_confirmation_is_valid(changed_text, operation_id, "c" * 64, b"secret", now=1200))
        changed_device = json.loads(json.dumps(confirmed))
        changed_device["source"]["device_id"] = "watch-b"
        self.assertFalse(srv.expense_confirmation_is_valid(changed_device, operation_id, "c" * 64, b"secret", now=1200))

        changed_operation = json.loads(json.dumps(confirmed))
        changed_operation["context"]["operation_id"] = "exp_dddddddddddddddddddddddddddddddd"
        self.assertFalse(
            srv.expense_confirmation_is_valid(
                changed_operation,
                "exp_dddddddddddddddddddddddddddddddd",
                "c" * 64,
                b"secret",
                now=1200,
            )
        )

    def test_expense_preview_then_confirm_uses_one_operation_across_delivery_ids(self):
        cfg = self.write_config("megan.expense_capture")
        helper_calls = []

        def helper(
            _payload,
            _cfg,
            *,
            dry_run,
            operation_id,
            expected_plan_hash=None,
            confirmed_plan=None,
            mode=None,
        ):
            helper_calls.append((mode, operation_id, expected_plan_hash))
            plan_hash = hashlib.sha256(operation_id.encode()).hexdigest()
            if mode == "plan":
                return self.expense_result(operation_id, plan_hash=plan_hash)
            self.assertEqual(expected_plan_hash, plan_hash)
            self.assertEqual(confirmed_plan, self.expense_plan(operation_id))
            if mode == "reconcile":
                return self.expense_result(operation_id, plan_hash=plan_hash, decision="absent")
            self.assertEqual(mode, "commit")
            return self.expense_result(operation_id, plan_hash=plan_hash, decision="committed")

        initial = self.expense_payload(live=True, request_id="preview-expense-request")
        with mock.patch.object(srv, "run_expense_helper", side_effect=helper):
            preview = srv.execute_capture(initial, cfg)
            confirmed = self.expense_payload(
                live=True,
                request_id="confirmed-expense-request",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=preview["operation_id"],
            )
            committed = srv.execute_capture(confirmed, cfg)

        self.assertEqual(preview["status"], "needs_confirmation")
        self.assertEqual(committed["status"], "committed")
        self.assertEqual(committed["operation_id"], preview["operation_id"])
        self.assertEqual(
            committed["external_ids"]["expense_operation_id"],
            preview["operation_id"],
        )
        self.assertEqual([call[0] for call in helper_calls], ["plan", "reconcile", "commit"])
        self.assertEqual(len({call[1] for call in helper_calls}), 1)

    def test_parallel_expense_confirmations_claim_single_commit(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(live=True, request_id="parallel-preview-request")
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        barrier = threading.Barrier(2)
        lock = threading.Lock()
        modes = []
        reconcile_calls = 0
        commit_calls = 0

        def helper(
            _payload,
            _cfg,
            *,
            dry_run,
            operation_id,
            expected_plan_hash=None,
            confirmed_plan=None,
            mode=None,
        ):
            nonlocal reconcile_calls, commit_calls
            with lock:
                modes.append(mode)
            if mode == "plan":
                return self.expense_result(operation_id)
            if mode == "reconcile":
                with lock:
                    reconcile_calls += 1
                    current_reconciliation = reconcile_calls
                if current_reconciliation <= 2:
                    barrier.wait(timeout=3)
                return self.expense_result(operation_id, decision="absent")
            self.assertEqual(mode, "commit")
            with lock:
                commit_calls += 1
            # Keep the winning commit in flight long enough for the losing
            # delivery to observe the durable writing claim.
            time.sleep(0.15)
            return self.expense_result(operation_id, decision="committed")

        with mock.patch.object(srv, "run_expense_helper", side_effect=helper):
            preview = srv.execute_capture(initial, cfg)
            confirmations = [
                self.expense_payload(
                    live=True,
                    request_id=f"parallel-confirm-request-{index}",
                    confirmed=True,
                    confirmation_token=preview["confirmation_token"],
                    operation_id=operation_id,
                )
                for index in range(2)
            ]
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda payload: srv.execute_capture(payload, cfg), confirmations))

        self.assertEqual(commit_calls, 1)
        self.assertEqual(modes.count("commit"), 1)
        self.assertTrue(all(response["status"] in {"committed", "duplicate", "rejected"} for response in responses))
        operation = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["status"], "committed")

    def test_committed_expense_operation_cannot_regress_to_uncertain(self):
        cfg = self.write_config("megan.expense_capture")
        payload = self.expense_payload(live=True, request_id="monotonic-operation-request")
        operation_id = srv.make_expense_operation_id(payload, cfg.secret)
        srv.persist_expense_preview(
            cfg,
            operation_id,
            srv.expense_capture_fingerprint(payload, cfg.secret),
            "a" * 64,
            self.expense_plan(operation_id),
        )
        self.assertTrue(srv.claim_expense_operation_for_write(cfg, operation_id))
        srv.set_expense_operation_status(
            cfg,
            operation_id,
            "committed",
            {"status": "committed", "operation_id": operation_id},
        )

        self.assertFalse(srv.mark_expense_operation_uncertain_if_uncommitted(cfg, operation_id))
        current = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current["status"], "committed")
        self.assertEqual(json.loads(current["response_json"])["status"], "committed")

        for target in ("preview", "writing", "uncertain"):
            with self.subTest(target=target):
                with self.assertRaisesRegex(srv.WriteExecutionError, "expense_operation_invalid_transition"):
                    srv.set_expense_operation_status(cfg, operation_id, target)
        current = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current["status"], "committed")

    def test_cached_expense_preview_preserves_confirmation_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.write_config(
                "megan.expense_capture",
                ledger_path=Path(directory) / "ledger.sqlite",
            )
            payload = self.expense_payload(live=True, request_id="cached-expense-preview")
            operation_id = srv.make_expense_operation_id(payload, cfg.secret)
            preflight = self.expense_result(operation_id, plan_hash="e" * 64)
            with mock.patch.object(srv, "run_expense_helper", return_value=preflight) as helper:
                responses = self.signed_posts(payload, cfg)

        first = responses[0][1]
        replay = responses[1][1]
        self.assertEqual(first["status"], "needs_confirmation")
        self.assertEqual(replay["status"], "needs_confirmation")
        self.assertTrue(replay["duplicate"])
        self.assertEqual(replay["operation_id"], first["operation_id"])
        self.assertEqual(replay["confirmation_token"], first["confirmation_token"])
        helper.assert_called_once()

    def test_cancelled_expense_cached_preview_replay_never_reexposes_token(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.write_config(
                "megan.expense_capture",
                ledger_path=Path(directory) / "ledger.sqlite",
            )
            initial = self.expense_payload(
                live=True,
                request_id="cached-preview-cancelled-later",
            )
            operation_id = srv.make_expense_operation_id(initial, cfg.secret)
            cancellation = self.expense_payload(
                live=True,
                request_id="cached-preview-cancellation",
                cancelling=True,
                operation_id=operation_id,
            )
            with mock.patch.object(
                srv,
                "run_expense_helper",
                return_value=self.expense_result(operation_id),
            ) as helper:
                responses = self.signed_payloads(
                    [initial, cancellation, initial],
                    cfg,
                )

        preview = responses[0][1]
        cancelled = responses[1][1]
        replay = responses[2][1]
        self.assertEqual(preview["status"], "needs_confirmation")
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(replay["status"], "rejected")
        self.assertTrue(replay["duplicate"])
        self.assertFalse(replay["write_committed"])
        self.assertFalse(replay["plan"]["would_write"])
        self.assertNotIn("confirmation_token", replay)
        self.assertNotIn("confirmation", replay)
        self.assertEqual(replay["operation_id"], operation_id)
        helper.assert_called_once()

    def test_unresolved_preview_reuses_operation_id_across_new_delivery_ids(self):
        cfg = self.write_config("megan.expense_capture")
        first_payload = self.expense_payload(live=True, request_id="preview-delivery-one")
        operation_id = srv.make_expense_operation_id(first_payload, cfg.secret)
        preflight = self.expense_result(operation_id)
        with mock.patch.object(srv, "run_expense_helper", return_value=preflight) as helper:
            first = srv.execute_capture(first_payload, cfg)
            second_payload = self.expense_payload(live=True, request_id="preview-delivery-two")
            second = srv.execute_capture(second_payload, cfg)

        self.assertEqual(first["operation_id"], operation_id)
        self.assertEqual(second["operation_id"], operation_id)
        self.assertEqual(second["status"], "needs_confirmation")
        helper.assert_called_once()

    def test_expense_preview_cancellation_is_terminal_and_old_token_cannot_commit(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(live=True, request_id="cancel-preview-request")
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        preflight = self.expense_result(operation_id)
        with mock.patch.object(srv, "run_expense_helper", return_value=preflight) as helper:
            preview = srv.execute_capture(initial, cfg)
            cancellation = self.expense_payload(
                live=True,
                request_id="cancel-operation-request",
                cancelling=True,
                operation_id=operation_id,
            )
            self.assertTrue(srv.write_is_authorized(cancellation, cfg))
            cancelled = srv.execute_capture(cancellation, cfg)

            old_confirmation = self.expense_payload(
                live=True,
                request_id="cancelled-old-confirmation",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=operation_id,
            )
            rejected = srv.execute_capture(old_confirmation, cfg)

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertFalse(cancelled["write_committed"])
        self.assertFalse(cancelled["plan"]["would_write"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse(rejected["write_committed"])
        helper.assert_called_once()
        operation = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["status"], "cancelled")

        replacement = self.expense_payload(
            live=True,
            request_id="replacement-after-cancel",
        )
        replacement_id = srv.make_expense_operation_id(replacement, cfg.secret)
        with mock.patch.object(
            srv,
            "run_expense_helper",
            return_value=self.expense_result(replacement_id),
        ):
            replacement_preview = srv.execute_capture(replacement, cfg)
        self.assertNotEqual(replacement_preview["operation_id"], operation_id)

    def test_expense_cancellation_is_honored_when_write_gate_is_disabled(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(
            live=True,
            request_id="gate-off-cancellation-preview",
        )
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        with mock.patch.object(
            srv,
            "run_expense_helper",
            return_value=self.expense_result(operation_id),
        ) as helper:
            preview = srv.execute_capture(initial, cfg)
            disabled_cfg = replace(
                cfg,
                write_enabled=False,
                write_domains=frozenset(),
            )
            cancellation = self.expense_payload(
                live=True,
                request_id="gate-off-cancellation-request",
                cancelling=True,
                operation_id=operation_id,
            )
            self.assertFalse(srv.write_is_authorized(cancellation, disabled_cfg))
            cancelled = srv.execute_capture(cancellation, disabled_cfg)

            old_confirmation = self.expense_payload(
                live=True,
                request_id="gate-off-old-confirmation",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=operation_id,
            )
            rejected = srv.execute_capture(old_confirmation, cfg)

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["operation_id"], operation_id)
        self.assertFalse(cancelled["write_committed"])
        self.assertFalse(cancelled["plan"]["would_write"])
        self.assertEqual(rejected["status"], "rejected")
        self.assertFalse(rejected["write_committed"])
        helper.assert_called_once()
        operation = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["status"], "cancelled")

    def test_expense_cancellation_is_durable_and_idempotent_across_delivery_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.sqlite"
            cfg = self.write_config(
                "megan.expense_capture",
                ledger_path=ledger_path,
            )
            initial = self.expense_payload(
                live=True,
                request_id="durable-cancel-preview",
            )
            operation_id = srv.make_expense_operation_id(initial, cfg.secret)
            with mock.patch.object(
                srv,
                "run_expense_helper",
                return_value=self.expense_result(operation_id),
            ) as helper:
                preview = srv.execute_capture(initial, cfg)
                first = srv.execute_capture(
                    self.expense_payload(
                        live=True,
                        request_id="durable-cancel-delivery-one",
                        cancelling=True,
                        operation_id=operation_id,
                    ),
                    cfg,
                )

                # A fresh Config instance models a process restart while reusing
                # the same durable SQLite operation ledger.
                restarted_cfg = replace(cfg, ledger_path=ledger_path)
                replay = srv.execute_capture(
                    self.expense_payload(
                        live=True,
                        request_id="durable-cancel-delivery-two",
                        cancelling=True,
                        operation_id=operation_id,
                    ),
                    restarted_cfg,
                )

            self.assertEqual(preview["status"], "needs_confirmation")
            self.assertEqual(first["status"], "cancelled")
            self.assertFalse(first["duplicate"])
            self.assertEqual(replay["status"], "cancelled")
            self.assertTrue(replay["duplicate"])
            self.assertEqual(replay["operation_id"], operation_id)
            self.assertFalse(replay["write_committed"])
            self.assertFalse(replay["plan"]["would_write"])
            helper.assert_called_once()

            persisted = srv.load_expense_operation(restarted_cfg, operation_id)
            self.assertIsNotNone(persisted)
            assert persisted is not None
            self.assertEqual(persisted["status"], "cancelled")

    def test_cancelled_expense_rejects_stale_and_replayed_confirmation_token(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(
            live=True,
            request_id="stale-token-preview",
        )
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        with mock.patch.object(
            srv,
            "run_expense_helper",
            return_value=self.expense_result(operation_id),
        ) as helper, mock.patch.object(srv.time, "time", return_value=1_000):
            preview = srv.execute_capture(initial, cfg)
            cancelled = srv.execute_capture(
                self.expense_payload(
                    live=True,
                    request_id="stale-token-cancel",
                    cancelling=True,
                    operation_id=operation_id,
                ),
                cfg,
            )

        rejected_responses = []
        for index, now in enumerate(
            (1_001, 1_002, 1_000 + srv.EXPENSE_CONFIRMATION_TTL_SECONDS + 1)
        ):
            with mock.patch.object(srv.time, "time", return_value=now):
                rejected_responses.append(
                    srv.execute_capture(
                        self.expense_payload(
                            live=True,
                            request_id=f"stale-token-replay-{index}",
                            confirmed=True,
                            confirmation_token=preview["confirmation_token"],
                            operation_id=operation_id,
                        ),
                        cfg,
                    )
                )

        self.assertEqual(cancelled["status"], "cancelled")
        self.assertTrue(
            all(response["status"] == "rejected" for response in rejected_responses)
        )
        self.assertTrue(
            all(not response["write_committed"] for response in rejected_responses)
        )
        self.assertTrue(
            all(not response["plan"]["would_write"] for response in rejected_responses)
        )
        helper.assert_called_once()
        persisted = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted["status"], "cancelled")

    def test_stale_unconfirmed_preview_expires_before_equivalent_redictation(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(live=True, request_id="stale-preview-request")
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        with mock.patch.object(
            srv,
            "run_expense_helper",
            return_value=self.expense_result(operation_id),
        ):
            srv.execute_capture(initial, cfg)
        with sqlite3.connect(cfg.ledger_path) as conn:
            conn.execute(
                "UPDATE expense_operation_ledger SET updated_at = ? WHERE operation_id = ?",
                ("2000-01-01T00:00:00+00:00", operation_id),
            )
            conn.commit()

        replacement = self.expense_payload(
            live=True,
            request_id="stale-preview-replacement",
        )
        replacement_id = srv.make_expense_operation_id(replacement, cfg.secret)
        with mock.patch.object(
            srv,
            "run_expense_helper",
            return_value=self.expense_result(replacement_id),
        ) as helper:
            preview = srv.execute_capture(replacement, cfg)

        self.assertEqual(preview["operation_id"], replacement_id)
        self.assertNotEqual(preview["operation_id"], operation_id)
        helper.assert_called_once()
        expired = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(expired)
        assert expired is not None
        self.assertEqual(expired["status"], "expired")

    def test_cancellation_compare_and_set_never_regresses_nonpreview_states(self):
        cfg = self.write_config("megan.expense_capture")
        for index, target in enumerate(("writing", "uncertain", "committed")):
            with self.subTest(target=target):
                payload = self.expense_payload(
                    live=True,
                    text=f"{45 + index} mil prueba cancelación",
                    request_id=f"nonpreview-cancel-{index}",
                )
                operation_id = srv.make_expense_operation_id(payload, cfg.secret)
                srv.persist_expense_preview(
                    cfg,
                    operation_id,
                    srv.expense_capture_fingerprint(payload, cfg.secret),
                    "a" * 64,
                    self.expense_plan(operation_id),
                )
                self.assertTrue(srv.claim_expense_operation_for_write(cfg, operation_id))
                if target == "uncertain":
                    self.assertTrue(
                        srv.mark_expense_operation_uncertain_if_uncommitted(cfg, operation_id)
                    )
                elif target == "committed":
                    srv.set_expense_operation_status(
                        cfg,
                        operation_id,
                        "committed",
                        {"status": "committed", "operation_id": operation_id},
                    )

                self.assertFalse(
                    srv.cancel_expense_operation_if_preview(cfg, operation_id)
                )
                current = srv.load_expense_operation(cfg, operation_id)
                self.assertIsNotNone(current)
                assert current is not None
                self.assertEqual(current["status"], target)

    def test_parallel_expense_previews_reuse_one_active_operation(self):
        cfg = self.write_config("megan.expense_capture")
        barrier = threading.Barrier(2)

        def helper(
            _payload,
            _cfg,
            *,
            dry_run,
            operation_id,
            expected_plan_hash=None,
            confirmed_plan=None,
            mode=None,
        ):
            self.assertEqual(mode, "plan")
            barrier.wait(timeout=3)
            return self.expense_result(
                operation_id,
                plan_hash=hashlib.sha256(operation_id.encode()).hexdigest(),
            )

        payloads = [
            self.expense_payload(live=True, request_id=f"parallel-preview-delivery-{index}")
            for index in range(2)
        ]
        with mock.patch.object(srv, "run_expense_helper", side_effect=helper):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                responses = list(executor.map(lambda payload: srv.execute_capture(payload, cfg), payloads))

        self.assertTrue(all(response["status"] == "needs_confirmation" for response in responses))
        self.assertEqual(len({response["operation_id"] for response in responses}), 1)
        fingerprint = srv.expense_capture_fingerprint(payloads[0], cfg.secret)
        with sqlite3.connect(cfg.ledger_path) as conn:
            rows = conn.execute(
                "SELECT operation_id FROM expense_operation_ledger WHERE capture_fingerprint = ? AND status IN ('preview', 'writing', 'uncertain')",
                (fingerprint,),
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_expired_confirmation_reconciles_then_refreshes_same_operation(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(live=True, request_id="expiry-preview-request")
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        preflight = self.expense_result(operation_id)
        absent = self.expense_result(operation_id, decision="absent")
        with mock.patch.object(srv, "run_expense_helper", side_effect=[preflight, absent]) as helper:
            with mock.patch.object(srv.time, "time", return_value=1000):
                preview = srv.execute_capture(initial, cfg)
            confirmed = self.expense_payload(
                live=True,
                request_id="expiry-confirm-request",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=operation_id,
            )
            with mock.patch.object(
                srv.time,
                "time",
                return_value=1000 + srv.EXPENSE_CONFIRMATION_TTL_SECONDS + 1,
            ):
                refreshed = srv.execute_capture(confirmed, cfg)

        self.assertEqual(refreshed["status"], "needs_confirmation")
        self.assertEqual(refreshed["operation_id"], operation_id)
        self.assertNotEqual(refreshed["confirmation_token"], preview["confirmation_token"])
        self.assertEqual([call.kwargs["mode"] for call in helper.call_args_list], ["plan", "reconcile"])

    def test_refreshed_confirmation_renews_preview_lease_for_cached_replay(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(live=True, request_id="lease-preview-request")
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        with mock.patch.object(srv, "run_expense_helper", return_value=self.expense_result(operation_id)):
            with mock.patch.object(srv.time, "time", return_value=1000):
                preview = srv.execute_capture(initial, cfg)

        stale_updated_at = "2000-01-01T00:00:00+00:00"
        with sqlite3.connect(cfg.ledger_path) as conn:
            conn.execute(
                "UPDATE expense_operation_ledger SET updated_at = ? WHERE operation_id = ?",
                (stale_updated_at, operation_id),
            )
            conn.commit()

        confirmed = self.expense_payload(
            live=True,
            request_id="lease-confirm-request",
            confirmed=True,
            confirmation_token=preview["confirmation_token"],
            operation_id=operation_id,
        )
        with mock.patch.object(
            srv,
            "run_expense_helper",
            return_value=self.expense_result(operation_id, decision="absent"),
        ) as helper:
            with mock.patch.object(
                srv.time,
                "time",
                return_value=1000 + srv.EXPENSE_CONFIRMATION_TTL_SECONDS + 1,
            ):
                (_, first), (_, replay) = self.signed_posts(confirmed, cfg, count=2)

        with sqlite3.connect(cfg.ledger_path) as conn:
            status, updated_at = conn.execute(
                "SELECT status, updated_at FROM expense_operation_ledger WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
        self.assertEqual(first["status"], "needs_confirmation")
        self.assertEqual(replay["status"], "needs_confirmation")
        self.assertEqual(replay["confirmation_token"], first["confirmation_token"])
        self.assertEqual(status, "preview")
        self.assertNotEqual(updated_at, stale_updated_at)
        helper.assert_called_once()

    def test_uncertain_expense_retry_is_reconciliation_only_and_never_second_commit(self):
        cfg = self.write_config("megan.expense_capture")
        initial = self.expense_payload(live=True, request_id="uncertain-preview-request")
        operation_id = srv.make_expense_operation_id(initial, cfg.secret)
        modes = []
        commit_statuses = []

        def helper(
            _payload,
            _cfg,
            *,
            dry_run,
            operation_id,
            expected_plan_hash=None,
            confirmed_plan=None,
            mode=None,
        ):
            modes.append(mode)
            if mode == "plan":
                return self.expense_result(operation_id)
            if mode == "reconcile":
                return self.expense_result(operation_id, decision="absent")
            self.assertEqual(mode, "commit")
            operation = srv.load_expense_operation(cfg, operation_id)
            self.assertIsNotNone(operation)
            assert operation is not None
            commit_statuses.append(operation["status"])
            uncertain = self.expense_result(operation_id, decision="uncertain")
            uncertain.update(post_attempted=True, error="firefly_write_failed")
            return uncertain

        with mock.patch.object(srv, "run_expense_helper", side_effect=helper):
            preview = srv.execute_capture(initial, cfg)
            confirmed = self.expense_payload(
                live=True,
                request_id="uncertain-confirm-request",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=operation_id,
            )
            first = srv.execute_capture(confirmed, cfg)
            retry = self.expense_payload(
                live=True,
                request_id="uncertain-retry-request",
                confirmed=True,
                confirmation_token=preview["confirmation_token"],
                operation_id=operation_id,
            )
            second = srv.execute_capture(retry, cfg)

        self.assertEqual(first["status"], "rejected")
        self.assertEqual(second["status"], "rejected")
        self.assertEqual(commit_statuses, ["writing"])
        self.assertEqual(modes, ["plan", "reconcile", "commit", "reconcile"])
        operation = srv.load_expense_operation(cfg, operation_id)
        self.assertIsNotNone(operation)
        assert operation is not None
        self.assertEqual(operation["status"], "uncertain")

    def test_expense_helper_must_prove_operation_and_external_id(self):
        cfg = self.write_config("megan.expense_capture")
        payload = self.expense_payload(live=True, request_id="missing-helper-proof")
        operation_id = srv.make_expense_operation_id(payload, cfg.secret)
        unproven = self.expense_result(operation_id)
        unproven.pop("external_id")
        with mock.patch.object(srv, "run_expense_helper", return_value=unproven):
            with self.assertRaisesRegex(srv.WriteExecutionError, "expense_helper_operation_mismatch"):
                srv.execute_capture(payload, cfg)
        self.assertIsNone(srv.load_expense_operation(cfg, operation_id))

    def test_expense_preflight_rejects_out_of_range_amount(self):
        cfg = self.write_config("megan.expense_capture")
        payload = self.expense_payload(live=True, request_id="oversized-expense-preview")
        operation_id = srv.make_expense_operation_id(payload, cfg.secret)
        oversized = self.expense_result(operation_id)
        oversized["plan"]["amount_cop"] = srv.EXPENSE_MAX_AMOUNT_COP + 1

        with mock.patch.object(srv, "run_expense_helper", return_value=oversized):
            with self.assertRaisesRegex(srv.WriteExecutionError, "expense_helper_incomplete_plan"):
                srv.execute_capture(payload, cfg)
        self.assertIsNone(srv.load_expense_operation(cfg, operation_id))

    def test_expense_confirmation_never_invokes_live_helper(self):
        payload = self.expense_payload(live=True, text="45 mil objeto misterioso")
        cfg = self.write_config("megan.expense_capture")
        preflight = {
            "ok": True,
            "intent": "expense",
            "decision": "needs_confirmation",
            "transaction_kind": "cash_expense",
            "question": "¿Qué categoría y presupuesto existentes debo usar?",
            "error": "unknown_category_or_budget",
        }
        with mock.patch.object(srv, "run_expense_helper", return_value=preflight) as helper:
            response = srv.execute_capture(payload, cfg)
        self.assertEqual(response["status"], "needs_input")
        self.assertFalse(response["write_committed"])
        self.assertFalse(response["plan"]["would_write"])
        helper.assert_called_once()

    def test_megan_ambiguous_card_needs_confirmation(self):
        payload = {
            "event_type": "mobile_capture.v1",
            "schema_version": 1,
            "request_id": "unit-megan-2",
            "route": {"agent": "megan", "intent": "expense", "domain": "megan.expense_capture"},
            "capture": {"text": "gasto tarjeta 80 mil ropa"},
            "context": {"dry_run": True, "allow_write": False},
        }
        plan = srv.plan_capture(payload)
        self.assertEqual(plan["status"], "needs_confirmation")
        self.assertIn("TC1", plan["question"])

    def test_aura_due_parses_spanish_pm(self):
        base = datetime.fromisoformat("2026-07-06T19:00:00-05:00").astimezone(ZoneInfo("America/Bogota"))
        due = srv.parse_spanish_due("recuérdame llamar a mamá mañana a las dos P.M.", base)
        self.assertIsNotNone(due)
        self.assertEqual(due["start"], "2026-07-07T14:00:00-05:00")

    def test_grocery_write_requires_client_and_server_gates(self):
        cfg = self.write_config("aura.grocery_capture")
        self.assertFalse(srv.write_is_authorized(self.grocery_payload(live=False), cfg))
        self.assertTrue(srv.write_is_authorized(self.grocery_payload(live=True), cfg))
        spoofed_route = self.grocery_payload(live=True)
        spoofed_route["route"].update(agent="megan", intent="expense")
        self.assertFalse(srv.write_is_authorized(spoofed_route, cfg))
        self.assertFalse(
            srv.write_is_authorized(
                self.grocery_payload(live=True),
                self.write_config("aura.grocery_capture", enabled=False),
            )
        )

    def test_reminder_write_requires_every_exact_client_and_server_gate(self):
        cfg = self.write_config("aura.reminder_capture", "aura.grocery_capture")
        self.assertTrue(srv.write_is_authorized(self.reminder_payload(live=True), cfg))
        self.assertTrue(srv.write_is_authorized(self.grocery_payload(live=True), cfg))

        self.assertFalse(srv.write_is_authorized(self.reminder_payload(live=False), cfg))
        self.assertFalse(
            srv.write_is_authorized(
                self.reminder_payload(live=True),
                self.write_config("aura.grocery_capture"),
            )
        )
        self.assertFalse(
            srv.write_is_authorized(
                self.reminder_payload(live=True),
                self.write_config("aura.reminder_capture", enabled=False),
            )
        )

        route_mismatches = {
            "agent": {"agent": "megan"},
            "intent": {"intent": "reminder_capture"},
            "domain": {"domain": "aura.grocery_capture"},
            "missing_domain": {"domain": None},
            "case_variant": {"agent": "Aura"},
        }
        for label, route_change in route_mismatches.items():
            with self.subTest(label=label):
                payload = self.reminder_payload(live=True)
                payload["route"].update(route_change)
                self.assertFalse(srv.write_is_authorized(payload, cfg))

        broad_client_request = self.reminder_payload(live=True)
        broad_client_request["context"]["allow_firefly_write"] = True
        self.assertFalse(srv.write_is_authorized(broad_client_request, cfg))

        for payload in [
            {
                **self.reminder_payload(live=True),
                "route": {"agent": "megan", "intent": "expense", "domain": "megan.expense_capture"},
            },
            {
                **self.reminder_payload(live=True),
                "route": {"agent": "argos", "intent": "general_capture", "domain": "argos.general_capture"},
            },
        ]:
            self.assertFalse(srv.write_is_authorized(payload, cfg))

    def test_reminder_authority_gates_never_invoke_ai_todoist_or_legacy_helper(self):
        cfg = replace(
            self.write_config("aura.reminder_capture"),
            reminder_openai_key_file=Path("synthetic-openai-key"),
            reminder_todoist_token_file=Path("synthetic-todoist-token"),
        )
        cases = []
        for label, changes in (
            ("dry_run", {"dry_run": True}),
            ("no_allow_write", {"allow_write": False}),
            ("financial_capability", {"allow_firefly_write": True}),
            ("financial_confirmation", {"user_confirmation": True}),
            ("cancellation", {"user_cancellation": True}),
            ("invalid_cancellation_type", {"user_cancellation": "false"}),
            ("confirmation_token", {"confirmation_token": "synthetic-token"}),
            ("operation_id", {"operation_id": "exp_" + "1" * 32}),
        ):
            payload = self.reminder_payload(live=True)
            payload["context"].update(changes)
            cases.append((label, payload, cfg))
        for field, value in (
            ("agent", "Aura"), ("agent", "megan"),
            ("intent", "reminder_capture"), ("intent", "grocery"),
            ("domain", "aura.grocery_capture"), ("domain", None),
        ):
            payload = self.reminder_payload(live=True)
            payload["route"][field] = value
            cases.append((f"route_{field}_{value}", payload, cfg))
        cases.extend([
            ("server_disabled", self.reminder_payload(live=True), replace(cfg, write_enabled=False)),
            ("domain_not_allowlisted", self.reminder_payload(live=True), replace(cfg, write_domains=frozenset({"aura.grocery_capture"}))),
        ])
        with mock.patch.object(srv.reminder_capture.reminder_ai, "interpret") as ai, \
             mock.patch.object(srv.reminder_capture, "TodoistGateway") as todoist, \
             mock.patch.object(srv, "run_aura_helper") as legacy:
            for label, payload, config in cases:
                with self.subTest(case=label):
                    self.assertFalse(srv.write_is_authorized(payload, config))
                    response = srv.execute_capture(payload, config)
                    self.assertTrue(response["dry_run"])
                    self.assertFalse(response["write_committed"])
            ai.assert_not_called()
            todoist.assert_not_called()
            legacy.assert_not_called()

    def test_grocery_helper_dry_run_preflight_uses_side_effect_free_flag(self):
        payload = self.grocery_payload(live=True)
        cfg = self.write_config("aura.grocery_capture")
        completed = mock.Mock(
            returncode=0,
            stdout=json.dumps({"ok": True, "intent": "grocery", "title": "Leche"}),
        )
        with mock.patch.object(srv.subprocess, "run", return_value=completed) as run:
            result = srv.run_aura_helper(payload, cfg, dry_run=True)
        self.assertEqual(result["intent"], "grocery")
        command = run.call_args.args[0]
        self.assertIn("--dry-run", command)
        self.assertIn("--no-notify", command)
        self.assertIn("--no-ai-title", command)
        helper_payload = json.loads(run.call_args.kwargs["input"])
        self.assertEqual(helper_payload["request_id"], payload["request_id"])
        self.assertEqual(helper_payload["text"], payload["capture"]["text"])

    def test_bare_grocery_item_commits_through_allowlisted_adapter(self):
        payload = self.grocery_payload(live=True, text="leche")
        with tempfile.TemporaryDirectory() as directory, grocery_environment(Path(directory)) as (helper_path, notion):
            cfg = self.write_config("aura.grocery_capture", ledger_path=Path(directory) / "ledger.sqlite")
            cfg = replace(cfg, aura_helper=helper_path)
            with mock.patch.object(srv, "run_aura_helper", side_effect=AssertionError("generic helper routing forbidden")):
                response = srv.execute_capture(payload, cfg)
        self.assertEqual(response["status"], "committed")
        self.assertFalse(response["dry_run"])
        self.assertTrue(response["write_committed"])
        self.assertTrue(response["plan"]["would_write"])
        self.assertEqual(response["request_id"], payload["request_id"])
        self.assertEqual(response["external_ids"]["notion_block_ids"], ["synthetic-created-1"])
        self.assertEqual(len(notion.writes), 1)

    def test_grocery_route_mismatch_does_not_execute_write(self):
        payload = self.grocery_payload(live=True, text="recuérdame llamar mañana")
        cfg = self.write_config("aura.grocery_capture")
        with mock.patch.object(
            srv,
            "run_aura_helper",
            return_value={"ok": True, "intent": "reminder", "interpreted_due": {"start": "2026-08-31T14:00:00-05:00", "timezone": "America/Bogota"}, "title": "Llamar"},
        ) as helper:
            response = srv.execute_capture(payload, cfg)

        self.assertEqual(response["status"], "rejected")
        self.assertFalse(response["dry_run"])
        self.assertFalse(response["write_committed"])
        self.assertFalse(response["plan"]["would_write"])
        self.assertEqual(response["gates_missing"], [])
        helper.assert_not_called()

    def test_reminder_missing_server_credentials_stays_dry_run_without_fallback(self):
        payload = self.reminder_payload(live=True)
        cfg = self.write_config("aura.reminder_capture")
        for openai_file, todoist_file in (
            (None, None), (Path("synthetic-openai-key"), None),
            (None, Path("synthetic-todoist-token")),
        ):
            with self.subTest(openai_file=openai_file, todoist_file=todoist_file), \
                 mock.patch.object(srv.reminder_capture.reminder_ai, "interpret") as ai, \
                 mock.patch.object(srv.reminder_capture, "TodoistGateway") as todoist, \
                 mock.patch.object(srv, "run_aura_helper") as legacy:
                config = replace(cfg, reminder_openai_key_file=openai_file,
                                 reminder_todoist_token_file=todoist_file)
                response = srv.execute_capture(payload, config)
                self.assertEqual(response["status"], "accepted")
                self.assertTrue(response["dry_run"])
                self.assertFalse(response["write_committed"])
                self.assertFalse(response["plan"]["would_write"])
                self.assertEqual(response["plan"]["side_effects"], ["todoist.task.plan_only"])
                self.assertTrue(response["gates_missing"])
                self.assertEqual(srv.ledger_status_for_response(payload, response), srv.LEDGER_WAITING_FOR_SERVER_GATE)
                ai.assert_not_called()
                todoist.assert_not_called()
                legacy.assert_not_called()

    def test_reminder_forwards_original_natural_capture_to_durable_adapter(self):
        payload = self.reminder_payload(
            live=True, text="La vacuna del perro el viernes a las nueve de la mañana",
        )
        cfg = replace(
            self.write_config("aura.reminder_capture"),
            reminder_openai_key_file=Path("synthetic-openai-key"),
            reminder_todoist_token_file=Path("synthetic-todoist-token"),
        )
        expected = {"status": "needs_input", "dry_run": False, "write_committed": False}
        with mock.patch.object(srv.reminder_capture, "execute", return_value=expected) as adapter, \
             mock.patch.object(srv, "run_aura_helper") as legacy:
            response = srv.execute_capture(payload, cfg)
        self.assertIs(response, expected)
        adapter.assert_called_once_with(payload, cfg, now=srv.reminder_dispatch_now)
        self.assertEqual(adapter.call_args.args[0]["capture"]["text"], payload["capture"]["text"])
        legacy.assert_not_called()

    def test_reminder_adapter_failure_never_falls_back_to_legacy_write(self):
        cfg = replace(
            self.write_config("aura.reminder_capture"),
            reminder_openai_key_file=Path("synthetic-openai-key"),
            reminder_todoist_token_file=Path("synthetic-todoist-token"),
        )
        with mock.patch.object(srv.reminder_capture, "execute", side_effect=srv.reminder_capture.ReminderRetryable("synthetic-failure")), \
             mock.patch.object(srv, "run_aura_helper") as legacy:
            with self.assertRaisesRegex(srv.WriteExecutionError, "reminder_pending_retry"):
                srv.execute_capture(self.reminder_payload(live=True), cfg)
        legacy.assert_not_called()

    def test_validate_wrong_event_is_ignored(self):
        valid, status, response = srv.validate_payload({"event_type": "other", "schema_version": 1}, "")
        self.assertFalse(valid)
        self.assertEqual(status, 200)
        self.assertEqual(response["status"], "ignored")

    def test_validate_requires_x_request_id(self):
        valid, status, response = srv.validate_payload(
            {"event_type": "mobile_capture.v1", "schema_version": 1, "request_id": "body-id", "capture": {"text": "45 mil en Uber"}},
            "",
        )
        self.assertFalse(valid)
        self.assertEqual(status, 400)
        self.assertEqual(response["error"], "missing X-Request-ID")

    def test_validate_rejects_header_body_request_id_mismatch(self):
        valid, status, response = srv.validate_payload(
            {"event_type": "mobile_capture.v1", "schema_version": 1, "request_id": "body-id", "capture": {"text": "45 mil en Uber"}},
            "header-id",
        )
        self.assertFalse(valid)
        self.assertEqual(status, 409)
        self.assertEqual(response["error"], "X-Request-ID mismatch")

    def test_validate_rejects_non_object_structured_fields(self):
        for field in ("route", "capture", "context", "source", "entities"):
            with self.subTest(field=field):
                payload = self.expense_payload(
                    live=True,
                    request_id=f"invalid-{field}-shape",
                )
                payload[field] = "not-an-object"
                valid, status, response = srv.validate_payload(
                    payload,
                    payload["request_id"],
                )
                self.assertFalse(valid)
                self.assertEqual(status, 400)
                self.assertEqual(response["status"], "rejected")
                self.assertEqual(response["error"], f"{field} must be object")

    def test_validate_rejects_schema_and_text_type_coercion(self):
        cases = []

        boolean_schema = self.expense_payload()
        boolean_schema["schema_version"] = True
        cases.append(("boolean_schema_version", boolean_schema, boolean_schema["request_id"]))

        numeric_request_id = self.expense_payload()
        numeric_request_id["request_id"] = 42
        cases.append(("numeric_request_id", numeric_request_id, "42"))

        object_capture_text = self.expense_payload()
        object_capture_text["capture"]["text"] = {"value": "45 mil almuerzo"}
        cases.append(("object_capture_text", object_capture_text, object_capture_text["request_id"]))

        for label, payload, header_request_id in cases:
            with self.subTest(case=label):
                valid, status, response = srv.validate_payload(payload, header_request_id)
                self.assertFalse(valid)
                self.assertEqual(status, 400)
                self.assertEqual(response["status"], "rejected")

    def test_live_request_gate_miss_does_not_poison_ledger_retry(self):
        secret = b"gate-rollout-test-secret"
        payload = self.reminder_payload(
            live=True,
            request_id="live-reminder-gate-rollout",
        )
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")

        class QuietHandler(srv.MobileCaptureHandler):
            def log_message(self, fmt, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            ledger_path = Path(directory) / "ledger.sqlite"
            disabled_cfg = srv.Config(
                secret=secret,
                ledger_path=ledger_path,
                host="127.0.0.1",
                port=0,
                write_enabled=True,
                write_domains=frozenset({"aura.grocery_capture"}),
                aura_helper=Path(__file__),
            )
            enabled_cfg = srv.Config(
                secret=secret,
                ledger_path=ledger_path,
                host="127.0.0.1",
                port=0,
                write_enabled=True,
                write_domains=frozenset({"aura.grocery_capture", "aura.reminder_capture"}),
                aura_helper=Path(__file__),
                reminder_openai_key_file=Path("synthetic-openai-key"),
                reminder_todoist_token_file=Path("synthetic-todoist-token"),
            )
            srv.init_db(ledger_path)
            server = srv.ThreadingHTTPServer((disabled_cfg.host, 0), QuietHandler)
            server.cfg = disabled_cfg  # type: ignore[attr-defined]
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            endpoint = f"http://127.0.0.1:{server.server_address[1]}/webhooks/mobile-capture-v1"

            def send(body=raw):
                timestamp = str(int(time.time()))
                signature = hmac.new(
                    secret,
                    timestamp.encode("utf-8") + b"." + body,
                    hashlib.sha256,
                ).hexdigest()
                req = request.Request(
                    endpoint,
                    data=body,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Timestamp": timestamp,
                        "X-Webhook-Signature-V2": signature,
                        "X-Request-ID": payload["request_id"],
                    },
                )
                with request.urlopen(req, timeout=5) as http_response:
                    return http_response.status, json.loads(http_response.read())

            changed_payload = json.loads(json.dumps(payload))
            changed_payload["capture"]["text"] = "recuérdame enviar un correo mañana"
            changed_raw = json.dumps(changed_payload, separators=(",", ":")).encode("utf-8")
            adapter_result = {
                "status": "committed", "dry_run": False, "write_committed": True,
                "request_id": payload["request_id"], "domain": "aura.reminder_capture",
                "display_message": "Guardado en Todoist · alertas pendientes de verificar",
                "external_ids": {"todoist_task_id": "synthetic-task-id"},
                "plan": {"would_write": True, "side_effects": ["todoist.task.write"]},
            }
            try:
                with mock.patch.object(srv.reminder_capture, "execute", return_value=adapter_result) as adapter:
                    first_status, first = send()
                    self.assertEqual(adapter.call_count, 0)
                    try:
                        send(changed_raw)
                    except error.HTTPError as http_error:
                        conflict_status = http_error.code
                        conflict = json.loads(http_error.read())
                    else:
                        self.fail("Changed payload reused a waiting request ID without conflict")
                    self.assertEqual(adapter.call_count, 0)
                    server.cfg = enabled_cfg  # type: ignore[attr-defined]
                    committed_status, committed = send()
                    duplicate_status, duplicate = send()
            finally:
                server.shutdown()
                server_thread.join()
                server.server_close()

        self.assertEqual(first_status, 200)
        self.assertEqual(first["status"], "accepted")
        self.assertTrue(first["dry_run"])
        self.assertFalse(first["write_committed"])
        self.assertEqual(conflict_status, 409)
        self.assertEqual(conflict["error"], "request_id_conflict")
        self.assertEqual(committed_status, 200)
        self.assertEqual(committed["status"], "committed")
        self.assertTrue(committed["write_committed"])
        self.assertEqual(duplicate_status, 200)
        self.assertEqual(duplicate["status"], "duplicate")
        self.assertTrue(duplicate["write_committed"])
        self.assertEqual(adapter.call_count, 1)

    def test_live_expense_replay_uses_cached_ledger_without_second_firefly_call(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = srv.Config(
                secret=b"live-expense-ledger-secret",
                ledger_path=Path(directory) / "ledger.sqlite",
                host="127.0.0.1",
                port=0,
                write_enabled=True,
                write_domains=frozenset({"megan.expense_capture"}),
                expense_helper=Path(__file__),
            )
            initial_payload = self.expense_payload(live=True, request_id="live-expense-preview-request")
            operation_id = srv.make_expense_operation_id(initial_payload, cfg.secret)
            preflight = self.expense_result(operation_id, plan_hash="b" * 64)
            absent = self.expense_result(operation_id, plan_hash="b" * 64, decision="absent")
            committed = self.expense_result(operation_id, plan_hash="b" * 64, decision="committed")
            with mock.patch.object(
                srv,
                "run_expense_helper",
                side_effect=[preflight, absent, committed],
            ) as helper:
                preview = srv.execute_capture(initial_payload, cfg)
                payload = self.expense_payload(
                    live=True,
                    request_id="live-expense-ledger-request",
                    confirmed=True,
                    confirmation_token=preview["confirmation_token"],
                    operation_id=operation_id,
                )
                responses = self.signed_posts(payload, cfg)

        self.assertEqual(responses[0][1]["status"], "committed")
        self.assertEqual(responses[1][1]["status"], "duplicate")
        self.assertEqual(responses[1][1]["external_ids"]["firefly_journal_ids"], ["journal-9001"])
        self.assertEqual(helper.call_count, 3)
        self.assertEqual([call.kwargs["mode"] for call in helper.call_args_list], ["plan", "reconcile", "commit"])

    def test_concurrent_duplicate_requests_return_cached_result(self):
        secret = b"concurrent-test-secret"
        payload = {
            "event_type": "mobile_capture.v1",
            "schema": "hermes.mobile_capture",
            "schema_version": 1,
            "request_id": "concurrent-request-id",
            "created_at": "2026-08-04T22:00:00Z",
            "source": {
                "platform": "watchOS",
                "surface": "watch_app",
                "app_version": "0.3.1",
                "os_version": "27.0",
                "device_id": "unit-device",
            },
            "route": {
                "agent": "argos",
                "intent": "general_capture",
                "domain": "argos.general_capture",
            },
            "capture": {"modality": "watch_dictation", "text": "concurrent test"},
            "context": {"dry_run": True, "allow_write": False, "allow_firefly_write": False},
        }
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        original_execute = srv.execute_capture

        def slow_execute(value, cfg, **kwargs):
            time.sleep(0.05)
            return original_execute(value, cfg, **kwargs)

        class QuietHandler(srv.MobileCaptureHandler):
            def log_message(self, fmt, *args):
                pass

        with tempfile.TemporaryDirectory() as directory:
            cfg = srv.Config(
                secret=secret,
                ledger_path=Path(directory) / "ledger.sqlite",
                host="127.0.0.1",
                port=0,
            )
            srv.init_db(cfg.ledger_path)
            self.assertEqual(cfg.ledger_path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(cfg.ledger_path.stat().st_mode & 0o777, 0o600)
            server = srv.ThreadingHTTPServer((cfg.host, 0), QuietHandler)
            server.cfg = cfg
            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            endpoint = f"http://127.0.0.1:{server.server_address[1]}/webhooks/mobile-capture-v1"
            worker_count = 8
            barrier = threading.Barrier(worker_count)

            def send(_):
                barrier.wait()
                timestamp = str(int(time.time()))
                signature = hmac.new(
                    secret,
                    timestamp.encode("utf-8") + b"." + raw,
                    hashlib.sha256,
                ).hexdigest()
                req = request.Request(
                    endpoint,
                    data=raw,
                    method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Timestamp": timestamp,
                        "X-Webhook-Signature-V2": signature,
                        "X-Request-ID": payload["request_id"],
                    },
                )
                with request.urlopen(req, timeout=5) as response:
                    return response.status, json.loads(response.read())["status"]

            try:
                with mock.patch.object(srv, "execute_capture", side_effect=slow_execute):
                    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
                        results = list(executor.map(send, range(worker_count)))
            finally:
                server.shutdown()
                server_thread.join()
                server.server_close()

        self.assertEqual([status for status, _ in results], [200] * worker_count)
        self.assertEqual(sum(result == (200, "accepted") for result in results), worker_count)


if __name__ == "__main__":
    unittest.main()
