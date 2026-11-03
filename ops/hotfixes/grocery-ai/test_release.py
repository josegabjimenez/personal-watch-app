"""Offline tests of the actual generated historical grocery-only AI package.

Reuse the sealed historical handler's synthetic fault scenarios without changing
its files. Every runtime import is then loaded from the newly generated package;
provider requests receive synthetic Responses objects, never external traffic.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest import mock

import build_release as build

ROOT = Path(__file__).resolve().parents[3]
HISTORICAL_SOURCE_COMMIT = "77885a5cc4d018b7e5b49266fd909e591078cf48"
sys.path[:0] = [str(ROOT / "backend/tests"), str(ROOT / "backend")]


def _legacy_tests():
    path = ROOT / "ops/hotfixes/grocery-multi-item/test_release.py"
    spec = importlib.util.spec_from_file_location("_grocery_ai_legacy_release_scenarios", path)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(sys.modules, {"build_release": build}):
        exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


_legacy = _legacy_tests()


class HistoricalGroceryAIReleaseTests(_legacy.HistoricalMultiItemReleaseTests):
    """Inherited safety cases execute this five-file package with AI disabled."""

    test_actual_package_uses_only_four_pinned_runtime_files_and_no_held_imports = None

    def setUp(self):
        original = sys.modules.get("grocery_ai")
        def restore_ai():
            if original is None:
                sys.modules.pop("grocery_ai", None)
            else:
                sys.modules["grocery_ai"] = original
        self.addCleanup(restore_ai)
        super().setUp()

    def restart(self):
        for name in ("grocery_item_parser", "grocery_ai", "grocery_helper_adapter", "grocery_capture"):
            module = _legacy.load_module(name, self.release / (name + ".py"))
            if name == "grocery_ai":
                self.ai = module
            elif name == "grocery_helper_adapter":
                self.adapter = module
                self.stack.enter_context(mock.patch.object(module, "PINNED_HELPER_SHA256",
                    hashlib.sha256(self.helper_source.encode()).hexdigest()))
            elif name == "grocery_capture":
                self.engine = module
        self.srv = _legacy.load_module("_historical_grocery_test_server", self.release / build.SERVER)
        self.cfg = self.srv.Config(secret=b"synthetic-historical-secret", ledger_path=self.case / "mobile.sqlite3",
            host="127.0.0.1", port=0, write_enabled=True,
            write_domains=frozenset({"aura.grocery_capture"}), aura_helper=self.helper)

    def enable_ai(self):
        key_file = self.case / "synthetic-provider-key"
        key_file.write_text("synthetic-grocery-key")
        key_file.chmod(0o600)
        self.stack.enter_context(mock.patch.dict("os.environ", {
            "GROCERY_AI_ENABLED": "1", "GROCERY_AI_OPENAI_KEY_FILE": str(key_file),
        }))

    def proposal(self, items=None, *, decision="apply", question=None):
        return {"decision": decision, "question": question, "items": [] if items is None else items}

    def synthetic_provider(self, proposal):
        response = {"status": "completed", "output": [{"type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": json.dumps(proposal, ensure_ascii=False)}]}]}
        return mock.patch.object(self.ai, "_post_json", return_value=response)

    def assert_swift_wire(self, response, *, wrote):
        # The installed Swift delivery gate requires these exact correlated
        # fields and permits terminal no-write clarification without tokens.
        self.assertEqual(response["request_id"], self.payload["request_id"])
        self.assertEqual(response["domain"], "aura.grocery_capture")
        self.assertIs(response["dry_run"], False)
        self.assertIs(response["write_committed"], wrote)
        self.assertIs(response["plan"]["would_write"], wrote)
        self.assertIn(response["status"], {"committed", "duplicate"} if wrote else {"needs_input", "rejected", "duplicate"})
        self.assertTrue(response["display_message"])
        self.assertNotIn("confirmation_token", response)
        self.assertNotIn("confirmation", response)
        self.assertNotIn("operation_id", response)

    def test_actual_package_uses_only_five_grocery_runtime_files_and_no_held_imports(self):
        self.assertEqual(sorted(path.name for path in self.release.iterdir()), sorted((*build.ARTIFACTS, "manifest.json")))
        self.assertEqual(self.expected["runtime_file_count"], 5)
        build.verify_release(self.release, self.expected)
        self.assertEqual(self.expected["baseline_sha256"], build.BASELINE_SHA256)
        self.assertEqual(self.expected["external_helper_sha256"], _legacy.contract.ORIGINAL_SHA256)
        for module in (self.engine, self.adapter, self.ai, sys.modules["grocery_item_parser"]):
            self.assertEqual(Path(module.__file__).parent, self.release)
        self.assertIs(self.srv.grocery_capture, self.engine)
        self.assertIs(self.engine.grocery_ai, self.ai)
        for unwanted in ("reminder_ai", "reminder_capture", "todoist_reminders", "execute_expense_capture", "EXPENSE_CONFIRMATION_TTL_SECONDS"):
            self.assertNotIn(unwanted, self.srv.__dict__)
        build.assert_scope(self.baseline, (self.release / build.SERVER).read_bytes(), build.OVERLAY.read_bytes())

    def test_ai_disabled_by_default_never_calls_provider(self):
        self.assertIsNone(self.ai.config_from_environment())
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("disabled AI called")):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=True)
        with sqlite3.connect(self.engine.journal_path(self.cfg.ledger_path)) as conn:
            plan = json.loads(conn.execute("SELECT plan_json FROM grocery_batches").fetchone()[0])
        self.assertEqual(plan["version"], 1)

    def test_ai_compound_and_alias_match_are_frozen_in_actual_historical_runtime(self):
        self.enable_ai()
        self.fake.add(_legacy.block("synthetic-potatoes", "papas", checked=False))
        self.payload["capture"]["text"] = "agrega patatas, pan de ajo y queso y huevos"
        proposal = self.proposal([
            {"text": "patatas", "candidate_id": "synthetic-potatoes"},
            {"text": "pan de ajo y queso", "candidate_id": None},
            {"text": "huevos", "candidate_id": None},
        ])
        with self.synthetic_provider(proposal) as provider:
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=True)
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(response["external_ids"]["item_count"], 3)
        self.assertEqual(response["external_ids"]["already_present"], 1)
        self.assertEqual(response["external_ids"]["created"], 2)
        self.assertEqual(len(self.fake.writes), 2)
        self.assertEqual(response["external_ids"]["grocery_items"][0]["block_id"], "synthetic-potatoes")
        with sqlite3.connect(self.engine.journal_path(self.cfg.ledger_path)) as conn:
            frozen = json.loads(conn.execute("SELECT plan_json FROM grocery_batches").fetchone()[0])
        self.assertEqual(frozen["version"], 2)
        self.assertEqual(frozen["interpretation"], proposal)
        self.restart()
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("completed AI reinterpreted")):
            code, replay = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(replay["external_ids"], response["external_ids"])

    def test_ai_clarification_is_terminal_no_write_and_full_redictation_uses_new_request(self):
        self.enable_ai()
        self.payload["capture"]["text"] = "leche de la que compro"
        question = "¿Qué tipo de leche quieres?"
        with self.synthetic_provider(self.proposal(decision="needs_input", question=question)) as provider:
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=False)
        self.assertEqual(response["status"], "needs_input")
        self.assertIn(question, response["display_message"])
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(provider.call_count, 1)
        self.restart()
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("terminal no-write reinterpreted")):
            self.assertEqual(self.invoke()[0], 200)
        self.payload["request_id"] += "-complete-clarification"
        self.payload["capture"]["text"] = "leche deslactosada"
        with self.synthetic_provider(self.proposal([{"text": "leche deslactosada", "candidate_id": None}])):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=True)
        self.assertEqual(len(self.fake.writes), 1)

    def test_ai_dietary_alias_reuses_existing_candidate_without_changing_it(self):
        self.enable_ai()
        self.fake.add(_legacy.block("synthetic-lactose-free", "leche sin lactosa", checked=False))
        self.payload["capture"]["text"] = "leche deslactosada"
        with self.synthetic_provider(self.proposal([
            {"text": "leche deslactosada", "candidate_id": "synthetic-lactose-free"},
        ])) as provider:
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=False)
        self.assertEqual(response["status"], "duplicate")
        self.assertEqual(response["external_ids"]["already_present"], 1)
        self.assertEqual(response["external_ids"]["notion_block_ids"], ["synthetic-lactose-free"])
        self.assertEqual(len(self.fake.writes), 0)
        self.assertEqual(provider.call_count, 1)

    def test_ai_bought_candidate_cannot_reactivate_or_partially_apply(self):
        self.enable_ai()
        self.fake.add(_legacy.block("synthetic-bought", "leche sin lactosa", checked=True))
        self.payload["capture"]["text"] = "pan integral, leche deslactosada"
        proposal = self.proposal([
            {"text": "pan integral", "candidate_id": None},
            {"text": "leche deslactosada", "candidate_id": "synthetic-bought"},
        ])
        with self.synthetic_provider(proposal):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=False)
        self.assertEqual(self.fake.writes, [])
        self.assertTrue(self.fake.blocks["synthetic-bought"]["to_do"]["checked"])

    def test_explicit_missing_updates_bought_item_and_creates_sibling_in_exact_package(self):
        self.enable_ai()
        self.fake.add(_legacy.block("synthetic-bought-milk", "leche", checked=True))
        self.payload["capture"]["text"] = "me falta otra vez leche y huevos"
        with self.synthetic_provider(self.proposal([
            {"text": "leche", "candidate_id": "synthetic-bought-milk"},
            {"text": "huevos", "candidate_id": None},
        ])) as provider:
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=True)
        self.assertEqual(response["external_ids"]["updated"], 1)
        self.assertEqual(response["external_ids"]["created"], 1)
        self.assertFalse(self.fake.blocks["synthetic-bought-milk"]["to_do"]["checked"])
        self.assertEqual(provider.call_count, 1)
        self.fake.blocks["synthetic-bought-milk"]["to_do"]["checked"] = True
        self.restart()
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("re-inferred missing intent")):
            self.assertEqual(self.invoke()[1]["status"], "duplicate")
        self.assertTrue(self.fake.blocks["synthetic-bought-milk"]["to_do"]["checked"])
        self.assertEqual(len(self.fake.writes), 2)

    def test_model_cannot_omit_qualifiers_and_still_write_other_items(self):
        self.enable_ai()
        self.payload["capture"]["text"] = "agrega huevos y 2 litros de leche sin lactosa"
        with self.synthetic_provider(self.proposal([
            {"text": "huevos", "candidate_id": None},
            {"text": "leche", "candidate_id": None},
        ])):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=False)
        self.assertEqual(response["status"], "needs_input")
        self.assertEqual(self.fake.writes, [])

    def test_model_cannot_split_decimal_amount_into_two_products(self):
        self.enable_ai()
        self.payload["capture"]["text"] = "1,5 kilos de arroz"
        with self.synthetic_provider(self.proposal([
            {"text": "1", "candidate_id": None},
            {"text": "5 kilos de arroz", "candidate_id": None},
        ])):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=False)
        self.assertEqual(self.fake.writes, [])
        self.payload["request_id"] += "-whole-decimal"
        with self.synthetic_provider(self.proposal([
            {"text": "1,5 kilos de arroz", "candidate_id": None},
        ])):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=True)
        self.assertEqual(response["external_ids"]["grocery_items"][0]["requested_item"], "1,5 kilos de arroz")
        self.assertEqual(len(self.fake.writes), 1)

    def test_ai_partial_retry_after_restart_never_calls_provider_or_replays_mutation(self):
        self.enable_ai()
        self.payload["capture"]["text"] = "leche, huevos"
        proposal = self.proposal([{"text": "leche", "candidate_id": None}, {"text": "huevos", "candidate_id": None}])
        def fail_second():
            if len(self.fake.writes) == 1:
                self.fake.after_mutation_error = TimeoutError("synthetic lost acknowledgement")
        self.fake.before_mutation = fail_second
        with self.synthetic_provider(proposal) as provider:
            code, response = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual((response["completed_count"], response["total_count"]), (1, 2))
        self.assertEqual(provider.call_count, 1)
        self.fake.before_mutation = self.fake.after_mutation_error = None
        self.restart()
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("partial batch reinterpreted")):
            code, response = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual(response["completed_count"], 1)
        self.assertEqual(len(self.fake.writes), 2)

    def test_existing_v1_batch_resumes_unchanged_when_ai_is_enabled(self):
        self.fake.fail_readback = True
        self.assertEqual(self.invoke()[0], 503)
        journal = self.engine.journal_path(self.cfg.ledger_path)
        with sqlite3.connect(journal) as conn:
            before = conn.execute("SELECT plan_json,plan_hash FROM grocery_batches").fetchone()
        self.assertEqual(json.loads(before[0])["version"], 1)
        self.fake.fail_readback = False
        self.restart()
        self.enable_ai()
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("legacy batch used AI")):
            code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assert_swift_wire(response, wrote=True)
        with sqlite3.connect(journal) as conn:
            after = conn.execute("SELECT plan_json,plan_hash FROM grocery_batches").fetchone()
        self.assertEqual(after, before)
        self.assertEqual(len(self.fake.writes), 2)

    def test_enabled_ai_still_cannot_cross_route_or_confirmation_gates(self):
        self.enable_ai()
        with mock.patch.object(self.ai, "interpret", side_effect=AssertionError("gated request interpreted")):
            self.test_hmac_client_server_and_cross_domain_gates_remain_closed()
            for key, value in (("user_confirmation", True), ("user_cancellation", True),
                               ("requires_confirmation", True), ("confirmation_token", "synthetic-token"),
                               ("operation_id", "synthetic-operation")):
                payload = copy.deepcopy(self.payload)
                payload["request_id"] += "-" + key
                payload["context"][key] = value
                code, response = self.invoke(payload)
                self.assertEqual(code, 200)
                self.assertEqual(response["status"], "rejected")
                self.assertIs(response["write_committed"], False)
        self.assertEqual(self.fake.calls, [])

    def test_fixed_closure_rejects_every_held_route_and_unlisted_dependency(self):
        _, runtime = build.candidate(self.baseline, self.snapshot)
        for module in ("reminder_ai", "reminder_capture", "todoist_reminders", "requests", "socket"):
            changed = dict(runtime)
            changed["grocery_ai.py"] += f"\nimport {module}\n".encode()
            with self.subTest(module=module), self.assertRaisesRegex(ValueError, "Unpackaged runtime dependency"):
                build.assert_import_closure(changed)
        changed = dict(runtime)
        changed["reminder_ai.py"] = b"pass\n"
        with self.assertRaises(ValueError):
            build.assert_import_closure(changed)

    def test_original_sealed_release_is_reproducible_from_immutable_source(self):
        # Original builder reads backend paths by design. Supply the sealed
        # immutable source snapshot instead of changing its files or manifest.
        historical = self.case / "historical-snapshot"
        (historical / "backend").mkdir(parents=True)
        prior = build._prior_builder()
        for name in prior.RUNTIME_MODULES:
            source = subprocess.check_output(["git", "-C", str(ROOT), "show", f"{HISTORICAL_SOURCE_COMMIT}:backend/{name}"])
            (historical / "backend" / name).write_bytes(source)
        expected = prior.load_manifest()
        before = prior.MANIFEST.read_bytes()
        prior.build_release(self.baseline, self.case / "original-four-file-release", historical, expected)
        self.assertEqual(prior.MANIFEST.read_bytes(), before)
        self.assertEqual(expected["runtime_file_count"], 4)


if __name__ == "__main__":
    unittest.main()
