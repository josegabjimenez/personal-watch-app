"""Synthetic regression coverage for the exact pre-MVP grocery refusal."""
from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.error import HTTPError

import mobile_capture_staging_server as srv
import test_mobile_capture_staging_server as existing


def legacy_response(payload):
    return {
        "status": "needs_confirmation",
        "dry_run": True,
        "write_committed": False,
        "request_id": payload["request_id"],
        "domain": "aura.grocery_capture",
        "display_message": "No parece un artículo de mercado · no cambié nada",
        "question": "¿Qué artículo quieres agregar o marcar como comprado?",
        "plan": {
            "side_effects": ["notion.official_grocery_list.plan_only"],
            "would_write": False,
            "notes": "Would update official grocery list; unchecked means missing.",
        },
        "gates_missing": ["server domain allowlist", "external write adapter"],
    }


class LegacyGroceryClarificationTests(unittest.TestCase):
    def setUp(self):
        self.payload = existing.MobileCaptureStagingServerTests.grocery_payload(live=True)
        self.cached = legacy_response(self.payload)

    def correct(self, payload=None, cached=None, ledger_status="needs_confirmation"):
        return srv.correct_legacy_grocery_clarification(
            self.payload if payload is None else payload,
            self.cached if cached is None else cached,
            ledger_status,
        )

    def test_exact_legacy_refusal_becomes_live_no_write_without_mutation(self):
        original = copy.deepcopy(self.cached)
        result = self.correct()
        self.assertEqual(self.cached, original)
        self.assertIsNot(result, self.cached)
        self.assertIsNot(result["plan"], self.cached["plan"])
        self.assertEqual(result["status"], "needs_input")
        self.assertIs(result["dry_run"], False)
        self.assertIs(result["write_committed"], False)
        self.assertIs(result["plan"]["would_write"], False)
        self.assertEqual(result["plan"]["side_effects"], [])
        self.assertEqual(result["gates_missing"], [])
        self.assertEqual(result["request_id"], self.payload["request_id"])
        self.assertEqual(result["domain"], "aura.grocery_capture")
        self.assertTrue(result["question"])

    def test_every_response_proof_is_required(self):
        mutations = {
            "status": "accepted", "dry_run": False, "write_committed": True,
            "request_id": "other-request", "domain": "aura.reminder_capture",
            "display_message": "Other clarification", "question": "Other question",
            "plan": {}, "gates_missing": [],
        }
        for field, replacement in mutations.items():
            for omit in (False, True):
                with self.subTest(field=field, omit=omit):
                    cached = copy.deepcopy(self.cached)
                    if omit:
                        del cached[field]
                    else:
                        cached[field] = replacement
                    self.assertIs(self.correct(cached=cached), cached)
        for field in ("external_ids", "confirmation_token", "operation_id", "duplicate", "unknown"):
            with self.subTest(extra_field=field):
                cached = dict(self.cached, **{field: None})
                self.assertIs(self.correct(cached=cached), cached)
        for field, value in (("dry_run", 1), ("dry_run", "true"), ("write_committed", 0), ("write_committed", None)):
            with self.subTest(field=field, value=value):
                cached = dict(self.cached, **{field: value})
                self.assertIs(self.correct(cached=cached), cached)

    def test_plan_and_terminal_ledger_proof_are_required(self):
        for field, value in (("would_write", True), ("would_write", 0), ("would_write", None),
                             ("side_effects", []), ("notes", "other"), ("extra", False)):
            with self.subTest(field=field, value=value):
                cached = copy.deepcopy(self.cached)
                cached["plan"][field] = value
                self.assertIs(self.correct(cached=cached), cached)
        for status in ("committed", "duplicate", "writing", "uncertain", "processing", "waiting_for_server_gate"):
            with self.subTest(ledger_status=status):
                self.assertIs(self.correct(ledger_status=status), self.cached)

    def test_only_exact_safe_live_grocery_request_qualifies(self):
        for field, value in (("dry_run", True), ("dry_run", 0), ("allow_write", False),
                             ("allow_write", 1), ("allow_firefly_write", True),
                             ("allow_firefly_write", 0), ("user_confirmation", True),
                             ("user_confirmation", 0), ("user_cancellation", True),
                             ("requires_confirmation", True), ("confirmation_token", "token"),
                             ("operation_id", "operation")):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.payload)
                payload["context"][field] = value
                self.assertIs(self.correct(payload=payload), self.cached)
        for field in ("dry_run", "allow_write", "allow_firefly_write"):
            payload = copy.deepcopy(self.payload)
            del payload["context"][field]
            self.assertIs(self.correct(payload=payload), self.cached)
        for route in ({"agent": "aura", "intent": "reminder", "domain": "aura.reminder_capture"},
                      {"agent": "megan", "intent": "expense", "domain": "megan.expense_capture"},
                      {"agent": "aura", "intent": "grocery", "domain": "AURA.GROCERY_CAPTURE"}):
            payload = dict(self.payload, route=route)
            self.assertIs(self.correct(payload=payload), self.cached)

    def test_cached_http_replays_do_not_call_helper_or_modify_stored_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "ledger.sqlite"
            cfg = existing.MobileCaptureStagingServerTests.write_config("aura.grocery_capture", ledger_path=ledger)
            srv.init_db(ledger)
            raw = json.dumps(self.payload, separators=(",", ":")).encode()
            row = (self.payload["request_id"], hashlib.sha256(raw).hexdigest(), "aura.grocery_capture",
                   "needs_confirmation", json.dumps(self.cached, ensure_ascii=False), "original-created", "original-updated")
            with sqlite3.connect(ledger) as conn:
                conn.execute("INSERT INTO mobile_capture_ledger VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            before_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
            with mock.patch.object(srv, "run_aura_helper") as helper:
                responses = existing.MobileCaptureStagingServerTests.signed_posts(self.payload, cfg)
                helper.assert_not_called()
            for status, response in responses:
                self.assertEqual(status, 200)
                self.assertEqual(response["status"], "needs_input")
                self.assertIs(response["dry_run"], False)
                self.assertIs(response["write_committed"], False)
                self.assertTrue(response["duplicate"])
            with sqlite3.connect(ledger) as conn:
                self.assertEqual(conn.execute("SELECT * FROM mobile_capture_ledger").fetchone(), row)
            self.assertEqual(hashlib.sha256(ledger.read_bytes()).hexdigest(), before_hash)
            changed = copy.deepcopy(self.payload)
            changed["capture"]["text"] = "different article"
            with mock.patch.object(srv, "run_aura_helper") as helper:
                with self.assertRaises(HTTPError) as caught:
                    existing.MobileCaptureStagingServerTests.signed_posts(changed, cfg, count=1)
                self.assertEqual(caught.exception.code, 409)
                helper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
