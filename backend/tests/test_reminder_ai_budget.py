"""Synthetic offline admission/settlement, crash, restore and concurrency checks."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest import mock

import reminder_ai_budget as budget

HASH = hashlib.sha256(b"synthetic-payload").hexdigest()
OTHER_HASH = hashlib.sha256(b"different-synthetic-payload").hexdigest()


class ReminderBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "reminder-usage.sqlite"
        self.current = datetime.fromisoformat("2026-11-05T12:00:00-05:00")
        self.guard = budget.ReminderBudget(self.path, "1.28", now=lambda: self.current)
        self.guard.initialize()

    def reserve(self, request_id="synthetic-request", body_hash=HASH):
        return self.guard.reserve(request_id, body_hash, budget.MODEL)

    def settle(self, reservation, inputs=1000, outputs=100):
        return self.guard.settle(reservation.reservation_id, inputs, outputs, budget.MODEL, "default")

    def sql(self, query, args=()):
        with sqlite3.connect(self.path) as conn:
            return conn.execute(query, args).fetchall()

    def test_attempt_count_remembers_zero_one_two_and_settled_calls(self):
        self.assertEqual(self.guard.attempt_count("synthetic-request", HASH), 0)
        one = self.reserve()
        self.assertEqual(self.guard.attempt_count("synthetic-request", HASH), 1)
        self.settle(one)
        self.assertEqual(self.guard.attempt_count("synthetic-request", HASH), 1)
        self.reserve()
        self.assertEqual(self.guard.attempt_count("synthetic-request", HASH), 2)
        self.assertEqual(self.guard.attempt_count("new-unrelated-request", HASH), 0)

    def test_attempt_count_never_mutates_db_witness_usage_or_clock_high_water(self):
        self.reserve()
        before_db = self.path.read_bytes()
        marker = budget.marker_path(self.path)
        before_marker = marker.read_bytes()
        before_marker_stat = marker.stat()
        before_state = self.sql("SELECT * FROM reminder_ai_budget_state")
        before_attempts = self.sql("SELECT * FROM reminder_ai_budget_attempts")
        self.current += timedelta(days=1)
        with mock.patch.object(budget, "_write_marker", side_effect=AssertionError("read must not write witness")):
            self.assertEqual(self.guard.attempt_count("synthetic-request", HASH), 1)
            self.assertEqual(self.guard.attempt_count("never-seen", HASH), 0)
        self.assertEqual(self.path.read_bytes(), before_db)
        self.assertEqual(marker.read_bytes(), before_marker)
        self.assertEqual(marker.stat().st_ino, before_marker_stat.st_ino)
        self.assertEqual(marker.stat().st_mtime_ns, before_marker_stat.st_mtime_ns)
        self.assertEqual(self.sql("SELECT * FROM reminder_ai_budget_state"), before_state)
        self.assertEqual(self.sql("SELECT * FROM reminder_ai_budget_attempts"), before_attempts)

    def test_attempt_count_conflicting_hash_and_invalid_identity_fail_closed(self):
        self.reserve()
        before = budget.marker_path(self.path).read_bytes()
        with self.assertRaisesRegex(budget.BudgetBlocked, "request_conflict"):
            self.guard.attempt_count("synthetic-request", OTHER_HASH)
        for request_id, digest in (("", HASH), ("synthetic-request", "invalid-hash")):
            with self.subTest(request_id=request_id), self.assertRaisesRegex(budget.BudgetBlocked, "identity_invalid"):
                self.guard.attempt_count(request_id, digest)
        self.assertEqual(budget.marker_path(self.path).read_bytes(), before)

    def test_attempt_count_validates_persisted_clock_and_private_file_contract(self):
        self.reserve()
        before = budget.marker_path(self.path).read_bytes()
        self.current -= timedelta(microseconds=1)
        with self.assertRaisesRegex(budget.BudgetBlocked, "clock_rollback"):
            self.guard.attempt_count("synthetic-request", HASH)
        self.current += timedelta(microseconds=1)
        self.path.chmod(0o644)
        with self.assertRaisesRegex(budget.BudgetBlocked, "state_invalid"):
            self.guard.attempt_count("synthetic-request", HASH)
        self.path.chmod(0o600)
        self.assertEqual(budget.marker_path(self.path).read_bytes(), before)

    def test_attempt_count_refuses_uninitialized_missing_and_restored_counter(self):
        missing = Path(self.temp.name) / "not-initialized.sqlite"
        guard = budget.ReminderBudget(missing, now=lambda: self.current)
        with self.assertRaisesRegex(budget.BudgetBlocked, "uninitialized"):
            guard.attempt_count("synthetic-request", HASH)
        self.assertFalse(missing.exists())
        backup = Path(self.temp.name) / "before-attempt.sqlite"
        shutil.copyfile(self.path, backup)
        self.reserve()
        shutil.copyfile(backup, self.path)
        with self.assertRaisesRegex(budget.BudgetBlocked, "state_invalid"):
            self.guard.attempt_count("synthetic-request", HASH)
        self.path.unlink()
        with self.assertRaisesRegex(budget.BudgetBlocked, "state_missing"):
            self.guard.attempt_count("synthetic-request", HASH)

    def test_attempt_count_rejects_witness_transition_during_read(self):
        self.reserve()
        original = budget._read_marker
        reads = 0
        def changed_witness(path):
            nonlocal reads
            value = original(path)
            reads += 1
            if reads > 1:
                return dict(value, sequence=value["sequence"] + 1)
            return value
        with mock.patch.object(budget, "_read_marker", side_effect=changed_witness):
            with self.assertRaisesRegex(budget.BudgetBlocked, "state_changed"):
                self.guard.attempt_count("synthetic-request", HASH)

    def test_safe_codes_distinguish_uninitialized_missing_and_exhausted(self):
        missing = Path(self.temp.name) / "new-counter.sqlite"
        guard = budget.ReminderBudget(missing, now=lambda: self.current)
        with self.assertRaises(budget.BudgetBlocked) as caught:
            guard.reserve("missing", HASH, budget.MODEL)
        self.assertEqual(caught.exception.code, "reminder_ai_budget_uninitialized")
        default = budget.ReminderBudget(self.path, now=lambda: self.current)
        with self.assertRaises(budget.BudgetBlocked) as caught:
            default.reserve("default", HASH, budget.MODEL)
        self.assertEqual(caught.exception.code, "reminder_ai_budget_monthly_limit")
        self.path.unlink()
        with self.assertRaises(budget.BudgetBlocked) as caught:
            self.reserve()
        self.assertEqual(caught.exception.code, "reminder_ai_budget_state_missing")

    def test_default_is_zero_even_with_a_valid_initialized_ledger(self):
        default = budget.ReminderBudget(self.path, now=lambda: self.current)
        with self.assertRaisesRegex(budget.BudgetBlocked, "monthly_limit"):
            default.reserve("zero-cap", HASH, budget.MODEL)
        self.assertEqual(default.snapshot().total_attempts, 0)

    def test_constructor_and_requests_never_initialize_or_repair_missing_state(self):
        missing = Path(self.temp.name) / "never-initialized.sqlite"
        guard = budget.ReminderBudget(missing, "2", now=lambda: self.current)
        with self.assertRaises(budget.BudgetBlocked):
            guard.reserve("missing", HASH, budget.MODEL)
        self.assertFalse(missing.exists())
        self.assertFalse(budget.marker_path(missing).exists())

    def test_limit_validation_uses_exact_decimal_without_rounding_up(self):
        for value in (0, 2.0, True, "NaN", "Infinity", "-0.01", "1000000.01", "0.0000000001", "nonsense"):
            with self.subTest(value=value), self.assertRaisesRegex(budget.BudgetBlocked, "limit_invalid"):
                budget.ReminderBudget(self.path, value)
        guard = budget.ReminderBudget(self.path, Decimal("0.319999999"), now=lambda: self.current)
        with self.assertRaisesRegex(budget.BudgetBlocked, "monthly_limit"):
            guard.reserve("one-nano-short", HASH, budget.MODEL)

    def test_fixed_reservation_exceeds_full_context_conservative_envelope(self):
        envelope = (Decimal(budget.CONTEXT_TOKENS) * Decimal("0.30")
                    + Decimal(budget.MAX_OUTPUT_TOKENS) * Decimal("1")) / 1_000_000
        self.assertGreaterEqual(budget.DEBIT_USD, envelope)
        self.assertEqual(budget.MAX_OUTPUT_TOKENS, 2048)
        self.assertEqual(budget.MAX_REQUEST_BYTES, 32768)
        self.assertEqual(self.reserve().debit_usd, Decimal("0.32"))

    def test_each_attempt_debits_before_transport_and_two_attempts_is_lifetime_limit(self):
        one = self.reserve()
        two = self.reserve()
        self.assertEqual((one.attempt, two.attempt), (1, 2))
        self.assertNotEqual(one.reservation_id, two.reservation_id)
        with self.assertRaisesRegex(budget.BudgetBlocked, "attempt_limit"):
            self.reserve()
        self.assertEqual(self.guard.snapshot().debited_usd, Decimal("0.64"))

    def test_timeout_refusal_crash_and_unknown_outcome_keep_full_debit_on_restart(self):
        # No receipt was obtained for either attempt. No refund operation exists.
        self.reserve()
        self.reserve()
        restarted = budget.ReminderBudget(self.path, "1.28", now=lambda: self.current)
        self.assertEqual(restarted.snapshot().reserved_usd, Decimal("0.64"))
        with self.assertRaisesRegex(budget.BudgetBlocked, "attempt_limit"):
            restarted.reserve("synthetic-request", HASH, budget.MODEL)

    def test_payload_hash_conflict_does_not_receive_another_allowance(self):
        self.reserve()
        with self.assertRaisesRegex(budget.BudgetBlocked, "request_conflict"):
            self.reserve(body_hash=OTHER_HASH)
        self.assertEqual(self.guard.snapshot().total_attempts, 1)

    def test_request_identity_and_model_fail_closed(self):
        for request_id, digest, model in (("", HASH, budget.MODEL), ("x\n", HASH, budget.MODEL),
                                          ("x" * 513, HASH, budget.MODEL), ("x", "bad", budget.MODEL),
                                          ("x", HASH, "gpt-6.1-sol"), ("x", HASH, "gpt-6-luna-new")):
            with self.subTest(request_id=repr(request_id), model=model), self.assertRaises(budget.BudgetBlocked):
                self.guard.reserve(request_id, digest, model)
        self.assertEqual(self.guard.snapshot().total_attempts, 0)

    def test_concurrent_distinct_requests_never_exceed_monthly_cap(self):
        def attempt(number):
            try:
                return self.guard.reserve(f"concurrent-{number}", HASH, budget.MODEL)
            except budget.BudgetBlocked:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(20)))
        self.assertEqual(sum(r is not None for r in results), 4)
        snapshot = self.guard.snapshot()
        self.assertEqual(snapshot.debited_usd, Decimal("1.28"))
        self.assertEqual(snapshot.remaining_usd, 0)

    def test_concurrent_same_request_never_exceeds_two_attempts(self):
        def attempt(_):
            try:
                return self.reserve()
            except budget.BudgetBlocked:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(attempt, range(12)))
        self.assertEqual(sorted(r.attempt for r in results if r), [1, 2])

    def test_completed_receipt_settles_at_conservative_rates_and_releases_excess(self):
        reservation = self.reserve()
        receipt = self.settle(reservation)
        self.assertTrue(receipt.settled)
        self.assertEqual(receipt.charged_usd, Decimal("0.0004"))
        snapshot = self.guard.snapshot()
        self.assertEqual(snapshot.reserved_usd, 0)
        self.assertEqual(snapshot.charged_usd, Decimal("0.0004"))
        self.assertEqual(snapshot.remaining_usd, Decimal("1.2796"))

    def test_repeated_identical_settlement_cannot_release_twice(self):
        reservation = self.reserve()
        first = self.settle(reservation)
        before = self.guard.snapshot()
        self.current += timedelta(seconds=1)
        self.assertEqual(self.settle(reservation), first)
        self.assertEqual(self.guard.snapshot(), before)

    def test_conflicting_settlement_is_rejected_without_changing_charge(self):
        reservation = self.reserve()
        self.settle(reservation)
        with self.assertRaisesRegex(budget.BudgetBlocked, "settlement_conflict"):
            self.settle(reservation, inputs=999)
        self.assertEqual(self.guard.snapshot().charged_usd, Decimal("0.0004"))

    def test_invalid_usage_never_releases_the_reservation(self):
        reservation = self.reserve()
        cases = [(-1, 100, budget.MODEL, "default"), (True, 100, budget.MODEL, "default"),
                 (1000, 1.5, budget.MODEL, "default"), ("1000", 100, budget.MODEL, "default"),
                 (1000, 2049, budget.MODEL, "default"), (budget.CONTEXT_TOKENS, 1, budget.MODEL, "default"),
                 (budget.CONTEXT_TOKENS + 1, 0, budget.MODEL, "default"),
                 (1000, 100, "another-model", "default"), (1000, 100, budget.MODEL, "fast"),
                 (1000, 100, budget.MODEL, None)]
        for args in cases:
            with self.subTest(args=args):
                result = self.guard.settle(reservation.reservation_id, *args)
                self.assertFalse(result.settled)
                self.assertEqual(self.guard.snapshot().reserved_usd, Decimal("0.32"))

    def test_settlement_never_restores_attempt_allowance(self):
        self.settle(self.reserve())
        self.settle(self.reserve())
        with self.assertRaisesRegex(budget.BudgetBlocked, "attempt_limit"):
            self.reserve()
        self.assertEqual(self.guard.snapshot().total_attempts, 2)

    def test_unknown_and_invalid_reservation_ids_cannot_release_allowance(self):
        self.reserve()
        for reservation_id in (True, 0, -1, "1", 999):
            with self.subTest(reservation_id=reservation_id), self.assertRaises(budget.BudgetBlocked):
                self.guard.settle(reservation_id, 1000, 100, budget.MODEL, "default")
        self.assertEqual(self.guard.snapshot().reserved_usd, Decimal("0.32"))

    def test_uncertain_reservations_carry_into_every_future_month(self):
        self.reserve()
        self.current = datetime.fromisoformat("2027-02-01T12:00:00-05:00")
        snapshot = self.guard.snapshot()
        self.assertEqual(snapshot.month, "2027-02")
        self.assertEqual(snapshot.attempts_this_month, 0)
        self.assertEqual(snapshot.reserved_usd, Decimal("0.32"))
        self.assertEqual(snapshot.remaining_usd, Decimal("0.96"))

    def test_calendar_month_boundary_is_bogota_and_settlement_covers_both_months(self):
        self.current = datetime.fromisoformat("2026-12-01T04:59:59+00:00")
        reservation = self.reserve()
        self.assertEqual(reservation.month, "2026-11")
        self.current += timedelta(seconds=1)
        self.assertEqual(self.guard.snapshot().month, "2026-12")
        self.settle(reservation)
        self.assertEqual(self.guard.snapshot().charged_usd, Decimal("0.0004"))
        row = self.sql("SELECT month,settled_month FROM reminder_ai_budget_attempts")[0]
        self.assertEqual(row, ("2026-11", "2026-12"))
        self.current = datetime.fromisoformat("2027-01-01T12:00:00-05:00")
        self.assertEqual(self.guard.snapshot().debited_usd, 0)
        # Lifetime call attempts do not reset with a calendar month.
        self.reserve()
        with self.assertRaisesRegex(budget.BudgetBlocked, "attempt_limit"):
            self.reserve()

    def test_clock_rollback_within_month_is_durable(self):
        self.reserve()
        self.current -= timedelta(microseconds=1)
        restarted = budget.ReminderBudget(self.path, "2", now=lambda: self.current)
        for operation in (restarted.snapshot, restarted.initialize,
                          lambda: restarted.reserve("rollback", HASH, budget.MODEL)):
            with self.subTest(operation=operation), self.assertRaisesRegex(budget.BudgetBlocked, "clock_rollback"):
                operation()

    def test_denied_call_still_advances_clock_witness(self):
        guard = budget.ReminderBudget(self.path, now=lambda: self.current)
        self.current += timedelta(minutes=1)
        with self.assertRaisesRegex(budget.BudgetBlocked, "monthly_limit"):
            guard.reserve("denied", HASH, budget.MODEL)
        self.current -= timedelta(seconds=1)
        with self.assertRaisesRegex(budget.BudgetBlocked, "clock_rollback"):
            self.guard.snapshot()

    def test_invalid_clock_never_grants_a_call(self):
        for clock in (lambda: datetime(2026, 11, 5), lambda: None, lambda: "bad"):
            guard = budget.ReminderBudget(self.path, "2", now=clock)
            with self.subTest(clock=clock), self.assertRaisesRegex(budget.BudgetBlocked, "clock_invalid"):
                guard.reserve("bad-clock", HASH, budget.MODEL)

    def test_deleted_database_cannot_reinitialize_against_existing_witness(self):
        self.reserve()
        self.path.unlink()
        for operation in (self.guard.snapshot, self.guard.initialize, lambda: self.reserve("after-loss")):
            with self.subTest(operation=operation), self.assertRaises(budget.BudgetBlocked):
                operation()
        self.assertFalse(self.path.exists())

    def test_empty_database_cannot_reinitialize_against_existing_witness(self):
        self.path.write_bytes(b"")
        for operation in (self.guard.snapshot, self.guard.initialize):
            with self.subTest(operation=operation), self.assertRaises(budget.BudgetBlocked):
                operation()

    def test_missing_or_empty_marker_is_not_automatically_repaired(self):
        marker = budget.marker_path(self.path)
        original = marker.read_bytes()
        for value in (None, b"", b"{}", b"x" * 2049):
            if marker.exists():
                marker.unlink()
            if value is not None:
                marker.write_bytes(value)
                marker.chmod(0o600)
            for operation in (self.guard.snapshot, self.guard.initialize):
                with self.subTest(value=value, operation=operation), self.assertRaises(budget.BudgetBlocked):
                    operation()
            if marker.exists():
                marker.unlink()
        marker.write_bytes(original)
        marker.chmod(0o600)

    def test_restoring_older_database_with_same_ledger_id_is_detected(self):
        backup = Path(self.temp.name) / "old.sqlite"
        shutil.copyfile(self.path, backup)
        self.reserve()
        shutil.copyfile(backup, self.path)
        with self.assertRaisesRegex(budget.BudgetBlocked, "state_invalid"):
            self.reserve("after-db-restore")
        with self.assertRaises(budget.BudgetBlocked):
            self.guard.initialize()

    def test_restoring_pre_settlement_database_is_detected(self):
        reservation = self.reserve()
        backup = Path(self.temp.name) / "reserved.sqlite"
        shutil.copyfile(self.path, backup)
        self.settle(reservation)
        shutil.copyfile(backup, self.path)
        with self.assertRaisesRegex(budget.BudgetBlocked, "state_invalid"):
            self.guard.snapshot()

    def test_corrupt_deleted_or_extra_sql_state_blocks(self):
        for sql in ("DELETE FROM reminder_ai_budget_state", "DROP TABLE reminder_ai_budget_attempts",
                    "UPDATE reminder_ai_budget_state SET sequence=0", "DELETE FROM reminder_ai_budget_attempts",
                    "UPDATE reminder_ai_budget_attempts SET debit_nanos=1", "CREATE TABLE unrelated(x)"):
            with self.subTest(sql=sql):
                directory = Path(self.temp.name) / hashlib.sha256(sql.encode()).hexdigest()[:8]
                directory.mkdir(mode=0o700)
                guard = budget.ReminderBudget(directory / "usage.sqlite", "2", now=lambda: self.current)
                guard.initialize()
                guard.reserve("before-corruption", HASH, budget.MODEL)
                with sqlite3.connect(guard.path) as conn:
                    conn.execute(sql)
                with self.assertRaises(budget.BudgetBlocked):
                    guard.reserve("after-corruption", HASH, budget.MODEL)

    def test_interrupted_initialization_retains_witness_and_fails_closed(self):
        path = Path(self.temp.name) / "interrupted.sqlite"
        guard = budget.ReminderBudget(path, "2", now=lambda: self.current)
        original = budget._write_marker
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic interrupted initialization")
        with mock.patch.object(budget, "_write_marker", side_effect=crash), self.assertRaises(budget.BudgetBlocked):
            guard.initialize()
        self.assertTrue(budget.marker_path(path).exists())
        with self.assertRaises(budget.BudgetBlocked):
            guard.initialize()

    def test_interrupted_reservation_after_witness_never_grants_a_transport(self):
        original = budget._write_marker
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic crash before SQL commit")
        with mock.patch.object(budget, "_write_marker", side_effect=crash), self.assertRaises(budget.BudgetBlocked):
            self.reserve()
        with self.assertRaises(budget.BudgetBlocked):
            self.reserve("retry-after-crash")

    def test_interrupted_settlement_fails_closed_instead_of_refunding_again(self):
        reservation = self.reserve()
        original = budget._write_marker
        def crash(*args, **kwargs):
            original(*args, **kwargs)
            raise OSError("synthetic crash after settlement witness")
        with mock.patch.object(budget, "_write_marker", side_effect=crash), self.assertRaises(budget.BudgetBlocked):
            self.settle(reservation)
        with self.assertRaises(budget.BudgetBlocked):
            self.guard.snapshot()

    def test_symlink_and_non_private_files_fail_closed(self):
        marker = budget.marker_path(self.path)
        real_marker = marker.with_name("actual-marker")
        marker.rename(real_marker)
        marker.symlink_to(real_marker)
        with self.assertRaises(budget.BudgetBlocked):
            self.reserve()
        marker.unlink()
        real_marker.rename(marker)
        self.path.chmod(0o644)
        with self.assertRaises(budget.BudgetBlocked):
            self.reserve()
        self.path.chmod(0o600)
        real_db = self.path.with_name("actual.sqlite")
        self.path.rename(real_db)
        self.path.symlink_to(real_db)
        with self.assertRaises(budget.BudgetBlocked):
            self.reserve()

    def test_bootstrap_refuses_shared_parent_directory(self):
        directory = Path(self.temp.name) / "shared"
        directory.mkdir(mode=0o755)
        guard = budget.ReminderBudget(directory / "usage.sqlite", "2", now=lambda: self.current)
        with self.assertRaisesRegex(budget.BudgetBlocked, "directory_not_private"):
            guard.initialize()
        self.assertFalse(guard.path.exists())

    def test_counter_contains_hashes_and_usage_only_no_raw_request_text(self):
        sentinel = "synthetic-sensitive-capture-never-persist-this"
        self.reserve(request_id=sentinel)
        rows = self.sql("SELECT * FROM reminder_ai_budget_attempts")
        self.assertNotIn(sentinel, str(rows))
        self.assertNotIn(sentinel.encode(), self.path.read_bytes())
        self.assertNotIn(sentinel.encode(), budget.marker_path(self.path).read_bytes())

    def test_cap_change_does_not_reset_usage_or_attempts(self):
        self.reserve()
        lower = budget.ReminderBudget(self.path, "0", now=lambda: self.current)
        self.assertEqual(lower.snapshot().remaining_usd, 0)
        with self.assertRaisesRegex(budget.BudgetBlocked, "monthly_limit"):
            lower.reserve("blocked-lower-cap", HASH, budget.MODEL)
        higher = budget.ReminderBudget(self.path, "2", now=lambda: self.current)
        self.assertEqual(higher.reserve("synthetic-request", HASH, budget.MODEL).attempt, 2)
        with self.assertRaisesRegex(budget.BudgetBlocked, "attempt_limit"):
            higher.reserve("synthetic-request", HASH, budget.MODEL)

    def test_initialize_existing_ledger_preserves_usage_and_never_grants_reset(self):
        self.reserve()
        before = self.guard.snapshot()
        self.current += timedelta(seconds=1)
        self.guard.initialize()
        self.assertEqual(self.guard.snapshot(), before)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(budget.marker_path(self.path)).st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
