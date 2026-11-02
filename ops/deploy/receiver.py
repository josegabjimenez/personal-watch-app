#!/usr/bin/env python3
"""Fixed-target, code-only release receiver. Install outside the managed code tree.

The CLI is deliberately not a general remote command runner. Its root-owned config
and this program are installed manually, after review; release requests contain no
paths, shell commands, credentials, configuration, or database files.
"""
import base64
import binascii
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_REQUEST = 2 * 1024 * 1024
MAX_FILE = 512 * 1024
HEALTH_DEADLINE = 30
PROBE_TIMEOUT = 5
SERVICE = "hermes-mobile-capture.service"
CONFIG = Path("/etc/personal-watch-deploy.json")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
SLOTS = {"bff", "aura_helper", "expense_helper"}


class ReleaseError(Exception):
    """Only these fixed, public-safe codes may cross the SSH boundary."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ReleaseError("invalid_json")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ReleaseError("invalid_json") from exc


def regular(path):
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ReleaseError("unsafe_file")
        return info
    except OSError as exc:
        raise ReleaseError("unsafe_file") from exc


def secure_path(path, directory=False):
    """Production-only ownership check, including every ancestor."""
    for item in [path] + list(path.parents):
        info = item.lstat()
        if info.st_uid != 0 or info.st_mode & 0o022 or stat.S_ISLNK(info.st_mode):
            raise ReleaseError("unsafe_installation")
        if item != path or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise ReleaseError("unsafe_installation")
    if not directory:
        regular(path)


def fsync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def uninterrupted_restore():
    """Finish the bounded rollback after an initial catchable interruption."""
    saved = {}
    try:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            try:
                saved[signum] = signal.signal(signum, signal.SIG_IGN)
            except ValueError:  # Fixtures may invoke the API from a worker thread.
                break
        yield
    finally:
        for signum, handler in saved.items():
            signal.signal(signum, handler)


def atomic_bytes(path, data, metadata=None):
    fd, temporary = tempfile.mkstemp(prefix=".release-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            if metadata:
                os.fchown(stream.fileno(), metadata["uid"], metadata["gid"])
                os.fchmod(stream.fileno(), metadata["mode"])
            else:
                os.fchmod(stream.fileno(), 0o600)
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        fsync_dir(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ReleaseError("health_failed")


def probe_health(config):
    """One network attempt, executed only inside a time-limited child process."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for url in config["health_urls"]:
        with opener.open(url, timeout=2) as response:
            body = response.read(4097)
            if response.status != 200 or len(body) > 4096 or strict_json(body) != config["expected_health"]:
                raise ReleaseError("health_failed")


