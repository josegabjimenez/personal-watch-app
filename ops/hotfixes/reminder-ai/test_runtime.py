"""Acceptance of the actual generated twelve-file historical HTTP runtime.

The real signed handler, interpretation validator, cost ledger, Reminder
operation ledger and Todoist readback code run against in-memory HTTP requests,
synthetic provider responses and private temporary SQLite. No listener, actual
credentials, provider requests, deployment or service mutation is used here.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import hmac
import importlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock
from zoneinfo import ZoneInfo


def load_fixture():
    path = Path(__file__).with_name("test_release.py")
    spec = importlib.util.spec_from_file_location("_reminder_package_fixture", path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


fixture = load_fixture()
build = fixture.build
NOW = datetime.fromisoformat("2026-11-05T11:00:00-05:00")
IDENTITY = {"user_id": "synthetic-user", "inbox_project_id": "synthetic-inbox", "timezone": "America/Bogota"}


def payload(request_id="synthetic-exact-reminder"):
    return {
        "event_type": "mobile_capture.v1", "schema_version": 1, "request_id": request_id,
        "created_at": "2026-11-05T10:00:45-05:00", "source": {"device_id": "synthetic-watch"},
        "route": {"agent": "aura", "intent": "reminder", "domain": "aura.reminder_capture"},
        "capture": {"text": "Recuérdame llamar a mamá en dos horas; la llamada toma cinco minutos"},
        "context": {"dry_run": False, "allow_write": True, "allow_firefly_write": False},
    }


def proposal():
    return {
        "action": "create_reminder", "title": "Llamar a mamá", "timezone": "America/Bogota",
        "schedule": {"mode": "relative", "date": None, "time": None, "offset_minutes": 120},
        "question": None, "deadline": None, "recurrence": None, "custom_alert": None,
        "labels": ["On_Phone", "5_min"],
    }


class SyntheticTodoist:
    """Protocol fake; actual gateway methods still validate their own receipts."""
    def __init__(self, test):
        self.test = test
        self.calls = []
        self.commands = []
        self.rest_requests = []
        self.tasks = {}
        self.command_ids = {}
        self.lose_ack = False
        self.fail_before_sync = False
        self.bad_readback = False

    def request(self, gateway, method, path, deadline, *, form=None, json_body=None,
                request_id=None, on_before_send=None, retry_unauthorized=True):
        self.calls.append((method, path))
        if method == "GET" and path == "/user":
            return {"id": IDENTITY["user_id"], "inbox_project_id": IDENTITY["inbox_project_id"], "tz_info": {"timezone": IDENTITY["timezone"]}}
        if method == "GET" and path.startswith("/labels?"):
            return {"results": [{"id": "synthetic-label-phone", "name": "On_Phone"}, {"id": "synthetic-label-five", "name": "5_min"}], "next_cursor": None}
        if method == "GET" and path.startswith("/tasks?"):
            self.test.assertIn("project_id=synthetic-inbox", path)
            return {"results": copy.deepcopy(list(self.tasks.values())), "next_cursor": None}
        if method == "GET" and path.startswith("/tasks/"):
            identifier = path.split("/")[-1]
            result = copy.deepcopy(self.tasks[identifier])
            if self.bad_readback:
                result["content"] = "Different synthetic action"
            return result
        if method == "POST" and path == "/tasks":
            self.test.assertIsNone(form)
            self.test.assertFalse(retry_unauthorized)
            self.test.assertTrue(callable(on_before_send))
            on_before_send()
            operation = self.test.operation()
            self.test.assertEqual((operation["transport"], operation["rest_dispatched"], operation["status"]), ("rest_v1", 1, "reconciliation_required"))
            self.test.assertEqual(operation["command_uuid"], request_id)
            frozen = json.loads(operation["plan_json"])
            self.test.assertEqual(frozen["title"], json_body["content"])
            self.test.assertEqual(frozen["labels"], json_body["labels"])
            self.test.assertIs(json_body["auto_reminder"], True)
            self.test.assertIs(json_body["auto_parse_labels"], False)
            self.rest_requests.append({"request_id": request_id, "body": copy.deepcopy(json_body)})
            identifier = "synthetic-task-" + str(len(self.tasks) + 1)
            # Match the documented REST conversion from UTC input to the
            # authenticated user's default timezone on task readback.
            local_due = datetime.fromisoformat(json_body["due_datetime"].replace("Z", "+00:00")).astimezone(ZoneInfo(IDENTITY["timezone"]))
            self.tasks[identifier] = {
                "id": identifier, "user_id": IDENTITY["user_id"], "project_id": json_body["project_id"],
                "content": json_body["content"], "description": json_body["description"],
                "due": {"date": local_due.isoformat(), "timezone": IDENTITY["timezone"], "is_recurring": False},
                "labels": list(json_body["labels"]), "priority": json_body["priority"],
                "parent_id": None, "section_id": None, "is_deleted": False, "checked": False, "deadline": None,
            }
            if self.lose_ack:
                self.lose_ack = False
                raise self.test.todoist.RetryableTodoistError("synthetic provider lost acknowledgement")
            return copy.deepcopy(self.tasks[identifier])
        if method == "POST" and path == "/sync":
            commands = json.loads(form["commands"])
            self.test.assertEqual(len(commands), 1)
            command = commands[0]
            self.commands.append(copy.deepcopy(command))
            operation = self.test.operation()
            self.test.assertEqual(operation["status"], "writing")
            self.test.assertEqual(operation["command_uuid"], command["uuid"])
            self.test.assertEqual(operation["temp_id"], command["temp_id"])
            frozen = json.loads(operation["plan_json"])
            self.test.assertEqual(frozen["title"], command["args"]["content"])
            self.test.assertEqual(frozen["labels"], command["args"]["labels"])
            if self.fail_before_sync:
                raise self.test.todoist.RetryableTodoistError("synthetic provider timeout before mutation")
            identifier = self.command_ids.setdefault(command["uuid"], "synthetic-task-" + str(len(self.command_ids) + 1))
            args = command["args"]
            self.tasks.setdefault(identifier, {
                "id": identifier, "user_id": IDENTITY["user_id"], "project_id": args["project_id"],
                "content": args["content"], "description": args["description"],
                "due": copy.deepcopy(args["due"]), "labels": list(args["labels"]), "priority": args["priority"],
                "parent_id": None, "section_id": None, "is_deleted": False, "checked": False, "deadline": None,
            })
            if self.lose_ack:
                self.lose_ack = False
                raise self.test.todoist.RetryableTodoistError("synthetic provider lost acknowledgement")
            return {"sync_status": {command["uuid"]: "ok"}, "temp_id_mapping": {command["temp_id"]: identifier}}
        raise AssertionError("Unexpected synthetic Todoist protocol request")


class ExactHistoricalReminderRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ReminderReleaseTests.setUpClass()

    def setUp(self):
        self.package = fixture.ReminderReleaseTests("test_candidate_exact_count_byte_identical_groceries_and_baseline_hashes")
        self.package.setUp()
        self.addCleanup(self.package.doCleanups)
        self.expected, _ = self.package.prepare()
        self.release = self.package.output
        self.state = self.package.directory / "synthetic-private-state"
        self.state.mkdir(mode=0o700)
        self.current = NOW
        self.budget_path = self.state / "reminder-budget.sqlite"
        self.env = {
            "HERMES_HOME": str(self.state / "synthetic-hermes-home"),
            "MOBILE_CAPTURE_REMINDER_OPENAI_KEY_FILE": str(self.state / "synthetic-openai.key"),
            "MOBILE_CAPTURE_REMINDER_TODOIST_TOKEN_FILE": str(self.state / "synthetic-todoist.key"),
            "MOBILE_CAPTURE_REMINDER_TODOIST_INBOX_ID": IDENTITY["inbox_project_id"],
            "MOBILE_CAPTURE_REMINDER_BUDGET_PATH": str(self.budget_path),
            "MOBILE_CAPTURE_REMINDER_MONTHLY_LIMIT_USD": "2",
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(mock.patch.dict(os.environ, self.env, clear=True))
        self.stack.enter_context(mock.patch("urllib.request.urlopen", side_effect=AssertionError("external network is not allowed")))
        self.stack.enter_context(mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("external provider transport is not allowed")))
        self.saved_path, self.saved_bytecode = list(sys.path), sys.dont_write_bytecode
        self.names = {Path(name).stem for name in build.ARTIFACTS} | {"_exact_historical_reminder_server"}
        self.saved_modules = {name: sys.modules.get(name) for name in self.names}
        self.stack.callback(self.restore_imports)
        for name in self.names:
            sys.modules.pop(name, None)
        sys.path.insert(0, str(self.release))
        sys.dont_write_bytecode = True
        for name in build.ARTIFACTS:
            if name != build.SERVER:
                importlib.import_module(Path(name).stem)
        server_path = self.release / build.SERVER
        spec = importlib.util.spec_from_file_location("_exact_historical_reminder_server", server_path)
        self.srv = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.srv
        exec(compile(server_path.read_bytes(), str(server_path), "exec"), self.srv.__dict__)
        self.runtime = sys.modules["reminder_runtime"]
        self.reminders = sys.modules["reminder_capture"]
        self.ai = sys.modules["reminder_ai"]
        self.todoist = sys.modules["todoist_reminders"]
        self.budget = sys.modules["reminder_ai_budget"]
        self.cfg = self.srv.Config(secret=b"synthetic-exact-release-hmac", ledger_path=self.state / "mobile.sqlite",
                                  host="127.0.0.1", port=0, write_enabled=True,
                                  write_domains=frozenset({"aura.reminder_capture", "aura.grocery_capture"}))
        self.srv.init_db(self.cfg.ledger_path)
        self.budget.ReminderBudget(self.budget_path, "2", now=lambda: self.current).initialize()
        self.grocery_path = sys.modules["grocery_capture"].journal_path(self.cfg.ledger_path)
        with sqlite3.connect(self.grocery_path) as conn:
            conn.execute("CREATE TABLE synthetic_preserved_grocery(value TEXT)")
            conn.execute("INSERT INTO synthetic_preserved_grocery VALUES('unchanged')")
        conn.close()
        self.grocery_marker = sys.modules["grocery_ai_budget"]._marker_path(self.grocery_path)
        self.grocery_marker.write_bytes(b'{"synthetic":"accepted grocery state witness"}\n')
        self.grocery_marker.chmod(0o600)
        self.grocery_state = self.grocery_fingerprints()
        self.provider = SyntheticTodoist(self)
        self.todoist_transport = self.stack.enter_context(mock.patch.object(self.todoist.TodoistGateway, "_request", autospec=True, side_effect=self.provider.request))
        self.model = self.stack.enter_context(mock.patch.object(self.ai, "_post_json", side_effect=self.model_response))
        self.stack.enter_context(mock.patch.object(self.reminders, "_token", return_value="synthetic-server-token"))
        self.stack.enter_context(mock.patch.object(self.todoist, "dispatch_now", side_effect=lambda: self.current))
        clock = self.stack.enter_context(mock.patch.object(self.runtime, "datetime", wraps=datetime))
        clock.now.side_effect = lambda tz=None: self.current.astimezone(tz or timezone.utc)

    def restore_imports(self):
        for name in self.names:
            sys.modules.pop(name, None)
            if self.saved_modules[name] is not None:
                sys.modules[name] = self.saved_modules[name]
        sys.path[:] = self.saved_path
        sys.dont_write_bytecode = self.saved_bytecode

    def grocery_fingerprints(self):
        return {str(path): (hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_ino, path.stat().st_mtime_ns)
                for path in (self.grocery_path, self.grocery_marker)}

    def assert_grocery_preserved(self):
        self.assertEqual(self.grocery_fingerprints(), self.grocery_state)
        for name in build.GROCERY_MODULES:
            self.assertEqual(build.digest((self.release / name).read_bytes()), self.expected["baseline_artifacts"][name])

    def model_response(self, body, api_key, timeout):
        self.assertEqual(api_key, "synthetic-server-token")
        request_body = json.loads(body)
        self.assertEqual(request_body["model"], "gpt-6-luna")
        self.assertEqual(request_body["service_tier"], "default")
        return {
            "status": "completed", "model": "gpt-6-luna", "service_tier": "default",
            "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
            "output": [{"type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(proposal(), ensure_ascii=False)}]}],
        }

    @staticmethod
    def raw(value):
        return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()

    def invoke(self, value=None, *, raw=None, signature=None, path="/webhooks/mobile-capture-v1", on_reply=None, method="POST"):
        value = payload() if value is None else value
        raw = self.raw(value) if raw is None else raw
        timestamp = str(int(time.time()))
        signed = hmac.new(self.cfg.secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        handler = object.__new__(self.srv.MobileCaptureHandler)
        handler.server = SimpleNamespace(cfg=self.cfg)
        handler.path = path
        handler.headers = {"Content-Length": str(len(raw)), "X-Webhook-Timestamp": timestamp,
                           "X-Webhook-Signature-V2": signature if signature is not None else signed,
                           "X-Request-ID": value.get("request_id", "")}
        handler.rfile = io.BytesIO(raw)
        replies = []
        def reply(received_handler, status, response):
            self.assertIs(received_handler, handler)
            if on_reply:
                on_reply(status, response)
            replies.append((status, copy.deepcopy(response)))
        with mock.patch.object(self.srv, "json_response", side_effect=reply):
            getattr(self.srv.MobileCaptureHandler, "do_" + method)(handler)
        self.assertEqual(len(replies), 1)
        self.assert_grocery_preserved()
        return replies[0]

    def parent(self, request_id="synthetic-exact-reminder"):
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM mobile_capture_ledger WHERE request_id=?", (request_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def operation(self, request_id="synthetic-exact-reminder"):
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM reminder_operation_ledger WHERE request_id=?", (request_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def seed_legacy(self):
        self.reminders.init_db(self.cfg.ledger_path)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.execute("""INSERT INTO reminder_operation_ledger
                (request_id,payload_hash,status,command_uuid,temp_id,updated_at)
                VALUES (?,?,'unbound',?,?,?)""", (payload()["request_id"], build.digest(self.raw(payload())),
                    "96e26ce3-5cd1-4b7b-a393-2f64abac5bdf", "c055f623-d8b0-44cf-b47a-856684b1293c", NOW.isoformat()))

    def test_generated_package_is_exact_and_every_local_import_comes_from_release(self):
        self.assertEqual(len(build.ARTIFACTS), 12)
        build.verify_release(self.release, self.expected)
        for name in build.ARTIFACTS:
            if name != build.SERVER:
                self.assertEqual(Path(sys.modules[Path(name).stem].__file__).parent, self.release)
        self.assertEqual(Path(self.srv.__file__).parent, self.release)
        self.assert_grocery_preserved()

    def test_signed_save_requires_exact_todoist_readback_then_replays_without_calls(self):
        code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(response["status"], "committed")
        self.assertTrue(response["write_committed"])
        self.assertEqual(response["interpreted_due"]["start"], "2026-11-05T12:00:45-05:00")
        self.assertFalse(response["notification_verified"])
        self.assertIsNone(response["alert_configured"])
        self.assertEqual(response["delivery_status"], "pending_verification")
        self.assertIn("alertas pendientes de verificar", response["display_message"])
        self.assertEqual(response["external_ids"], {"todoist_task_id": "synthetic-task-1"})
        self.assertIn(("GET", "/tasks/synthetic-task-1"), self.provider.calls)
        operation = self.operation()
        frozen = json.loads(operation["plan_json"])
        self.assertEqual(frozen["labels"], ["5_min", "On_Phone"])
        self.assertEqual(operation["command_uuid"], self.provider.rest_requests[0]["request_id"])
        self.assertEqual(self.parent()["payload_hash"], build.digest(self.raw(payload())))
        before_calls = len(self.provider.calls)
        code, replay = self.invoke()
        self.assertEqual((code, replay["status"]), (200, "duplicate"))
        self.assertEqual(replay["interpreted_due"], frozen["due"])
        self.assertEqual(self.operation()["command_uuid"], operation["command_uuid"])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.provider.rest_requests), 1)
        self.assertEqual(self.provider.commands, [])
        self.assertEqual(len(self.provider.calls), before_calls)

    def test_raw_hash_conflict_409_never_replans_or_mutates(self):
        self.invoke()
        parent, operation = self.parent(), self.operation()
        calls = len(self.provider.calls)
        code, response = self.invoke(raw=self.raw(payload()) + b" ")
        self.assertEqual((code, response["error"]), (409, "request_id_conflict"))
        self.assertEqual(self.parent(), parent)
        self.assertEqual(self.operation(), operation)
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(self.model.call_count, 1)

    def test_invalid_hmac_never_interprets_or_contacts_todoist(self):
        code, response = self.invoke(signature="0" * 64)
        self.assertEqual(code, 401)
        self.assertEqual(response["status"], "rejected")
        self.model.assert_not_called()
        self.todoist_transport.assert_not_called()
        self.assertIsNone(self.parent())

    def test_zero_budget_retains_original_processing_claim_then_allows_same_id_retry(self):
        os.environ["MOBILE_CAPTURE_REMINDER_MONTHLY_LIMIT_USD"] = "0"
        code, response = self.invoke()
        self.assertEqual(code, 502)
        self.assertEqual(response, {"status": "error", "error": "reminder_configuration_pending", "request_id": payload()["request_id"], "domain": "aura.reminder_capture"})
        original = self.parent()
        self.assertEqual(original["status"], "processing")
        self.model.assert_not_called()
        self.assertEqual(self.provider.commands, [])
        os.environ["MOBILE_CAPTURE_REMINDER_MONTHLY_LIMIT_USD"] = "2"
        code, response = self.invoke()
        self.assertEqual((code, response["status"]), (200, "committed"))
        self.assertEqual(self.parent()["payload_hash"], original["payload_hash"])
        self.assertEqual(self.parent()["created_at"], original["created_at"])
        self.assertEqual(self.model.call_count, 1)

    def test_uninitialized_budget_retains_claim_and_only_explicit_setup_permits_retry(self):
        uninitialized = self.state / "synthetic-uninitialized-reminder-budget.sqlite"
        os.environ["MOBILE_CAPTURE_REMINDER_BUDGET_PATH"] = str(uninitialized)
        code, response = self.invoke()
        self.assertEqual((code, response["error"]), (502, "reminder_configuration_pending"))
        original, operation = self.parent(), self.operation()
        self.assertEqual(original["status"], "processing")
        self.assertFalse(uninitialized.exists())
        self.model.assert_not_called()
        self.todoist_transport.assert_not_called()
        self.budget.ReminderBudget(uninitialized, "2", now=lambda: self.current).initialize()
        code, response = self.invoke()
        self.assertEqual((code, response["status"]), (200, "committed"))
        self.assertEqual(self.operation()["command_uuid"], operation["command_uuid"])
        self.assertEqual(self.parent()["created_at"], original["created_at"])
        self.assertEqual(self.model.call_count, 1)

    def test_lost_ack_reconciles_frozen_relative_plan_without_new_rest_or_model(self):
        self.provider.lose_ack = True
        code, response = self.invoke()
        self.assertEqual(code, 502)
        self.assertEqual(response, {"status": "error", "error": "reminder_pending_retry", "request_id": payload()["request_id"], "domain": "aura.reminder_capture"})
        original = self.operation()
        self.assertEqual(original["status"], "reconciliation_required")
        self.assertEqual(original["rest_dispatched"], 1)
        frozen = json.loads(original["plan_json"])
        self.current = datetime.fromisoformat("2026-11-05T12:10:00-05:00")
        code, response = self.invoke()
        self.assertEqual((code, response["status"]), (200, "committed"))
        self.assertEqual(response["interpreted_due"], frozen["due"])
        self.assertEqual(self.operation()["command_uuid"], original["command_uuid"])
        self.assertEqual(self.model.call_count, 1)
        self.assertEqual(len(self.provider.rest_requests), 1)
        self.assertEqual(self.provider.commands, [])
        self.assertEqual(len(self.provider.tasks), 1)

    def test_uncertain_pre_save_retry_reuses_identical_sync_uuid_temp_and_plan(self):
        self.seed_legacy()
        self.provider.fail_before_sync = True
        self.assertEqual(self.invoke()[0], 502)
        self.provider.fail_before_sync = False
        self.assertEqual(self.invoke()[0], 200)
        self.assertEqual(len(self.provider.commands), 2)
        self.assertEqual(self.provider.commands[0], self.provider.commands[1])
        self.assertEqual(len(self.provider.tasks), 1)
        self.assertEqual(self.model.call_count, 1)

    def test_mismatched_task_readback_cannot_claim_saved(self):
        self.provider.bad_readback = True
        code, response = self.invoke()
        self.assertEqual((code, response["error"]), (502, "reminder_pending_retry"))
        self.assertNotIn("write_committed", response)
        self.assertEqual(self.operation()["status"], "reconciliation_required")
        self.assertEqual(self.operation()["rest_dispatched"], 1)
        self.assertEqual(len(self.provider.rest_requests), 1)
        self.assertEqual(self.operation()["task_id"], "synthetic-task-1")

    def test_slow_model_holds_neither_global_grocery_execution_lock_nor_sql_transaction(self):
        entered, release = threading.Event(), threading.Event()
        original = self.model_response
        def slow(*args):
            entered.set()
            if not release.wait(2):
                raise AssertionError("synthetic model wait timed out")
            return original(*args)
        result = []
        self.model.side_effect = slow
        thread = threading.Thread(target=lambda: result.append(self.invoke()), daemon=True)
        thread.start()
        self.assertTrue(entered.wait(1))
        try:
            self.assertTrue(self.srv.CAPTURE_EXECUTION_LOCK.acquire(timeout=0.1))
            self.srv.CAPTURE_EXECUTION_LOCK.release()
            conn = sqlite3.connect(self.cfg.ledger_path, timeout=0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
            finally:
                conn.close()
        finally:
            release.set()
            thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result[0][0], 200)

    def test_cached_reply_and_conflict_close_sqlite_before_json_response(self):
        self.invoke()
        tracked = []
        class Connection(sqlite3.Connection):
            closed_for_test = False
            def close(self):
                self.closed_for_test = True
                return super().close()
        original = sqlite3.connect
        def connect(*args, **kwargs):
            connection = original(*args, **kwargs, factory=Connection)
            tracked.append(connection)
            return connection
        def safe_reply(status, response):
            self.assertTrue(tracked)
            self.assertTrue(all(connection.closed_for_test for connection in tracked))
            conn = original(self.cfg.ledger_path, timeout=0)
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.rollback()
            finally:
                conn.close()
        with mock.patch.object(self.runtime, "sqlite3", SimpleNamespace(connect=connect)):
            self.assertEqual(self.invoke(on_reply=safe_reply)[0], 200)
            self.assertEqual(self.invoke(raw=self.raw(payload()) + b" ", on_reply=safe_reply)[0], 409)

    def test_corrupt_cache_returns_static_correlated_retry_without_provider_calls(self):
        self.invoke()
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.execute("UPDATE mobile_capture_ledger SET response_json='synthetic-private-provider-error' WHERE request_id=?", (payload()["request_id"],))
        conn.close()
        calls = len(self.provider.calls)
        code, response = self.invoke()
        self.assertEqual(code, 502)
        self.assertEqual(response, {"status": "error", "error": "reminder_pending_retry", "request_id": payload()["request_id"], "domain": "aura.reminder_capture"})
        self.assertEqual(len(self.provider.calls), calls)
        self.assertEqual(self.model.call_count, 1)

    def test_unhashable_nonreminder_intent_is_passed_unchanged_to_legacy(self):
        for intent in ([], {}):
            value = payload("synthetic-nonreminder")
            value["route"] = {"agent": "aura", "intent": intent, "domain": "unrelated.domain"}
            raw = b"  " + json.dumps(value, ensure_ascii=False, indent=1).encode() + b"\n"
            observed = []
            def legacy(handler):
                observed.append((handler.path, handler.rfile.read(), copy.deepcopy(handler.headers)))
                self.srv.json_response(handler, 200, {"status": "synthetic-original-handler"})
            with mock.patch.object(self.srv.MobileCaptureHandler, "_grocery_do_POST", legacy):
                self.assertEqual(self.invoke(value, raw=raw)[0], 200)
            self.assertEqual(observed[0][0:2], ("/webhooks/mobile-capture-v1", raw))
            self.assertEqual(observed[0][2]["X-Request-ID"], value["request_id"])
        self.model.assert_not_called()
        self.todoist_transport.assert_not_called()

    def test_original_grocery_method_receives_identical_raw_body_and_executes_dry_run(self):
        value = payload("synthetic-grocery-dry-run")
        value["route"] = {"agent": "aura", "intent": "grocery", "domain": "aura.grocery_capture"}
        value["capture"]["text"] = "leche y huevos"
        value["context"].update(dry_run=True, allow_write=False)
        raw = b"\n " + json.dumps(value, ensure_ascii=False, indent=2).encode() + b"\n"
        observed = []
        original = self.srv.MobileCaptureHandler._grocery_do_POST
        def legacy(handler):
            position = handler.rfile.tell()
            observed.append((handler.path, handler.rfile.read()))
            handler.rfile.seek(position)
            return original(handler)
        with mock.patch.object(self.srv.MobileCaptureHandler, "_grocery_do_POST", legacy):
            code, response = self.invoke(value, raw=raw)
        self.assertEqual(code, 200)
        self.assertTrue(response["dry_run"])
        self.assertEqual(observed, [("/webhooks/mobile-capture-v1", raw)])
        self.model.assert_not_called()
        self.todoist_transport.assert_not_called()

    def test_other_path_and_get_fallback_keep_original_path_and_stream(self):
        observed = []
        value = payload("synthetic-other-path")
        raw = self.raw(value)
        def legacy(handler):
            observed.append((handler.path, handler.rfile.read()))
            self.srv.json_response(handler, 404, {"error": "synthetic-legacy-route"})
        with mock.patch.object(self.srv.MobileCaptureHandler, "_grocery_do_POST", legacy):
            self.assertEqual(self.invoke(value, path="/other-webhook", raw=raw)[0], 404)
        with mock.patch.object(self.srv.MobileCaptureHandler, "_grocery_do_GET", legacy):
            self.assertEqual(self.invoke(value, path="/other-health", raw=raw, method="GET")[0], 404)
        self.assertEqual(observed, [("/other-webhook", raw), ("/other-health", raw)])

    def test_health_adds_only_enabled_reminder_domain_without_editing_original_gates(self):
        original_domains = self.srv.SUPPORTED_WRITE_DOMAINS
        code, response = self.invoke(path="/health", method="GET")
        self.assertEqual(code, 200)
        self.assertEqual(response["write_domains"], ["aura.grocery_capture", "aura.reminder_capture"])
        self.assertIs(self.srv.SUPPORTED_WRITE_DOMAINS, original_domains)
        self.cfg = replace(self.cfg, write_domains=frozenset({"aura.grocery_capture"}))
        code, response = self.invoke(path="/health", method="GET")
        self.assertEqual(response["write_domains"], ["aura.grocery_capture"])
        self.model.assert_not_called()
        self.todoist_transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
