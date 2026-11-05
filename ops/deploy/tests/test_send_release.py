"""SSH transport boundary tests with a fake subprocess, never a real connection."""

import importlib.util
import json
from pathlib import Path
import stat
import subprocess
import sys
import types
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "send_release.py"


def load_sender():
    spec = importlib.util.spec_from_file_location("watch_send_release_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(MODULE_PATH.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class SendReleaseTests(unittest.TestCase):
    def setUp(self):
        self.module = load_sender()
        self.key = "synthetic-private-key-sentinel"
        self.hosts = "host.example.invalid ssh-ed25519 synthetic-known-host-sentinel"

    def send(self, request=None, **changes):
        args = {"request": request or {"action": "status"}, "host": "host.example.invalid", "port": "22", "user": "watch_deploy", "private_key": self.key, "known_hosts": self.hosts}
        args.update(changes)
        return self.module.send_request(**args)

    def fake_run(self, result, returncode=0, observed=None):
        def run(command, **kwargs):
            key = Path(command[command.index("-i") + 1])
            host_arg = next(arg for arg in command if arg.startswith("UserKnownHostsFile="))
            hosts = Path(host_arg.split("=", 1)[1])
            self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(hosts.stat().st_mode), 0o600)
            self.assertEqual(key.read_text().strip(), self.key)
            self.assertEqual(hosts.read_text().strip(), self.hosts)
            self.assertEqual(command[-1], "personal-watch-release-v1")
            self.assertNotIn("shell", kwargs)
            self.assertEqual(kwargs["stderr"], subprocess.DEVNULL)
            self.assertGreater(kwargs["timeout"], 0)
            self.assertLessEqual(kwargs["timeout"], 480)
            self.assertNotIn(self.key, json.dumps(command))
            if observed is not None:
                observed.update({"key": key, "hosts": hosts, "command": command, "payload": kwargs["input"]})
            kwargs["stdout"].write(result if isinstance(result, bytes) else json.dumps(result).encode())
            return types.SimpleNamespace(returncode=returncode)
        return run

    def test_ssh_preserves_verified_host_identity_and_disables_forwarding(self):
        command = self.module.ssh_command("host.example.invalid", 2222, "watch_deploy", "/tmp/key path", "/tmp/known hosts")
        for required in ("StrictHostKeyChecking=yes", "BatchMode=yes", "IdentitiesOnly=yes", "ClearAllForwardings=yes", "ForwardAgent=no", "GlobalKnownHostsFile=/dev/null", "UserKnownHostsFile=/tmp/known hosts"):
            self.assertIn(required, command)
        self.assertEqual(command[1:3], ["-F", "/dev/null"])
        self.assertIn("-T", command)
        self.assertEqual(command[-2:], ["watch_deploy@host.example.invalid", "personal-watch-release-v1"])
        self.assertEqual(command[command.index("-i") + 1], "/tmp/key path")

    def test_destination_options_and_shell_injection_are_rejected(self):
        for host, port, user in (("-oProxyCommand=evil", 22, "watch_deploy"), ("host;cmd", 22, "watch_deploy"), ("$(cmd)", 22, "watch_deploy"), ("host name", 22, "watch_deploy"), ("host", 22, "root --bad"), ("host", 0, "watch_deploy"), ("host", 65536, "watch_deploy")):
            with self.subTest(host=host, port=port, user=user):
                with self.assertRaises((ValueError, TypeError)):
                    self.module.ssh_command(host, port, user, "/tmp/key", "/tmp/known_hosts")

    def test_transport_uses_private_temporary_files_and_cleans_up(self):
        observed = {}
        with mock.patch.object(self.module.subprocess, "run", side_effect=self.fake_run({"status": "ok", "current": "a" * 64}, observed=observed)) as run:
            code, result = self.send()
        self.assertEqual(code, 0)
        self.assertEqual(result, {"status": "ok", "current": "a" * 64})
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(observed["payload"]), {"action": "status"})
        self.assertFalse(observed["key"].exists())
        self.assertFalse(observed["hosts"].exists())

    def test_missing_credentials_and_oversize_payload_never_connect(self):
        with mock.patch.object(self.module.subprocess, "run") as run:
            for changes in ({"private_key": ""}, {"known_hosts": "  "}, {"request": {"action": "deploy", "extra": "x" * 2_000_001}}):
                with self.subTest(changes=list(changes)):
                    with self.assertRaises(ValueError):
                        self.send(**changes)
            run.assert_not_called()

    def test_remote_error_codes_survive_but_arbitrary_diagnostics_do_not(self):
        body = {"status": "error", "code": "release_failed_rolled_back", "diagnostic": "private-test-sentinel", "path": "/private/runtime/file"}
        with mock.patch.object(self.module.subprocess, "run", side_effect=self.fake_run(body, returncode=1)):
            code, result = self.send({"action": "recover"})
        self.assertEqual(code, 1)
        self.assertEqual(result.get("code"), "release_failed_rolled_back")
        self.assertNotIn("private-test-sentinel", json.dumps(result))
        self.assertNotIn("/private/runtime", json.dumps(result))

    def test_hash_fields_do_not_relay_arbitrary_receiver_text(self):
        result = self.module.safe_result({"status": "ok", "revision": "private_text", "current": "not_a_hash", "release_id": "/private/file", "debug": self.key})
        encoded = json.dumps(result)
        self.assertNotIn("private_text", encoded)
        self.assertNotIn("not_a_hash", encoded)
        self.assertNotIn("/private/file", encoded)
        self.assertNotIn(self.key, encoded)

    def test_inhibitor_recovery_and_exact_batch_compatibility_flags_survive(self):
        value = {"status": "ok", "current": "a" * 64, "inhibited": True,
                 "recovery_required": True, "retention_pending": False,
                 "state_compatibility": "grocery-batch-v1"}
        self.assertEqual(self.module.safe_result(value), value)
        for state in ("legacy-single-item", "grocery-batch-v2", "/private/state", True, 1):
            with self.subTest(state=state):
                result = self.module.safe_result({"status": "ok", "state_compatibility": state})
                self.assertNotIn("state_compatibility", result)

    def test_timeout_is_never_retried_and_cleans_up_key(self):
        observed = {}
        def timed_out(command, **kwargs):
            observed["key"] = Path(command[command.index("-i") + 1])
            raise subprocess.TimeoutExpired(command, 480)
        with mock.patch.object(self.module.subprocess, "run", side_effect=timed_out) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                self.send({"action": "deploy", "expected_current": "a" * 64})
        self.assertEqual(run.call_count, 1)
        self.assertFalse(observed["key"].exists())

    def test_invalid_or_oversize_remote_output_is_not_relayed(self):
        for body in (b"private arbitrary output", b"x" * 8193):
            with self.subTest(size=len(body)), mock.patch.object(self.module.subprocess, "run", side_effect=self.fake_run(body)):
                with self.assertRaises(ValueError):
                    self.send()


if __name__ == "__main__":
    unittest.main()
