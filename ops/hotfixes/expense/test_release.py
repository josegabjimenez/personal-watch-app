"""The exact generated package on signed requests and synthetic Firefly storage."""
from __future__ import annotations

from contextlib import ExitStack
import copy
from dataclasses import replace
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import build_release as build


AURA_DOMAINS = frozenset({"aura.grocery_capture", "aura.reminder_capture"})
EXPENSE_DOMAIN = "megan.expense_capture"
PAYLOAD = {
    "event_type": "mobile_capture.v1", "schema_version": 1, "request_id": "synthetic-watch-expense-1",
    "created_at": "2026-11-19T09:15:00-05:00",
    "source": {"device_id": "synthetic-test-watch"},
    "route": {"agent": "megan", "intent": "expense", "domain": EXPENSE_DOMAIN},
    "capture": {"text": "45 mil almuerzo en efectivo"},
    "context": {"dry_run": False, "allow_write": True, "allow_firefly_write": True,
                "user_confirmation": False, "requires_confirmation": True},
}
CATALOG = {
    "currencies": [{"id": "1", "code": "COP"}],
    "accounts": [
        {"id": "4", "name": "Cash", "type": "asset", "active": True, "currency_code": "COP"},
        {"id": "7", "name": "Food", "type": "expense", "active": True},
    ],
    "categories": [{"id": "1", "name": "Food"}],
    "budgets": [{"id": "12", "name": "Comidas afuera", "active": True}],
    "tags": [],
}


class FakeFirefly:
    def __init__(self):
        self.records = []
        self.hide_readback = False
        self.lose_helper_reply = False
        self.modes = []

    def load_catalog(self):
        return CATALOG

    def search_transactions(self, _query):
        return []

    def find_by_external_id(self, external_id, _date):
        return [] if self.hide_readback else [row for row in self.records if row["external_id"] == external_id]

    def create_withdrawal(self, transaction):
        self.records.append({**transaction, "journal_id": "9001", "group_id": "8001"})
        return self.records[-1]


class ExpenseReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packager, cls.manifest, cls.runtime = build.candidate()
        _, cls.baseline_manifest, cls.baseline = build.sealed_baseline()

    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        self.folder = Path(stack.enter_context(tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())))
        self.release = self.folder / "candidate"
        self.release.mkdir()
        for name, code in self.runtime.items():
            (self.release / name).write_bytes(code)
        (self.release / "manifest.json").write_bytes(self.packager.encoded(self.manifest))

        names = {Path(name).stem for name in self.runtime} | {"_gasto_candidate_server", "_gasto_legacy_server"}
        saved_modules = {name: sys.modules.pop(name) for name in names if name in sys.modules}
        def restore_modules():
            for name in names:
                sys.modules.pop(name, None)
            sys.modules.update(saved_modules)
        stack.callback(restore_modules)
        stack.enter_context(mock.patch.object(sys, "path", [str(self.release), *sys.path]))
        stack.enter_context(mock.patch.object(sys, "dont_write_bytecode", True))

        self.server = self.load_module("_gasto_candidate_server", self.release / build.SERVER)
        legacy_path = self.folder / "legacy.py"
        legacy_path.write_bytes(self.baseline[build.SERVER])
        self.legacy = self.load_module("_gasto_legacy_server", legacy_path)
        self.engine = sys.modules["expense_engine"]
        self.adapter = self.load_module("mobile_expense_capture", self.release / "mobile_expense_capture.py")
        self.cfg = self.server.Config(secret=b"synthetic-unit-test-hmac", ledger_path=self.folder / "state/mobile.sqlite",
                                      host="127.0.0.1", port=0, write_enabled=True,
                                      write_domains=AURA_DOMAINS | {EXPENSE_DOMAIN})
        self.gateway = FakeFirefly()
        self.transport = stack.enter_context(mock.patch.object(self.engine.subprocess, "run", side_effect=self.fake_helper))

    @staticmethod
    def load_module(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def fake_helper(self, command, *, input, **_kwargs):
        self.assertEqual(Path(command[1]), self.release / "mobile_expense_capture.py")
        mode = command[command.index("--mode") + 1]
        self.gateway.modes.append(mode)
        self.assertEqual("--dry-run" in command, mode == "plan")
        result = self.adapter.process_payload(json.loads(input), self.gateway, dry_run=mode == "plan", mode=mode)
        if mode == "commit" and self.gateway.lose_helper_reply:
            raise subprocess.TimeoutExpired(command, 90)
        return SimpleNamespace(stdout=json.dumps(result), returncode=0)

    def request(self, body, *, server=None, cfg=None, signature=None, method="POST"):
        server, cfg = server or self.server, cfg or self.cfg
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
        stamp = str(int(time.time()))
        signed = hmac.new(cfg.secret, stamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        response = io.BytesIO()
        handler = object.__new__(server.MobileCaptureHandler)
        handler.server = SimpleNamespace(cfg=cfg)
        handler.path = "/health" if method == "GET" else "/webhooks/mobile-capture-v1"
        handler.headers = {"Content-Length": str(len(raw)), "X-Webhook-Timestamp": stamp,
                           "X-Webhook-Signature-V2": signed if signature is None else signature,
                           "X-Request-ID": body["request_id"]}
        handler.rfile, handler.wfile = io.BytesIO(raw), response
        handler.send_response = mock.Mock()
        handler.send_header = mock.Mock()
        handler.end_headers = mock.Mock()
        getattr(handler, "do_" + method)()
        return int(handler.send_response.call_args.args[0]), json.loads(response.getvalue())

    @staticmethod
    def action(initial, preview, request_id, *, cancel=False):
        body = copy.deepcopy(initial)
        body["request_id"] = request_id
        context = body["context"]
        context.update(operation_id=preview["operation_id"], user_confirmation=not cancel,
                       requires_confirmation=False, allow_firefly_write=not cancel)
        if cancel:
            context["user_cancellation"] = True
        else:
            context["confirmation_token"] = preview["confirmation_token"]
        return body

    def preview(self, payload=PAYLOAD):
        code, reply = self.request(payload)
        self.assertEqual((code, reply["status"]), (200, "needs_confirmation"), reply)
        self.assertFalse(reply["write_committed"])
        self.assertEqual(reply["confirmation"]["currency_code"], "COP")
        self.assertEqual(reply["confirmation"]["amount_cop"], 45000)
        self.assertEqual(reply["confirmation"]["source_name"], "Cash")
        self.assertEqual(self.gateway.records, [])
        return reply

    def assert_receipt(self, reply, preview):
        self.assertTrue(reply["write_committed"])
        self.assertEqual(reply["operation_id"], preview["operation_id"])
        self.assertEqual(reply["external_ids"]["firefly_journal_ids"], ["9001"])
        self.assertEqual(reply["external_ids"]["firefly_external_id"], "hermes-watch:" + preview["operation_id"])
        self.assertEqual(len(self.gateway.records), 1)

    def test_sealed_baseline_matches_all_pins_and_only_wrapper_bytes_change(self):
        build.verify(self.release, self.packager, self.manifest)
        self.assertEqual(self.manifest["baseline_manifest_sha256"], build.BASELINE_MANIFEST_SHA256)
        for name in self.baseline:
            self.assertEqual(self.packager.digest(self.baseline[name]), self.baseline_manifest["artifacts"][name])
            if name != build.SERVER:
                self.assertEqual(self.runtime[name], self.baseline[name])
        (self.release / "expense_runtime.py").write_text("# tampered")
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            build.verify(self.release, self.packager, self.manifest)

    def test_health_and_non_expense_routes_preserve_legacy_behavior(self):
        disabled = replace(self.cfg, write_domains=AURA_DOMAINS)
        inactive = replace(self.cfg, write_enabled=False)
        self.assertEqual(self.request(PAYLOAD, cfg=disabled, method="GET"),
                         self.request(PAYLOAD, cfg=disabled, server=self.legacy, method="GET"))
        self.assertEqual(self.request(PAYLOAD, cfg=inactive, method="GET")[1]["write_domains"], [])
        self.assertEqual(self.request(PAYLOAD, method="GET")[1]["write_domains"], sorted(AURA_DOMAINS | {EXPENSE_DOMAIN}))
        for domain, intent in (("aura.grocery_capture", "grocery"), ("aura.reminder_capture", "reminder")):
            body = copy.deepcopy(PAYLOAD)
            body.update(request_id="synthetic-" + intent, route={"agent": "aura", "intent": intent, "domain": domain})
            body["context"].update(dry_run=True, allow_write=False, allow_firefly_write=False, requires_confirmation=False)
            reference = replace(disabled, ledger_path=self.folder / (intent + "/legacy.sqlite"))
            self.assertEqual(self.request(body, cfg=disabled),
                             self.request(body, cfg=reference, server=self.legacy))
        self.transport.assert_not_called()

    def test_old_dry_run_and_shared_request_hash_are_preserved_then_new_id_previews(self):
        disabled = replace(self.cfg, write_domains=AURA_DOMAINS)
        code, original = self.request(PAYLOAD, cfg=disabled, server=self.legacy)
        self.assertEqual(code, 200)
        self.assertTrue(original["dry_run"])
        code, replay = self.request(PAYLOAD)
        self.assertEqual(code, 200)
        self.assertTrue(replay["dry_run"])
        self.assertNotIn("confirmation_token", replay)
        conflict = copy.deepcopy(PAYLOAD)
        conflict["capture"]["text"] = "45 mil almuerzo en efectivo sintético"
        self.assertEqual(self.request(conflict)[0], 409)
        crossed = copy.deepcopy(PAYLOAD)
        crossed["route"].update(agent="aura", intent="grocery", domain="aura.grocery_capture")
        self.assertEqual(self.request(crossed)[0], 409)
        self.transport.assert_not_called()
        fresh = dict(PAYLOAD, request_id="synthetic-new-expense-draft")
        self.assertEqual(self.preview(fresh)["request_id"], fresh["request_id"])

    def test_only_signed_exact_expense_with_explicit_gate_reaches_the_adapter(self):
        self.assertEqual(self.request(PAYLOAD, signature="0" * 64)[0], 401)
        wrong_route = copy.deepcopy(PAYLOAD)
        wrong_route.update(request_id="synthetic-alias-expense")
        wrong_route["route"]["intent"] = "expense_capture"
        self.assertTrue(self.request(wrong_route)[1]["dry_run"])
        disabled = replace(self.cfg, write_domains=AURA_DOMAINS)
        self.assertTrue(self.request(PAYLOAD, cfg=disabled)[1]["dry_run"])
        self.transport.assert_not_called()
        self.preview()

    def test_cancel_survives_gate_disable_and_keeps_zero_records(self):
        preview = self.preview()
        cancel = self.action(PAYLOAD, preview, "synthetic-cancel", cancel=True)
        disabled = replace(self.cfg, write_domains=AURA_DOMAINS)
        self.assertEqual(self.request(cancel, cfg=disabled)[1]["status"], "cancelled")
        self.assertNotIn("confirmation_token", self.request(PAYLOAD)[1])
        confirm = self.action(PAYLOAD, preview, "synthetic-confirm-after-cancel")
        self.assertEqual(self.request(confirm)[1]["status"], "rejected")
        self.assertEqual(self.gateway.modes, ["plan"])
        self.assertEqual(self.gateway.records, [])

    def test_confirm_once_then_exact_and_new_confirmation_deliveries_keep_one_receipt(self):
        preview = self.preview()
        confirm = self.action(PAYLOAD, preview, "synthetic-confirm")
        code, receipt = self.request(confirm)
        self.assertEqual((code, receipt["status"]), (200, "committed"))
        self.assert_receipt(receipt, preview)
        for duplicate in (confirm, dict(confirm, request_id="synthetic-confirm-new-id")):
            code, replay = self.request(duplicate)
            self.assertEqual((code, replay["status"]), (200, "duplicate"))
            self.assert_receipt(replay, preview)
        self.assertEqual(self.gateway.modes.count("commit"), 1)

    def test_delayed_readback_after_one_post_is_retryable_and_never_posts_again(self):
        preview = self.preview()
        confirm = self.action(PAYLOAD, preview, "synthetic-uncertain-confirm")
        self.gateway.hide_readback = True
        code, pending = self.request(confirm)
        self.assertEqual(code, 503)
        self.assertEqual(pending["operation_id"], preview["operation_id"])
        self.assertEqual(len(self.gateway.records), 1)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            self.assertEqual(conn.execute("SELECT status FROM expense_operation_ledger").fetchone()[0], "uncertain")
        self.assertEqual(self.request(confirm)[0], 503)
        repeat_text = dict(PAYLOAD, request_id="synthetic-repeat-draft")
        self.assertNotIn("confirmation_token", self.request(repeat_text)[1])
        self.gateway.hide_readback = False
        # A fresh request handler and database connection recover from the same
        # signed confirmation after the fake's journal becomes visible.
        code, replacement = self.request(confirm)
        self.assertEqual(code, 200)
        self.assert_receipt(replacement, preview)
        _, replay = self.request(dict(confirm, request_id="synthetic-uncertain-confirm-new-id"))
        self.assert_receipt(replay, preview)
        self.assertEqual(self.gateway.modes.count("commit"), 1)

    def test_lost_helper_ack_preserves_hash_and_reconciles_only(self):
        preview = self.preview()
        confirm = self.action(PAYLOAD, preview, "synthetic-lost-confirm")
        self.gateway.lose_helper_reply = True
        self.assertEqual(self.request(confirm)[0], 502)
        self.assertEqual(len(self.gateway.records), 1)
        modes = list(self.gateway.modes)
        disabled = replace(self.cfg, write_domains=AURA_DOMAINS)
        code, pending = self.request(confirm, cfg=disabled)
        self.assertEqual((code, pending["request_id"]), (503, confirm["request_id"]))
        self.assertEqual(self.gateway.modes, modes)
        changed = copy.deepcopy(confirm)
        changed["capture"]["text"] = "45 mil almuerzo en efectivo sintético"
        self.assertEqual(self.request(changed)[0], 409)
        code, recovered = self.request(confirm)
        self.assertEqual(code, 200)
        self.assert_receipt(recovered, preview)
        self.assertEqual(self.gateway.modes.count("commit"), 1)


if __name__ == "__main__":
    unittest.main()