class Receiver:
    def __init__(self, config):
        self.config = config
        try:
            if set(config) != {"targets", "state_dir", "expected_cmdline", "health_urls", "expected_health", "service", "keep"}:
                raise ValueError()
            if config["service"] != SERVICE or type(config["keep"]) is not int or not 2 <= config["keep"] <= 10:
                raise ValueError()
            if not isinstance(config["targets"], dict) or "bff" not in config["targets"] or not set(config["targets"]) <= SLOTS:
                raise ValueError()
            self.targets = {key: Path(value) for key, value in config["targets"].items()}
            self.state = Path(config["state_dir"])
            for path in list(self.targets.values()) + [self.state]:
                if not path.is_absolute() or path.resolve() != path:
                    raise ValueError()
            if len(set(self.targets.values())) != len(self.targets):
                raise ValueError()
            argv = config["expected_cmdline"]
            if not isinstance(argv, list) or not all(isinstance(x, str) and x and "\0" not in x for x in argv):
                raise ValueError()
            if len(argv) < 4 or not Path(argv[0]).is_absolute() or argv[1] != str(self.targets["bff"]) or argv.count("--ledger") != 1:
                raise ValueError()
            ledger = Path(argv[argv.index("--ledger") + 1])
            if not ledger.is_absolute() or not ledger.is_file():
                raise ValueError()
            for target in self.targets.values():
                regular(target)
                if target.suffix != ".py" or ledger == target or self.state.is_relative_to(target.parent):
                    raise ValueError()
            for slot, flag in (("aura_helper", "--aura-helper"), ("expense_helper", "--expense-helper")):
                if slot in self.targets and (argv.count(flag) != 1 or argv[argv.index(flag) + 1] != str(self.targets[slot])):
                    raise ValueError()
            if self.state.is_relative_to(ledger.parent) or ledger.is_relative_to(self.state):
                raise ValueError()
            urls = config["health_urls"]
            if not isinstance(urls, list) or len(urls) != 2 or not isinstance(config["expected_health"], dict):
                raise ValueError()
            parsed = [urllib.parse.urlsplit(url) for url in urls]
            if not any(p.scheme == "http" and p.hostname in {"127.0.0.1", "::1"} for p in parsed) or not any(p.scheme == "https" for p in parsed):
                raise ValueError()
            if any(p.scheme not in {"http", "https"} or p.username or p.password or p.path != "/health" or p.query or p.fragment for p in parsed):
                raise ValueError()
            self.state.mkdir(mode=0o700, parents=False, exist_ok=True)
            self.releases = self.state / "releases"
            self.releases.mkdir(mode=0o700, exist_ok=True)
            for path in (self.state, self.releases):
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                    raise ReleaseError("unsafe_state")
        except (KeyError, ValueError, IndexError, TypeError, OSError) as exc:
            raise ReleaseError("invalid_config") from exc

    def service(self, action):
        try:
            subprocess.run(["/usr/bin/systemctl", action, SERVICE], check=True, timeout=30,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ReleaseError("service_failed") from exc

    def running_identity(self):
        try:
            subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet", SERVICE], check=True, timeout=5,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            pid = subprocess.check_output(["/usr/bin/systemctl", "show", "--property=MainPID", "--value", SERVICE],
                                          timeout=5, stderr=subprocess.DEVNULL).decode().strip()
            if not pid.isdigit() or int(pid) <= 1:
                raise ValueError()
            proc = Path("/proc") / pid
            argv = (proc / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
            if argv != self.config["expected_cmdline"]:
                raise ValueError()
            started = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
            return pid + ":" + started
        except (OSError, ValueError, IndexError, UnicodeError, subprocess.SubprocessError) as exc:
            raise ReleaseError("runtime_mismatch") from exc

    def _probe_command(self):
        # The child has no inherited release-lock fd. Its own kernel alarm also
        # bounds DNS/body reads if the parent dies before it can kill the probe.
        program = (
            "import json,runpy,signal,sys;"
            "signal.signal(signal.SIGALRM,signal.SIG_DFL);signal.alarm(5);"
            "module=runpy.run_path(sys.argv[1],run_name='personal_watch_health_probe');"
            "module['probe_health'](json.load(sys.stdin))"
        )
        return [sys.executable, "-I", "-c", program, str(Path(__file__).resolve())]

    def _probe_with_deadline(self, timeout):
        payload = canonical({key: self.config[key] for key in ("health_urls", "expected_health")})
        process = None
        signals = {signal.SIGHUP, signal.SIGINT, signal.SIGTERM}
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, signals)
        try:
            process = subprocess.Popen(self._probe_command(), stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       close_fds=True, env={"PATH": "/usr/bin:/bin", "LANG": "C"})
        finally:
            if process is None:
                signal.pthread_sigmask(signal.SIG_SETMASK, previous)
        try:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)
            process.communicate(input=payload, timeout=timeout)
            return process.returncode == 0
        except subprocess.TimeoutExpired:
            return False
        finally:
            with uninterrupted_restore():
                if process.poll() is None:
                    process.kill()
                process.wait()
                if process.stdin is not None:
                    process.stdin.close()

    def check_health(self):
        deadline = time.monotonic() + HEALTH_DEADLINE
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReleaseError("health_failed")
            try:
                if self._probe_with_deadline(min(PROBE_TIMEOUT, remaining)):
                    return
            except OSError:
                pass
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ReleaseError("health_failed")
            time.sleep(min(0.5, remaining))

    def _files(self):
        result, metadata = {}, {}
        for key, path in self.targets.items():
            info = regular(path)
            with path.open("rb") as stream:
                data = stream.read(MAX_FILE + 1)
            if len(data) > MAX_FILE:
                raise ReleaseError("code_too_large")
            result[key] = {"sha256": digest(data), "content_b64": base64.b64encode(data).decode("ascii")}
            metadata[key] = {"uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}
        return result, metadata

    def _fingerprint(self):
        files, _ = self._files()
        return digest(canonical({key: item["sha256"] for key, item in files.items()}))

    def _bundle(self, bundle):
        if not isinstance(bundle, dict) or set(bundle) != {"schema", "revision", "files"} or type(bundle["schema"]) is not int or bundle["schema"] != 1:
            raise ReleaseError("invalid_bundle")
        if not isinstance(bundle["revision"], str) or not HEX40.fullmatch(bundle["revision"]):
            raise ReleaseError("invalid_bundle")
        if not isinstance(bundle["files"], dict) or set(bundle["files"]) != set(self.targets):
            raise ReleaseError("invalid_bundle")
        for value in bundle["files"].values():
            if not isinstance(value, dict) or set(value) != {"sha256", "content_b64"}:
                raise ReleaseError("invalid_bundle")
            try:
                data = base64.b64decode(value["content_b64"], validate=True)
                if not data or len(data) > MAX_FILE or digest(data) != value["sha256"]:
                    raise ValueError()
                compile(data, "<release>", "exec")
            except (ValueError, TypeError, binascii.Error, SyntaxError) as exc:
                raise ReleaseError("invalid_bundle") from exc
        return digest(canonical(bundle))

    def _read(self, path):
        regular(path)
        with path.open("rb") as stream:
            data = stream.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise ReleaseError("invalid_state")
        return strict_json(data)

    def _write(self, path, value):
        atomic_bytes(path, canonical(value))

    def _receipt(self):
        path = self.state / "state.json"
        value = self._read(path) if path.exists() else {"active": None, "previous": None, "baseline": None}
        self._validate_receipt(value)
        return value

    def _validate_receipt(self, value):
        if not isinstance(value, dict) or set(value) != {"active", "previous", "baseline"}:
            raise ReleaseError("invalid_state")
        if any(item is not None and (not isinstance(item, str) or not HEX64.fullmatch(item)) for item in value.values()):
            raise ReleaseError("invalid_state")
        if (value["active"] is None) != (value["baseline"] is None) or value["active"] is None and value["previous"] is not None:
            raise ReleaseError("invalid_state")

    def _snapshot(self, bundle, metadata):
        release_id = self._bundle(bundle)
        path = self.releases / (release_id + ".json")
        value = {"bundle": bundle, "metadata": metadata}
        if path.exists():
            if self._read(path) != value:
                raise ReleaseError("snapshot_conflict")
        else:
            self._write(path, value)
        return release_id

    def _load(self, release_id):
        if not isinstance(release_id, str) or not HEX64.fullmatch(release_id):
            raise ReleaseError("unknown_release")
        path = self.releases / (release_id + ".json")
        if not path.exists():
            raise ReleaseError("unknown_release")
        value = self._read(path)
        if set(value) != {"bundle", "metadata"} or self._bundle(value["bundle"]) != release_id or set(value["metadata"]) != set(self.targets):
            raise ReleaseError("invalid_state")
        for metadata in value["metadata"].values():
            if set(metadata) != {"uid", "gid", "mode"} or not all(type(x) is int and x >= 0 for x in metadata.values()) or metadata["mode"] & ~0o777:
                raise ReleaseError("invalid_state")
        return value

    def _verify(self, bundle, before_identity=None):
        identity = self.running_identity()
        if before_identity is not None and identity == before_identity:
            raise ReleaseError("restart_unverified")
        expected = digest(canonical({key: item["sha256"] for key, item in bundle["files"].items()}))
        if self._fingerprint() != expected:
            raise ReleaseError("code_mismatch")
        self.check_health()
        if self.running_identity() != identity or self._fingerprint() != expected:
            raise ReleaseError("runtime_changed")

    def _install(self, snapshot):
        self.service("stop")
        for key, path in self.targets.items():
            regular(path)
            atomic_bytes(path, base64.b64decode(snapshot["bundle"]["files"][key]["content_b64"]), snapshot["metadata"][key])
        self.service("start")

    def _remove_journal(self):
        (self.state / "transaction.json").unlink()
        fsync_dir(self.state)

    def _recover(self):
        with uninterrupted_restore():
            return self._restore_transaction()

    def _restore_transaction(self):
        journal = self._read(self.state / "transaction.json")
        if not isinstance(journal, dict) or set(journal) != {"before", "after", "old_state"}:
            raise ReleaseError("invalid_state")
        self._validate_receipt(journal["old_state"])
        if journal["before"] != journal["old_state"]["active"]:
            raise ReleaseError("invalid_state")
        before, after = self._load(journal["before"]), self._load(journal["after"])
        actual, _ = self._files()
        if any(actual[key]["sha256"] not in {before["bundle"]["files"][key]["sha256"], after["bundle"]["files"][key]["sha256"]} for key in self.targets):
            raise ReleaseError("recovery_code_drift")
        self._install(before)
        self._verify(before["bundle"])
        self._write(self.state / "state.json", journal["old_state"])
        self._remove_journal()
        retention_pending = self._safe_prune(journal["old_state"])
        return {"status": "recovered", "current": self._fingerprint(), "active": journal["before"], "retention_pending": retention_pending}

    def _prune(self, receipt):
        protected = {receipt[key] for key in ("active", "previous", "baseline")}
        candidates = []
        for path in self.releases.iterdir():
            if re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                regular(path)
                self._load(path.stem)
                candidates.append(path)
        candidates.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
        keep = protected | {path.stem for path in candidates[:self.config["keep"]]}
        for path in candidates:
            if path.stem not in keep:
                path.unlink()
        fsync_dir(self.releases)

    def _safe_prune(self, receipt):
        try:
            self._prune(receipt)
            return False
        except Exception:
            return True  # Retention failure does not invalidate verified recovery.

    def handle(self, request):
        if not isinstance(request, dict) or not isinstance(request.get("action"), str):
            raise ReleaseError("invalid_request")
        action = request["action"]
        keys = {"status": {"action"}, "recover": {"action"}, "deploy": {"action", "bundle", "expected_current"}, "rollback": {"action", "release_id", "expected_current"}}
        if action not in keys or set(request) != keys[action]:
            raise ReleaseError("invalid_request")
        lock_path = self.state / ".lock"
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ReleaseError("release_busy") from exc
            journal = self.state / "transaction.json"
            receipt = self._receipt()
            if action == "status":
                return {"status": "ok", "current": self._fingerprint(), "active": receipt["active"], "previous": receipt["previous"], "recovery_required": journal.exists()}
            if action == "recover":
                if not journal.exists():
                    raise ReleaseError("no_recovery_pending")
                return self._recover()
            if journal.exists():
                raise ReleaseError("recovery_required")
            current = request["expected_current"]
            if not isinstance(current, str) or not HEX64.fullmatch(current) or self._fingerprint() != current:
                raise ReleaseError("stale_current")
            if receipt["active"] is not None:
                active = self._load(receipt["active"])["bundle"]
                if digest(canonical({key: value["sha256"] for key, value in active["files"].items()})) != current:
                    raise ReleaseError("unmanaged_code_drift")
            before_identity = self.running_identity()
            self.check_health()
            files, metadata = self._files()
            if self._fingerprint() != current:
                raise ReleaseError("code_mismatch")
            if receipt["active"] is None:
                baseline = {"schema": 1, "revision": "0" * 40, "files": files}
                baseline_id = self._snapshot(baseline, metadata)
                receipt = {"active": baseline_id, "previous": None, "baseline": baseline_id}
            elif self._load(receipt["active"])["metadata"] != metadata:
                raise ReleaseError("metadata_drift")
            if action == "rollback":
                release_id = request["release_id"]
                if release_id not in {receipt["previous"], receipt["baseline"]}:
                    raise ReleaseError("rollback_not_retained")
                candidate = self._load(release_id)
            else:
                release_id = self._snapshot(request["bundle"], metadata)
                candidate = self._load(release_id)
            if release_id == receipt["active"]:
                return {"status": "unchanged", "revision": candidate["bundle"]["revision"],
                        "release_id": release_id, "current": current, "previous": receipt["previous"]}
            self._write(journal, {"before": receipt["active"], "after": release_id, "old_state": receipt})
            try:
                self._install(candidate)
                self._verify(candidate["bundle"], before_identity)
                updated = {"active": release_id, "previous": receipt["active"], "baseline": receipt["baseline"]}
                self._write(self.state / "state.json", updated)
                self._remove_journal()
            except BaseException as exc:
                try:
                    self._recover()
                except BaseException as recovery_error:
                    raise ReleaseError("rollback_failed_recovery_required") from recovery_error
                raise ReleaseError("release_failed_rolled_back") from exc
            retention_pending = self._safe_prune(updated)
            return {"status": "deployed" if action == "deploy" else "rolled_back", "revision": candidate["bundle"]["revision"], "release_id": release_id, "current": self._fingerprint(), "previous": updated["previous"], "retention_pending": retention_pending}
        finally:
            os.close(fd)


def main():
    try:
        if os.geteuid() != 0 or len(sys.argv) != 1 or os.environ.get("SSH_ORIGINAL_COMMAND") != "personal-watch-release-v1":
            raise ReleaseError("unauthorized_invocation")
        secure_path(Path(__file__).absolute())
        secure_path(CONFIG)
        config = strict_json(CONFIG.read_bytes())
        receiver = Receiver(config)
        secure_path(receiver.state, directory=True)
        secure_path(receiver.releases, directory=True)
        for target in receiver.targets.values():
            secure_path(target)
        def interrupted(signum, frame):
            raise ReleaseError("interrupted")
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, interrupted)
        signal.signal(signal.SIGALRM, interrupted)
        signal.alarm(30)
        raw = sys.stdin.buffer.read(MAX_REQUEST + 1)
        signal.alarm(0)
        if len(raw) > MAX_REQUEST:
            raise ReleaseError("request_too_large")
        result = receiver.handle(strict_json(raw))
        print(canonical(result).decode())
        return 0
    except ReleaseError as exc:
        print(canonical({"status": "error", "code": str(exc)}).decode())
    except BaseException:
        print('{"status":"error","code":"receiver_failed"}')
    return 1


if __name__ == "__main__":
    sys.exit(main())
