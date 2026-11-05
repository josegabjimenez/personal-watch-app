"""Synthetic engine integration: inference is frozen before trusted dispatch."""
from __future__ import annotations

import concurrent.futures
import copy
import importlib
from pathlib import Path
import sqlite3
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

import grocery_ai
import grocery_ai_budget
import grocery_capture as batch
import grocery_helper_adapter as adapter
from fixtures.grocery_test_support import grocery_environment
from test_grocery_helper_adapter import block


class GroceryAICaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        environment = grocery_environment(self.directory)
        self.helper, self.fake = environment.__enter__()
        self.addCleanup(environment.__exit__, None, None, None)
        self.cfg = SimpleNamespace(ledger_path=self.directory / "synthetic-parent.sqlite", aura_helper=self.helper)
        batch.init_db(batch.journal_path(self.cfg.ledger_path))
        grocery_ai_budget.initialize_schema(batch.journal_path(self.cfg.ledger_path))
        self.payload = {
            "request_id": "synthetic-ai-request",
            "route": {"agent": "aura", "intent": "grocery", "domain": batch.DOMAIN},
            "capture": {"text": "agrega leche y huevos al mercado"},
            "context": {"dry_run": False, "allow_write": True, "allow_firefly_write": False},
        }
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict("os.environ", {"GROCERY_AI_ENABLED": "1"}).start()
        self.config = mock.patch.object(grocery_ai, "config_from_environment",
            return_value=SimpleNamespace(api_key="synthetic-key", model="gpt-6-luna", timeout=1)).start()
        self.provider_impl = grocery_ai.interpret
        self.model = mock.patch.object(grocery_ai, "interpret", return_value=self.proposal("leche", "huevos")).start()

    @staticmethod
    def proposal(*texts):
        return {"decision": "apply", "question": None,
                "items": [{"text": text, "candidate_id": None} for text in texts]}

    def execute(self):
        return batch.execute(self.payload, self.cfg)

    def row(self):
        with sqlite3.connect(batch.journal_path(self.cfg.ledger_path)) as conn:
            return conn.execute("SELECT status,plan_json FROM grocery_batches WHERE request_id=?",
                                (self.payload["request_id"],)).fetchone()

    def test_quantities_multi_item_and_conjunction_names_are_frozen(self):
        self.payload["capture"]["text"] = "necesito 2 litros de leche y pan de ajo y perejil"
        self.model.return_value = self.proposal("2 litros de leche", "pan de ajo y perejil")
        response = self.execute()
        self.assertEqual(response["external_ids"]["created"], 2)
        self.assertEqual([i["requested_item"] for i in response["external_ids"]["grocery_items"]],
                         ["2 litros de leche", "pan de ajo y perejil"])
        self.assertEqual(self.row()[0], "committed")
        self.assertIn('"interpretation"', self.row()[1])
        self.model.assert_called_once()

    def test_synonym_selects_real_existing_row_without_renaming_or_writing(self):
        self.fake.add(block("synthetic-banana", "bananos"))
        self.payload["capture"]["text"] = "añade bananas"
        self.model.return_value = {"decision": "apply", "question": None,
            "items": [{"text": "bananas", "candidate_id": "synthetic-banana"}]}
        response = self.execute()
        self.assertEqual(response["status"], "duplicate")
        self.assertEqual(response["external_ids"]["notion_block_ids"], ["synthetic-banana"])
        self.assertEqual(response["external_ids"]["grocery_items"][0]["item"], "bananos")
        self.assertEqual(self.fake.writes, [])
        self.assertEqual(self.model.call_args.args[1][0]["id"], "synthetic-banana")

    def test_ambiguity_clarifies_whole_batch_and_retry_does_not_reinterpret(self):
        self.model.return_value = {"decision": "needs_input", "question": "¿Leche entera o deslactosada?", "items": []}
        first = self.execute()
        self.assertEqual(first["status"], "needs_input")
        self.assertIn("¿Leche entera o deslactosada?", first["display_message"])
        self.assertFalse(first["plan"]["would_write"])
        self.assertFalse(first["write_committed"])
        self.model.return_value = self.proposal("leche", "huevos")
        second = self.execute()
        self.assertEqual(second["display_message"], first["display_message"])
        self.assertEqual(self.fake.writes, [])
        self.model.assert_called_once()

    def test_existing_bought_status_is_preserved_and_quantity_conflict_clarifies(self):
        self.fake.add(block("synthetic-milk", "1 litro de leche", checked=True))
        self.payload["capture"]["text"] = "2 litros de leche y huevos"
        self.model.return_value = {"decision": "apply", "question": None,
            "items": [{"text": "2 litros de leche", "candidate_id": "synthetic-milk"},
                      {"text": "huevos", "candidate_id": None}]}
        response = self.execute()
        self.assertEqual(response["status"], "needs_input")
        self.assertTrue(self.fake.blocks["synthetic-milk"]["to_do"]["checked"])
        self.assertEqual(self.fake.writes, [])

    def test_unavailable_provider_is_terminal_honest_no_write_without_fallback(self):
        self.model.side_effect = grocery_ai.InterpretationUnavailable("synthetic_unavailable")
        first = self.execute()
        self.assertEqual(first["status"], "needs_input")
        self.assertFalse(first["write_committed"])
        self.assertIn("No pude interpretar", first["display_message"])
        self.execute()
        self.model.assert_called_once()
        self.assertEqual(self.fake.writes, [])

    def test_explicit_missing_resolves_bought_item_and_retry_preserves_later_purchase(self):
        self.fake.add(block("synthetic-milk", "leche", checked=True))
        self.payload["capture"]["text"] = "me falta otra vez leche y huevos"
        self.model.return_value = {"decision": "apply", "question": None,
            "items": [{"text": "leche", "candidate_id": "synthetic-milk"},
                      {"text": "huevos", "candidate_id": None}]}
        response = self.execute()
        self.assertEqual(response["external_ids"]["updated"], 1)
        self.assertEqual(response["external_ids"]["created"], 1)
        self.assertFalse(self.fake.blocks["synthetic-milk"]["to_do"]["checked"])
        self.fake.blocks["synthetic-milk"]["to_do"]["checked"] = True
        self.execute()
        self.assertTrue(self.fake.blocks["synthetic-milk"]["to_do"]["checked"])
        self.assertEqual(len(self.fake.writes), 2)
        self.model.assert_called_once()

    def test_missing_configuration_never_calls_provider_or_legacy_parser(self):
        self.config.side_effect = grocery_ai.InterpretationUnavailable("interpreter_not_configured")
        with mock.patch.object(batch, "parse_grocery_items", side_effect=AssertionError("legacy fallback")):
            response = self.execute()
        self.assertEqual(response["status"], "needs_input")
        self.model.assert_not_called()
        self.assertEqual(self.fake.writes, [])

    def test_budget_exhaustion_is_cached_no_write_and_never_reaches_provider(self):
        guard = grocery_ai_budget.GroceryAIBudget(batch.journal_path(self.cfg.ledger_path))
        for index in range(6):
            self.assertTrue(guard.reserve(f"synthetic-unknown-{index}", "a" * 64).allowed)
        self.model.side_effect = self.provider_impl
        with mock.patch.object(grocery_ai, "_post_json", side_effect=AssertionError("exhausted budget dispatched")) as post:
            first = self.execute()
            replay = self.execute()
        self.assertEqual(first["status"], "needs_input")
        self.assertIn("USD 2/mes", first["display_message"])
        self.assertEqual(replay["display_message"], first["display_message"])
        self.assertEqual(self.fake.writes, [])
        self.model.assert_called_once()
        post.assert_not_called()

    def test_missing_budget_state_fails_closed_without_reinitializing(self):
        guard_path = batch.journal_path(self.cfg.ledger_path)
        with sqlite3.connect(guard_path) as conn:
            conn.execute("DROP TABLE grocery_ai_budget_state")
        self.model.side_effect = self.provider_impl
        with mock.patch.object(grocery_ai, "_post_json", side_effect=AssertionError("missing guard dispatched")) as post:
            response = self.execute()
        self.assertEqual(response["status"], "needs_input")
        self.assertIn("Presupuesto", response["display_message"])
        self.assertEqual(self.fake.writes, [])
        post.assert_not_called()
        with sqlite3.connect(guard_path) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='grocery_ai_budget_state'").fetchone())

    def test_accepted_interpretation_survives_prepare_outage_and_configuration_removal(self):
        with mock.patch.object(adapter.GroceryAdapter, "prepare_semantic",
                side_effect=adapter.GroceryAdapterError("synthetic_read_outage", "synthetic outage")):
            with self.assertRaises(batch.GroceryPending):
                self.execute()
        self.assertEqual(self.row()[0], "interpreted")
        self.config.side_effect = AssertionError("must not load config again")
        self.model.side_effect = AssertionError("must not infer again")
        with mock.patch.dict("os.environ", {"GROCERY_AI_ENABLED": "0"}):
            response = self.execute()
        self.assertEqual(response["status"], "committed")
        self.assertEqual(len(self.fake.writes), 2)
        self.model.assert_called_once()

    def test_inference_claim_survives_process_interruption_without_second_call(self):
        self.model.side_effect = KeyboardInterrupt("synthetic crash after claim")
        with self.assertRaises(KeyboardInterrupt):
            self.execute()
        self.assertEqual(self.row()[0], "interpreting")
        with self.assertRaises(batch.GroceryPending) as raised:
            self.execute()
        self.assertEqual(raised.exception.response["error"], "grocery_interpretation_pending_review")
        self.model.assert_called_once()
        self.assertEqual(self.fake.writes, [])

    def test_concurrent_same_request_has_one_inference(self):
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("synthetic inference not released")
            return self.proposal("leche", "huevos")
        self.model.side_effect = slow
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.execute)
            self.assertTrue(entered.wait(5))
            try:
                with self.assertRaises(batch.GroceryPending):
                    self.execute()
            finally:
                release.set()
            self.assertEqual(first.result(timeout=5)["status"], "committed")
        self.model.assert_called_once()
        self.assertEqual(len(self.fake.writes), 2)

    def test_partial_lost_ack_never_replans_or_repeats_confirmed_item_after_restart(self):
        def fail_second():
            if len(self.fake.writes) == 1:
                self.fake.after_mutation_error = TimeoutError("synthetic lost ack")
        self.fake.before_mutation = fail_second
        with self.assertRaises(batch.GroceryPending) as raised:
            self.execute()
        self.assertEqual(raised.exception.response["completed_count"], 1)
        self.assertEqual(len(self.fake.writes), 2)
        importlib.reload(batch)
        self.model.side_effect = AssertionError("restart reinterpreted request")
        with self.assertRaises(batch.GroceryPending):
            self.execute()
        self.assertEqual(len(self.fake.writes), 2)
        self.model.assert_called_once()

    def test_lost_parent_ack_replays_without_model_catalog_or_writes(self):
        first = self.execute()
        self.fake.add(block("synthetic-after", "new unrelated catalog text"))
        with mock.patch.object(adapter.GroceryAdapter, "catalog", side_effect=AssertionError("replay read catalog")):
            response = self.execute()
        self.assertEqual(response["status"], "duplicate")
        self.assertEqual(response["external_ids"], first["external_ids"])
        self.assertEqual(len(self.fake.writes), 2)
        self.model.assert_called_once()

    def test_changed_body_conflicts_and_no_other_domain_can_reach_ai(self):
        self.execute()
        self.payload["capture"]["text"] += " extra"
        with self.assertRaises(batch.GroceryConflict):
            self.execute()
        for domain in ("aura.reminder_capture", "megan.expense_capture", "argos.general_capture"):
            changed = copy.deepcopy(self.payload)
            changed["route"]["domain"] = domain
            response = batch.execute(changed, self.cfg)
            self.assertEqual(response["status"], "rejected")
        self.model.assert_called_once()

    def test_new_ai_flag_does_not_reinterpret_existing_legacy_batch(self):
        with mock.patch.dict("os.environ", {"GROCERY_AI_ENABLED": "0"}):
            with mock.patch.object(adapter.GroceryAdapter, "prepare",
                    side_effect=adapter.GroceryAdapterError("synthetic_read_outage", "synthetic outage")):
                with self.assertRaises(batch.GroceryPending):
                    self.execute()
        response = self.execute()
        self.assertEqual(response["status"], "committed")
        self.model.assert_not_called()
        self.assertEqual(len(self.fake.writes), 2)


if __name__ == "__main__":
    unittest.main()
