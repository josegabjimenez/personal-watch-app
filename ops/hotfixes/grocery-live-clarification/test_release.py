from __future__ import annotations

import ast
import copy
import hashlib
import hmac
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

import build_release as build

ROOT = Path(__file__).resolve().parents[3]


def load_source(name: str, source: bytes) -> types.ModuleType:
    module = types.ModuleType(name)
    module.__file__ = __file__
    sys.modules[name] = module
    exec(compile(source, __file__, "exec"), module.__dict__)
    return module


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


class HistoricalReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.source = subprocess.check_output([
            "git", "-C", str(ROOT), "show", f"{build.BASELINE_COMMIT}:{build.BASELINE_PATH}"
        ])
        cls.release = cls.directory / "release"
        build.build_release(cls.source, cls.release)
        cls.released = (cls.release / build.ARTIFACT_NAME).read_bytes()
        cls.srv = load_source("grocery_compatibility_release", cls.released)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def payload(self):
        return {
            "event_type": "mobile_capture.v1", "schema_version": 1,
            "request_id": "synthetic-legacy-grocery",
            "route": {"agent": "aura", "intent": "grocery", "domain": "aura.grocery_capture"},
            "capture": {"text": "synthetic article"},
            "context": {"dry_run": False, "allow_write": True, "allow_firefly_write": False},
        }

    def legacy(self, payload):
        cached = self.srv.plan_capture(payload)
        cached.update(status="needs_confirmation",
            display_message="No parece un artículo de mercado · no cambié nada",
            question="¿Qué artículo quieres agregar o marcar como comprado?")
        return cached

    def config(self, ledger: Path, enabled=True):
        return self.srv.Config(secret=b"synthetic-release-only", ledger_path=ledger,
            host="127.0.0.1", port=0, write_enabled=enabled,
            write_domains=frozenset({"aura.grocery_capture"}), aura_helper=Path(__file__))

    def assert_clarification(self, response, payload):
        self.assertEqual(response["status"], "needs_input")
        self.assertIs(response["dry_run"], False)
        self.assertIs(response["write_committed"], False)
        self.assertEqual(response["request_id"], payload["request_id"])
        self.assertEqual(response["domain"], "aura.grocery_capture")
        self.assertEqual(response["plan"]["side_effects"], [])
        self.assertIs(response["plan"]["would_write"], False)
        self.assertEqual(response["gates_missing"], [])

    def test_fresh_mismatch_and_fallback_never_call_write_helper(self):
        for intents in [["reminder"], ["note", "note"]]:
            payload = self.payload()
            with self.subTest(intents=intents), mock.patch.object(self.srv, "run_aura_helper",
                    side_effect=[{"ok": True, "intent": value} for value in intents]) as helper:
                response = self.srv.execute_capture(payload, self.config(self.directory / "unused.sqlite"))
                self.assert_clarification(response, payload)
                self.assertEqual(helper.call_count, len(intents))
                self.assertTrue(all(call.kwargs["dry_run"] is True for call in helper.call_args_list))

    def test_exact_cache_shape_is_corrected_without_mutating_input(self):
        payload = self.payload()
        cached = self.legacy(payload)
        before = copy.deepcopy(cached)
        for explicit_flags in [False, True]:
            value = copy.deepcopy(payload)
            if explicit_flags:
                value["context"].update(user_confirmation=False, user_cancellation=False,
                    requires_confirmation=False, confirmation_token=None, operation_id=None)
            corrected = self.srv.correct_legacy_grocery_clarification(value, cached, "needs_confirmation")
            self.assertIsNot(corrected, cached)
            self.assert_clarification(corrected, payload)
            self.assertEqual(cached, before)

    def test_adversarial_cache_and_request_shapes_remain_identical(self):
        changes = []
        for key, value in [("dry_run", 0), ("dry_run", True), ("allow_write", 1),
                ("allow_write", False), ("allow_firefly_write", 0), ("allow_firefly_write", True),
                ("confirmation_token", ""), ("operation_id", "operation"),
                ("user_confirmation", 0), ("user_confirmation", None),
                ("user_cancellation", True), ("requires_confirmation", 0)]:
            changes.append(("context " + key + repr(value), lambda p, c, k=key, v=value: p["context"].update({k: v})))
        for key, value in [("agent", "megan"), ("intent", "reminder"), ("domain", "aura.reminder_capture")]:
            changes.append(("route " + key, lambda p, c, k=key, v=value: p["route"].update({k: v})))
        for key, value in [("status", "duplicate"), ("dry_run", 1), ("dry_run", False),
                ("write_committed", 0), ("write_committed", True), ("request_id", "different"),
                ("domain", "megan.expense_capture"), ("display_message", "different"),
                ("question", "different"), ("gates_missing", []), ("external_ids", {})]:
            changes.append(("response " + key, lambda p, c, k=key, v=value: c.update({k: v})))
        for key, value in [("would_write", 0), ("would_write", True), ("notes", "different"),
                ("side_effects", []), ("external_ids", {})]:
            changes.append(("plan " + key, lambda p, c, k=key, v=value: c["plan"].update({k: v})))
        changes += [
            ("missing response key", lambda p, c: c.pop("question")),
            ("missing plan key", lambda p, c: c["plan"].pop("notes")),
            ("missing finance flag", lambda p, c: p["context"].pop("allow_firefly_write")),
            ("blank request", lambda p, c: p.update(request_id=" ")),
            ("non-dict context", lambda p, c: p.update(context=[])),
            ("non-dict plan", lambda p, c: c.update(plan=[])),
            ("generic gate response", lambda p, c: c.update(status="accepted")),
        ]
        for label, change in changes:
            payload = self.payload(); cached = self.legacy(payload)
            change(payload, cached)
            with self.subTest(label=label):
                self.assertIs(self.srv.correct_legacy_grocery_clarification(payload, cached, "needs_confirmation"), cached)
        for state in ["processing", "committed", "needs_input", "duplicate", None]:
            payload = self.payload(); cached = self.legacy(payload)
            with self.subTest(ledger_state=state):
                self.assertIs(self.srv.correct_legacy_grocery_clarification(payload, cached, state), cached)

    def invoke_cached(self, payload, cached, status="needs_confirmation", *, conflict=False):
        with tempfile.TemporaryDirectory() as directory:
            cfg = self.config(Path(directory) / "ledger.sqlite")
            raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
            raw_hash = hashlib.sha256(raw).hexdigest()
            self.srv.init_db(cfg.ledger_path)
            original_json = json.dumps(cached, ensure_ascii=False, indent=3)
            with sqlite3.connect(cfg.ledger_path) as connection:
                connection.execute("INSERT INTO mobile_capture_ledger VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (payload["request_id"], raw_hash, "aura.grocery_capture", status, original_json, "synthetic", "synthetic"))
            before_bytes = cfg.ledger_path.read_bytes()
            if conflict:
                changed = copy.deepcopy(payload); changed["capture"]["text"] = "another synthetic article"
                raw = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
            timestamp = str(int(time.time()))
            signature = hmac.new(cfg.secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
            handler = types.SimpleNamespace(path="/webhooks/mobile-capture-v1", cfg=cfg,
                headers={"Content-Length": str(len(raw)), "X-Webhook-Timestamp": timestamp,
                    "X-Webhook-Signature-V2": signature, "X-Request-ID": payload["request_id"]},
                rfile=io.BytesIO(raw))
            with mock.patch.object(self.srv, "json_response") as reply, \
                    mock.patch.object(self.srv, "execute_capture", side_effect=AssertionError("cached request executed")), \
                    mock.patch.object(self.srv, "run_aura_helper", side_effect=AssertionError("helper executed")):
                self.srv.MobileCaptureHandler.do_POST(handler)
                reply.assert_called_once()
                code, response = reply.call_args.args[1:]
            with sqlite3.connect(cfg.ledger_path) as connection:
                row = connection.execute("SELECT * FROM mobile_capture_ledger").fetchone()
            self.assertEqual(row, (payload["request_id"], raw_hash, "aura.grocery_capture", status, original_json, "synthetic", "synthetic"))
            self.assertEqual(cfg.ledger_path.read_bytes(), before_bytes)
            return code, response

    def test_actual_cached_handler_keeps_ledger_bytes_and_hash_unchanged(self):
        payload = self.payload()
        code, response = self.invoke_cached(payload, self.legacy(payload))
        self.assertEqual(code, 200)
        self.assert_clarification(response, payload)
        self.assertIs(response["duplicate"], True)

    def test_cache_payload_conflict_still_returns_409_without_helper(self):
        payload = self.payload()
        code, response = self.invoke_cached(payload, self.legacy(payload), conflict=True)
        self.assertEqual(code, 409)
        self.assertEqual(response["error"], "request_id_conflict")

    def test_committed_and_generic_dry_run_cache_semantics_are_unchanged(self):
        payload = self.payload()
        for committed in [False, True]:
            cached = self.srv.plan_capture(payload)
            if committed:
                cached.update(status="committed", dry_run=False, write_committed=True,
                    external_ids={"notion_block_id": "synthetic-block"})
                cached["plan"].update(would_write=True, side_effects=["notion.official_grocery_list.write"])
            code, response = self.invoke_cached(payload, cached, status=cached["status"])
            self.assertEqual(code, 200)
            expected = copy.deepcopy(cached); expected.update(status="duplicate", duplicate=True)
            self.assertEqual(response, expected)

    def test_server_gate_off_and_client_dry_run_stay_planning_only(self):
        for live in [False, True]:
            payload = self.payload()
            if not live: payload["context"].update(dry_run=True, allow_write=False)
            with mock.patch.object(self.srv, "run_aura_helper", side_effect=AssertionError("gate bypass")):
                response = self.srv.execute_capture(payload, self.config(self.directory / "unused.sqlite", enabled=False))
            self.assertEqual(response["status"], "accepted")
            self.assertIs(response["dry_run"], True)
            self.assertIs(response["write_committed"], False)

    def test_release_build_is_deterministic_and_contains_only_two_files(self):
        second = self.directory / "second-build"
        build.build_release(self.source, second)
        self.assertEqual(sorted(p.name for p in second.iterdir()), ["manifest.json", build.ARTIFACT_NAME])
        for name in ["manifest.json", build.ARTIFACT_NAME]:
            self.assertEqual((second / name).read_bytes(), (self.release / name).read_bytes())
        build.verify_release(second)

    def test_packager_rejects_changed_baseline_patch_output_and_extra_files(self):
        with self.assertRaisesRegex(ValueError, "Baseline SHA256"):
            build.build_release(self.source + b"\n", self.directory / "bad-source")
        bad_patch = self.directory / "bad.patch"; bad_patch.write_bytes(build.PATCH_PATH.read_bytes() + b"\n")
        with mock.patch.object(build, "PATCH_PATH", bad_patch), self.assertRaisesRegex(ValueError, "Patch SHA256"):
            build.build_release(self.source, self.directory / "bad-patch")
        for change in ["artifact", "manifest", "extra"]:
            out = self.directory / ("tampered-" + change); build.build_release(self.source, out)
            if change == "artifact": (out / build.ARTIFACT_NAME).write_bytes(self.released + b"\n")
            elif change == "manifest": (out / "manifest.json").write_bytes(b"{}")
            else: (out / "unexpected.py").write_text("pass")
            with self.subTest(change=change), self.assertRaises(ValueError): build.verify_release(out)

    def test_all_unrelated_historical_functions_and_imports_are_identical(self):
        old = ast.parse(self.source); new = ast.parse(self.released)
        original_nodes = {getattr(node, "name", f"node-{index}"): node for index, node in enumerate(old.body)}
        allowed = {"execute_capture", "MobileCaptureHandler"}
        for key, node in original_nodes.items():
            if key in allowed: continue
            if key.startswith("node-"):
                self.assertIn(ast.dump(node), [ast.dump(value) for value in new.body])
            else:
                matched = next(value for value in new.body if getattr(value, "name", None) == key)
                self.assertEqual(ast.dump(node), ast.dump(matched), key)
        old_class = next(node for node in old.body if getattr(node, "name", None) == "MobileCaptureHandler")
        new_class = next(node for node in new.body if getattr(node, "name", None) == "MobileCaptureHandler")
        for node in old_class.body:
            if getattr(node, "name", None) == "do_POST": continue
            self.assertIn(ast.dump(node), [ast.dump(value) for value in new_class.body])

    def test_unchanged_historical_suite_except_explicit_obsolete_expectation(self):
        source = subprocess.check_output(["git", "-C", str(ROOT), "show",
            f"{build.BASELINE_COMMIT}:backend/tests/test_mobile_capture_staging_server.py"])
        previous = sys.modules.get("mobile_capture_staging_server")
        sys.modules["mobile_capture_staging_server"] = self.srv
        try:
            legacy = load_source("historical_grocery_test_source", source)
        finally:
            if previous is None: sys.modules.pop("mobile_capture_staging_server", None)
            else: sys.modules["mobile_capture_staging_server"] = previous
        cases = list(flatten(unittest.defaultTestLoader.loadTestsFromModule(legacy)))
        obsolete = [case for case in cases if case.id().endswith(".test_grocery_route_mismatch_does_not_execute_write")]
        self.assertEqual(len(obsolete), 1)
        # This one test explicitly asserts the malformed old needs_confirmation /
        # dry_run=True envelope. Its replacement assertions are above; its source
        # is never rewritten. Every other historical test runs unchanged.
        unchanged = unittest.TestSuite(case for case in cases if case not in obsolete)
        stream = io.StringIO(); result = unittest.TextTestRunner(stream=stream).run(unchanged)
        self.assertTrue(result.wasSuccessful(), stream.getvalue())
        print(f"Unchanged historical tests passed: {result.testsRun}; obsolete envelope expectation replaced explicitly: 1")


if __name__ == "__main__":
    unittest.main()
