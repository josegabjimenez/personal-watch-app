"""Synthetic release boundary tests; never invoke systemd or live endpoints."""

import base64
import copy
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "receiver.py"


def load_receiver():
    spec = importlib.util.spec_from_file_location("watch_receiver_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def bundle(code, n=1):
    return {
        "schema": 1, "revision": "%040x" % n,
        "files": {"bff": {"sha256": digest(code), "content_b64": base64.b64encode(code).decode()}},
    }


class ReceiverTests(unittest.TestCase):
    def setUp(self):
        self.module = load_receiver()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.target = self.root / "code" / "server.py"
        self.target.parent.mkdir()
        self.old = b"VALUE = 'original'\n"
        self.target.write_bytes(self.old)
        self.target.chmod(0o640)
        self.state = self.root / "release-state"
        self.data = self.root / "data"
        self.data.mkdir()
        self.ledger = self.data / "ledger.sqlite"
        self.ledger.write_bytes(b"synthetic-sqlite-sentinel-never-reset")
        self.secret = self.data / "secret.txt"
        self.secret.write_bytes(b"synthetic-credential-never-package")
        self.config = {
            "targets": {"bff": str(self.target)}, "state_dir": str(self.state),
            "service": "hermes-mobile-capture.service", "keep": 5,
            "expected_cmdline": ["/usr/bin/python3", str(self.target), "--ledger", str(self.ledger)],
            "health_urls": ["http://127.0.0.1:8650/health", "https://example.invalid:8650/health"],
            "expected_health": {"status": "ok", "service": "mobile-capture-v1-staging", "mode": "allowlisted-write", "write_domains": ["aura.grocery_capture"]},
        }
        module = self.module
        target = self.target

        class SyntheticReceiver(module.Receiver):
            def __init__(self, config):
                self.events = []
                self.instance = 1
                self.active = True
                self.bad_start_code = None
                self.bad_health_code = None
                self.fail_all_starts = False
                super().__init__(config)

            def service(self, action):
                self.events.append(action)
                if action == "stop":
                    self.active = False
                elif action in ("start", "restart"):
                    if self.fail_all_starts or target.read_bytes() == self.bad_start_code:
                        raise module.ReleaseError("synthetic_start_failure")
                    self.instance += 1
                    self.active = True
                else:
                    raise AssertionError("Unexpected service action")

            def running_identity(self):
                if not self.active:
                    raise module.ReleaseError("synthetic_inactive")
                return "synthetic-instance-%s" % self.instance

            def check_health(self):
                if target.read_bytes() == self.bad_health_code:
                    raise module.ReleaseError("synthetic_health_failure")

        self.receiver = SyntheticReceiver(self.config)

    def current(self):
        return digest(canonical({"bff": digest(self.target.read_bytes())}))

    def deploy(self, code, n=1, expected=None):
        return self.receiver.handle({"action": "deploy", "bundle": bundle(code, n), "expected_current": expected or self.current()})

    def multi_file_fixture(self):
        helper = self.root / "helper" / "apple_watch_capture.py"
        helper.parent.mkdir()
        helper.write_bytes(b"HELPER = 'old'\n")
        helper.chmod(0o600)
        config = copy.deepcopy(self.config)
        config["targets"]["aura_helper"] = str(helper)
        config["expected_cmdline"].extend(["--aura-helper", str(helper)])
        receiver = type(self.receiver)(config)
        started_files = []
        service = receiver.service
        def recording_service(action):
            if action in ("start", "restart"):
                started_files.append((self.target.read_bytes(), helper.read_bytes()))
            service(action)
        receiver.service = recording_service
        candidate = bundle(b"VALUE = 'new'\n")
        new_helper = b"HELPER = 'new'\n"
        candidate["files"]["aura_helper"] = {"sha256": digest(new_helper), "content_b64": base64.b64encode(new_helper).decode()}
        expected = digest(canonical({"bff": digest(self.old), "aura_helper": digest(helper.read_bytes())}))
        return helper, receiver, candidate, expected, started_files

    def assert_rejected_without_stop(self, request):
        before = self.target.read_bytes()
        self.receiver.events.clear()
        with self.assertRaises(self.module.ReleaseError):
            self.receiver.handle(request)
        self.assertNotIn("stop", self.receiver.events)
        self.assertEqual(self.target.read_bytes(), before)

    def test_stale_baseline_is_rejected_before_service_mutation(self):
        self.assert_rejected_without_stop({"action": "deploy", "bundle": bundle(b"VALUE = 'new'\n"), "expected_current": "0" * 64})

    def test_success_preserves_metadata_credentials_and_sqlite(self):
        before = self.target.stat()
        ledger = self.ledger.read_bytes()
        secret = self.secret.read_bytes()
        code = b"VALUE = 'reviewed-new'\n"
        result = self.deploy(code)
        self.assertEqual(self.target.read_bytes(), code)
        after = self.target.stat()
        self.assertEqual(stat.S_IMODE(after.st_mode), stat.S_IMODE(before.st_mode))
        self.assertEqual((after.st_uid, after.st_gid), (before.st_uid, before.st_gid))
        self.assertEqual(self.ledger.read_bytes(), ledger)
        self.assertEqual(self.secret.read_bytes(), secret)
        self.assertEqual(self.receiver.events, ["stop", "start"])
        encoded = json.dumps(result)
        self.assertNotIn(str(self.target), encoded)
        self.assertNotIn("synthetic-credential", encoded)
        self.assertNotIn("synthetic-sqlite", encoded)
        for path in self.state.rglob("*"):
            if path.is_file():
                data = path.read_bytes()
                self.assertNotIn(ledger, data)
                self.assertNotIn(secret, data)

    def test_new_start_failure_restores_original_and_reports_failure(self):
        code = b"VALUE = 'start-fails'\n"
        self.receiver.bad_start_code = code
        with self.assertRaises(self.module.ReleaseError):
            self.deploy(code)
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertTrue(self.receiver.active)
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertGreaterEqual(self.receiver.events.count("start"), 2)

    def test_new_health_failure_restores_original_and_reports_failure(self):
        code = b"VALUE = 'health-fails'\n"
        self.receiver.bad_health_code = code
        with self.assertRaises(self.module.ReleaseError):
            self.deploy(code)
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertTrue(self.receiver.active)
        self.assertGreaterEqual(self.receiver.events.count("start"), 2)

    def test_failed_recovery_retains_transaction_until_explicit_recover(self):
        self.receiver.fail_all_starts = True
        with self.assertRaises(self.module.ReleaseError):
            self.deploy(b"VALUE = 'new'\n")
        self.receiver.fail_all_starts = False
        self.receiver.handle({"action": "recover"})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertTrue(self.receiver.active)
        # A successful recovery clears the block for the next reviewed update.
        self.deploy(b"VALUE = 'later-reviewed'\n", 2)

    def test_rollback_uses_immutable_successful_snapshot(self):
        first = b"VALUE = 'release-one'\n"
        second = b"VALUE = 'release-two'\n"
        first_bundle = bundle(first, 1)
        self.deploy(first, 1)
        self.deploy(second, 2)
        self.receiver.handle({"action": "rollback", "release_id": digest(canonical(first_bundle)), "expected_current": self.current()})
        self.assertEqual(self.target.read_bytes(), first)
        self.assertTrue(self.receiver.active)

    def test_rollback_rejects_stale_current_or_untrusted_snapshot(self):
        self.deploy(b"VALUE = 'first'\n")
        for release_id, expected in (("0" * 64, self.current()), (digest(canonical(bundle(b"VALUE = 'first'\n"))), "0" * 64), ("../../private", self.current())):
            with self.subTest(release_id=release_id, expected=expected):
                self.assert_rejected_without_stop({"action": "rollback", "release_id": release_id, "expected_current": expected})

    def test_status_contains_no_runtime_paths_or_private_values(self):
        result = self.receiver.handle({"action": "status"})
        text = json.dumps(result)
        self.assertNotIn(str(self.root), text)
        self.assertNotIn("synthetic-credential", text)
        self.assertNotIn("synthetic-sqlite", text)
        self.assertIn(self.current(), text)
        self.assertEqual(self.receiver.events, [])

    def test_malformed_bundles_are_rejected_before_stop(self):
        valid = bundle(b"VALUE = 'new'\n")
        mutations = []
        bad = copy.deepcopy(valid)
        bad["files"]["bff"]["sha256"] = "0" * 64
        mutations.append(bad)
        bad = copy.deepcopy(valid)
        bad["files"]["bff"]["content_b64"] = "%%%invalid%%%"
        mutations.append(bad)
        bad = copy.deepcopy(valid)
        bad["files"]["environment"] = bad["files"]["bff"]
        mutations.append(bad)
        bad = copy.deepcopy(valid)
        bad["files"] = {}
        mutations.append(bad)
        bad = copy.deepcopy(valid)
        bad["revision"] = "main"
        mutations.append(bad)
        bad = copy.deepcopy(valid)
        bad["files"]["bff"]["path"] = str(self.secret)
        mutations.append(bad)
        bad = copy.deepcopy(valid)
        bad["schema"] = 2
        mutations.append(bad)
        for candidate in mutations:
            with self.subTest(candidate=candidate):
                self.assert_rejected_without_stop({"action": "deploy", "bundle": candidate, "expected_current": self.current()})

    def test_unknown_request_fields_and_actions_are_rejected(self):
        for request in ({"action": "shell", "command": "true"}, {"action": "status", "service": "other.service"}, {"action": "deploy", "bundle": bundle(b"VALUE = 1\n"), "expected_current": self.current(), "path": str(self.secret)}):
            with self.subTest(request=request):
                self.assert_rejected_without_stop(request)

    def test_preflight_health_failure_does_not_stop_current_service(self):
        self.receiver.bad_health_code = self.old
        self.assert_rejected_without_stop({"action": "deploy", "bundle": bundle(b"VALUE = 'new'\n"), "expected_current": self.current()})

    def test_unmanaged_code_and_metadata_drift_require_operator_review(self):
        self.deploy(b"VALUE = 'reviewed'\n")
        self.target.write_bytes(b"VALUE = 'unmanaged'\n")
        self.assert_rejected_without_stop({"action": "deploy", "bundle": bundle(b"VALUE = 'next'\n", 2), "expected_current": self.current()})
        self.target.write_bytes(b"VALUE = 'reviewed'\n")
        self.target.chmod(0o600)
        self.assert_rejected_without_stop({"action": "deploy", "bundle": bundle(b"VALUE = 'next'\n", 2), "expected_current": self.current()})

    def test_noop_redeploy_preserves_previous_without_restarting(self):
        self.deploy(b"VALUE = 'first'\n", 1)
        self.deploy(b"VALUE = 'second'\n", 2)
        receipt = (self.state / "state.json").read_bytes()
        self.receiver.events.clear()
        result = self.deploy(b"VALUE = 'second'\n", 2)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.receiver.events, [])
        self.assertEqual((self.state / "state.json").read_bytes(), receipt)

    def test_multi_file_partial_install_failure_restores_every_target(self):
        helper, receiver, candidate, expected, started = self.multi_file_fixture()
        new_helper = b"HELPER = 'new'\n"
        real_atomic = self.module.atomic_bytes
        def failing_atomic(path, data, metadata=None):
            if path == helper and data == new_helper:
                raise OSError("synthetic second-file write failure")
            return real_atomic(path, data, metadata)
        with mock.patch.object(self.module, "atomic_bytes", side_effect=failing_atomic):
            with self.assertRaisesRegex(self.module.ReleaseError, "release_failed_rolled_back"):
                receiver.handle({"action": "deploy", "bundle": candidate, "expected_current": expected})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(helper.read_bytes(), b"HELPER = 'old'\n")
        self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o600)
        self.assertTrue(receiver.active)
        self.assertEqual(started, [(self.old, b"HELPER = 'old'\n")])

    def test_multi_file_success_starts_only_after_all_files_are_installed(self):
        helper, receiver, candidate, expected, started = self.multi_file_fixture()
        receiver.handle({"action": "deploy", "bundle": candidate, "expected_current": expected})
        self.assertEqual(started, [(b"VALUE = 'new'\n", b"HELPER = 'new'\n")])
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(helper.stat().st_mode), 0o600)

    def test_multi_file_mixed_crash_state_recovers_without_starting_mixed_code(self):
        helper, receiver, candidate, expected, started = self.multi_file_fixture()
        script = r'''
import importlib.util,json,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver_child',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
config=json.loads(sys.argv[2]); request=json.loads(sys.argv[3])
target=Path(config['targets']['bff']); real_atomic=module.atomic_bytes
def crash_after_first_file(path,data,metadata=None):
    real_atomic(path,data,metadata)
    if path == target:
        os.kill(os.getpid(),signal.SIGKILL)
module.atomic_bytes=crash_after_first_file
class Synthetic(module.Receiver):
    def service(self,action): pass
    def running_identity(self): return 'synthetic'
    def check_health(self): pass
Synthetic(config).handle(request)
'''
        request = {"action": "deploy", "bundle": candidate, "expected_current": expected}
        result = subprocess.run([sys.executable, "-c", script, str(MODULE_PATH), json.dumps(receiver.config), json.dumps(request)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertEqual(self.target.read_bytes(), b"VALUE = 'new'\n")
        self.assertEqual(helper.read_bytes(), b"HELPER = 'old'\n")
        receiver.handle({"action": "recover"})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertEqual(helper.read_bytes(), b"HELPER = 'old'\n")
        self.assertEqual(started, [(self.old, b"HELPER = 'old'\n")])

    def test_repeated_failed_updates_do_not_accumulate_unbounded_snapshots(self):
        self.config["keep"] = 2
        for n in range(1, 9):
            code = ("VALUE = 'failing-%s'\n" % n).encode()
            self.receiver.bad_health_code = code
            with self.assertRaisesRegex(self.module.ReleaseError, "release_failed_rolled_back"):
                self.deploy(code, n)
        snapshots = list((self.state / "releases").glob("*.json"))
        self.assertLessEqual(len(snapshots), 5)
        self.assertEqual(self.target.read_bytes(), self.old)

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

    def run_interrupted_update(self, signum):
        script = r'''
import importlib.util,json,os,signal,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver_child',sys.argv[1])
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
config=json.loads(sys.argv[2]); request=json.loads(sys.argv[3])
target=Path(config['targets']['bff'])
class Synthetic(module.Receiver):
    counter=0
    interrupted=False
    def service(self,action):
        self.counter += 1
        if action == 'start' and b"interrupt" in target.read_bytes() and not self.interrupted:
            self.interrupted=True
            os.kill(os.getpid(),int(sys.argv[4]))
    def running_identity(self): return str(self.counter)
    def check_health(self): pass
def interrupted(signum,frame): raise module.ReleaseError('interrupted')
signal.signal(signal.SIGTERM,interrupted)
try:
    Synthetic(config).handle(request)
except module.ReleaseError as exc:
    print(str(exc)); sys.exit(1)
'''
        request = {"action": "deploy", "bundle": bundle(b"VALUE = 'interrupt'\n"), "expected_current": self.current()}
        return subprocess.run([
            sys.executable, "-c", script, str(MODULE_PATH), json.dumps(self.config), json.dumps(request), str(int(signum)),
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=10)

    def test_sigterm_during_install_rolls_back_before_exit(self):
        result = self.run_interrupted_update(signal.SIGTERM)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("release_failed_rolled_back", result.stdout)
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertFalse((self.state / "transaction.json").exists())

    def test_sigkill_retains_journal_and_blocks_until_explicit_recovery(self):
        result = self.run_interrupted_update(signal.SIGKILL)
        self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
        self.assertTrue((self.state / "transaction.json").exists())
        with self.assertRaisesRegex(self.module.ReleaseError, "recovery_required"):
            self.deploy(b"VALUE = 'next'\n", 2)
        self.receiver.handle({"action": "recover"})
        self.assertEqual(self.target.read_bytes(), self.old)
        self.assertFalse((self.state / "transaction.json").exists())

    def test_recovery_refuses_to_overwrite_unrecognized_code_drift(self):
        result = self.run_interrupted_update(signal.SIGKILL)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        changed = b"VALUE = 'external-edit-after-crash'\n"
        self.target.write_bytes(changed)
        self.assert_rejected_without_stop({"action": "recover"})
        self.assertEqual(self.target.read_bytes(), changed)
        self.assertTrue((self.state / "transaction.json").exists())

    def test_retention_protects_baseline_active_previous_and_unrelated_files(self):
        self.config["keep"] = 2
        unrelated = self.state / "releases" / "operator-note.txt"
        unrelated.write_text("keep unrelated files")
        for n in range(1, 9):
            self.deploy(("VALUE = %s\n" % n).encode(), n)
        receipt = json.loads((self.state / "state.json").read_text())
        retained = {p.stem for p in (self.state / "releases").glob("*.json")}
        self.assertTrue({receipt["active"], receipt["previous"], receipt["baseline"]} <= retained)
        self.assertLessEqual(len(retained), 5)
        self.assertEqual(unrelated.read_text(), "keep unrelated files")
        self.receiver.handle({"action": "rollback", "release_id": receipt["baseline"], "expected_current": self.current()})
        self.assertEqual(self.target.read_bytes(), self.old)

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
