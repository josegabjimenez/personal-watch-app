"""Synthetic local upgrade faults; no real service, helper, key or network use."""
from __future__ import annotations

from dataclasses import replace
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

import upgrade_release as upgrade


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def tree_snapshot(root):
    return {str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o777)
            for path in root.rglob("*") if path.is_file()}


class FakeController:
    def __init__(self, case):
        self.case = case
        self.active, self.pid = True, 123
        self.events = []
        self.on_stop = self.on_start = self.on_reload = None
        self.environment = (("MOBILE_CAPTURE_AURA_HELPER", str(case.helper)), ("UNCHANGED_SECRET_ENV", "private-unreported"))
        self.argv = ("/usr/bin/python3", str(case.entrypoint), "--host", "127.0.0.1", "--port", "8650",
                     "--ledger", str(case.ledger), "--aura-helper", str(case.helper),
                     "--secret-file", str(case.secret), "--write-enabled", "--write-domains", upgrade.DOMAIN)
        self.unit_files = (case.unit_file,)

    def snapshot(self):
        return upgrade.ServiceSnapshot(self.active, self.pid, self.argv, self.case.runtime,
            os.getuid(), os.getgid(), tuple(sorted(self.environment)), self.unit_files, "stable-unit-exec")

    def stop(self):
        self.events.append("stop")
        if self.active:
            # At the first stop, the parent admission reservation must be held.
            with sqlite3.connect(self.case.ledger, timeout=0) as conn:
                try:
                    conn.execute("BEGIN IMMEDIATE")
                except sqlite3.OperationalError:
                    self.case.stop_guard_seen = True
                else:
                    conn.rollback()
        self.active, self.pid = False, 0
        if self.on_stop:
            self.on_stop()

    def reload(self):
        self.events.append("reload")
        self.unit_files = (self.case.unit_file, self.case.paths.dropin)
        if self.on_reload:
            self.on_reload()

    def start(self):
        self.events.append("start")
        self.active, self.pid = True, 123 + self.events.count("start")
        if self.case.paths.dropin.exists():
            self.environment = tuple((key, value) for key, value in self.environment if not key.startswith("GROCERY_AI_")) + tuple(
                {**upgrade.GROCERY_SETTINGS, "GROCERY_AI_OPENAI_KEY_FILE": str(self.case.key)}.items())
        if self.on_start:
            self.on_start()


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name).resolve()
        self.runtime, self.package = self.case / "runtime", self.case / "package"
        self.runtime.mkdir()
        self.package.mkdir()
        self.entrypoint = self.runtime / upgrade.SERVER
        self.baseline = {name: ("# synthetic old " + name + "\n").encode() for name in upgrade.BASELINE_ARTIFACTS}
        self.target = {name: ("# synthetic new " + name + "\n").encode() for name in upgrade.ARTIFACT_NAMES}
        for name, raw in self.baseline.items():
            (self.runtime / name).write_bytes(raw)
            (self.runtime / name).chmod(0o640)
        for name, raw in self.target.items():
            (self.package / name).write_bytes(raw)
        self.manifest = {"format_version": 1, "runtime_file_count": 6, "journal_suffix": ".grocery.sqlite3",
            "external_helper_sha256": upgrade.HELPER_SHA256,
            "artifacts": {name: digest(raw) for name, raw in self.target.items()}}
        raw = (json.dumps(self.manifest, sort_keys=True) + "\n").encode()
        (self.package / "manifest.json").write_bytes(raw)
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(upgrade, "EXPECTED_UID", os.getuid()).start()
        mock.patch.object(upgrade, "MANIFEST_SHA256", digest(raw)).start()
        mock.patch.object(upgrade, "BASELINE_ARTIFACTS", {name: digest(raw) for name, raw in self.baseline.items()}).start()
        self.helper = self.case / "helper.py"
        self.helper.write_text("# synthetic never execute\n")
        self.key = self.case / "provider-key.private"
        self.key.write_text("synthetic-never-read-provider-key")
        self.key.chmod(0o600)
        self.secret = self.case / "route-secret.private"
        self.secret.write_text("synthetic-never-read-hmac-secret")
        self.secret.chmod(0o600)
        self.unit_root = self.case / "units"
        self.unit_root.mkdir()
        self.unit_file = self.unit_root / upgrade.UNIT
        self.unit_file.write_text("[Service]\nExecStart=/usr/bin/python3 synthetic\nEnvironment=UNCHANGED_SECRET_ENV=private-unreported\n")
        self.ledger = self.case / "mobile.sqlite3"
        with sqlite3.connect(self.ledger) as conn:
            conn.execute("CREATE TABLE mobile_capture_ledger(request_id TEXT PRIMARY KEY, domain TEXT, status TEXT, response_json TEXT)")
            conn.execute("INSERT INTO mobile_capture_ledger VALUES(?,?,?,?)",
                ("synthetic-private-request", upgrade.DOMAIN, "committed", '{"private":"dictated words"}'))
        self.original_parent = self.ledger.read_bytes()
        self.original_parent_identity = upgrade.metadata(self.ledger)[:2]
        self.paths = upgrade.Paths(self.package, self.case / "backups", self.case / "upgrade.lock", self.key, self.unit_root)
        self.controller = FakeController(self)
        self.stop_guard_seen = False
        self.health_calls = 0
        self.initializer_calls = 0
        self.init_hook = None
        self.health_hook = None
        # The controller, initializer and health are injected; any accidental
        # process/service/provider access makes an offline test fail immediately.
        mock.patch("subprocess.run", side_effect=AssertionError("external subprocess forbidden")).start()
        mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("network forbidden")).start()
        original_read = upgrade.regular_bytes
        def no_credentials(path):
            if path in {self.key, self.secret}:
                raise AssertionError("credential contents must never be read")
            return original_read(path)
        mock.patch.object(upgrade, "regular_bytes", side_effect=no_credentials).start()

    @property
    def journal(self):
        return Path(str(self.ledger) + ".grocery.sqlite3")

    @property
    def marker(self):
        return Path(str(self.journal) + ".ai-budget-initialized")

    def journal_create(self, *, version=1, status="committed", budget=False, operation_status="committed"):
        with sqlite3.connect(self.journal) as conn:
            conn.execute("CREATE TABLE grocery_batches(request_id TEXT PRIMARY KEY, payload_hash TEXT, plan_json TEXT, plan_hash TEXT, response_json TEXT, status TEXT, created_at TEXT, updated_at TEXT)")
            plan = {"version": version, "items": [{"private": "raw dictation"}]} if status != "preflight" else {"version": version, "phase": "preflight"}
            conn.execute("INSERT INTO grocery_batches(request_id,plan_json,status) VALUES(?,?,?)", ("synthetic-private-batch", json.dumps(plan), status))
            conn.execute("CREATE TABLE grocery_operations(operation_id TEXT PRIMARY KEY, plan_hash TEXT, plan_json TEXT, destination TEXT, status TEXT, candidate_id TEXT, receipt_json TEXT, error_code TEXT)")
            if operation_status:
                conn.execute("INSERT INTO grocery_operations(operation_id,status) VALUES(?,?)", ("synthetic-private-operation", operation_status))
            if budget:
                self.budget_create(conn)
        if budget:
            self.marker.write_text("synthetic-marker-retained")
            self.marker.chmod(0o600)

    def budget_create(self, conn):
        conn.execute("CREATE TABLE grocery_ai_budget_state(id INTEGER PRIMARY KEY, policy_json TEXT, policy_hash TEXT, ledger_id TEXT, high_water_month TEXT, request_count INTEGER, settled_count INTEGER, total_reserved_nanodollars INTEGER, total_charged_nanodollars INTEGER, integrity_hash TEXT)")
        conn.execute("INSERT INTO grocery_ai_budget_state(id,request_count) VALUES(1,0)")
        conn.execute("CREATE TABLE grocery_ai_budget_requests(request_id TEXT PRIMARY KEY, body_hash TEXT, max_output_tokens INTEGER, admitted_month TEXT, reserved_nanodollars INTEGER, status TEXT, settled_month TEXT, input_tokens INTEGER, output_tokens INTEGER, usage_hash TEXT, charged_nanodollars INTEGER, integrity_hash TEXT)")

    def initialize(self, package, journal):
        self.initializer_calls += 1
        self.assertFalse(self.controller.active)
        self.assertEqual({path.name for path in package.iterdir()}, upgrade.ARTIFACT_NAMES)
        self.assertTrue((self.installer.backup / "backup.json").exists())
        for name, raw in self.target.items():
            self.assertEqual((package / name).read_bytes(), raw)
        if self.init_hook:
            return self.init_hook(package, journal)
        with sqlite3.connect(journal) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "grocery_batches" not in tables:
                conn.execute("CREATE TABLE grocery_batches(request_id TEXT PRIMARY KEY, payload_hash TEXT, plan_json TEXT, plan_hash TEXT, response_json TEXT, status TEXT, created_at TEXT, updated_at TEXT)")
            if "grocery_ai_budget_state" not in tables:
                self.budget_create(conn)
                self.marker.write_text("synthetic-marker-retained")
                self.marker.chmod(0o600)

    def health(self):
        self.health_calls += 1
        self.assertTrue(self.controller.active)
        if self.health_hook:
            return self.health_hook()
        return dict(upgrade.EXPECTED_HEALTH)

    def upgrader(self):
        self.installer = upgrade.Upgrader(self.paths, self.controller, self.health,
            initializer=self.initialize, helper_sha256=digest(self.helper.read_bytes()),
            sleep=lambda _: None, lock_timeout=0, drain_timeout=0)
        return self.installer

    def assert_report_private(self, report):
        raw = json.dumps(report)
        for value in (str(self.case), "synthetic-private", "dictated words", "private-unreported", "provider-key", "raw dictation"):
            self.assertNotIn(value, raw)

    def test_default_preflight_readonly(self):
        self.journal_create()
        before = tree_snapshot(self.case)
        report = self.upgrader().preflight()
        self.assertEqual(report["status"], "preflight_ok")
        self.assertEqual(report["batch_count"], 1)
        self.assertEqual(tree_snapshot(self.case), before)
        self.assertEqual(self.controller.events, [])
        self.assertEqual(self.initializer_calls, 0)
        self.assert_report_private(report)

    def test_apply_requires_explicit_budget_initialization(self):
        with self.assertRaisesRegex(upgrade.UpgradeError, "explicit_budget"):
            self.upgrader().apply()
        self.assertEqual(self.controller.events, [])

    def test_success_backups_complete_preserves_data_and_installs_entrypoint_last(self):
        self.journal_create()
        original_journal_identity = upgrade.metadata(self.journal)[:2]
        old_journal = self.journal.read_bytes()
        installer = self.upgrader()
        replaced = []
        original_replace = installer._replace
        def record(source, destination):
            replaced.append(destination.name)
            original_replace(source, destination)
        installer._replace = record
        report = installer.apply(initialize=True)
        self.assertEqual(report["status"], "installed")
        self.assertEqual(self.controller.events, ["stop", "reload", "start"])
        self.assertTrue(self.stop_guard_seen)
        self.assertEqual(replaced[-1], upgrade.SERVER)
        self.assertEqual(set(replaced), upgrade.ARTIFACT_NAMES)
        self.assertEqual(self.ledger.read_bytes(), self.original_parent)
        self.assertEqual(upgrade.metadata(self.ledger)[:2], self.original_parent_identity)
        self.assertEqual(upgrade.metadata(self.journal)[:2], original_journal_identity)
        for name, raw in self.target.items():
            self.assertEqual((self.runtime / name).read_bytes(), raw)
            self.assertEqual((self.runtime / name).stat().st_mode & 0o777, 0o640)
        for name, raw in self.baseline.items():
            self.assertEqual((installer.backup / ("previous-" + name)).read_bytes(), raw)
        with sqlite3.connect(installer.backup / "parent.sqlite3") as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchall(), [("ok",)])
        with sqlite3.connect(installer.backup / "journal.sqlite3") as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_batches").fetchone()[0], 1)
            self.assertNotIn("grocery_ai_budget_state", {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        self.assertTrue(old_journal)
        self.assertTrue(self.marker.exists())
        self.assert_report_private(report)
        backup_record = (installer.backup / "backup.json").read_text()
        self.assertNotIn(str(self.case), backup_record)
        self.assertNotIn("private-unreported", backup_record)
        self.assertEqual(set(dict(self.controller.environment)) & set(upgrade.GROCERY_SETTINGS), set(upgrade.GROCERY_SETTINGS))

    def test_absent_journal_created_additively(self):
        report = self.upgrader().apply(initialize=True)
        self.assertEqual(report["status"], "installed")
        self.assertEqual(self.ledger.read_bytes(), self.original_parent)
        records = json.loads((self.installer.backup / "backup.json").read_bytes())["records"]
        self.assertEqual(records["journal"], {"present": False})

    def test_existing_budget_and_marker_backed_up_not_reset(self):
        self.journal_create(version=2, budget=True)
        original_marker = self.marker.read_bytes()
        original_journal = self.journal.read_bytes()
        report = self.upgrader().apply(initialize=True)
        self.assertEqual(report["status"], "installed")
        self.assertEqual(self.marker.read_bytes(), original_marker)
        self.assertEqual((self.installer.backup / "budget-initialized.marker").read_bytes(), original_marker)
        self.assertEqual(self.journal.read_bytes(), original_journal)

    def test_backup_failure_resumes_verified_baseline(self):
        installer = self.upgrader()
        installer._backup_db = mock.Mock(side_effect=upgrade.UpgradeError("readback_failed"))
        report = installer.apply(initialize=True)
        self.assertEqual(report["status"], "baseline_resumed")
        self.assertTrue(self.controller.active)
        self.assertEqual(self.initializer_calls, 0)
        self.assertEqual(self.controller.events, ["stop", "start"])
        self.assertFalse(self.paths.dropin.exists())
        self.assertEqual(self.ledger.read_bytes(), self.original_parent)

    def test_failure_during_budget_initialization_retains_marker_and_stops(self):
        def fail(package, journal):
            self.marker.write_text("incomplete-marker-must-survive")
            raise RuntimeError("raw private error must not enter report")
        self.init_hook = fail
        report = self.upgrader().apply(initialize=True)
        self.assertEqual(report["status"], "attention")
        self.assertFalse(self.controller.active)
        self.assertEqual(self.marker.read_text(), "incomplete-marker-must-survive")
        self.assertNotIn("start", self.controller.events)
        self.assertNotIn("raw private error", json.dumps(report))
        for name, raw in self.baseline.items():
            self.assertEqual((self.runtime / name).read_bytes(), raw)

    def test_partial_code_failure_never_restores_database_or_old_writer(self):
        installer = self.upgrader()
        original_replace = installer._replace
        count = 0
        def fail(source, destination):
            nonlocal count
            count += 1
            original_replace(source, destination)
            if count == 2:
                raise OSError("private path")
        installer._replace = fail
        report = installer.apply(initialize=True)
        self.assertEqual(report["code"], "compatible_runtime_required")
        self.assertFalse(self.controller.active)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.ledger.read_bytes(), self.original_parent)
        self.assertNotIn("start", self.controller.events)
        self.assert_report_private(report)

    def test_restart_health_failure_stops_new_compatible_runtime(self):
        def health():
            if "start" in self.controller.events:
                return {**upgrade.EXPECTED_HEALTH, "write_domains": ["finance.expense"]}
            return dict(upgrade.EXPECTED_HEALTH)
        self.health_hook = health
        report = self.upgrader().apply(initialize=True)
        self.assertEqual(report["status"], "attention")
        self.assertFalse(self.controller.active)
        for name, raw in self.target.items():
            self.assertEqual((self.runtime / name).read_bytes(), raw)
        self.assertTrue(self.marker.exists())

    def test_restart_transient_pre_exec_arguments_waits_for_verified_runtime(self):
        installer = self.upgrader()
        original_argv = self.controller.argv
        self.controller.on_start = lambda: setattr(self.controller, "argv", ("/usr/lib/systemd/systemd",))
        def ready(_):
            self.controller.argv = original_argv
        installer.sleep = mock.Mock(side_effect=ready)
        report = installer.apply(initialize=True)
        self.assertEqual(report["status"], "installed")
        installer.sleep.assert_called_once_with(0.1)
        self.assertEqual(self.controller.events, ["stop", "reload", "start"])
        self.assertTrue(self.controller.active)
        self.assertEqual(self.initializer_calls, 1)
        self.assertTrue(self.marker.exists())
        self.assert_report_private(report)

    def test_restart_transient_inactive_and_snapshot_metadata_waits(self):
        installer = self.upgrader()
        snapshot = self.controller.snapshot
        observations = iter(("inactive", "metadata", "ready"))
        def starting_snapshot():
            current = snapshot()
            if "start" not in self.controller.events:
                return current
            observation = next(observations)
            if observation == "inactive":
                return replace(current, active=False, pid=0)
            if observation == "metadata":
                raise upgrade.UpgradeError("service_metadata")
            return current
        self.controller.snapshot = starting_snapshot
        installer.sleep = mock.Mock()
        report = installer.apply(initialize=True)
        self.assertEqual(report["status"], "installed")
        self.assertEqual(installer.sleep.call_args_list, [mock.call(0.1), mock.call(0.1)])
        self.assertTrue(self.controller.active)

    def test_restart_persistent_bad_arguments_deadline_stops_and_retains_marker(self):
        installer = self.upgrader()
        self.controller.on_start = lambda: setattr(self.controller, "argv", ("/usr/lib/systemd/systemd",))
        elapsed = 0.0
        def advance(seconds):
            nonlocal elapsed
            elapsed += seconds
        installer.monotonic = lambda: elapsed
        installer.sleep = mock.Mock(side_effect=advance)
        report = installer.apply(initialize=True)
        self.assertEqual(report["code"], "compatible_runtime_required")
        self.assertEqual(report["cause"], "service_arguments")
        self.assertTrue(report["unit_stopped"])
        self.assertTrue(report["budget_marker_retained"])
        self.assertFalse(self.controller.active)
        self.assertEqual(self.controller.events, ["stop", "reload", "start", "stop"])
        self.assertEqual(elapsed, 4)
        self.assertTrue(all(0 < call.args[0] <= 0.1 for call in installer.sleep.call_args_list))
        self.assertEqual(self.initializer_calls, 1)
        self.assertTrue(self.marker.exists())
        self.assertEqual(self.ledger.read_bytes(), self.original_parent)
        for name, raw in self.target.items():
            self.assertEqual((self.runtime / name).read_bytes(), raw)
        self.assert_report_private(report)

    def test_restart_nontransient_runtime_drift_fails_without_waiting(self):
        installer = self.upgrader()
        self.controller.on_start = lambda: self.entrypoint.write_text("# unexpected runtime drift\n")
        installer.sleep = mock.Mock()
        report = installer.apply(initialize=True)
        self.assertEqual(report["cause"], "runtime_drift")
        installer.sleep.assert_not_called()
        self.assertFalse(self.controller.active)
        self.assertTrue(self.marker.exists())

    def test_restart_valid_but_changed_arguments_fail_strict_comparison_without_waiting(self):
        installer = self.upgrader()
        self.controller.on_start = lambda: setattr(self.controller, "argv", self.controller.argv + ("--unexpected",))
        installer.sleep = mock.Mock()
        report = installer.apply(initialize=True)
        self.assertEqual(report["cause"], "service_changed")
        installer.sleep.assert_not_called()
        self.assertFalse(self.controller.active)
        self.assertTrue(self.marker.exists())

    def test_restart_environment_drift_stops_unit(self):
        self.controller.on_start = lambda: setattr(self.controller, "environment", self.controller.environment + (("UNEXPECTED", "value"),))
        report = self.upgrader().apply(initialize=True)
        self.assertEqual(report["cause"], "environment_changed")
        self.assertFalse(self.controller.active)

    def test_restart_secret_metadata_drift_stops_unit_without_reading_secret(self):
        self.controller.on_start = lambda: self.secret.chmod(0o640)
        report = self.upgrader().apply(initialize=True)
        self.assertEqual(report["cause"], "secret_metadata")
        self.assertFalse(self.controller.active)

    def test_preflight_rejects_unexpected_versions_and_new_runtime_files(self):
        (self.runtime / "grocery_ai.py").write_text("# newer installed module\n")
        with self.assertRaisesRegex(upgrade.UpgradeError, "unexpected_runtime"):
            self.upgrader().preflight()
        self.assertEqual(self.controller.events, [])

    def test_preflight_rejects_baseline_drift(self):
        self.entrypoint.write_text("# drift\n")
        with self.assertRaisesRegex(upgrade.UpgradeError, "runtime_drift"):
            self.upgrader().preflight()

    def test_preflight_rejects_tampered_package(self):
        (self.package / "grocery_ai.py").write_text("# tampered\n")
        with self.assertRaisesRegex(upgrade.UpgradeError, "package_invalid"):
            self.upgrader().preflight()

    def test_preflight_rejects_key_metadata_without_reading_key(self):
        self.key.chmod(0o644)
        with self.assertRaisesRegex(upgrade.UpgradeError, "key_metadata"):
            self.upgrader().preflight()

    def test_preflight_rejects_missing_key_without_creating_files(self):
        self.key.unlink()
        before = tree_snapshot(self.case)
        with self.assertRaises(upgrade.UpgradeError):
            self.upgrader().preflight()
        self.assertEqual(tree_snapshot(self.case), before)

    def test_preflight_rejects_old_unknown_grocery_dropin(self):
        self.unit_file.write_text("[Service]\nEnvironment=GROCERY_AI_BUDGET=9999\n")
        with self.assertRaisesRegex(upgrade.UpgradeError, "grocery_dropin"):
            self.upgrader().preflight()

    def test_preflight_rejects_unloaded_dropin_before_daemon_reload(self):
        self.paths.dropin.parent.mkdir()
        (self.paths.dropin.parent / "unknown.conf").write_text("[Service]\nEnvironment=UNEXPECTED=1\n")
        with self.assertRaisesRegex(upgrade.UpgradeError, "unit_configuration_not_loaded"):
            self.upgrader().preflight()
        self.assertEqual(self.controller.events, [])

    def test_preflight_rejects_unloaded_unit_configuration(self):
        original = self.controller.snapshot
        self.controller.snapshot = lambda: replace(original(), needs_reload=True)
        with self.assertRaisesRegex(upgrade.UpgradeError, "unit_configuration_not_loaded"):
            self.upgrader().preflight()

    def test_preflight_rejects_unknown_journal_schema(self):
        self.journal_create()
        with sqlite3.connect(self.journal) as conn:
            conn.execute("ALTER TABLE grocery_batches ADD COLUMN unknown_future_state TEXT")
        with self.assertRaisesRegex(upgrade.UpgradeError, "journal_incompatible"):
            self.upgrader().preflight()

    def test_preflight_rejects_grocery_rate_or_budget_environment(self):
        self.controller.environment += (("GROCERY_AI_MONTHLY_BUDGET", "9999"),)
        with self.assertRaisesRegex(upgrade.UpgradeError, "grocery_environment"):
            self.upgrader().preflight()

    def test_preflight_rejects_other_write_domain(self):
        self.controller.argv = tuple("finance.expense" if value == upgrade.DOMAIN else value for value in self.controller.argv)
        with self.assertRaisesRegex(upgrade.UpgradeError, "service_arguments"):
            self.upgrader().preflight()

    def test_preflight_rejects_parent_processing_or_pending(self):
        for status in ("processing", "grocery_pending"):
            with self.subTest(status=status):
                with sqlite3.connect(self.ledger) as conn:
                    conn.execute("UPDATE mobile_capture_ledger SET status=?", (status,))
                with self.assertRaisesRegex(upgrade.UpgradeError, "pending_state"):
                    self.upgrader().preflight()
        self.assertEqual(self.controller.events, [])

    def test_preflight_rejects_inflight_uncertain_operation(self):
        self.journal_create(operation_status="uncertain")
        with self.assertRaisesRegex(upgrade.UpgradeError, "pending_state"):
            self.upgrader().preflight()

    def test_preflight_rejects_unbudgeted_v2_nonpreflight(self):
        self.journal_create(version=2)
        with self.assertRaisesRegex(upgrade.UpgradeError, "unaccounted_ai_history"):
            self.upgrader().preflight()

    def test_preflight_accepts_undispatched_v2_preflight(self):
        self.journal_create(version=2, status="preflight", operation_status=None)
        self.assertEqual(self.upgrader().preflight()["status"], "preflight_ok")

    def test_preflight_rejects_orphan_budget_marker(self):
        self.marker.write_text("marker")
        with self.assertRaisesRegex(upgrade.UpgradeError, "budget_state_invalid"):
            self.upgrader().preflight()

    def test_sqlite_drain_busy_never_stops_service(self):
        installer = self.upgrader()
        installer._inspect = mock.Mock(return_value=(installer._inspect()[0], {}))
        conn = sqlite3.connect(self.ledger)
        self.addCleanup(conn.close)
        conn.execute("BEGIN IMMEDIATE")
        with self.assertRaisesRegex(upgrade.UpgradeError, "drain_busy"):
            installer.apply(initialize=True)
        conn.rollback()
        self.assertEqual(self.controller.events, [])

    def test_deployment_lock_busy_never_stops_service(self):
        descriptor = os.open(self.paths.lock_file, os.O_CREAT | os.O_RDWR, 0o600)
        self.addCleanup(os.close, descriptor)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with self.assertRaisesRegex(upgrade.UpgradeError, "deployment_busy"):
            self.upgrader().apply(initialize=True)
        self.assertEqual(self.controller.events, [])

    def test_symlink_key_rejected(self):
        actual = self.case / "actual-key"
        self.key.rename(actual)
        self.key.symlink_to(actual)
        with self.assertRaises(upgrade.UpgradeError):
            self.upgrader().preflight()

    def test_preflight_rejects_symlink_backup_parent_before_creating_anything(self):
        actual = self.case / "actual-backups-parent"
        actual.mkdir()
        alias = self.case / "aliased-backups-parent"
        alias.symlink_to(actual, target_is_directory=True)
        self.paths = replace(self.paths, backup_root=alias / "new-backups")
        before = tree_snapshot(self.case)
        with self.assertRaisesRegex(upgrade.UpgradeError, "invalid_path"):
            self.upgrader().preflight()
        self.assertEqual(tree_snapshot(self.case), before)
        self.assertFalse((actual / "new-backups").exists())

    def test_entrypoint_and_unit_owner_write_permissions_required(self):
        self.entrypoint.chmod(0o666)
        with self.assertRaisesRegex(upgrade.UpgradeError, "unsafe_metadata"):
            self.upgrader().preflight()

    def test_systemd_exec_fingerprint_ignores_invocation_results(self):
        before = "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 /root/server.py ; ignore_errors=no ; start_time=old ; stop_time=old ; pid=111 ; code=exited ; status=0 }"
        after = "{ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 /root/server.py ; ignore_errors=no ; start_time=new ; stop_time= ; pid=222 ; code=(null) ; status=0 }"
        self.assertEqual(upgrade.execution_fingerprint(before), upgrade.execution_fingerprint(after))
        self.assertNotEqual(upgrade.execution_fingerprint(before), upgrade.execution_fingerprint(after.replace("server.py", "other.py")))

    def test_controller_commands_only_stop_and_start_exact_unit(self):
        controller = upgrade.SystemdController()
        with mock.patch("subprocess.run", return_value=mock.Mock(stdout="")) as run:
            controller.stop()
            controller.start()
            controller.reload()
        self.assertEqual([call.args[0] for call in run.call_args_list], [
            ["systemctl", "stop", upgrade.UNIT], ["systemctl", "start", upgrade.UNIT], ["systemctl", "daemon-reload"]])

    def test_https_health_strict_url(self):
        for url in ("http://example.test/health", "https://name:secret@example.test/health", "https://example.test/other", "https://example.test/health?token=private"):
            with self.subTest(url=url), self.assertRaisesRegex(upgrade.UpgradeError, "health_url_invalid"):
                upgrade.HTTPSHealth(url)

    def test_real_initializer_only_uses_reviewed_staging_no_environment_or_key(self):
        # Inspect the child contract with subprocess mocked, never execute it.
        with mock.patch("subprocess.run") as run:
            upgrade.initialize_budget(self.package, self.journal)
        args, kwargs = run.call_args
        self.assertEqual(args[0][:3], ["/usr/bin/python3", "-I", "-B"])
        self.assertIn("grocery_capture.init_db(p);grocery_ai_budget.initialize_schema(p)", args[0][4])
        self.assertNotIn(str(self.key), json.dumps([args, kwargs]))
        self.assertNotIn("OPENAI", json.dumps(kwargs["env"]))


class ReviewedBudgetInitializationTests(unittest.TestCase):
    """The real initializer child imports reviewed code and touches only temp DBs."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name).resolve()
        self.package = self.case / "reviewed-stage"
        self.package.mkdir()
        folder = Path(__file__).resolve().parent
        manifest_raw = (folder / "release_manifest.json").read_bytes()
        self.assertEqual(digest(manifest_raw), upgrade.MANIFEST_SHA256)
        manifest = json.loads(manifest_raw)
        backend = folder.parents[2] / "backend"
        for name in upgrade.ARTIFACT_NAMES - {upgrade.SERVER}:
            raw = (backend / name).read_bytes()
            self.assertEqual(digest(raw), manifest["artifacts"][name])
            (self.package / name).write_bytes(raw)
        self.journal = self.case / "synthetic.sqlite3"
        self.marker = Path(str(self.journal) + ".ai-budget-initialized")

    def test_baseline_pins_are_exact_immutable_previous_four_file_manifest(self):
        prior = Path(__file__).resolve().parents[1] / "grocery-multi-item" / "release_manifest.json"
        manifest = json.loads(prior.read_bytes())
        self.assertEqual(upgrade.BASELINE_ARTIFACTS, manifest["artifacts"])
        self.assertEqual(upgrade.HELPER_SHA256, manifest["external_helper_sha256"])

    def test_real_initializer_creates_additive_budget_and_repeated_init_keeps_identity(self):
        upgrade.initialize_budget(self.package, self.journal)
        self.assertTrue(self.marker.exists())
        original_marker = self.marker.read_bytes()
        original_inode = self.journal.stat().st_ino
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("PRAGMA quick_check").fetchall(), [("ok",)])
            self.assertEqual(conn.execute("SELECT request_count FROM grocery_ai_budget_state").fetchone(), (0,))
            original_budget_id = conn.execute("SELECT ledger_id FROM grocery_ai_budget_state").fetchone()[0]
        upgrade.initialize_budget(self.package, self.journal)
        self.assertEqual(self.marker.read_bytes(), original_marker)
        self.assertEqual(self.journal.stat().st_ino, original_inode)
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT ledger_id,request_count FROM grocery_ai_budget_state").fetchone(), (original_budget_id, 0))

    def test_real_initializer_refuses_unbudgeted_v2_history_without_reset(self):
        with sqlite3.connect(self.journal) as conn:
            conn.execute("CREATE TABLE grocery_batches(request_id TEXT PRIMARY KEY, payload_hash TEXT, plan_json TEXT, plan_hash TEXT, response_json TEXT, status TEXT, created_at TEXT, updated_at TEXT)")
            conn.execute("INSERT INTO grocery_batches(request_id,plan_json,status) VALUES(?,?,?)",
                ("synthetic-private-id", '{"version":2,"phase":"interpreted"}', "no_write"))
        with self.assertRaisesRegex(upgrade.UpgradeError, "budget_initialization_failed"):
            upgrade.initialize_budget(self.package, self.journal)
        with sqlite3.connect(self.journal) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM grocery_batches").fetchone(), (1,))
            self.assertNotIn("grocery_ai_budget_state", {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")})
        self.assertFalse(self.marker.exists())


if __name__ == "__main__":
    unittest.main()
