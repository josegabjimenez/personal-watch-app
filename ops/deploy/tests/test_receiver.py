"""Offline sealed-package activation, batch-state and recovery boundary tests."""

import base64
import contextlib
import copy
import fcntl
import importlib.util
import io
import json
import os
from pathlib import Path
import py_compile
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from sealed_fixture import FILENAMES, HELPER, LEGACY, canonical, digest, fixture

MODULE_PATH = Path(__file__).resolve().parents[1] / "receiver.py"


def load_receiver():
    spec = importlib.util.spec_from_file_location("watch_receiver_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.module = load_receiver()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.artifacts, self.manifest, self.raw_manifest, self.profile, self.bundle = fixture()
        self.module.PACKAGE_REVISION = self.profile["package_revision"]
        self.module.MANIFEST_SHA256 = self.profile["manifest_sha256"]
        self.module.EXTERNAL_HELPER_SHA256 = digest(HELPER)
        self.live_dir = self.root / "original-code"
        self.live_dir.mkdir()
        self.entry = self.live_dir / FILENAMES[0]
        self.entry.write_bytes(LEGACY)
        self.entry.chmod(0o640)
        self.helper = self.root / "external" / "apple_watch_capture.py"
        self.helper.parent.mkdir()
        self.helper.write_bytes(HELPER)
        self.secret = self.root / "secret.txt"
        self.secret.write_bytes(b"synthetic-private-credential-never-package")
        self.data = self.root / "data"
        self.data.mkdir()
        self.ledger = self.data / "parent.sqlite"
        self.batch_journal = Path(str(self.ledger) + ".grocery.sqlite3")
        for path, table, status in ((self.ledger, "parent_claims", "uncertain"),):
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE %s (request_id TEXT,status TEXT)" % table)
                db.execute("INSERT INTO %s VALUES (?,?)" % table, ("synthetic-id", status))
        self.state = self.root / "release-state"
        self.code_dir = self.root / "package-code"
        self.config = {
            "service": "hermes-mobile-capture.service", "entrypoint": str(self.entry),
            "code_dir": str(self.code_dir), "state_dir": str(self.state), "keep": 5,
            "expected_cmdline": ["/usr/bin/python3", str(self.entry), "--host", "127.0.0.1", "--port", "8650", "--secret-file", str(self.secret), "--ledger", str(self.ledger), "--aura-helper", str(self.helper)],
            "health_urls": ["http://127.0.0.1:8650/health", "https://example.invalid:8650/health"],
            "expected_health": {"status": "ok", "service": "mobile-capture-v1-staging", "mode": "allowlisted-write", "write_domains": ["aura.grocery_capture"]},
            "package_profile": self.profile,
        }
        module = self.module
        entry = self.entry

        class SyntheticReceiver(module.Receiver):
            def __init__(self, config):
                self.events = []
                self.start_results = []
                self.active = True
                self.instance = 1
                self.fail_candidate_start = False
                self.reject_candidate_health_once = False
                super().__init__(config)

            def service(self, action):
                self.events.append(action)
                if action == "stop":
                    self.active = False
                elif action in ("start", "restart"):
                    if self.fail_candidate_start and entry.is_symlink():
                        raise module.ReleaseError("synthetic_start_failed")
                    output = subprocess.run([sys.executable, str(entry)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5, env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"}, text=True)
                    if output.returncode:
                        raise module.ReleaseError("synthetic_import_failed")
                    self.start_results.append(output.stdout.strip())
                    self.active = True
                    self.instance += 1
                else:
                    raise AssertionError("Unexpected service action")

            def running_identity(self):
                if not self.active:
                    raise module.ReleaseError("synthetic_inactive")
                return "synthetic-instance-%s" % self.instance

            def check_health(self):
                if self.reject_candidate_health_once and entry.is_symlink():
                    self.reject_candidate_health_once = False
                    raise module.ReleaseError("synthetic_health_failed")

        self.receiver = SyntheticReceiver(self.config)

    def make_batch_journal(self):
        with sqlite3.connect(self.batch_journal) as db:
            db.execute("CREATE TABLE IF NOT EXISTS batch_claims (request_id TEXT,status TEXT)")
            db.execute("INSERT INTO batch_claims VALUES (?,?)", ("synthetic-batch-id", "processing"))

    def compatible_baseline(self):
        self.make_batch_journal()
        self.entry.write_bytes(self.artifacts[FILENAMES[0]])
        for name in FILENAMES[1:]:
            (self.live_dir / name).write_bytes(self.artifacts[name])

    def current(self):
        return self.receiver._fingerprint()

    def deploy(self, expected=None):
        return self.receiver.handle({"action": "deploy", "bundle": copy.deepcopy(self.bundle), "expected_current": expected or self.current()})

    def assert_rejected_without_stop(self, request):
        before = self.entry.read_bytes()
        self.receiver.events.clear()
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle(request)
        self.assertNotIn("stop", self.receiver.events)
        self.assertEqual(self.entry.read_bytes(), before)

    def data_snapshot(self):
        return (self.ledger.read_bytes(), self.batch_journal.read_bytes() if self.batch_journal.exists() else None, self.secret.read_bytes())

    def assert_data_unchanged(self, before):
        self.assertEqual(self.data_snapshot(), before)
        self.assertEqual(self.helper.read_bytes(), HELPER)
        for path in self.state.rglob("*"):
            if path.is_file():
                value = path.read_bytes()
                for private in before:
                    if private is not None:
                        self.assertNotIn(private, value)

    def current_pinned_runtime(self, count):
        """Read/compile public package sources; never import application code."""
        repo = MODULE_PATH.parents[2]

        def packager(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
            return module

        prior = packager("_negative_compat_grocery_builder", repo / "ops/hotfixes/grocery-multi-item/build_release.py")
        reminder = packager("_negative_compat_reminder_builder", repo / "ops/hotfixes/reminder-ai/build_release.py")
        historical = prior.baseline_source(repo)
        server = prior.overlay_server(historical, prior.OVERLAY.read_bytes())
        baseline = reminder.baseline_manifest()
        runtime = {reminder.SERVER: server}
        runtime.update({name: (repo / "backend" / name).read_bytes() for name in reminder.GROCERY_MODULES})
        self.assertEqual({name: digest(source) for name, source in runtime.items()}, baseline["artifacts"])
        if count == 12:
            runtime[reminder.SERVER] = reminder.overlay_server(server)
            runtime.update({name: (repo / "backend" / name).read_bytes() for name in reminder.REMINDER_MODULES})
            expected = reminder.load_manifest()
        else:
            self.assertEqual(count, 6)
            expected = baseline
        self.assertEqual(len(runtime), count)
        self.assertEqual({name: digest(source) for name, source in runtime.items()}, expected["artifacts"])
        return runtime

    def assert_current_runtime_rejected_without_mutation(self, runtime, expected_code):
        # Use the production four-file pins, not setUp's synthetic profile.
        module = load_receiver()
        config = copy.deepcopy(self.config)
        config["package_profile"] = json.loads((MODULE_PATH.parent / "grocery-package.json").read_bytes())
        receiver = module.Receiver(config)
        for name, source in runtime.items():
            (self.live_dir / name).write_bytes(source)
        self.make_batch_journal()
        with sqlite3.connect(self.batch_journal) as db:
            db.execute("CREATE TABLE grocery_ai_budget_requests (request_id TEXT,status TEXT,reserved_nanodollars INTEGER)")
            db.execute("INSERT INTO grocery_ai_budget_requests VALUES ('synthetic-grocery','reserved',318000000)")
        reminder_budget = self.data / "reminder-ai-budget.sqlite3"
        with sqlite3.connect(reminder_budget) as db:
            db.execute("CREATE TABLE reservations (request_id TEXT,status TEXT)")
            db.execute("INSERT INTO reservations VALUES ('synthetic-reminder','uncertain')")
        for path in (Path(str(self.batch_journal) + ".ai-budget-initialized"),
                     Path(str(reminder_budget) + ".reminder-budget-initialized")):
            path.write_bytes(b"synthetic-persistent-budget-witness\n")
        for database in (self.ledger, self.batch_journal, reminder_budget):
            for suffix in ("-wal", "-shm"):
                Path(str(database) + suffix).write_bytes(b"synthetic-private-paired-state\n")
        (self.root / "service.conf").write_bytes(b"[Service]\nEnvironment=GROCERY_AI_ENABLED=1\n")
        (self.root / "receiver-config.json").write_text(json.dumps(config, sort_keys=True))

        def snapshot():
            result = {}
            for path in self.root.rglob("*"):
                if path == self.state / ".lock":
                    continue  # Receiver bookkeeping may create its own lock.
                info = path.lstat()
                content = path.read_bytes() if stat.S_ISREG(info.st_mode) else os.readlink(path) if stat.S_ISLNK(info.st_mode) else None
                result[str(path.relative_to(self.root))] = (info.st_ino, info.st_uid, info.st_gid, info.st_mode, content)
            return result

        before = snapshot()
        config_before = copy.deepcopy(config)
        expanded = {"schema": 2, "revision": config["package_profile"]["package_revision"],
                    "manifest_b64": receiver.manifest_b64,
                    "files": {name: {"sha256": digest(source), "content_b64": base64.b64encode(source).decode()}
                              for name, source in runtime.items()}}
        requests = (
            ({"action": "status"}, expected_code),
            ({"action": "rollback", "release_id": "0" * 64, "expected_current": "0" * 64}, expected_code),
            ({"action": "deploy", "bundle": expanded, "expected_current": "0" * 64}, "invalid_bundle"),
        )
        with contextlib.ExitStack() as stack:
            spies = [stack.enter_context(mock.patch.object(receiver, name, side_effect=AssertionError("unexpected receiver side effect")))
                     for name in ("service", "_install", "_stage", "_snapshot", "_write", "_inhibit", "_switch",
                                  "running_identity", "check_health", "_external_helper")]
            for request, code in requests:
                with self.subTest(action=request["action"]):
                    with self.assertRaisesRegex(module.ReleaseError, "^" + code + "$"):
                        receiver.handle(request)
                    for spy in spies:
                        spy.assert_not_called()
                    self.assertEqual(snapshot(), before)
                    self.assertEqual(config, config_before)
                    self.assertFalse(self.entry.is_symlink())

    def test_accepted_six_file_runtime_is_rejected_before_mutation(self):
        self.assert_current_runtime_rejected_without_mutation(self.current_pinned_runtime(6), "invalid_bundle")

    def test_twelve_file_reminder_runtime_is_rejected_before_mutation(self):
        self.assert_current_runtime_rejected_without_mutation(self.current_pinned_runtime(12), "unrecognized_live_code")

    def test_exact_four_file_package_is_staged_and_entry_switch_is_atomic(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        result = self.deploy()
        package_id = digest(canonical(self.bundle))
        self.assertEqual(result["release_id"], package_id)
        self.assertTrue(self.entry.is_symlink())
        self.assertEqual(self.entry.resolve(), self.code_dir / package_id / FILENAMES[0])
        self.assertEqual(set(p.name for p in self.entry.resolve().parent.iterdir()), set(FILENAMES) | {"manifest.json"})
        self.assertEqual((self.entry.resolve().parent / "manifest.json").read_bytes(), self.raw_manifest)
        for name in FILENAMES:
            path = self.entry.resolve().parent / name
            self.assertEqual(path.read_bytes(), self.artifacts[name])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual(self.receiver.start_results, ['{"package": "synthetic-sealed-batch"}'])
        self.assertEqual(self.receiver.events, ["stop", "start"])
        self.assert_data_unchanged(before)
        self.assertTrue((self.state / "compatibility.json").exists())
        self.assertFalse((self.state / "transaction.json").exists())

    def test_wire_rejects_missing_extra_path_changed_or_unsealed_artifacts(self):
        candidates = []
        for mutation in (
            lambda b: b["files"].pop(FILENAMES[-1]),
            lambda b: b["files"].update({"extra.py": b["files"][FILENAMES[0]]}),
            lambda b: b["files"].update({"../grocery_capture.py": b["files"].pop("grocery_capture.py")}),
            lambda b: b["files"][FILENAMES[0]].update({"content_b64": base64.b64encode(b"VALUE='changed'\n").decode(), "sha256": digest(b"VALUE='changed'\n")}),
            lambda b: b.update({"revision": "2" * 40}),
            lambda b: b.update({"manifest_b64": base64.b64encode(self.raw_manifest + b"\n").decode()}),
            lambda b: b.update({"schema": 1}),
            lambda b: b["files"][FILENAMES[0]].update({"sha256": "0" * 64}),
            lambda b: b["files"][FILENAMES[0]].update({"content_b64": "%%invalid%%"}),
            lambda b: b["files"][FILENAMES[0]].update({"path": str(self.secret)}),
        ):
            candidate = copy.deepcopy(self.bundle)
            mutation(candidate)
            candidates.append(candidate)
        for candidate in candidates:
            with self.subTest(candidate=candidate):
                self.assert_rejected_without_stop({"action": "deploy", "bundle": candidate, "expected_current": self.current()})

    def test_stale_current_and_generic_schema1_are_rejected(self):
        self.assert_rejected_without_stop({"action": "deploy", "bundle": self.bundle, "expected_current": "0" * 64})
        generic = {"schema": 1, "revision": self.profile["package_revision"], "files": {"bff": self.bundle["files"][FILENAMES[0]]}}
        self.assert_rejected_without_stop({"action": "deploy", "bundle": generic, "expected_current": self.current()})

    def test_failed_health_restores_a_compatible_four_file_predecessor(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        self.receiver.reject_candidate_health_once = True
        with self.assertRaises(self.module.ReleaseError):
            self.deploy()
        self.assertFalse(self.entry.is_symlink())
        self.assertTrue(self.receiver.active)
        self.assertEqual(self.entry.read_bytes(), self.artifacts[FILENAMES[0]])
        self.assertEqual(stat.S_IMODE(self.entry.stat().st_mode), 0o640)
        self.assertEqual(self.receiver.start_results, ['{"package": "synthetic-sealed-batch"}', '{"package": "synthetic-sealed-batch"}'])
        self.assert_data_unchanged(before)

    def test_first_activation_failure_never_restarts_single_item_legacy(self):
        before = self.data_snapshot()
        self.receiver.reject_candidate_health_once = True
        with self.assertRaises(self.module.ReleaseError):
            self.deploy()
        self.assertFalse(self.receiver.active)
        self.assertNotIn("synthetic-legacy-single-item", self.receiver.start_results)
        self.assertTrue((self.state / "compatibility.json").exists())
        self.assertTrue((self.state / "transaction.json").exists())
        for request in ({"action": "recover"}, {"action": "deploy", "bundle": self.bundle, "expected_current": self.current()}):
            with self.subTest(action=request["action"]):
                with self.assertRaises(self.module.ReleaseError):
                    self.receiver.handle(request)
                self.assertFalse(self.receiver.active)
        baseline = json.loads((self.state / "state.json").read_text())["baseline"] if (self.state / "state.json").exists() else json.loads((self.state / "transaction.json").read_text())["before"]
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "rollback", "release_id": baseline, "expected_current": self.current()})
        self.assertFalse(self.receiver.active)
        self.assert_data_unchanged(before)

    def test_failed_start_with_legacy_predecessor_retains_safe_stop_and_journal(self):
        self.receiver.fail_candidate_start = True
        with self.assertRaises(self.module.ReleaseError):
            self.deploy()
        self.assertFalse(self.receiver.active)
        self.assertTrue((self.state / "transaction.json").exists())
        self.assertTrue((self.state / "compatibility.json").exists())
        self.assertEqual(self.receiver.start_results, [])

    def test_repeated_stop_failure_inhibits_boot_without_claiming_process_stopped(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        service = self.receiver.service
        stops = []
        def failing_recovery_stop(action):
            if action == "stop":
                stops.append(action)
                if len(stops) > 1:
                    self.receiver.events.append(action)
                    raise self.module.ReleaseError("synthetic_stop_failed")
            service(action)
        self.receiver.service = failing_recovery_stop
        self.receiver.reject_candidate_health_once = True
        with self.assertRaisesRegex(self.module.ReleaseError, "^stop_failed_recovery_required$"):
            self.deploy()
        self.assertTrue(self.receiver.active, "A failed stop must not claim the existing process stopped")
        self.assertTrue(self.entry.is_symlink())
        self.assertFalse(self.entry.exists())
        self.assertTrue((self.state / "compatibility.json").exists())
        self.assertTrue((self.state / "transaction.json").exists())
        self.assertEqual(self.receiver.start_results, ['{"package": "synthetic-sealed-batch"}'])
        with self.assertRaisesRegex(self.module.ReleaseError, "^stop_failed_recovery_required$"):
            self.receiver.handle({"action": "recover"})
        self.assertTrue(self.receiver.active)
        self.assertFalse(self.entry.exists())
        self.assertTrue((self.state / "transaction.json").exists())
        self.assertNotIn("synthetic-legacy-single-item", self.receiver.start_results)
        self.assert_data_unchanged(before)

    def test_helper_drift_during_health_cannot_be_reported_as_release_success(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        changed = b"HELPER = 'external-edit-during-health'\n"
        def changing_health():
            if self.entry.is_symlink() and self.entry.exists():
                self.helper.write_bytes(changed)
        self.receiver.check_health = changing_health
        with self.assertRaises(self.module.ReleaseError):
            self.deploy()
        self.assertFalse(self.receiver.active)
        self.assertTrue((self.state / "transaction.json").exists())
        self.assertEqual(self.helper.read_bytes(), changed, "External helper drift must not be silently reverted")
        self.assertEqual(self.data_snapshot(), before)

    def interrupted_staging(self):
        release_id = self.receiver._snapshot({"kind": "package", "bundle": self.bundle})
        staging = self.code_dir / (".staging-" + release_id)
        staging.mkdir(mode=0o700)
        return staging

    def test_interrupted_staging_cleanup_accepts_only_sealed_content_prefixes(self):
        staging = self.interrupted_staging()
        (staging / FILENAMES[0]).write_bytes(self.artifacts[FILENAMES[0]][:10])
        (staging / FILENAMES[1]).write_bytes(b"")
        (staging / "manifest.json").write_bytes(self.raw_manifest[:12])
        self.receiver._clear_staging(staging)
        self.assertFalse(staging.exists())

    def test_interrupted_staging_tampered_or_unknown_files_are_preserved(self):
        staging = self.interrupted_staging()
        prefix = staging / FILENAMES[0]
        prefix_bytes = self.artifacts[FILENAMES[0]][:10]
        prefix.write_bytes(prefix_bytes)
        tampered = staging / FILENAMES[1]
        tampered.write_bytes(b"not-a-prefix-of-the-sealed-source")
        with self.assertRaises(self.module.ReleaseError):
            self.receiver._clear_staging(staging)
        self.assertEqual(prefix.read_bytes(), prefix_bytes)
        self.assertEqual(tampered.read_bytes(), b"not-a-prefix-of-the-sealed-source")
        tampered.unlink()
        unknown = staging / "operator-backup.txt"
        unknown.write_bytes(b"keep operator bytes")
        with self.assertRaises(self.module.ReleaseError):
            self.receiver._clear_staging(staging)
        self.assertEqual(prefix.read_bytes(), prefix_bytes)
        self.assertEqual(unknown.read_bytes(), b"keep operator bytes")

    def test_legacy_runtime_with_existing_batch_state_fails_before_mutation(self):
        self.make_batch_journal()
        before = self.data_snapshot()
        self.assert_rejected_without_stop({"action": "deploy", "bundle": self.bundle, "expected_current": self.current()})
        self.assertFalse(self.entry.is_symlink())
        self.assertEqual(self.entry.read_bytes(), LEGACY)
        self.assert_data_unchanged(before)

    def run_interrupted(self, signum, before_switch=False, after_inhibitor=False):
        script = r'''
import importlib.util,json,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver_child',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
config=json.loads(sys.argv[2]);request=json.loads(sys.argv[3]);entry=Path(config['entrypoint'])
m.PACKAGE_REVISION=config['package_profile']['package_revision'];m.MANIFEST_SHA256=config['package_profile']['manifest_sha256'];m.EXTERNAL_HELPER_SHA256=config['package_profile']['sealed_manifest']['external_helper_sha256']
class Synthetic(m.Receiver):
    counter=0
    done=False
    def service(self,action):
        self.counter+=1
        if action=='start' and entry.is_symlink() and not self.done and sys.argv[5]=='0':
            self.done=True;os.kill(os.getpid(),int(sys.argv[4]))
    def _switch(self,snapshot):
        if sys.argv[5]=='1' and not self.done:
            self.done=True;os.kill(os.getpid(),int(sys.argv[4]))
        return super()._switch(snapshot)
    def _inhibit(self):
        result=super()._inhibit()
        if sys.argv[5]=='2' and not self.done:
            self.done=True;os.kill(os.getpid(),int(sys.argv[4]))
        return result
    def running_identity(self): return str(self.counter)
    def check_health(self): pass
def interrupted(signum,frame):raise m.ReleaseError('interrupted')
signal.signal(signal.SIGTERM,interrupted)
try:Synthetic(config).handle(request)
except m.ReleaseError as exc:print(str(exc));sys.exit(1)
'''
        request = {"action": "deploy", "bundle": self.bundle, "expected_current": self.current()}
        phase = "2" if after_inhibitor else "1" if before_switch else "0"
        return subprocess.run([sys.executable, "-c", script, str(MODULE_PATH), json.dumps(self.config), json.dumps(request), str(int(signum)), phase], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, text=True)

    def test_sigterm_after_switch_recovers_compatible_runtime_and_data(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        result = self.run_interrupted(signal.SIGTERM)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertFalse(self.entry.is_symlink())
        self.assertFalse((self.state / "transaction.json").exists())
        self.assert_data_unchanged(before)

    def test_sigkill_after_switch_recovers_compatible_runtime_and_data(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        result = self.run_interrupted(signal.SIGKILL)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue(self.entry.is_symlink())
        self.assertTrue((self.state / "transaction.json").exists())
        self.receiver.handle({"action": "recover"})
        self.assertFalse(self.entry.is_symlink())
        self.assertTrue(self.receiver.active)
        self.assert_data_unchanged(before)

    def test_sigkill_before_final_link_inhibits_execution_and_recovers_compatible_code(self):
        self.compatible_baseline()
        result = self.run_interrupted(signal.SIGKILL, before_switch=True)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue(self.entry.is_symlink())
        self.assertFalse(self.entry.exists())
        self.assertTrue((self.state / "transaction.json").exists())
        output = subprocess.run([sys.executable, str(self.entry)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertNotEqual(output.returncode, 0)
        self.assertEqual(output.stdout, b"")
        self.receiver.handle({"action": "recover"})
        self.assertEqual(self.receiver.start_results, ['{"package": "synthetic-sealed-batch"}'])

    def test_sigkill_after_inhibitor_before_floor_cannot_restart_legacy(self):
        result = self.run_interrupted(signal.SIGKILL, after_inhibitor=True)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue(self.entry.is_symlink())
        self.assertFalse(self.entry.exists())
        self.assertFalse((self.state / "compatibility.json").exists())
        self.assertTrue((self.state / "transaction.json").exists())
        output = subprocess.run([sys.executable, str(self.entry)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
        self.assertNotEqual(output.returncode, 0)
        self.assertEqual(output.stdout, b"")
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "recover"})
        self.assertFalse(self.receiver.active)
        self.assertTrue((self.state / "compatibility.json").exists())
        self.assertTrue((self.state / "transaction.json").exists())
        self.assertNotIn("synthetic-legacy-single-item", self.receiver.start_results)

    def test_sigkill_after_inhibitor_recovers_only_compatible_predecessor(self):
        self.compatible_baseline()
        before = self.data_snapshot()
        result = self.run_interrupted(signal.SIGKILL, after_inhibitor=True)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue(self.entry.is_symlink())
        self.assertFalse(self.entry.exists())
        self.receiver.handle({"action": "recover"})
        self.assertFalse(self.entry.is_symlink())
        self.assertTrue(self.receiver.active)
        self.assertEqual(self.receiver.start_results, ['{"package": "synthetic-sealed-batch"}'])
        self.assert_data_unchanged(before)

    def test_sigkill_with_legacy_before_blocks_unsafe_recovery(self):
        result = self.run_interrupted(signal.SIGKILL)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "recover"})
        self.assertFalse(self.receiver.active)
        self.assertTrue((self.state / "transaction.json").exists())

    def test_compatible_rollback_and_retention_preserve_operator_backups(self):
        self.compatible_baseline()
        operator = self.code_dir / "operator-backup"
        operator.mkdir()
        (operator / "keep.txt").write_text("operator retained")
        self.deploy()
        receipt = json.loads((self.state / "state.json").read_text())
        for key in ("active", "previous", "baseline"):
            self.assertTrue((self.state / "releases" / (receipt[key] + ".json")).exists())
        self.receiver.handle({"action": "rollback", "release_id": receipt["previous"], "expected_current": self.current()})
        self.assertFalse(self.entry.is_symlink())
        self.assertEqual((operator / "keep.txt").read_text(), "operator retained")

    def test_pending_crash_retains_both_snapshots_and_complete_package_stage(self):
        self.compatible_baseline()
        result = self.run_interrupted(signal.SIGKILL, before_switch=True)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        journal = json.loads((self.state / "transaction.json").read_text())
        for key in ("before", "after"):
            self.assertTrue((self.state / "releases" / (journal[key] + ".json")).exists())
        stage = self.code_dir / digest(canonical(self.bundle))
        self.assertEqual(set(path.name for path in stage.iterdir()), set(FILENAMES) | {"manifest.json"})
        with self.assertRaises(self.module.ReleaseError):
            self.deploy()
        self.assertEqual(set(path.name for path in stage.iterdir()), set(FILENAMES) | {"manifest.json"})
        self.assertTrue((self.state / "transaction.json").exists())

    def test_tampered_stage_and_unmanaged_symlink_are_not_adopted(self):
        self.deploy()
        parser = self.entry.resolve().parent / "grocery_item_parser.py"
        parser.write_bytes(b"VALUE='unreviewed-edit'\n")
        drifted = digest(canonical({name: digest((parser.parent / name).read_bytes()) for name in FILENAMES}))
        self.receiver.events.clear()
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "deploy", "bundle": self.bundle, "expected_current": drifted})
        self.assertEqual(self.receiver.events, [])

        self.assertEqual(parser.read_bytes(), b"VALUE='unreviewed-edit'\n")
        unrelated = self.root / "unmanaged.py"
        unrelated.write_bytes(LEGACY)
        self.entry.unlink()
        self.entry.symlink_to(unrelated)
        self.receiver.events.clear()
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "status"})
        self.assertEqual(self.receiver.events, [])

    def test_known_bytecode_cache_is_accepted_and_purged_before_package_restart(self):
        self.compatible_baseline()
        first = self.deploy()
        stage = self.entry.resolve().parent
        cache = stage / "__pycache__"
        cache.mkdir()
        for name in FILENAMES:
            py_compile.compile(str(stage / name), cfile=str(cache / (Path(name).stem + "." + sys.implementation.cache_tag + ".pyc")), doraise=True)
        self.assertTrue(cache.is_dir())
        self.receiver.handle({"action": "status"})
        self.receiver.handle({"action": "rollback", "release_id": first["previous"], "expected_current": self.current()})
        self.deploy()
        self.assertFalse(cache.exists())
        self.assertEqual(self.receiver.start_results[-1], '{"package": "synthetic-sealed-batch"}')

    def test_unknown_or_symlinked_bytecode_cache_fails_closed_without_deletion(self):
        self.deploy()
        cache = self.entry.resolve().parent / "__pycache__"
        cache.mkdir()
        unexpected = cache / "operator-notes.txt"
        unexpected.write_text("preserve unrelated operator bytes")
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "status"})
        self.assertEqual(unexpected.read_text(), "preserve unrelated operator bytes")
        unexpected.unlink()
        cache.rmdir()
        outside = self.root / "outside-cache"
        outside.mkdir()
        (outside / "keep.txt").write_text("outside untouched")
        cache.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle({"action": "status"})
        self.assertTrue(cache.is_symlink())
        self.assertEqual((outside / "keep.txt").read_text(), "outside untouched")

    def test_configuration_cannot_replace_the_pinned_package_seal(self):
        for mutate in (
            lambda p: p.update({"package_revision": "2" * 40}),
            lambda p: p.update({"manifest_sha256": "0" * 64}),
            lambda p: p["sealed_manifest"].update({"external_helper_sha256": "0" * 64}),
        ):
            config = copy.deepcopy(self.config)
            mutate(config["package_profile"])
            with self.subTest(profile=config["package_profile"]):
                with self.assertRaises(self.module.ReleaseError):
                    type(self.receiver)(config)

    def test_killed_health_parent_does_not_leave_a_probe_holding_release_lock(self):
        pidfile = self.root / "orphan-probe-pid"
        script = r'''
import fcntl,importlib.util,json,os,sys
spec=importlib.util.spec_from_file_location('receiver_child',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
config=json.loads(sys.argv[2]);m.PACKAGE_REVISION=config['package_profile']['package_revision'];m.MANIFEST_SHA256=config['package_profile']['manifest_sha256'];m.EXTERNAL_HELPER_SHA256=config['package_profile']['sealed_manifest']['external_helper_sha256']
receiver=m.Receiver(config)
lock=open(config['state_dir']+'/.lock','a+');fcntl.flock(lock,fcntl.LOCK_EX);os.set_inheritable(lock.fileno(),True)
probe="import os,signal,sys,time;signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(10);open(sys.argv[1],'w').write(str(os.getpid()));time.sleep(60)"
receiver._probe_command=lambda:[sys.executable,'-c',probe,sys.argv[3]]
receiver._probe_with_deadline(20)
'''
        parent = subprocess.Popen([sys.executable, "-c", script, str(MODULE_PATH), json.dumps(self.config), str(pidfile)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        probe_pid = None
        try:
            deadline = time.monotonic() + 3
            while not pidfile.exists() and time.monotonic() < deadline:
                if parent.poll() is not None:
                    self.fail("Synthetic health parent exited before probe started")
                time.sleep(0.01)
            self.assertTrue(pidfile.exists())
            probe_pid = int(pidfile.read_text())
            parent.kill()
            parent.wait(timeout=3)
            self.assertEqual(parent.returncode, -signal.SIGKILL)
            os.kill(probe_pid, 0)  # The blocked orphan still exists while its lock must be absent.
            self.receiver.handle({"action": "status"})
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=3)
            parent.stderr.close()
            if probe_pid is None and pidfile.exists():
                probe_pid = int(pidfile.read_text())
            if probe_pid is not None:
                try:
                    os.kill(probe_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_status_exposes_only_receipts_hashes_and_floor(self):
        result = self.receiver.handle({"action": "status"})
        encoded = json.dumps(result)
        self.assertIn(self.current(), encoded)
        self.assertNotIn(str(self.root), encoded)
        self.assertNotIn("synthetic-private-credential", encoded)
        self.assertEqual(self.receiver.events, [])

    def test_unknown_request_fields_and_actions_are_rejected(self):
        for request in ({"action": "shell", "command": "true"}, {"action": "status", "entrypoint": str(self.secret)}, {"action": "deploy", "bundle": self.bundle, "expected_current": self.current(), "path": str(self.secret)}):
            with self.subTest(request=request):
                self.assert_rejected_without_stop(request)

    def test_real_flock_refuses_a_second_release_process(self):
        child = subprocess.Popen([
            sys.executable, "-c",
            "import fcntl,sys; f=open(sys.argv[1],'a+'); fcntl.flock(f,fcntl.LOCK_EX); print('locked',flush=True); sys.stdin.read()",
            str(self.state / ".lock"),
        ], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline().strip(), "locked")
            with self.assertRaisesRegex(self.module.ReleaseError, "release_busy"):
                self.receiver.handle({"action": "status"})
            self.assertEqual(self.receiver.events, [])
        finally:
            child.stdin.close()
            child.wait(timeout=5)
            child.stdout.close()
            child.stderr.close()
        self.receiver.handle({"action": "status"})

    def test_health_checks_disable_proxies_and_read_bounded_bodies(self):
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = canonical(self.config["expected_health"])
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(self.module.urllib.request, "build_opener", return_value=opener) as build:
            self.module.probe_health(self.config)
        handlers = build.call_args.args
        self.assertTrue(any(isinstance(h, self.module.urllib.request.ProxyHandler) and h.proxies == {} for h in handlers))
        self.assertTrue(any(isinstance(h, self.module.NoRedirect) for h in handlers))
        self.assertEqual(opener.open.call_count, 2)
        for call in opener.open.call_args_list:
            self.assertGreater(call.kwargs["timeout"], 0)
            self.assertLessEqual(call.kwargs["timeout"], 5)
        response.read.assert_called_with(4097)

    def test_health_rejects_redirects_and_wrong_domain_profile(self):
        with self.assertRaises(self.module.ReleaseError):
            self.module.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://elsewhere.invalid/health")
        response = mock.MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = canonical({**self.config["expected_health"], "write_domains": ["megan.expense_capture"]})
        opener = mock.Mock()
        opener.open.return_value = response
        with mock.patch.object(self.module.urllib.request, "build_opener", return_value=opener):
            with self.assertRaisesRegex(self.module.ReleaseError, "health_failed"):
                self.module.probe_health(self.config)

    def blocked_probe_command(self, pidfile, interrupt_parent=False):
        script = r'''
import os,signal,sys,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
with open(sys.argv[1],'a') as stream:
    stream.write(str(os.getpid())+'\n')
if sys.argv[2] != '0':
    os.kill(int(sys.argv[2]),signal.SIGTERM)
time.sleep(60)
'''
        return [sys.executable, "-c", script, str(pidfile), str(os.getpid()) if interrupt_parent else "0"]

    def assert_probe_children_reaped(self, pidfile):
        self.assertTrue(pidfile.exists(), "Synthetic probe did not start")
        for line in pidfile.read_text().splitlines():
            pid = int(line)
            try:
                waited, _ = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                continue
            if waited == 0:
                os.kill(pid, signal.SIGKILL)
                os.waitpid(pid, 0)
            self.fail("Health probe child was not reaped by receiver")

    def test_blocked_health_probe_has_a_hard_deadline_and_is_reaped(self):
        pidfile = self.root / "blocked-probe-pids"
        command = self.blocked_probe_command(pidfile)
        started = time.monotonic()
        with mock.patch.object(self.receiver, "_probe_command", return_value=command):
            self.assertFalse(self.receiver._probe_with_deadline(0.5))
        self.assertLess(time.monotonic() - started, 3)
        self.assert_probe_children_reaped(pidfile)

    def test_health_total_deadline_bounds_a_blocked_probe(self):
        pidfile = self.root / "bounded-health-pids"
        command = self.blocked_probe_command(pidfile)
        started = time.monotonic()
        with mock.patch.object(self.receiver, "_probe_command", return_value=command), mock.patch.object(self.module, "HEALTH_DEADLINE", 0.5), mock.patch.object(self.module, "PROBE_TIMEOUT", 0.25):
            with self.assertRaisesRegex(self.module.ReleaseError, "health_failed"):
                self.module.Receiver.check_health(self.receiver)
        self.assertLess(time.monotonic() - started, 3)
        self.assert_probe_children_reaped(pidfile)

    def test_parent_interruption_kills_and_reaps_a_blocked_probe(self):
        pidfile = self.root / "interrupted-health-pids"
        command = self.blocked_probe_command(pidfile, interrupt_parent=True)
        def interrupted(signum, frame):
            raise self.module.ReleaseError("synthetic_probe_interrupted")
        previous = signal.signal(signal.SIGTERM, interrupted)
        try:
            with mock.patch.object(self.receiver, "_probe_command", return_value=command):
                with self.assertRaisesRegex(self.module.ReleaseError, "synthetic_probe_interrupted"):
                    self.receiver._probe_with_deadline(3)
        finally:
            signal.signal(signal.SIGTERM, previous)
        self.assert_probe_children_reaped(pidfile)

    def test_probe_does_not_inherit_an_explicitly_inheritable_release_lock(self):
        lock_fd = os.open(self.state / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        inherited_fd = fcntl.fcntl(lock_fd, fcntl.F_DUPFD, 64)
        os.set_inheritable(inherited_fd, True)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        resultfile = self.root / "probe-lock-inheritance"
        script = r'''
import json,os,sys
try:
    os.fstat(int(sys.argv[2])); inherited=True
except OSError:
    inherited=False
with open(sys.argv[1],'w') as stream: stream.write(json.dumps(inherited))
sys.stdin.buffer.read()
sys.exit(1 if inherited else 0)
'''
        try:
            command = [sys.executable, "-c", script, str(resultfile), str(inherited_fd)]
            with mock.patch.object(self.receiver, "_probe_command", return_value=command):
                self.assertTrue(self.receiver._probe_with_deadline(3))
            self.assertFalse(json.loads(resultfile.read_text()))
        finally:
            os.close(inherited_fd)
            os.close(lock_fd)

    def test_strict_json_rejects_duplicate_keys_and_invalid_input(self):
        for raw in (b'{"action":"status","action":"deploy"}', b'not-json', b'\xff'):
            with self.subTest(raw=raw):
                with self.assertRaises(self.module.ReleaseError):
                    self.module.strict_json(raw)

    def test_cli_bounds_stdin_before_handling_a_request(self):
        config_path = self.root / "receiver-config.json"
        config_path.write_text(json.dumps(self.config))
        fake_receiver = mock.Mock()
        fake_receiver.state = self.state
        fake_receiver.releases = self.state / "releases"
        fake_receiver.targets = {}
        fake_stdin = mock.Mock(buffer=io.BytesIO(b"x" * (self.module.MAX_REQUEST + 1)))
        output = io.StringIO()
        with mock.patch.object(self.module, "CONFIG", config_path), mock.patch.object(self.module, "secure_path"), mock.patch.object(self.module, "Receiver", return_value=fake_receiver), mock.patch.object(self.module.os, "geteuid", return_value=0), mock.patch.object(self.module.signal, "signal"), mock.patch.object(self.module.sys, "argv", ["receiver.py"]), mock.patch.object(self.module.sys, "stdin", fake_stdin), mock.patch.dict(self.module.os.environ, {"SSH_ORIGINAL_COMMAND": "personal-watch-release-v1"}), contextlib.redirect_stdout(output):
            result = self.module.main()
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "error", "code": "request_too_large"})
        fake_receiver.handle.assert_not_called()

    def test_cli_rejects_an_arbitrary_original_ssh_command(self):
        output = io.StringIO()
        with mock.patch.object(self.module.os, "geteuid", return_value=0), mock.patch.object(self.module.sys, "argv", ["receiver.py"]), mock.patch.dict(self.module.os.environ, {"SSH_ORIGINAL_COMMAND": "sh -c arbitrary"}), mock.patch.object(self.module, "Receiver") as receiver, contextlib.redirect_stdout(output):
            self.assertEqual(self.module.main(), 1)
        self.assertEqual(json.loads(output.getvalue()), {"status": "error", "code": "unauthorized_invocation"})
        receiver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
