"""Offline admission/accounting tests; no credentials or provider calls."""
from __future__ import annotations

import concurrent.futures
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import grocery_ai_budget as budget


class GroceryAIBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "synthetic.grocery.sqlite3"
        self.now = datetime(2026, 11, 4, 12, tzinfo=timezone.utc)
        self.clock = lambda: self.now
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE grocery_batches (request_id TEXT PRIMARY KEY, plan_json TEXT)")
        budget.initialize_schema(self.path, clock=self.clock)
        self.guard = budget.GroceryAIBudget(self.path, clock=self.clock)
        self.body_hash = hashlib.sha256(b"synthetic request").hexdigest()

    def reserve(self, request_id="synthetic-request", **kwargs):
        return self.guard.reserve(request_id, self.body_hash, **kwargs)

    @staticmethod
    def response(input_tokens=1000, output_tokens=100):
        return {"model": "gpt-6-luna", "service_tier": "default", "status": "completed",
                "error": None, "incomplete_details": None,
                "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens,
                          "total_tokens": input_tokens + output_tokens,
                          "input_tokens_details": {"cached_tokens": 0},
                          "output_tokens_details": {"reasoning_tokens": output_tokens}}}

    def test_pinned_policy_full_context_reserve_and_integer_costs(self):
        admission = self.reserve()
        self.assertTrue(admission.allowed and admission.fresh)
        self.assertEqual(admission.reservation_nanodollars, 318_000_000)
        snapshot = self.guard.snapshot()
        self.assertEqual(snapshot.month, "2026-11")
        self.assertEqual(snapshot.remaining_nanodollars, 1_682_000_000)
        self.assertIs(type(snapshot.remaining_nanodollars), int)
        self.assertEqual(budget.BASE_INPUT_NANODOLLARS_PER_TOKEN, 100)
        self.assertEqual(budget.BASE_OUTPUT_NANODOLLARS_PER_TOKEN, 500)
        self.assertEqual(budget.MONTHLY_CAP_NANODOLLARS, 2_000_000_000)

    def test_six_unknown_calls_exhaust_bounded_admission_and_restart_keeps_it(self):
        for index in range(6):
            self.assertTrue(self.reserve(f"request-{index}").fresh)
        denied = self.reserve("request-denied")
        self.assertFalse(denied.allowed or denied.fresh)
        self.assertEqual(denied.reason, "monthly_budget_exhausted")
        restarted = budget.GroceryAIBudget(self.path, clock=self.clock)
        self.assertFalse(restarted.reserve("after-restart", self.body_hash).allowed)
        self.assertEqual(restarted.snapshot().reserved_nanodollars, 1_908_000_000)

    def test_duplicate_identity_never_authorizes_second_dispatch_or_reserves_twice(self):
        first = self.reserve()
        for guard in (self.guard, budget.GroceryAIBudget(self.path, clock=self.clock)):
            replay = guard.reserve("synthetic-request", self.body_hash)
            self.assertTrue(replay.allowed)
            self.assertFalse(replay.fresh)
            self.assertEqual(replay.reason, "duplicate")
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, first.reservation_nanodollars)
        with self.assertRaisesRegex(budget.BudgetGuardError, "request_conflict"):
            self.guard.reserve("synthetic-request", "f" * 64)
        with self.assertRaisesRegex(budget.BudgetGuardError, "request_conflict"):
            self.reserve(max_output_tokens=2000)

    def test_same_request_concurrency_has_one_fresh_durable_claim(self):
        def reserve(_):
            return budget.GroceryAIBudget(self.path, clock=self.clock).reserve("shared", self.body_hash)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            admissions = list(pool.map(reserve, range(24)))
        self.assertEqual(sum(item.fresh for item in admissions), 1)
        self.assertTrue(all(item.allowed for item in admissions))
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 318_000_000)

    def test_different_request_concurrency_cannot_overdraw_cap(self):
        def reserve(index):
            return budget.GroceryAIBudget(self.path, clock=self.clock).reserve(f"worker-{index}", self.body_hash)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            admissions = list(pool.map(reserve, range(24)))
        self.assertEqual(sum(item.fresh for item in admissions), 6)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 1_908_000_000)

    def test_settle_known_usage_refunds_only_excess_and_never_double_counts_reasoning(self):
        self.reserve()
        response = self.response()
        response["usage"]["input_tokens_details"]["cached_tokens"] = 1000
        settled = self.guard.settle("synthetic-request", self.body_hash, response)
        self.assertTrue(settled.settled)
        # Cache gets no discount; reasoning is already included in output_tokens.
        self.assertEqual(settled.charged_nanodollars, 400_000)
        snapshot = self.guard.snapshot()
        self.assertEqual(snapshot.reserved_nanodollars, 0)
        self.assertEqual(snapshot.charged_nanodollars, 400_000)
        self.assertEqual(snapshot.remaining_nanodollars, 1_999_600_000)
        self.assertFalse(self.reserve().fresh)
        self.assertEqual(self.guard.settle("synthetic-request", self.body_hash, response), settled)
        self.assertEqual(self.guard.snapshot(), snapshot)
        with self.assertRaisesRegex(budget.BudgetGuardError, "settlement_conflict"):
            self.guard.settle("synthetic-request", self.body_hash, self.response(10, 1))

    def test_concurrent_identical_settlement_is_idempotent(self):
        self.reserve()
        def settle(_):
            return budget.GroceryAIBudget(self.path, clock=self.clock).settle(
                "synthetic-request", self.body_hash, self.response())
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(settle, range(16)))
        self.assertTrue(all(item.settled for item in receipts))
        self.assertEqual(self.guard.snapshot().charged_nanodollars, 400_000)

    def test_official_cache_write_detail_is_accounted_at_conservative_input_rate(self):
        self.reserve()
        response = self.response()
        response["usage"]["input_tokens_details"] = {"cached_tokens": 0, "cache_write_tokens": 1000}
        receipt = self.guard.settle("synthetic-request", self.body_hash, response)
        self.assertTrue(receipt.settled)
        self.assertEqual(receipt.charged_nanodollars, 400_000)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 0)

    def test_official_thirty_minute_cache_breakdown_can_settle(self):
        self.reserve()
        response = self.response()
        response["usage"]["input_tokens_details"] = {
            "cached_tokens": 200, "cache_write_tokens": 800,
            "cache_write_tokens_details": {"ttl_30m_tokens": 800, "ttl_12h_tokens": 0}}
        receipt = self.guard.settle("synthetic-request", self.body_hash, response)
        self.assertTrue(receipt.settled)
        self.assertEqual(receipt.charged_nanodollars, 400_000)

    def test_unknown_response_envelopes_retain_whole_reservation(self):
        self.reserve()
        mutations = [
            lambda r: r.pop("usage"),
            lambda r: r.update(model="gpt-5-mini"),
            lambda r: r.pop("model"),
            lambda r: r.update(service_tier="priority"),
            lambda r: r.pop("service_tier"),
            lambda r: r.update(status="in_progress"),
            lambda r: r.update(status="failed"),
            lambda r: r.update(error={"code": "synthetic_error"}),
            lambda r: r.update(incomplete_details={"reason": "unexpected"}),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                response = self.response()
                mutate(response)
                self.assertFalse(self.guard.settle("synthetic-request", self.body_hash, response).settled)
                self.assertEqual(self.guard.snapshot().reserved_nanodollars, 318_000_000)
        self.assertFalse(self.guard.settle("synthetic-request", self.body_hash, None).settled)

    def test_inconsistent_or_unbounded_usage_never_refunds(self):
        self.reserve()
        invalid = [
            {"input_tokens": True}, {"output_tokens": True}, {"total_tokens": True},
            {"input_tokens": -1}, {"output_tokens": -1}, {"output_tokens": 3001},
            {"input_tokens": 1_050_001}, {"input_tokens": 1.0}, {"total_tokens": 2},
            {"input_tokens_details": {"cached_tokens": 1001}},
            {"input_tokens_details": {"cached_tokens": -1}},
            {"input_tokens_details": {"cached_tokens": False}},
            {"input_tokens_details": {"cached_tokens": 1, "cache_writes": 500}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1001}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": True}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": -1}},
            {"input_tokens_details": {"cached_tokens": 1000, "cache_write_tokens": 1000}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1000,
                "cache_write_tokens_details": {"ttl_30m_tokens": 500, "ttl_12h_tokens": 500}}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1000,
                "cache_write_tokens_details": {"ttl_30m_tokens": 900, "ttl_12h_tokens": 0}}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 1000,
                "cache_write_tokens_details": {"ttl_30m_tokens": 1000, "ttl_12h_tokens": False}}},
            {"input_tokens_details": {"cached_tokens": 0, "cache_write_tokens_details": {}}},
            {"input_tokens_details": None},
            {"output_tokens_details": {"reasoning_tokens": 101}},
            {"output_tokens_details": {"reasoning_tokens": -1}},
            {"output_tokens_details": {"reasoning_tokens": 1.0}},
            {"output_tokens_details": None}, {"new_billable_dimension": 100},
        ]
        for update in invalid:
            with self.subTest(usage=update):
                response = self.response()
                response["usage"].update(update)
                self.assertFalse(self.guard.settle("synthetic-request", self.body_hash, response).settled)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 318_000_000)

    def test_known_terminal_usage_can_settle_refusal_and_incomplete_output(self):
        self.reserve("refusal")
        response = self.response()
        response["output"] = [{"type": "refusal", "refusal": "synthetic refusal"}]
        self.assertTrue(self.guard.settle("refusal", self.body_hash, response).settled)
        self.reserve("incomplete")
        response = self.response(output_tokens=3000)
        response.update(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        self.assertTrue(self.guard.settle("incomplete", self.body_hash, response).settled)

    def test_missing_optional_details_still_uses_conservative_whole_usage(self):
        self.reserve()
        response = self.response(1000, 100)
        response["usage"].pop("input_tokens_details")
        response["usage"].pop("output_tokens_details")
        self.assertEqual(self.guard.settle("synthetic-request", self.body_hash, response).charged_nanodollars, 400_000)

    def test_unresolved_liability_does_not_reset_at_month_boundary(self):
        for index in range(6):
            self.reserve(f"unknown-{index}")
        self.now = datetime(2027, 2, 1, 12, tzinfo=timezone.utc)
        self.assertFalse(self.reserve("months-later").allowed)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 1_908_000_000)
        self.assertFalse(self.guard.settle("unknown-0", self.body_hash, {}).settled)
        self.assertFalse(self.reserve("still-blocked").allowed)

    def test_settled_prior_month_cost_resets_only_when_calendar_advances(self):
        for index in range(6):
            name = f"maximum-{index}"
            self.reserve(name)
            self.guard.settle(name, self.body_hash, self.response(1_050_000, 3000))
        self.assertFalse(self.reserve("november-exhausted").allowed)
        self.now = datetime(2026, 12, 1, 4, 59, tzinfo=timezone.utc)
        self.assertFalse(self.reserve("still-november-bogota").allowed)
        self.now = datetime(2026, 12, 1, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(self.guard.snapshot().charged_nanodollars, 0)
        self.assertTrue(self.reserve("december").fresh)

    def test_late_settlement_charges_current_month_and_preserves_admission_month(self):
        self.reserve()
        self.now = datetime(2026, 12, 1, 5, tzinfo=timezone.utc)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 318_000_000)
        self.guard.settle("synthetic-request", self.body_hash, self.response())
        self.assertEqual(self.guard.snapshot().charged_nanodollars, 400_000)
        with sqlite3.connect(self.path) as conn:
            row = conn.execute("SELECT admitted_month,settled_month FROM grocery_ai_budget_requests").fetchone()
        self.assertEqual(row, ("2026-11", "2026-12"))
        self.now = datetime(2027, 1, 1, 5, tzinfo=timezone.utc)
        self.assertEqual(self.guard.snapshot().charged_nanodollars, 0)

    def test_clock_rollback_is_durable_and_fails_closed(self):
        self.now = datetime(2026, 12, 1, 5, tzinfo=timezone.utc)
        self.reserve()
        self.now = datetime(2026, 11, 30, 12, tzinfo=timezone.utc)
        restarted = budget.GroceryAIBudget(self.path, clock=self.clock)
        for operation in (lambda: restarted.reserve("rollback", self.body_hash),
                          restarted.snapshot,
                          lambda: restarted.settle("synthetic-request", self.body_hash, self.response()),
                          lambda: budget.initialize_schema(self.path, clock=self.clock)):
            with self.subTest(operation=operation), self.assertRaisesRegex(budget.BudgetGuardError, "clock_rollback"):
                operation()

    def test_naive_or_failed_clock_never_admits(self):
        for clock in (lambda: datetime(2026, 11, 4), lambda: "2026-11"):
            with self.assertRaisesRegex(budget.BudgetGuardError, "clock_invalid"):
                budget.GroceryAIBudget(self.path, clock=clock).reserve("bad-clock", self.body_hash)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 0)

    def test_missing_initialized_table_or_policy_is_not_recreated(self):
        self.reserve()
        with sqlite3.connect(self.path) as conn:
            conn.execute("DROP TABLE grocery_ai_budget_requests")
        for operation in (self.guard.snapshot, lambda: self.reserve("new"),
                          lambda: budget.initialize_schema(self.path, clock=self.clock)):
            with self.subTest(operation=operation), self.assertRaises(budget.BudgetGuardError):
                operation()

    def test_deleted_database_is_not_implicitly_created_or_reinitialized(self):
        self.reserve()
        self.path.unlink()
        with self.assertRaises(budget.BudgetGuardError):
            self.reserve("after-deletion")
        self.assertFalse(self.path.exists())
        # Even if the grocery engine recreates an empty journal, the independent
        # initialization marker prevents a fresh allowance.
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE grocery_batches (request_id TEXT PRIMARY KEY, plan_json TEXT)")
        with self.assertRaises(budget.BudgetGuardError):
            budget.initialize_schema(self.path, clock=self.clock)

    def test_lost_marker_cannot_be_repaired_to_regrant_spending(self):
        self.reserve()
        self.path.with_name(self.path.name + ".ai-budget-initialized").unlink()
        with self.assertRaises(budget.BudgetGuardError):
            self.reserve("after-marker-loss")
        with self.assertRaises(budget.BudgetGuardError):
            budget.initialize_schema(self.path, clock=self.clock)

    def test_record_deletion_or_accounting_tamper_fails_closed(self):
        self.reserve()
        with sqlite3.connect(self.path) as conn:
            conn.execute("DELETE FROM grocery_ai_budget_requests")
        with self.assertRaisesRegex(budget.BudgetGuardError, "state_invalid"):
            self.reserve("after-deletion")

    def test_policy_tamper_does_not_adopt_new_cap_or_model(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE grocery_ai_budget_state SET policy_json='{}'")
        with self.assertRaisesRegex(budget.BudgetGuardError, "state_invalid"):
            self.reserve()

    def test_corrupt_sqlite_never_dispatches_or_repairs(self):
        self.path.write_bytes(b"synthetic corrupt SQLite")
        with self.assertRaises(budget.BudgetGuardError):
            self.reserve()
        with self.assertRaises(budget.BudgetGuardError):
            budget.initialize_schema(self.path, clock=self.clock)
        self.assertEqual(self.path.read_bytes(), b"synthetic corrupt SQLite")

    def test_unknown_identity_cannot_settle_and_conflicts_never_refund(self):
        with self.assertRaisesRegex(budget.BudgetGuardError, "request_conflict"):
            self.guard.settle("not-reserved", self.body_hash, self.response())
        self.reserve()
        with self.assertRaisesRegex(budget.BudgetGuardError, "request_conflict"):
            self.guard.settle("synthetic-request", "f" * 64, self.response())
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 318_000_000)

    def test_output_limit_and_identity_inputs_are_strict(self):
        for maximum in (0, -1, 3001, 128_000, True, 3.0, "3000"):
            with self.subTest(maximum=maximum), self.assertRaises(budget.BudgetGuardError):
                self.reserve(max_output_tokens=maximum)
        for identity, body_hash in (("", self.body_hash), ("\n", self.body_hash),
                                    ("ok", "not-a-hash"), (True, self.body_hash)):
            with self.subTest(identity=identity), self.assertRaises(budget.BudgetGuardError):
                self.guard.reserve(identity, body_hash)
        self.assertEqual(self.guard.snapshot().reserved_nanodollars, 0)

    def test_environment_cannot_override_policy_or_ledger(self):
        with mock.patch.dict("os.environ", {"GROCERY_AI_BUDGET_USD": "999999", "GROCERY_AI_MODEL": "other",
                                           "GROCERY_AI_BUDGET_PATH": str(self.path.parent / "other.sqlite")}):
            self.assertEqual(self.reserve().reservation_nanodollars, 318_000_000)
            self.assertEqual(self.guard.snapshot().remaining_nanodollars, 1_682_000_000)
        self.assertFalse((self.path.parent / "other.sqlite").exists())

    def test_repeated_explicit_initialization_preserves_reservations_and_ledger(self):
        self.reserve()
        before = self.guard.snapshot()
        marker = self.path.with_name(self.path.name + ".ai-budget-initialized").read_bytes()
        budget.initialize_schema(self.path, clock=self.clock)
        self.assertEqual(self.guard.snapshot(), before)
        self.assertEqual(self.path.with_name(self.path.name + ".ai-budget-initialized").read_bytes(), marker)

    def test_initialization_refuses_unbudgeted_ai_history(self):
        for plan in ({"version": 2, "phase": "interpreting", "catalog": []},
                     {"version": 2, "phase": "interpreted", "interpretation": {}},
                     {"version": 2, "prepared": {"items": []}}):
            with self.subTest(plan=plan):
                path = self.path.parent / (hashlib.sha256(json.dumps(plan).encode()).hexdigest() + ".sqlite")
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE grocery_batches (plan_json TEXT)")
                    conn.execute("INSERT INTO grocery_batches VALUES (?)", (json.dumps(plan),))
                with self.assertRaisesRegex(budget.BudgetGuardError, "unaccounted_history"):
                    budget.initialize_schema(path, clock=self.clock)
                self.assertFalse(path.with_name(path.name + ".ai-budget-initialized").exists())

    def test_empty_uninitialized_journal_never_admits(self):
        path = self.path.parent / "uninitialized.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE grocery_batches (plan_json TEXT)")
        with self.assertRaises(budget.BudgetGuardError):
            budget.GroceryAIBudget(path, clock=self.clock).reserve("uninitialized", self.body_hash)

    def test_interrupted_initialization_retains_marker_and_cannot_reset(self):
        path = self.path.parent / "interrupted.sqlite"
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE grocery_batches (plan_json TEXT)")
        original = budget._write_marker
        def interrupt_after_marker(path, ledger_id):
            original(path, ledger_id)
            raise KeyboardInterrupt("synthetic installation interruption")
        with mock.patch.object(budget, "_write_marker", side_effect=interrupt_after_marker):
            with self.assertRaises(KeyboardInterrupt):
                budget.initialize_schema(path, clock=self.clock)
        self.assertTrue(path.with_name(path.name + ".ai-budget-initialized").exists())
        with self.assertRaises(budget.BudgetGuardError):
            budget.initialize_schema(path, clock=self.clock)


if __name__ == "__main__":
    unittest.main()
