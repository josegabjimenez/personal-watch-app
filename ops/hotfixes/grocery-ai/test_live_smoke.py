"""Synthetic smoke harness contracts; no real provider or grocery calls."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
from datetime import datetime, timezone
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend")]
import grocery_ai as ai
import grocery_capture
from grocery_ai_budget import GroceryAIBudget, initialize_schema
import live_smoke


class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        journal = Path(self.temp.name) / "synthetic.sqlite3"
        grocery_capture.init_db(journal)
        initialize_schema(journal)
        self.guard = GroceryAIBudget(journal)

    @staticmethod
    def response(proposal):
        return {"status": "completed", "model": "gpt-6-luna", "service_tier": "default",
                "usage": {"input_tokens": 2000, "output_tokens": 200, "total_tokens": 2200,
                          "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": 150}},
                "output": [{"type": "message", "role": "assistant", "status": "completed",
                            "content": [{"type": "output_text", "text": json.dumps(proposal)}]}]}

    def test_fixed_synthetic_cases_use_same_guard_and_repeat_never_dispatches(self):
        expected = [case[3] for case in live_smoke.cases()]
        expected[1] = {"decision": "needs_input", "question": "¿Entera o sin lactosa?", "items": []}
        with mock.patch.object(ai, "_post_json", side_effect=[self.response(value) for value in expected]) as post:
            result = live_smoke.run_cases(ai, self.guard, "synthetic-key", "synthetic-run")
            repeated = live_smoke.run_cases(ai, self.guard, "synthetic-key", "synthetic-run")
        self.assertTrue(result["passed"])
        self.assertEqual(post.call_count, 3)
        self.assertFalse(repeated["passed"])
        self.assertEqual(repeated["cases"][0]["status"], "grocery_ai_budget_duplicate")
        self.assertEqual(result["grocery_writes"], 0)
        self.assertEqual(result["unsettled_reserved_nanodollars"], 0)
        self.assertEqual(result["guard_accounting_delta_nanodollars"], 3 * (2000 * 300 + 200 * 1000))

    def test_unexpected_semantics_stops_after_one_charged_call(self):
        with mock.patch.object(ai, "_post_json", return_value=self.response({
                "decision": "needs_input", "question": "¿Qué productos?", "items": []})) as post:
            result = live_smoke.run_cases(ai, self.guard, "synthetic-key", "synthetic-failure")
        self.assertFalse(result["passed"])
        post.assert_called_once()
        self.assertEqual(result["cases"][0]["status"], "unexpected_interpretation")

    def test_provider_failure_keeps_reservation_and_stops_without_retry(self):
        with mock.patch.object(ai, "_post_json", side_effect=TimeoutError("synthetic private failure")) as post:
            result = live_smoke.run_cases(ai, self.guard, "synthetic-key", "synthetic-timeout")
        self.assertFalse(result["passed"])
        post.assert_called_once()
        self.assertEqual(result["unsettled_reserved_nanodollars"], 318_000_000)
        self.assertNotIn("private failure", json.dumps(result))

    def test_exact_verified_sources_bypass_preloaded_or_cached_modules(self):
        sources = {"grocery_item_parser.py": b"VALUE = 'verified'\n",
                   "grocery_ai_budget.py": b"VALUE = 'verified-budget'\n",
                   "grocery_ai.py": b"from grocery_item_parser import VALUE\n"}
        poison = types.ModuleType("grocery_item_parser")
        poison.VALUE = "unverified"
        names = ("grocery_item_parser", "grocery_ai_budget", "grocery_ai")
        with mock.patch.dict(sys.modules, {name: poison for name in names}):
            loaded, budget = live_smoke.load_runtime(Path(self.temp.name), sources)
            self.assertEqual(loaded.VALUE, "verified")
            self.assertEqual(budget.VALUE, "verified-budget")

    def test_month_change_has_no_false_smoke_cost_delta(self):
        journal = Path(self.temp.name) / "boundary.sqlite3"
        grocery_capture.init_db(journal)
        now = [datetime(2026, 11, 1, 4, 59, tzinfo=timezone.utc)]
        initialize_schema(journal, clock=lambda: now[0])
        guard = GroceryAIBudget(journal, clock=lambda: now[0])
        expected = [case[3] for case in live_smoke.cases()]
        expected[1] = {"decision": "needs_input", "question": "¿Entera o sin lactosa?", "items": []}
        def transport(*args):
            now[0] = datetime(2026, 11, 1, 5, 1, tzinfo=timezone.utc)
            return self.response(expected.pop(0))
        with mock.patch.object(ai, "_post_json", side_effect=transport):
            result = live_smoke.run_cases(ai, guard, "synthetic-key", "synthetic-boundary")
        self.assertTrue(result["passed"])
        self.assertTrue(result["month_changed"])
        self.assertIsNone(result["guard_accounting_delta_nanodollars"])

    def test_separate_manifest_verifies_installed_bytes_without_seventh_file(self):
        runtime = Path(self.temp.name) / "installed"
        package = Path(self.temp.name) / "package"
        runtime.mkdir()
        package.mkdir()
        names = ("mobile_capture_staging_server.py", "grocery_capture.py", "grocery_helper_adapter.py",
                 "grocery_item_parser.py", "grocery_ai.py", "grocery_ai_budget.py")
        hashes = {}
        for name in names:
            source = ("# synthetic " + name + "\n").encode()
            (runtime / name).write_bytes(source)
            hashes[name] = hashlib.sha256(source).hexdigest()
        raw = json.dumps({"runtime_file_count": 6, "artifacts": hashes}).encode()
        manifest = package / "manifest.json"
        manifest.write_bytes(raw)
        pin = hashlib.sha256(raw).hexdigest()
        self.assertEqual(set(live_smoke.verify_runtime(runtime, pin, manifest_path=manifest)), set(names))
        self.assertFalse((runtime / "manifest.json").exists())
        (runtime / "grocery_ai.py").write_text("# unexpected installed code\n")
        with self.assertRaisesRegex(ValueError, "runtime_mismatch"):
            live_smoke.verify_runtime(runtime, pin, manifest_path=manifest)


if __name__ == "__main__":
    unittest.main()
