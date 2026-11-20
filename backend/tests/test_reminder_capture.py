"""Offline synthetic acceptance tests for the durable Reminder path."""
from __future__ import annotations
import concurrent.futures
from dataclasses import replace
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest import mock
from urllib import request, error

import mobile_capture_staging_server as srv
import reminder_ai as ai
import reminder_capture as reminders
from fixtures.grocery_test_support import grocery_environment
from todoist_reminders import RetryableTodoistError, PermanentTodoistError

NOW = datetime.fromisoformat("2026-10-29T16:00:00-05:00")
IDENTITY = {"user_id": "synthetic-user", "inbox_project_id": "synthetic-inbox", "timezone": "America/Bogota"}


def payload(text="Recuérdame llamar mañana a las 2 pm", request_id="synthetic-reminder"):
    return {"event_type": "mobile_capture.v1", "schema_version": 1,
            "request_id": request_id, "created_at": "2026-10-29T15:00:45-05:00",
            "source": {"device_id": "synthetic-watch"},
            "route": {"agent": "aura", "intent": "reminder", "domain": "aura.reminder_capture"},
            "capture": {"text": text},
            "context": {"dry_run": False, "allow_write": True, "allow_firefly_write": False}}


def proposal(**overrides):
    result = {"action": "create_reminder", "title": "Llamar", "timezone": "America/Bogota",
              "schedule": {"mode": "absolute", "date": "2026-10-30", "time": "14:00", "offset_minutes": None},
              "question": None, "deadline": None, "recurrence": None, "custom_alert": None, "labels": []}
    result.update(overrides)
    return result


class FakeGateway:
    def __init__(self):
        self.remote = {}
        self.posts = []
        self.reconciliations = 0
        self.crash_after_post = False
        self.lose_ack = False
        self.fail_before_post = False
        self.before_post = None
        self.transports = []
        self.crash_after_claim = False

    def identity(self):
        return IDENTITY.copy()

    def label_catalog(self):
        return ("On_Phone", "5_min")

    def reconcile(self, plan, command_uuid, identity, external_id=None):
        self.reconciliations += 1
        row = self.remote.get(command_uuid)
        return {**row, "duplicate": True} if row else None

    def create_or_reconcile(self, plan, command_uuid, temp_id, identity, external_id=None, on_external_id=None):
        self.transports.append("sync_v1")
        return self._create(plan, command_uuid, temp_id, identity, on_external_id)

    def create_rest_or_reconcile(self, plan, command_uuid, identity, external_id=None, *, on_before_post, on_external_id=None):
        self.transports.append("rest_v1")
        on_before_post()
        if self.crash_after_claim:
            self.crash_after_claim = False
            raise SystemExit("synthetic process death before REST POST")
        return self._create(plan, command_uuid, None, identity, on_external_id)

    def _create(self, plan, command_uuid, temp_id, identity, on_external_id):
        if self.before_post:
            self.before_post(plan, command_uuid, temp_id, identity)
        self.posts.append((command_uuid, temp_id, json.loads(json.dumps(plan))))
        if self.fail_before_post:
            raise RetryableTodoistError("synthetic timeout")
        # Synthetic Todoist implements UUID idempotency independently of the
        # application, as the live Sync command API promises.
        self.remote.setdefault(command_uuid, {"task_id": "synthetic-task", "duplicate": False})
        if self.crash_after_post:
            self.crash_after_post = False
            raise SystemExit("synthetic process death after external save")
        if self.lose_ack:
            self.lose_ack = False
            raise RetryableTodoistError("synthetic lost ack")
        if on_external_id:
            on_external_id(self.remote[command_uuid]["task_id"])
        return self.remote[command_uuid].copy()


class ReminderCaptureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.cfg = srv.Config(secret=b"synthetic-hmac", ledger_path=Path(self.directory.name) / "ledger.sqlite", host="127.0.0.1", port=0,
                              write_enabled=True, write_domains=frozenset({"aura.reminder_capture", "aura.grocery_capture"}),
                              reminder_openai_key_file=Path("synthetic-openai"), reminder_todoist_token_file=Path("synthetic-todoist"),
                              reminder_monthly_limit_usd="2", reminder_todoist_inbox_id="synthetic-inbox")
        reminders.ReminderBudget(Path(str(self.cfg.ledger_path) + ".reminder-ai-budget.sqlite3"), monthly_limit_usd="2", now=lambda: NOW).initialize()
        srv.init_db(self.cfg.ledger_path)
        self.gateway = FakeGateway()
        self.clock = mock.patch.object(srv, "reminder_dispatch_now", return_value=NOW).start()
        self.model = mock.patch.object(ai, "interpret", return_value=proposal()).start()
        mock.patch.object(reminders, "_token", return_value="synthetic-token").start()
        mock.patch.object(reminders, "TodoistGateway", return_value=self.gateway).start()
        self.addCleanup(mock.patch.stopall)

    def execute(self, value=None):
        return srv.execute_capture(value or payload(), self.cfg)

    def operation(self):
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM reminder_operation_ledger").fetchone())

    def seed_legacy(self, value=None):
        value = value or payload()
        reminders.init_db(self.cfg.ledger_path)
        binding = hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.execute("""INSERT INTO reminder_operation_ledger
                (request_id,payload_hash,status,command_uuid,temp_id,updated_at)
                VALUES (?,?,'unbound',?,?,?)""", (value["request_id"], binding,
                    "96e26ce3-5cd1-4b7b-a393-2f64abac5bdf", "c055f623-d8b0-44cf-b47a-856684b1293c", NOW.isoformat()))

    def test_exact_plan_and_command_are_durable_before_post_and_receipt_is_truthful(self):
        def before(plan, command, temp_id, identity):
            op = self.operation()
            self.assertEqual(op["status"], "reconciliation_required")
            self.assertEqual(op["transport"], "rest_v1")
            self.assertEqual(op["rest_dispatched"], 1)
            self.assertEqual(json.loads(op["plan_json"]), plan)
            self.assertEqual(op["command_uuid"], command)
            self.assertIsNone(temp_id)  # REST does not transmit the retained legacy temp UUID.
            self.assertEqual(json.loads(op["identity_json"]), identity)
        self.gateway.before_post = before
        with mock.patch.object(srv, "run_aura_helper") as legacy:
            response = self.execute()
        legacy.assert_not_called()
        self.assertTrue(response["write_committed"])
        self.assertEqual(response["external_ids"], {"todoist_task_id": "synthetic-task"})
        self.assertEqual(response["interpreted_due"]["start"], "2026-10-30T14:00:00-05:00")
        self.assertEqual(response["plan"]["side_effects"], ["todoist.task.write"])
        self.assertFalse(response["notification_verified"])
        self.assertIsNone(response["alert_configured"])
        self.assertTrue(response["auto_reminder_requested"])
        self.assertEqual(response["delivery_status"], "pending_verification")
        self.assertIn("alertas pendientes de verificar", response["display_message"])
        self.assertNotIn("calendar", json.dumps(response).lower())
        self.assertNotIn("notion", json.dumps(response).lower())

    def test_context_labels_are_frozen_with_exact_plan(self):
        self.model.return_value = proposal(labels=["On_Phone", "5_min"])
        self.execute(payload(text="Recuérdame llamar cinco minutos mañana a las 2 pm"))
        self.assertEqual(self.gateway.posts[0][2]["labels"], ["5_min", "On_Phone"])
        self.assertEqual(json.loads(self.operation()["plan_json"])["labels"], ["5_min", "On_Phone"])
        self.assertEqual(self.model.call_args.kwargs["available_labels"], ("On_Phone", "5_min"))

    def test_first_account_must_match_the_explicit_existing_inbox_pin(self):
        self.cfg = replace(self.cfg, reminder_todoist_inbox_id="another-inbox")
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.model.assert_not_called()
        self.assertEqual(self.gateway.posts, [])

    def test_todoist_authorization_precedes_paid_interpretation(self):
        with mock.patch.object(self.gateway, "identity", side_effect=PermanentTodoistError("todoist_authorization_required")):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.model.assert_not_called()
        budget_path = Path(str(self.cfg.ledger_path) + ".reminder-ai-budget.sqlite3")
        with sqlite3.connect(budget_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM reminder_ai_budget_attempts").fetchone()[0], 0)

    def test_model_attempt_limit_never_dispatches_a_third_call(self):
        self.model.side_effect = ai.InterpretationUnavailable("interpreter_unavailable")
        for _ in range(3):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(self.gateway.posts, [])

    def test_payment_reminder_creates_only_a_task_without_expense_execution(self):
        self.model.return_value = proposal(title="Pagar el recibo")
        with mock.patch.object(srv, "run_expense_helper") as expense, mock.patch.object(srv, "run_aura_helper") as legacy:
            result = self.execute(payload(text="Recuérdame pagar el recibo mañana a las 2 pm"))
        self.assertTrue(result["write_committed"])
        self.assertEqual(result["plan"]["side_effects"], ["todoist.task.write"])
        self.assertEqual(self.gateway.posts[0][2]["title"], "Pagar el recibo")
        expense.assert_not_called()
        legacy.assert_not_called()

    def test_replay_never_calls_model_or_gateway_twice(self):
        first = self.execute()
        second = self.execute()
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(first["interpreted_due"], second["interpreted_due"])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.gateway.posts), 1)

    def test_relative_plan_frozen_across_lost_ack_and_later_retry(self):
        self.model.return_value = proposal(schedule={"mode": "relative", "date": None, "time": None, "offset_minutes": 120})
        self.gateway.lose_ack = True
        with self.assertRaises(srv.WriteExecutionError):
            self.execute()
        frozen = json.loads(self.operation()["plan_json"])
        self.assertEqual(frozen["due"]["start"], "2026-10-29T17:00:45-05:00")
        self.clock.return_value = datetime.fromisoformat("2026-10-29T16:40:00-05:00")
        self.model.return_value = proposal(title="This new interpretation must never be used")
        result = self.execute()
        self.assertEqual(result["interpreted_due"], frozen["due"])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.gateway.posts), 1)
        self.assertEqual(len(self.gateway.remote), 1)

    def test_main_ledger_restore_cannot_spend_again_or_duplicate_external_task(self):
        backup = Path(self.directory.name) / "synthetic-before-reminder.sqlite"
        with sqlite3.connect(self.cfg.ledger_path) as source, sqlite3.connect(backup) as saved:
            source.backup(saved)
        self.execute()
        original_command = self.operation()["command_uuid"]
        with sqlite3.connect(backup) as saved, sqlite3.connect(self.cfg.ledger_path) as restored:
            saved.backup(restored)
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual(self.operation()["status"], "reconciliation_required")
        self.assertEqual(self.operation()["command_uuid"], original_command)
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.gateway.remote), 1)
        self.assertEqual(len(self.gateway.posts), 1)

    def test_crash_after_post_reconciles_after_due_without_new_post(self):
        self.gateway.crash_after_post = True
        with self.assertRaises(SystemExit):
            self.execute()
        self.assertEqual(self.operation()["status"], "reconciliation_required")
        self.clock.return_value = datetime.fromisoformat("2026-11-01T16:00:00-05:00")
        result = self.execute()
        self.assertTrue(result["write_committed"])
        self.assertEqual(len(self.gateway.posts), 1)
        self.assertEqual(self.model.call_count, 1)

    def test_unknown_outcome_after_due_retains_uncertain_operation(self):
        self.gateway.fail_before_post = True
        with self.assertRaises(srv.WriteExecutionError):
            self.execute()
        original = self.operation()
        self.clock.return_value = datetime.fromisoformat("2026-11-01T16:00:00-05:00")
        with self.assertRaises(srv.WriteExecutionError):
            self.execute()
        self.assertEqual(len(self.gateway.posts), 1)
        self.assertEqual(self.operation()["command_uuid"], original["command_uuid"])
        self.assertIsNone(self.operation()["response_json"])

    def test_retry_before_due_reuses_same_uuid_and_temp_id(self):
        self.seed_legacy()
        self.gateway.fail_before_post = True
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.gateway.fail_before_post = False
        self.execute()
        self.assertEqual(self.gateway.posts[0], self.gateway.posts[1])
        self.assertEqual(self.model.call_count, 1)

    def test_existing_legacy_row_keeps_sync_transport_and_identifiers(self):
        self.seed_legacy()
        before = self.operation()
        self.execute()
        after = self.operation()
        self.assertEqual(self.gateway.transports, ["sync_v1"])
        self.assertEqual(after["transport"], "sync_v1")
        self.assertEqual(after["rest_dispatched"], 0)
        self.assertEqual((after["command_uuid"], after["temp_id"]), (before["command_uuid"], before["temp_id"]))

    def test_rest_unknown_outcome_is_forever_readonly_even_if_status_becomes_ready(self):
        self.gateway.fail_before_post = True
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        first = self.operation()
        self.assertEqual((first["transport"], first["rest_dispatched"], first["status"]), ("rest_v1", 1, "reconciliation_required"))
        budget = Path(str(self.cfg.ledger_path) + ".reminder-ai-budget.sqlite3")
        budget_before = budget.read_bytes()
        witness_before = Path(str(budget) + ".reminder-budget-initialized").read_bytes()
        reminders._update(self.cfg.ledger_path, payload()["request_id"], status="ready")
        self.gateway.fail_before_post = False
        for _ in range(2):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        after = self.operation()
        self.assertEqual(len(self.gateway.posts), 1)
        self.assertEqual(self.gateway.transports, ["rest_v1"])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(after["status"], "reconciliation_required")
        self.assertEqual((after["plan_json"], after["command_uuid"], after["temp_id"]), (first["plan_json"], first["command_uuid"], first["temp_id"]))
        self.assertEqual(budget.read_bytes(), budget_before)
        self.assertEqual(Path(str(budget) + ".reminder-budget-initialized").read_bytes(), witness_before)

    def test_rest_crash_after_claim_before_post_is_fenced_from_old_code_and_retry(self):
        self.gateway.crash_after_claim = True
        with self.assertRaises(SystemExit): self.execute()
        first = self.operation()
        self.assertEqual(first["status"], "reconciliation_required")
        self.assertEqual(first["rest_dispatched"], 1)
        for _ in range(2):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual(self.gateway.posts, [])
        self.assertEqual(self.gateway.transports, ["rest_v1"])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.operation()["command_uuid"], first["command_uuid"])

    def test_rest_preflight_failure_without_claim_can_retry_same_frozen_plan(self):
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=RetryableTodoistError("preflight unavailable")):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual((self.operation()["status"], self.operation()["rest_dispatched"]), ("ready", 0))
        self.assertTrue(self.execute()["write_committed"])
        self.assertEqual(len(self.gateway.posts), 1)
        self.assertEqual(self.model.call_count, 1)

    def test_consumed_rest_claim_with_missing_or_inconsistent_frozen_state_never_interprets(self):
        self.seed_legacy()
        for transport, status, plan, identity in (
            ("rest_v1", "reconciliation_required", None, None),
            ("rest_v1", "unbound", "{}", "{}"),
            ("rest_v1", "interpreting", "{}", "{}"),
            ("sync_v1", "ready", None, None),
        ):
            with self.subTest(transport=transport, status=status):
                reminders._update(self.cfg.ledger_path, payload()["request_id"], transport=transport,
                    rest_dispatched=1, status=status, plan_json=plan, identity_json=identity)
                with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.model.assert_not_called()
        self.assertEqual(self.gateway.posts, [])

    def test_rest_dispatch_claim_is_atomic_compare_and_set(self):
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.execute()
        operation = self.operation()
        def claim():
            try:
                reminders._claim_rest_dispatch(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"])
                return "claimed"
            except reminders.ReminderRetryable:
                return "blocked"
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(lambda _: claim(), range(2))), ["blocked", "claimed"])
        self.assertEqual((self.operation()["rest_dispatched"], self.operation()["status"]), (1, "reconciliation_required"))
        self.assertEqual(self.gateway.posts, [])

    def test_timeout_status_transaction_cannot_erase_a_late_dispatch_fence(self):
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.execute()
        operation = self.operation()
        read, release, attempting = threading.Event(), threading.Event(), threading.Event()
        original_timestamp = reminders._timestamp
        def paused_timestamp():
            if threading.current_thread().name == "synthetic-failure":
                read.set()
                self.assertTrue(release.wait(2))
            return original_timestamp()
        failures = []
        def failure():
            failures.append(reminders._mark_rest_failure(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"]))
        def late_claim():
            attempting.set()
            reminders._claim_rest_dispatch(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"])
        with mock.patch.object(reminders, "_timestamp", side_effect=paused_timestamp):
            worker = threading.Thread(target=failure, name="synthetic-failure")
            worker.start()
            self.assertTrue(read.wait(1))
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                claim = pool.submit(late_claim)
                self.assertTrue(attempting.wait(1))
                try:
                    with self.assertRaises(concurrent.futures.TimeoutError): claim.result(timeout=0.05)
                finally:
                    release.set()
                claim.result(timeout=2)
            worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(failures, [False])
        self.assertEqual((self.operation()["rest_dispatched"], self.operation()["status"]), (1, "reconciliation_required"))

    def test_atomic_elapsed_terminalization_blocks_late_claim_and_preserves_consumed_fence(self):
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.execute()
        operation = self.operation()
        response = reminders._no_write(payload(), question="Hora transcurrida")
        self.assertFalse(reminders._mark_rest_failure(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"], terminal_response=response))
        with self.assertRaises(reminders.ReminderRetryable):
            reminders._claim_rest_dispatch(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"])
        self.assertEqual((self.operation()["status"], self.operation()["rest_dispatched"]), ("terminal", 0))
        self.assertEqual(json.loads(self.operation()["response_json"]), response)
        reminders._update(self.cfg.ledger_path, payload()["request_id"], status="ready", response_json=None)
        reminders._claim_rest_dispatch(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"])
        self.assertTrue(reminders._mark_rest_failure(self.cfg.ledger_path, payload()["request_id"], operation["payload_hash"], terminal_response=response))
        self.assertEqual((self.operation()["status"], self.operation()["rest_dispatched"], self.operation()["response_json"]), ("reconciliation_required", 1, None))

    def test_elapsed_preflight_cannot_terminalize_a_late_consumed_rest_claim(self):
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.execute()
        original = self.operation()
        self.clock.return_value = datetime.fromisoformat("2026-10-30T15:00:00-05:00")
        def late_gateway(cfg):
            reminders._claim_rest_dispatch(cfg.ledger_path, payload()["request_id"], original["payload_hash"])
            return self.gateway
        with mock.patch.object(reminders, "_gateway", side_effect=late_gateway):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual((self.operation()["status"], self.operation()["rest_dispatched"], self.operation()["response_json"]), ("reconciliation_required", 1, None))
        self.assertEqual(self.gateway.posts, [])
        self.assertEqual(self.model.call_count, 1)

    def test_first_legacy_preflight_expiry_remains_terminal_without_any_post(self):
        self.seed_legacy()
        with mock.patch.object(self.gateway, "create_or_reconcile", side_effect=PermanentTodoistError("todoist_due_elapsed_before_dispatch")):
            response = self.execute()
        self.assertFalse(response["write_committed"])
        self.assertEqual((self.operation()["transport"], self.operation()["status"], self.operation()["rest_dispatched"]), ("sync_v1", "terminal", 0))
        self.assertEqual(self.gateway.posts, [])
        self.assertEqual(self.model.call_count, 1)

    def test_atomic_schema_migration_preserves_existing_rows_during_concurrent_init(self):
        path = Path(self.directory.name) / "legacy-operation.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("""CREATE TABLE reminder_operation_ledger (
                request_id TEXT PRIMARY KEY,payload_hash TEXT NOT NULL,status TEXT NOT NULL,
                command_uuid TEXT NOT NULL UNIQUE,temp_id TEXT NOT NULL UNIQUE,plan_json TEXT,
                identity_json TEXT,task_id TEXT,response_json TEXT,updated_at TEXT NOT NULL)""")
            original = ("legacy-unknown", "bound-hash", "uncertain", "old-command", "old-temp", "old-plan", "old-identity", None, None, "old-time")
            conn.execute("INSERT INTO reminder_operation_ledger VALUES(?,?,?,?,?,?,?,?,?,?)", original)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: reminders.init_db(path), range(8)))
        with sqlite3.connect(path) as conn:
            row = conn.execute("SELECT * FROM reminder_operation_ledger").fetchone()
        self.assertEqual(row[:10], original)
        self.assertEqual(row[10:], ("sync_v1", 0))

    def test_frozen_plan_recovery_does_not_require_model_credentials_again(self):
        self.gateway.lose_ack = True
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        def token(path):
            if path == self.cfg.reminder_openai_key_file:
                raise reminders.ReminderRetryable("model_credential_removed")
            return "synthetic-token"
        with mock.patch.object(reminders, "_token", side_effect=token):
            self.assertTrue(self.execute()["write_committed"])
        self.assertEqual(self.model.call_count, 1)

    def test_unsupported_provider_configuration_never_falls_back(self):
        self.cfg = replace(self.cfg, reminder_provider="unconfigured-provider")
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.model.assert_not_called()
        self.assertEqual(self.gateway.posts, [])

    def test_crash_after_interpretation_preserves_plan(self):
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=SystemExit):
            with self.assertRaises(SystemExit): self.execute()
        self.assertEqual(self.operation()["status"], "ready")
        self.assertEqual(self.operation()["rest_dispatched"], 0)
        self.model.return_value = proposal(title="changed")
        self.execute()
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(self.gateway.posts[0][2]["title"], "Llamar")

    def test_due_elapsed_during_identity_preflight_never_posts(self):
        def identity():
            self.clock.return_value = datetime.fromisoformat("2026-11-01T16:00:00-05:00")
            return IDENTITY
        self.gateway.identity = identity
        response = self.execute()
        self.assertEqual(response["status"], "needs_input")
        self.assertFalse(response["write_committed"])
        self.assertEqual(self.gateway.posts, [])

    def test_first_attempt_expiry_inside_gateway_preflight_is_terminal_no_write(self):
        def preflight(*args, **kwargs):
            self.clock.return_value = datetime.fromisoformat("2026-11-01T16:00:00-05:00")
            raise PermanentTodoistError("todoist_due_elapsed_before_dispatch")
        with mock.patch.object(self.gateway, "create_rest_or_reconcile", side_effect=preflight):
            result = self.execute()
        self.assertEqual(result["status"], "needs_input")
        self.assertFalse(result["write_committed"])
        self.assertEqual(self.operation()["status"], "terminal")
        self.assertEqual(self.gateway.posts, [])

    def test_preflight_expiry_never_erases_earlier_uncertain_attempt(self):
        self.seed_legacy()
        self.gateway.fail_before_post = True
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        with mock.patch.object(self.gateway, "create_or_reconcile", side_effect=PermanentTodoistError("todoist_due_elapsed_before_dispatch")):
            with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertEqual(self.operation()["status"], "uncertain")
        self.assertIsNone(self.operation()["response_json"])

    def test_invalid_original_timestamp_never_calls_model(self):
        for i, value in enumerate((None, "", "invalid", "2026-10-29T15:00:00", "2026-10-30T15:00:00-05:00")):
            with self.subTest(timestamp=value):
                item = payload(request_id=f"synthetic-invalid-{i}"); item["created_at"] = value
                self.assertEqual(self.execute(item)["status"], "needs_input")
        self.model.assert_not_called()
        self.assertEqual(self.gateway.posts, [])

    def test_missing_ambiguous_unsupported_and_refused_input_never_writes(self):
        for i, value in enumerate((proposal(action="needs_input",title=None,schedule=None,question="¿A qué hora?"),
                                   proposal(action="unsupported",title=None,schedule=None,question="Solo recordatorios personales nuevos."),
                                   proposal(schedule={"mode":"absolute","date":"2026-10-30","time":None,"offset_minutes":None}),
                                   proposal(recurrence="diario"), proposal(deadline="2026-10-30"), proposal(custom_alert="10 minutos antes"))):
            with self.subTest(proposal=value):
                self.model.return_value=value
                result=self.execute(payload(request_id=f"synthetic-no-write-{i}"))
                self.assertIn(result["status"], ("rejected", "needs_input"))
                self.assertFalse(result["write_committed"])
        self.assertEqual(self.gateway.posts, [])

    def test_provider_unavailable_retries_without_freezing_a_fabricated_plan(self):
        self.model.side_effect = ai.InterpretationUnavailable("synthetic")
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        self.assertIsNone(self.operation()["plan_json"])
        self.model.side_effect = None
        self.execute()
        self.assertEqual(self.model.call_count, 2)
        self.assertEqual(len(self.gateway.remote), 1)

    def test_refused_incomplete_and_invalid_model_output_returns_clear_no_write(self):
        for i, reason in enumerate(("interpreter_refused", "interpreter_incomplete", "interpreter_invalid_output", "interpreter_invalid_schema")):
            with self.subTest(reason=reason):
                self.model.side_effect = ai.InterpretationUnavailable(reason)
                response = self.execute(payload(request_id=f"synthetic-provider-rejection-{i}"))
                self.assertEqual(response["status"], "needs_input")
                self.assertFalse(response["write_committed"])
                self.assertIn("No pude interpretar", response["display_message"])
        self.assertEqual(self.gateway.posts, [])

    def test_request_id_conflict_remains_bound_after_unknown_outcome(self):
        self.gateway.lose_ack=True
        with self.assertRaises(srv.WriteExecutionError): self.execute()
        with self.assertRaisesRegex(srv.WriteExecutionError,"request_id_conflict"):
            self.execute(payload(text="A changed original capture"))
        self.assertEqual(self.model.call_count,1)
        self.assertEqual(len(self.gateway.remote),1)

    def test_private_credential_files_required_without_exposing_content(self):
        key=Path(self.directory.name)/"synthetic.key";key.write_text("synthetic-only-token");key.chmod(0o644)
        with mock.patch.object(reminders,"_token", wraps=self.original_token):
            with self.assertRaises(reminders.ReminderRetryable) as caught: reminders._token(key)
        self.assertNotIn("synthetic-only-token",str(caught.exception))
        key.chmod(0o600)
        self.assertEqual(self.original_token(key),"synthetic-only-token")

    original_token = staticmethod(reminders._token)

    def start_server(self):
        class QuietHandler(srv.MobileCaptureHandler):
            def log_message(self,*args): pass
        server=srv.ThreadingHTTPServer(("127.0.0.1",0),QuietHandler);server.cfg=self.cfg
        self.server = server
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(thread.join);self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/webhooks/mobile-capture-v1"

    def send(self, endpoint, item, *, bad_signature=False):
        raw=json.dumps(item,separators=(",", ":")).encode(); ts=str(int(time.time()))
        sig=hmac.new(self.cfg.secret,ts.encode()+b"."+raw,hashlib.sha256).hexdigest()
        req=request.Request(endpoint,data=raw,headers={"Content-Type":"application/json","X-Webhook-Timestamp":ts,"X-Webhook-Signature-V2":"bad" if bad_signature else sig,"X-Request-ID":item["request_id"]})
        try:
            with request.urlopen(req,timeout=5) as response: return response.status,json.loads(response.read())
        except error.HTTPError as response:
            with response: return response.code,json.loads(response.read())

    def test_signed_http_replay_lost_ack_and_payload_conflict(self):
        endpoint=self.start_server(); self.gateway.lose_ack=True
        self.assertEqual(self.send(endpoint,payload())[0],502)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            self.assertEqual(conn.execute("SELECT status FROM mobile_capture_ledger").fetchone()[0],"processing")
        self.assertEqual(self.send(endpoint,payload(text="different"))[0],409)
        code,saved=self.send(endpoint,payload());self.assertEqual(code,200);self.assertTrue(saved["write_committed"])
        code,replay=self.send(endpoint,payload());self.assertEqual(code,200);self.assertEqual(replay["status"],"duplicate")
        self.assertEqual(self.model.call_count,1);self.assertEqual(len(self.gateway.remote),1)

    def test_budget_configuration_gate_retries_original_capture_after_setup(self):
        self.cfg = replace(self.cfg, reminder_monthly_limit_usd="0")
        endpoint = self.start_server()
        code, response = self.send(endpoint, payload())
        self.assertEqual(code, 502)
        self.assertEqual(response["error"], "reminder_configuration_pending")
        self.model.assert_not_called()
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            self.assertEqual(conn.execute("SELECT status FROM mobile_capture_ledger").fetchone()[0], "processing")
        self.cfg = replace(self.cfg, reminder_monthly_limit_usd="2")
        self.server.cfg = self.cfg
        code, response = self.send(endpoint, payload())
        self.assertEqual(code, 200)
        self.assertTrue(response["write_committed"])
        self.assertEqual(self.model.call_count, 1)

    def test_invalid_signature_never_reaches_ai(self):
        self.assertEqual(self.send(self.start_server(),payload(),bad_signature=True)[0],401)
        self.model.assert_not_called()

    def test_slow_ai_does_not_hold_global_lock_and_concurrent_replay_cannot_write_twice(self):
        entered=threading.Event();release=threading.Event()
        def slow(*args,**kwargs):
            entered.set();release.wait(4);return proposal()
        self.model.side_effect=slow
        grocery={**payload(request_id="synthetic-grocery"),"route":{"agent":"aura","intent":"grocery","domain":"aura.grocery_capture"},"capture":{"text":"falta leche"}}
        with tempfile.TemporaryDirectory() as directory, grocery_environment(Path(directory)) as (helper_path, notion):
            self.cfg=replace(self.cfg,aura_helper=helper_path)
            endpoint=self.start_server()
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                first=pool.submit(self.send,endpoint,payload())
                self.assertTrue(entered.wait(2))
                try:
                    self.assertEqual(self.send(endpoint,payload())[0],502)
                    result=pool.submit(self.send,endpoint,grocery).result(timeout=2)
                    self.assertTrue(result[1]["write_committed"])
                    self.assertEqual(len(notion.writes),1)
                finally: release.set()
                self.assertTrue(first.result(timeout=4)[1]["write_committed"])
        self.assertEqual(self.model.call_count,1);self.assertEqual(len(self.gateway.remote),1)


if __name__=="__main__": unittest.main()
