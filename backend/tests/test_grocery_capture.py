"""Batch integration against real extracted helper code and a fake Notion API."""
from __future__ import annotations

import concurrent.futures
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from dataclasses import replace
from unittest import mock
from urllib.error import HTTPError

import grocery_capture as batch
import grocery_helper_adapter as adapter
import mobile_capture_staging_server as srv
import test_mobile_capture_staging_server as staging_tests
from fixtures.grocery_test_support import grocery_environment
from test_grocery_helper_adapter import block


class GroceryCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        environment = grocery_environment(self.directory)
        self.helper, self.fake = environment.__enter__()
        self.addCleanup(environment.__exit__, None, None, None)
        self.cfg = staging_tests.MobileCaptureStagingServerTests.write_config(
            "aura.grocery_capture", ledger_path=self.directory / "parent.sqlite")
        self.cfg = __import__("dataclasses").replace(self.cfg, aura_helper=self.helper)
        self.payload = staging_tests.MobileCaptureStagingServerTests.grocery_payload(
            live=True, text="agrega leche y huevos al mercado")
        self.payload["request_id"] = "synthetic-batch-request"

    def execute(self, payload=None, **kwargs):
        return batch.execute(payload or self.payload, self.cfg, **kwargs)

    def test_two_items_use_real_helper_contract_and_distinct_unchecked_blocks(self):
        response = self.execute()
        self.assertEqual(response["status"], "committed")
        self.assertIs(response["write_committed"], True)
        self.assertIs(response["plan"]["would_write"], True)
        self.assertEqual(response["external_ids"]["created"], 2)
        self.assertEqual(response["external_ids"]["item_count"], 2)
        self.assertEqual(len(set(response["external_ids"]["notion_block_ids"])), 2)
        self.assertEqual([item["requested_item"] for item in response["external_ids"]["grocery_items"]], ["leche", "huevos"])
        self.assertEqual(len(self.fake.writes), 2)
        for item in response["external_ids"]["grocery_items"]:
            self.assertIs(self.fake.blocks[item["block_id"]]["to_do"]["checked"], False)
        self.assertTrue(all(call[1].startswith("/blocks/") for call in self.fake.calls))

    def test_quantities_compounds_and_measurement_conjunction_are_kept(self):
        for index, (text, expected) in enumerate((
            ("2 litros de leche y 6 huevos", ["2 litros de leche", "6 huevos"]),
            ("un litro y medio de leche", ["un litro y medio de leche"]),
            ("aceite de oliva y pan integral", ["aceite de oliva", "pan integral"]),
        )):
            payload = copy.deepcopy(self.payload)
            payload["request_id"] += str(index)
            payload["capture"]["text"] = text
            # Each case uses a distinct synthetic shopping list.
            self.fake.blocks = {"synthetic-others": self.fake.blocks["synthetic-others"]}
            self.fake.children["synthetic-others"] = []
            response = self.execute(payload)
            self.assertEqual([item["requested_item"] for item in response["external_ids"]["grocery_items"]], expected)

    def test_existing_canonical_receipt_and_counts_remain_truthful(self):
        self.fake.add(block("synthetic-milk", "1 litro de leche = 4.500", checked=False))
        self.fake.add(block("synthetic-eggs", "huevos", checked=True))
        response = self.execute()
        self.assertEqual(response["external_ids"]["created"], 0)
        self.assertEqual(response["external_ids"]["updated"], 1)
        self.assertEqual(response["external_ids"]["already_present"], 1)
        milk = response["external_ids"]["grocery_items"][0]
        self.assertEqual(milk["item"], "1 litro de leche = 4.500")
        self.assertEqual(milk["requested_item"], "leche")
        self.assertFalse(milk["write_committed"])
        self.assertEqual(len(self.fake.writes), 1)

    def test_all_existing_is_terminal_no_new_write(self):
        self.fake.add(block("synthetic-milk", "leche"))
        self.fake.add(block("synthetic-eggs", "huevos"))
        response = self.execute()
        self.assertEqual(response["status"], "duplicate")
        self.assertIs(response["write_committed"], False)
        self.assertIs(response["plan"]["would_write"], False)
        self.assertEqual(response["external_ids"]["already_present"], 2)
        self.assertEqual(self.fake.writes, [])

    def test_quantity_conflict_or_ambiguity_is_atomic_before_any_write(self):
        self.fake.add(block("synthetic-milk", "1 litro de leche"))
        payload = copy.deepcopy(self.payload)
        payload["capture"]["text"] = "huevos y 2 litros de leche"
        response = self.execute(payload)
        self.assertEqual(response["status"], "needs_input")
        self.assertIs(response["write_committed"], False)
        self.assertEqual(self.fake.writes, [])
        payload["request_id"] += "-ambiguous"
        payload["capture"]["text"] = "mezcla tropical y especial"
        with mock.patch.object(batch, "GroceryAdapter", side_effect=AssertionError("ambiguous parser reached adapter")):
            response = self.execute(payload)
        self.assertEqual(response["status"], "needs_input")

    def test_replay_never_reparses_rewrites_or_reunchecks(self):
        first = self.execute(raw_payload_hash="a" * 64)
        self.fake.blocks[first["external_ids"]["notion_block_ids"][0]]["to_do"]["checked"] = True
        calls = len(self.fake.calls)
        with mock.patch.object(batch, "parse_grocery_items", side_effect=AssertionError("reparsed frozen plan")), \
                mock.patch.object(batch, "GroceryAdapter", side_effect=AssertionError("replayed completed batch")):
            replay = self.execute(raw_payload_hash="a" * 64)
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(replay["external_ids"], first["external_ids"])
        self.assertEqual(len(self.fake.calls), calls)
        self.assertEqual(len(self.fake.writes), 2)
        with self.assertRaises(batch.GroceryConflict):
            self.execute(raw_payload_hash="b" * 64)

    def test_partial_lost_ack_survives_restart_without_duplicate_or_false_success(self):
        def fail_second():
            if len(self.fake.writes) == 1:
                self.fake.after_mutation_error = TimeoutError("synthetic lost acknowledgement")
        self.fake.before_mutation = fail_second
        with self.assertRaises(batch.GroceryPending) as caught:
            self.execute(raw_payload_hash="c" * 64)
        self.assertEqual(caught.exception.response["completed_count"], 1)
        self.assertEqual(caught.exception.response["total_count"], 2)
        self.assertNotIn("write_committed", caught.exception.response)
        self.assertEqual(len(self.fake.writes), 2)
        self.fake.before_mutation = None
        self.fake.after_mutation_error = None
        with mock.patch.object(adapter.GroceryAdapter, "prepare", side_effect=AssertionError("replanned partial batch")):
            for _ in range(2):
                with self.assertRaises(batch.GroceryPending) as retry:
                    self.execute(raw_payload_hash="c" * 64)
                self.assertEqual(retry.exception.response["completed_count"], 1)
        self.assertEqual(len(self.fake.writes), 2)
        with self.assertRaises(batch.GroceryConflict):
            self.execute(raw_payload_hash="d" * 64)

    def test_acknowledged_candidate_recovers_read_only_then_finishes_remaining_item(self):
        self.fake.fail_readback = True
        with self.assertRaises(batch.GroceryPending):
            self.execute()
        self.assertEqual(len(self.fake.writes), 1)
        self.fake.fail_readback = False
        response = self.execute()
        self.assertEqual(response["status"], "committed")
        self.assertEqual(len(self.fake.writes), 2)
        self.assertEqual(response["external_ids"]["created"], 2)

    def test_concurrent_batches_dispatch_each_item_once(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.execute(), range(2)))
        self.assertEqual(len(self.fake.writes), 2)
        self.assertTrue(all(value["status"] in {"committed", "duplicate"} for value in results))
        self.assertEqual(results[0]["external_ids"], results[1]["external_ids"])

    def test_preflight_error_cannot_mask_another_workers_committed_progress(self):
        original_prepare = adapter.GroceryAdapter.prepare
        def racing_prepare(gateway, items):
            prepared = original_prepare(gateway, items)
            gateway.register_operations([(adapter.child_operation_id(self.payload["request_id"], item), item)
                                         for item in prepared["items"]])
            frozen = {"version": batch.PLAN_VERSION, "prepared": prepared}
            serialized = batch.canonical(frozen)
            with batch.connection(batch.journal_path(self.cfg.ledger_path)) as conn:
                conn.execute("UPDATE grocery_batches SET plan_json=?,plan_hash=?,status='pending' WHERE request_id=? AND payload_hash=? AND status='preflight'",
                             (serialized, batch.sha(serialized), self.payload["request_id"], batch.sha(batch.canonical(self.payload))))
            first = prepared["items"][0]
            gateway.execute(adapter.child_operation_id(self.payload["request_id"], first), first)
            raise adapter.GroceryAdapterError("quantity_conflict", "Synthetic racing preflight result")
        with mock.patch.object(adapter.GroceryAdapter, "prepare", autospec=True, side_effect=racing_prepare):
            response = self.execute()
        self.assertEqual(response["status"], "committed")
        self.assertTrue(response["write_committed"])
        self.assertEqual(len(self.fake.writes), 2)

    def test_corrupt_frozen_plan_stops_without_helper_or_state_replacement(self):
        self.fake.fail_readback = True
        with self.assertRaises(batch.GroceryPending):
            self.execute()
        path = batch.journal_path(self.cfg.ledger_path)
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE grocery_batches SET plan_json='{}'")
        calls = len(self.fake.calls)
        with self.assertRaises(batch.GroceryPending) as caught:
            self.execute()
        self.assertEqual(caught.exception.response["error"], "grocery_batch_state_invalid")
        self.assertEqual(len(self.fake.calls), calls)

    def test_http_partial_parent_is_retained_and_changed_body_conflicts(self):
        self.fake.after_mutation_error = TimeoutError("synthetic lost acknowledgement")
        with self.assertRaises(HTTPError) as failed:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(failed.exception.code, 503)
        progress = json.loads(failed.exception.read())
        self.assertEqual(progress["status"], "needs_reconciliation")
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            before = conn.execute("SELECT payload_hash,status,response_json FROM mobile_capture_ledger WHERE request_id=?", (self.payload["request_id"],)).fetchone()
        self.assertEqual(before[1], "grocery_pending")
        changed = copy.deepcopy(self.payload)
        changed["capture"]["text"] = "arroz"
        with self.assertRaises(HTTPError) as conflict:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(changed, self.cfg, count=1)
        self.assertEqual(conflict.exception.code, 409)
        self.fake.after_mutation_error = None
        with self.assertRaises(HTTPError) as repeated:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(repeated.exception.code, 503)
        self.assertEqual(len(self.fake.writes), 1)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            after = conn.execute("SELECT payload_hash,status FROM mobile_capture_ledger WHERE request_id=?", (self.payload["request_id"],)).fetchone()
        self.assertEqual(after, before[:2])

    def test_frozen_batch_conflict_cannot_poison_restored_transport_ledger(self):
        staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.execute("DELETE FROM mobile_capture_ledger")
        changed = copy.deepcopy(self.payload)
        changed["capture"]["text"] = "arroz"
        with self.assertRaises(HTTPError) as conflict:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(changed, self.cfg, count=1)
        self.assertEqual(conflict.exception.code, 409)
        replay = staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(replay[0][1]["status"], "duplicate")
        self.assertEqual(len(self.fake.writes), 2)

    def test_stale_pending_handler_cannot_downgrade_terminal_winner(self):
        winner_row = None

        def complete_before_stale_pending(payload, cfg, planned, **kwargs):
            # Deterministically reproduce another handler finishing between
            # execution-lock unwind and this caller's pending-ledger update.
            nonlocal winner_row
            winner = batch.execute(payload, cfg, raw_payload_hash=kwargs["raw_payload_hash"])
            with sqlite3.connect(cfg.ledger_path) as conn:
                conn.execute(
                    "UPDATE mobile_capture_ledger SET status='committed',response_json=?,updated_at=? WHERE request_id=?",
                    (json.dumps(winner, ensure_ascii=False), "synthetic-winner-time", payload["request_id"]),
                )
                winner_row = conn.execute("SELECT * FROM mobile_capture_ledger").fetchone()
            raise batch.GroceryPending(payload["request_id"], code="synthetic_stale_pending")

        with mock.patch.object(srv, "execute_grocery_capture", side_effect=complete_before_stale_pending):
            with self.assertRaises(HTTPError) as stale:
                staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(stale.exception.code, 503)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            self.assertEqual(conn.execute("SELECT * FROM mobile_capture_ledger").fetchone(), winner_row)
        calls = len(self.fake.calls)
        replay = staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(replay[0][1]["status"], "duplicate")
        self.assertEqual(replay[0][1]["external_ids"]["created"], 2)
        self.assertEqual(len(self.fake.calls), calls)
        self.assertEqual(len(self.fake.writes), 2)

    def test_disabling_server_gate_preserves_pending_parent_and_items(self):
        self.fake.after_mutation_error = TimeoutError("synthetic lost acknowledgement")
        with self.assertRaises(HTTPError):
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            before = conn.execute("SELECT * FROM mobile_capture_ledger").fetchall()
        cfg = replace(self.cfg, write_enabled=False)
        calls = len(self.fake.calls)
        with self.assertRaises(HTTPError) as disabled:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, cfg, count=1)
        self.assertEqual(disabled.exception.code, 503)
        self.assertNotIn("write_committed", json.loads(disabled.exception.read()))
        self.assertEqual(len(self.fake.calls), calls)
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            self.assertEqual(conn.execute("SELECT * FROM mobile_capture_ledger").fetchall(), before)

    def test_missing_helper_configuration_fails_closed(self):
        with self.assertRaises(batch.GroceryPending) as caught:
            batch.execute(self.payload, replace(self.cfg, aura_helper=None))
        self.assertEqual(caught.exception.response["error"], "grocery_adapter_unavailable")
        self.assertNotIn("completed_count", caught.exception.response)
        self.assertEqual(self.fake.writes, [])

    def test_transient_preflight_failure_resumes_after_read_connection_recovers(self):
        self.fake.bad_cursor = True
        with self.assertRaises(HTTPError) as first:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(first.exception.code, 503)
        self.assertEqual(json.loads(first.exception.read())["completed_count"], 0)
        self.assertEqual(self.fake.writes, [])
        with sqlite3.connect(batch.journal_path(self.cfg.ledger_path)) as conn:
            self.assertEqual(conn.execute("SELECT status FROM grocery_batches").fetchone()[0], "preflight")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0], 0)
        self.fake.bad_cursor = False
        result = staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(result[0][1]["status"], "committed")
        self.assertEqual(len(self.fake.writes), 2)

    def test_terminal_clarification_never_reparses_or_dispatches_later(self):
        payload = copy.deepcopy(self.payload)
        payload["capture"]["text"] = "salsa de tomate y albahaca"
        response = self.execute(payload)
        self.assertEqual(response["status"], "needs_input")
        with mock.patch.object(batch, "parse_grocery_items", side_effect=AssertionError("terminal refusal reparsed")), \
                mock.patch.object(batch, "GroceryAdapter", side_effect=AssertionError("terminal refusal dispatched")):
            replay = self.execute(payload, require_existing_batch=True)
        self.assertEqual(replay["status"], "needs_input")
        self.assertFalse(replay["write_committed"])
        self.assertEqual(self.fake.writes, [])

    def _assert_missing_recovery_state_stops(self, missing):
        def lose_second_ack():
            if len(self.fake.writes) == 1:
                self.fake.after_mutation_error = TimeoutError("synthetic lost ACK")
        self.fake.before_mutation = lose_second_ack
        with self.assertRaises(HTTPError) as first:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(json.loads(first.exception.read())["completed_count"], 1)
        first_item = next(value for value in self.fake.blocks.values()
                          if value.get("type") == "to_do" and value["to_do"]["rich_text"][0]["plain_text"] == "leche")
        first_item["to_do"]["checked"] = True
        journal = batch.journal_path(self.cfg.ledger_path)
        if missing == "file":
            journal.unlink()
        else:
            with sqlite3.connect(journal) as conn:
                if missing == "batch":
                    conn.execute("DELETE FROM grocery_batches")
                else:
                    conn.execute("DELETE FROM grocery_operations WHERE status='committed'")
        self.fake.before_mutation = None
        self.fake.after_mutation_error = None
        calls, writes = len(self.fake.calls), len(self.fake.writes)
        with self.assertRaises(HTTPError) as repeated:
            staging_tests.MobileCaptureStagingServerTests.signed_posts(self.payload, self.cfg, count=1)
        self.assertEqual(repeated.exception.code, 503)
        progress = json.loads(repeated.exception.read())
        self.assertEqual(progress["completed_count"], 1)
        self.assertEqual(progress["total_count"], 2)
        self.assertEqual(len(self.fake.calls), calls)
        self.assertEqual(len(self.fake.writes), writes)
        self.assertTrue(first_item["to_do"]["checked"])
        if missing == "file":
            self.assertFalse(journal.exists())
        else:
            with sqlite3.connect(journal) as conn:
                table = "grocery_batches" if missing == "batch" else "grocery_operations WHERE status='committed'"
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_missing_journal_on_pending_http_retry_never_recreates_or_replays(self):
        self._assert_missing_recovery_state_stops("file")

    def test_missing_batch_on_pending_http_retry_never_replans(self):
        self._assert_missing_recovery_state_stops("batch")

    def test_missing_committed_item_row_never_reunchecks_or_dispatches_again(self):
        self._assert_missing_recovery_state_stops("item")

    def test_checked_in_wire_fixtures_are_actual_backend_outputs(self):
        directory = Path(__file__).with_name("fixtures")
        request = json.loads((directory / "grocery_batch_request.json").read_text())
        names = ("full_success", "mixed_success", "all_already", "partial")
        for name in names:
            path = self.directory / name
            path.mkdir()
            with self.subTest(name=name), grocery_environment(path) as (helper, fake):
                cfg = SimpleNamespace(ledger_path=path / "mobile.sqlite3", aura_helper=helper)
                if name == "mixed_success":
                    fake.add(block("synthetic-eggs", "6 huevos", checked=True))
                    fake.add(block("synthetic-bread", "pan integral"))
                elif name == "all_already":
                    for identity, item in zip(("synthetic-milk", "synthetic-eggs", "synthetic-bread"),
                                              ("2 litros de leche", "6 huevos", "pan integral")):
                        fake.add(block(identity, item))
                elif name == "partial":
                    def fail_second():
                        if len(fake.writes) == 1:
                            fake.after_mutation_error = TimeoutError("synthetic lost ACK")
                    fake.before_mutation = fail_second
                try:
                    actual = batch.execute(request, cfg, raw_payload_hash=batch.sha(batch.canonical(request)))
                except batch.GroceryPending as error:
                    self.assertEqual(name, "partial")
                    actual = error.response
                expected = json.loads((directory / f"grocery_batch_{name}.json").read_text())
                self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
