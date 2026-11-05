#!/usr/bin/env python3
"""Narrow, sealed four-to-six-file grocery Luna upgrade; default is read-only.

Never restore a database, reset history, invoke a capture, or read the provider
key. After budget initialization begins, any failure leaves this exact unit
stopped: the old four-file writer must never be restarted over newer state.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import subprocess
import tempfile
import time
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

UNIT = "hermes-mobile-capture.service"
DOMAIN = "aura.grocery_capture"
SERVER = "mobile_capture_staging_server.py"
# Reseal this constant only after the six-file runtime and manifest are reviewed.
MANIFEST_SHA256 = "bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281"
HELPER_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
BASELINE_ARTIFACTS = {
    SERVER: "309602498c2a9ccf3186e7c336ed9ff5cc2fe3b693866fc1fbfdb18c1b0d5100",
    "grocery_capture.py": "30638a74cc96c4ab71a7d3df8e89552ca1180798715fe6c04be33b4dd58ef943",
    "grocery_helper_adapter.py": "2894998b54a22bbc3b398a801f8cf5a080a22bcd5fec51678ccd4d8f7baacbde",
    "grocery_item_parser.py": "bf0097c86e43ca8b3349a96bc2ebf65f8d5d2e93eca4e9e3856ff6a2d134fdc7",
}
ARTIFACT_NAMES = frozenset((*BASELINE_ARTIFACTS, "grocery_ai.py", "grocery_ai_budget.py"))
DROPIN_NAME = "60-grocery-ai.conf"
EXPECTED_UID = 0
EXPECTED_HEALTH = {"status": "ok", "service": "mobile-capture-v1-staging",
                   "mode": "allowlisted-write", "write_domains": [DOMAIN]}
VOLATILE_ENV = frozenset({"INVOCATION_ID", "JOURNAL_STREAM", "SYSTEMD_EXEC_PID", "MAINPID"})
GROCERY_SETTINGS = {"GROCERY_AI_ENABLED": "1", "GROCERY_AI_MODEL": "gpt-6-luna",
                    "GROCERY_AI_TIMEOUT_SECONDS": "8"}
JOURNAL_COLUMNS = {
    "grocery_batches": {"request_id", "payload_hash", "plan_json", "plan_hash", "response_json", "status", "created_at", "updated_at"},
    "grocery_operations": {"operation_id", "plan_hash", "plan_json", "destination", "status", "candidate_id", "receipt_json", "error_code"},
    "grocery_ai_budget_state": {"id", "policy_json", "policy_hash", "ledger_id", "high_water_month", "request_count", "settled_count", "total_reserved_nanodollars", "total_charged_nanodollars", "integrity_hash"},
    "grocery_ai_budget_requests": {"request_id", "body_hash", "max_output_tokens", "admitted_month", "reserved_nanodollars", "status", "settled_month", "input_tokens", "output_tokens", "usage_hash", "charged_nanodollars", "integrity_hash"},
}


class UpgradeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Paths:
    package: Path
    backup_root: Path
    lock_file: Path
    key_file: Path
    dropin_root: Path = Path("/etc/systemd/system")

    @property
    def dropin(self) -> Path:
        return self.dropin_root / (UNIT + ".d") / DROPIN_NAME


@dataclass(frozen=True)
class ServiceSnapshot:
    active: bool
    pid: int
    argv: tuple[str, ...]
    working_directory: Path
    uid: int
    gid: int
    environment: tuple[tuple[str, str], ...]
    unit_files: tuple[Path, ...]
    execution_fingerprint: str
    needs_reload: bool = False
    environment_files: tuple[Path, ...] = ()


@dataclass(frozen=True)
class Runtime:
    snapshot: ServiceSnapshot
    entrypoint: Path
    ledger: Path
    helper: Path
    secret: Path
    file_metadata: tuple[tuple[str, tuple], ...]
    ledger_identity: tuple[int, int]
    journal_identity: tuple[int, int] | None
    key_metadata: tuple
    secret_metadata: tuple
    config_metadata: tuple[tuple, ...]

    @property
    def journal(self) -> Path:
        return Path(str(self.ledger) + ".grocery.sqlite3")

    @property
    def marker(self) -> Path:
        return Path(str(self.journal) + ".ai-budget-initialized")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def metadata(path: Path) -> tuple:
    """Metadata only: this function must never open/read credential contents."""
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise UpgradeError("invalid_path")
        value = path.lstat()
        if not stat.S_ISREG(value.st_mode):
            raise UpgradeError("invalid_path")
        return (value.st_dev, value.st_ino, value.st_uid, value.st_gid,
                stat.S_IMODE(value.st_mode), value.st_size, value.st_mtime_ns)
    except OSError:
        raise UpgradeError("invalid_path") from None


def regular_bytes(path: Path) -> bytes:
    before = metadata(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            actual = os.fstat(stream.fileno())
            if (actual.st_dev, actual.st_ino) != before[:2]:
                raise UpgradeError("file_changed")
            raw = stream.read()
        if metadata(path) != before:
            raise UpgradeError("file_changed")
        return raw
    except OSError:
        raise UpgradeError("invalid_path") from None


def root_file(path: Path) -> tuple:
    value = metadata(path)
    if value[2] != EXPECTED_UID or value[4] & 0o022:
        raise UpgradeError("unsafe_metadata")
    return value


def private_file(path: Path, uid: int, *, key=False) -> tuple:
    value = metadata(path)
    if value[2] != uid or value[4] != 0o600 or value[5] < 1 or (key and value[5] > 4096):
        raise UpgradeError("key_metadata" if key else "secret_metadata")
    return value


def secure_directory(path: Path) -> None:
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path:
            raise UpgradeError("invalid_path")
        info = path.stat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != EXPECTED_UID or stat.S_IMODE(info.st_mode) & 0o022:
            raise UpgradeError("unsafe_metadata")
    except OSError:
        raise UpgradeError("invalid_path") from None


def option(argv: tuple[str, ...], name: str, default=None):
    values = []
    for index, arg in enumerate(argv):
        if arg == name:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise UpgradeError("service_arguments")
            values.append(argv[index + 1])
        elif arg.startswith(name + "="):
            values.append(arg.partition("=")[2])
    if len(values) > 1 or (values and not values[0]):
        raise UpgradeError("service_arguments")
    return values[0] if values else default


def execution_fingerprint(raw: str) -> str:
    # systemd appends per-invocation times, PID and exit result after these
    # stable fields. Hashing the whole show property would reject every restart.
    stable = raw.split(" ; start_time=", 1)[0].split("; start_time=", 1)[0]
    if "path=" not in stable or "argv[]=" not in stable or "ignore_errors=" not in stable:
        raise UpgradeError("service_metadata")
    return digest(stable.encode())


def readonly_db(path: Path):
    metadata(path)
    conn = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=3)
    conn.execute("PRAGMA query_only=ON")
    return conn


def quick_check(conn):
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise UpgradeError("database_invalid")


def logical_hash(conn):
    result = hashlib.sha256()
    for statement in conn.iterdump():
        result.update(statement.encode())
        result.update(b"\n")
    return result.hexdigest()


def sync_directory(path: Path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SystemdController:
    """Only the literal unit above can be inspected, stopped or started."""
    def _run(self, *args, timeout=5):
        try:
            return subprocess.run(["systemctl", *args], capture_output=True, check=True,
                                  text=True, timeout=timeout).stdout
        except (OSError, subprocess.SubprocessError):
            raise UpgradeError("service_command_failed") from None

    def snapshot(self):
        fields = dict(line.split("=", 1) for line in self._run("show", UNIT,
            "--property=MainPID,ActiveState,SubState,WorkingDirectory,FragmentPath,DropInPaths,ExecStart,User,Group,NeedDaemonReload,EnvironmentFiles",
            "--no-pager").splitlines() if "=" in line)
        try:
            pid = int(fields.get("MainPID", "0"))
            active = fields.get("ActiveState") == "active" and fields.get("SubState") == "running"
            if not active or pid < 1:
                return ServiceSnapshot(False, pid, (), Path("/"), -1, -1, (), (), "")
            proc = Path("/proc") / str(pid)
            argv = tuple(value.decode() for value in (proc / "cmdline").read_bytes().split(b"\0") if value)
            pairs = []
            for raw in (proc / "environ").read_bytes().split(b"\0"):
                if raw:
                    name, separator, value = raw.partition(b"=")
                    if not separator:
                        raise ValueError()
                    pairs.append((name.decode(), value.decode()))
            names = [name for name, _ in pairs]
            if len(set(names)) != len(names):
                raise ValueError()
            status = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines() if ":" in line)
            uids = {int(value) for value in status["Uid"].split()}
            gids = {int(value) for value in status["Gid"].split()}
            if uids != {EXPECTED_UID} or len(gids) != 1 or fields.get("User") not in ("", "root", "0"):
                raise UpgradeError("service_owner")
            unit_files = tuple(Path(value) for value in
                (fields.get("FragmentPath", "") + " " + fields.get("DropInPaths", "")).split())
            if not unit_files:
                raise ValueError()
            env_raw = fields.get("EnvironmentFiles", "")
            env_matches = re.findall(r"(/[^\s;]+) \(ignore_errors=(?:yes|no)\)", env_raw)
            if env_raw.strip() and not env_matches:
                raise ValueError()
            return ServiceSnapshot(True, pid, argv, Path(fields["WorkingDirectory"]),
                next(iter(uids)), next(iter(gids)), tuple(sorted(pairs)), unit_files,
                execution_fingerprint(fields["ExecStart"]), fields.get("NeedDaemonReload", "no") != "no",
                tuple(Path(value) for value in env_matches))
        except (OSError, ValueError, KeyError, UnicodeError):
            raise UpgradeError("service_metadata") from None

    def stop(self):
        self._run("stop", UNIT, timeout=30)

    def start(self):
        self._run("start", UNIT, timeout=30)

    def reload(self):
        self._run("daemon-reload")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise UpgradeError("health_redirect")


class HTTPSHealth:
    def __init__(self, url: str):
        value = urlsplit(url)
        if (value.scheme != "https" or not value.hostname or value.username or value.password
                or value.path != "/health" or value.query or value.fragment):
            raise UpgradeError("health_url_invalid")
        self.url = url
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())

    def __call__(self):
        with self.opener.open(Request(self.url, method="GET"), timeout=3) as reply:
            raw = reply.read(16385)
            if reply.status != 200 or len(raw) > 16384:
                raise UpgradeError("health_mismatch")
            return json.loads(raw)


def initialize_budget(package: Path, journal: Path):
    """Load only the just-verified staged six-file package in an isolated child.

    No key/config/provider call is needed. All child output is discarded. The
    module writes its durable initialization marker before committing SQL.
    """
    script = ("import pathlib,sys;sys.path.insert(0,sys.argv[1]);"
              "import grocery_capture,grocery_ai_budget;"
              "p=pathlib.Path(sys.argv[2]);"
              "grocery_capture.init_db(p);grocery_ai_budget.initialize_schema(p)")
    try:
        subprocess.run(["/usr/bin/python3", "-I", "-B", "-c", script, str(package), str(journal)],
                       capture_output=True, check=True, timeout=15,
                       env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
    except (OSError, subprocess.SubprocessError):
        raise UpgradeError("budget_initialization_failed") from None


class Upgrader:
    def __init__(self, paths: Paths, controller, health, *, initializer=initialize_budget,
                 helper_sha256=HELPER_SHA256, sleep=time.sleep, monotonic=time.monotonic,
                 lock_timeout=3, drain_timeout=3):
        self.paths, self.controller, self.health = paths, controller, health
        self.initializer, self.helper_sha256 = initializer, helper_sha256
        self.sleep, self.monotonic = sleep, monotonic
        self.lock_timeout = min(max(lock_timeout, 0), 5)
        self.drain_timeout = min(max(drain_timeout, 0), 5)
        self.files = {}
        self.artifacts = {}
        self.backup = paths.backup_root / ("upgrade-" + MANIFEST_SHA256[:16])

    def report(self, status, code="ok", **extra):
        return {"status": status, "code": code, "unit": UNIT,
                "manifest_sha256": MANIFEST_SHA256, "data_restored": False, **extra}

    def _package(self):
        try:
            secure_directory(self.paths.package)
            if {path.name for path in self.paths.package.iterdir()} != ARTIFACT_NAMES | {"manifest.json"}:
                raise UpgradeError("package_invalid")
            raw = regular_bytes(self.paths.package / "manifest.json")
            manifest = json.loads(raw)
            if (digest(raw) != MANIFEST_SHA256 or manifest.get("runtime_file_count") != 6
                    or manifest.get("format_version") != 1 or manifest.get("journal_suffix") != ".grocery.sqlite3"
                    or manifest.get("external_helper_sha256") != HELPER_SHA256
                    or set(manifest.get("artifacts", {})) != ARTIFACT_NAMES):
                raise UpgradeError("package_invalid")
            files = {name: regular_bytes(self.paths.package / name) for name in ARTIFACT_NAMES}
            for name, value in files.items():
                if digest(value) != manifest["artifacts"][name]:
                    raise UpgradeError("package_invalid")
                compile(value, name, "exec")
            self.files, self.artifacts = files, manifest["artifacts"]
        except (OSError, ValueError, TypeError, SyntaxError, UpgradeError):
            raise UpgradeError("package_invalid") from None

    def _health(self):
        for attempt in range(3):
            try:
                if self.health() == EXPECTED_HEALTH:
                    return
            except Exception:
                pass
            if attempt < 2:
                self.sleep(0.25)
        raise UpgradeError("health_mismatch")

    def _dropin(self):
        # A strict character set prevents systemd expansion/newline injection.
        if not re.fullmatch(r"/[A-Za-z0-9_./-]+", str(self.paths.key_file)):
            raise UpgradeError("key_metadata")
        values = {**GROCERY_SETTINGS, "GROCERY_AI_OPENAI_KEY_FILE": str(self.paths.key_file)}
        return ("[Service]\n" + "".join('Environment="' + key + "=" + value + '"\n'
                                             for key, value in sorted(values.items()))).encode()

    def _runtime(self, snapshot, *, target=False):
        if (not snapshot.active or snapshot.pid < 1 or snapshot.uid != EXPECTED_UID
                or not snapshot.working_directory.is_absolute()):
            raise UpgradeError("service_inactive")
        if snapshot.needs_reload:
            raise UpgradeError("unit_configuration_not_loaded")
        secure_directory(snapshot.working_directory)
        candidates = [Path(value) for value in snapshot.argv if Path(value).name == SERVER]
        if len(candidates) != 1:
            raise UpgradeError("service_arguments")
        entry = candidates[0]
        secure_directory(entry.parent)
        environment = dict(snapshot.environment)
        grocery_env = {key: value for key, value in environment.items() if key.startswith("GROCERY_AI_")}
        wanted = {**GROCERY_SETTINGS, "GROCERY_AI_OPENAI_KEY_FILE": str(self.paths.key_file)} if target else {}
        if grocery_env != wanted:
            raise UpgradeError("grocery_environment")
        if (option(snapshot.argv, "--host") != "127.0.0.1" or option(snapshot.argv, "--port") != "8650"
                or option(snapshot.argv, "--write-domains", environment.get("MOBILE_CAPTURE_WRITE_DOMAINS")) != DOMAIN
                or not (snapshot.argv.count("--write-enabled") == 1 or
                        environment.get("MOBILE_CAPTURE_WRITE_ENABLED", "").lower() in {"1", "true", "yes", "on"})):
            raise UpgradeError("service_arguments")
        ledger_value = option(snapshot.argv, "--ledger")
        helper_value = option(snapshot.argv, "--aura-helper", environment.get("MOBILE_CAPTURE_AURA_HELPER",
                                     "/root/.hermes/scripts/apple_watch_capture.py"))
        secret_value = option(snapshot.argv, "--secret-file", environment.get("MOBILE_CAPTURE_SECRET_FILE",
                                     "/root/.hermes/mobile-capture-v1-secret.txt"))
        if not ledger_value:
            raise UpgradeError("service_arguments")
        ledger, helper, secret = Path(ledger_value), Path(helper_value), Path(secret_value)
        for path in (ledger, helper, secret, self.paths.key_file):
            secure_directory(path.parent)
        expected = self.artifacts if target else BASELINE_ARTIFACTS
        file_metadata = []
        for name, expected_hash in expected.items():
            path = entry.parent / name
            info = root_file(path)
            if digest(regular_bytes(path)) != expected_hash:
                raise UpgradeError("runtime_drift")
            file_metadata.append((name, info))
        if not target:
            for name in ARTIFACT_NAMES - BASELINE_ARTIFACTS.keys():
                if (entry.parent / name).exists() or (entry.parent / name).is_symlink():
                    raise UpgradeError("unexpected_runtime")
        root_file(helper)
        if digest(regular_bytes(helper)) != self.helper_sha256:
            raise UpgradeError("helper_drift")
        key_info = private_file(self.paths.key_file, snapshot.uid, key=True)
        secret_info = private_file(secret, snapshot.uid)
        parent_info = root_file(ledger)
        journal = Path(str(ledger) + ".grocery.sqlite3")
        journal_info = root_file(journal) if journal.exists() or journal.is_symlink() else None
        configs = []
        if not snapshot.unit_files:
            raise UpgradeError("service_metadata")
        if target and snapshot.unit_files.count(self.paths.dropin) != 1:
            raise UpgradeError("grocery_dropin")
        if self.paths.dropin.parent.exists():
            secure_directory(self.paths.dropin.parent)
            for path in self.paths.dropin.parent.glob("*.conf"):
                if path not in snapshot.unit_files:
                    raise UpgradeError("unit_configuration_not_loaded")
        for path in (*snapshot.unit_files, *snapshot.environment_files):
            secure_directory(path.parent)
            if path == self.paths.dropin and target:
                info = root_file(path)
                if info[2:5] != (EXPECTED_UID, snapshot.gid, 0o644) or regular_bytes(path) != self._dropin():
                    raise UpgradeError("grocery_dropin")
                continue
            info = root_file(path)
            raw = regular_bytes(path)
            if b"GROCERY_AI_" in raw:
                raise UpgradeError("grocery_dropin")
            configs.append((*info, digest(raw)))
        if not target and (self.paths.dropin.exists() or self.paths.dropin.is_symlink()):
            raise UpgradeError("grocery_dropin")
        return Runtime(snapshot, entry, ledger, helper, secret, tuple(sorted(file_metadata)),
            parent_info[:2], journal_info[:2] if journal_info else None, key_info, secret_info, tuple(configs))

    def _state(self, runtime, parent=None):
        own = parent is None
        try:
            if own:
                parent = readonly_db(runtime.ledger)
            quick_check(parent)
            if parent.execute("SELECT COUNT(*) FROM mobile_capture_ledger WHERE status IN ('processing','grocery_pending')").fetchone()[0]:
                raise UpgradeError("pending_state")
            result = {"journal_present": runtime.journal.exists(), "batch_count": 0,
                      "operation_count": 0, "budget_present": False}
            if not runtime.journal.exists():
                if runtime.marker.exists() or runtime.marker.is_symlink():
                    raise UpgradeError("budget_state_invalid")
                return result
            with closing(readonly_db(runtime.journal)) as conn:
                quick_check(conn)
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                allowed = {"grocery_batches", "grocery_operations", "grocery_ai_budget_state", "grocery_ai_budget_requests"}
                if "grocery_batches" not in tables or not tables <= allowed:
                    raise UpgradeError("journal_incompatible")
                for table in tables:
                    if {row[1] for row in conn.execute("PRAGMA table_info(" + table + ")")} != JOURNAL_COLUMNS[table]:
                        raise UpgradeError("journal_incompatible")
                if conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type IN ('trigger','view')").fetchone()[0]:
                    raise UpgradeError("journal_incompatible")
                budget = tables & {"grocery_ai_budget_state", "grocery_ai_budget_requests"}
                if budget and (len(budget) != 2 or not runtime.marker.exists()):
                    raise UpgradeError("budget_state_invalid")
                if runtime.marker.exists() or runtime.marker.is_symlink():
                    root_file(runtime.marker)
                    if len(budget) != 2:
                        raise UpgradeError("budget_state_invalid")
                for plan_json, status in conn.execute("SELECT plan_json,status FROM grocery_batches"):
                    plan = json.loads(plan_json)
                    if not isinstance(plan, dict) or type(plan.get("version")) is not int or plan["version"] not in {1, 2}:
                        raise UpgradeError("journal_incompatible")
                    if status not in {"committed", "no_write"}:
                        if not (status == "preflight" and plan == {"version": 2, "phase": "preflight"}):
                            raise UpgradeError("pending_state")
                    if plan["version"] == 2 and plan != {"version": 2, "phase": "preflight"} and not budget:
                        raise UpgradeError("unaccounted_ai_history")
                    result["batch_count"] += 1
                if "grocery_operations" in tables:
                    if conn.execute("SELECT COUNT(*) FROM grocery_operations WHERE status IS NULL OR status NOT IN ('committed','rejected')").fetchone()[0]:
                        raise UpgradeError("pending_state")
                    result["operation_count"] = conn.execute("SELECT COUNT(*) FROM grocery_operations").fetchone()[0]
                if budget and conn.execute("SELECT COUNT(*) FROM grocery_ai_budget_requests WHERE status IS NULL OR status NOT IN ('reserved','settled')").fetchone()[0]:
                    raise UpgradeError("budget_state_invalid")
                result["budget_present"] = bool(budget)
            return result
        except (sqlite3.Error, ValueError, TypeError):
            raise UpgradeError("database_invalid") from None
        finally:
            if own and parent is not None:
                parent.close()

    def _inspect(self, health=True):
        self._package()
        self._dropin()
        if not self.paths.backup_root.is_absolute() or not self.paths.lock_file.is_absolute():
            raise UpgradeError("invalid_path")
        for path in (self.paths.backup_root, self.paths.lock_file):
            if path.resolve(strict=False) != path:
                raise UpgradeError("invalid_path")
        if self.paths.backup_root.exists():
            secure_directory(self.paths.backup_root)
        else:
            secure_directory(self.paths.backup_root.parent)
        if self.paths.lock_file.parent != self.paths.backup_root:
            secure_directory(self.paths.lock_file.parent)
        if self.backup.exists() or self.backup.is_symlink():
            raise UpgradeError("backup_exists")
        runtime = self._runtime(self.controller.snapshot())
        state = self._state(runtime)
        secure_directory(self.paths.dropin_root)
        if self.paths.dropin.parent.exists():
            secure_directory(self.paths.dropin.parent)
        if health:
            self._health()
        return runtime, state

    def preflight(self):
        _, state = self._inspect()
        return self.report("preflight_ok", readonly=True, **state)

    @contextmanager
    def _lock(self):
        self.paths.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        secure_directory(self.paths.backup_root)
        secure_directory(self.paths.lock_file.parent)
        descriptor = os.open(self.paths.lock_file, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != EXPECTED_UID or stat.S_IMODE(info.st_mode) != 0o600:
                raise UpgradeError("unsafe_metadata")
            deadline = self.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if self.monotonic() >= deadline:
                        raise UpgradeError("deployment_busy") from None
                    self.sleep(0.05)
            yield
        finally:
            os.close(descriptor)

    def _drain(self, runtime):
        conn = None
        try:
            conn = sqlite3.connect("file:" + quote(str(runtime.ledger), safe="/") + "?mode=rw", uri=True,
                                   timeout=self.drain_timeout, isolation_level=None)
            conn.execute("BEGIN IMMEDIATE")
            self._state(runtime, conn)
            return conn
        except sqlite3.Error:
            if conn:
                conn.close()
            raise UpgradeError("drain_busy") from None
        except Exception:
            if conn:
                conn.close()
            raise

    def _off(self):
        snapshot = self.controller.snapshot()
        if snapshot.active or snapshot.pid:
            raise UpgradeError("stop_failed")

    def _write(self, path, raw, owner_mode=None):
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            if owner_mode:
                os.fchown(stream.fileno(), owner_mode[0], owner_mode[1])
                os.fchmod(stream.fileno(), owner_mode[2])
            stream.flush()
            os.fsync(stream.fileno())
        if regular_bytes(path) != raw:
            raise UpgradeError("readback_failed")

    def _backup_db(self, source_path, backup_path):
        self._write(backup_path, b"")
        source, target = readonly_db(source_path), sqlite3.connect(backup_path)
        try:
            quick_check(source)
            original = logical_hash(source)
            version = source.execute("PRAGMA data_version").fetchone()[0]
            deadline = self.monotonic() + 5
            def progress(*args):
                if self.monotonic() > deadline:
                    raise UpgradeError("backup_failed")
            source.backup(target, pages=128, sleep=0.01, progress=progress)
            quick_check(target)
            if (logical_hash(target) != original or logical_hash(source) != original
                    or source.execute("PRAGMA data_version").fetchone()[0] != version):
                raise UpgradeError("backup_failed")
        finally:
            source.close()
            target.close()
        # Reopen and verify the actual persisted backup before replacing code.
        with closing(readonly_db(backup_path)) as verified:
            quick_check(verified)
            if logical_hash(verified) != original:
                raise UpgradeError("backup_failed")
        with backup_path.open("rb") as stream:
            os.fsync(stream.fileno())
        return digest(regular_bytes(backup_path))

    def _backup(self, runtime):
        self.backup.mkdir(mode=0o700, exist_ok=False)
        records = {}
        for name, expected in BASELINE_ARTIFACTS.items():
            original = runtime.entrypoint.parent / name
            self._write(self.backup / ("previous-" + name), regular_bytes(original))
            if digest(regular_bytes(self.backup / ("previous-" + name))) != expected:
                raise UpgradeError("backup_failed")
            records[name] = {"sha256": expected, "metadata": metadata(original)}
        for alias, path in (("parent", runtime.ledger), ("journal", runtime.journal)):
            records[alias] = {"present": path.exists()}
            if path.exists():
                records[alias].update(metadata=metadata(path), sha256=self._backup_db(path, self.backup / (alias + ".sqlite3")))
        records["budget_marker"] = {"present": runtime.marker.exists()}
        if runtime.marker.exists():
            self._write(self.backup / "budget-initialized.marker", regular_bytes(runtime.marker))
            records["budget_marker"].update(metadata=metadata(runtime.marker), sha256=digest(regular_bytes(runtime.marker)))
        records["config_metadata"] = runtime.config_metadata
        records["key_metadata"] = runtime.key_metadata
        records["secret_metadata"] = runtime.secret_metadata
        self._write(self.backup / "backup.json", (json.dumps({"manifest_sha256": MANIFEST_SHA256,
            "data_restore_permitted": False, "records": records}, sort_keys=True) + "\n").encode())
        sync_directory(self.backup)

    def _identity(self, runtime):
        if metadata(runtime.ledger)[:2] != runtime.ledger_identity:
            raise UpgradeError("data_identity_changed")
        if runtime.journal_identity is not None and metadata(runtime.journal)[:2] != runtime.journal_identity:
            raise UpgradeError("data_identity_changed")
        if private_file(self.paths.key_file, runtime.snapshot.uid, key=True) != runtime.key_metadata:
            raise UpgradeError("key_metadata")
        if private_file(runtime.secret, runtime.snapshot.uid) != runtime.secret_metadata:
            raise UpgradeError("secret_metadata")
        if digest(regular_bytes(runtime.helper)) != self.helper_sha256:
            raise UpgradeError("helper_drift")

    def _replace(self, source, destination):
        os.replace(source, destination)
        sync_directory(destination.parent)

    def _verify_started(self, runtime, *, target):
        # Type=simple can report running before exec has populated /proc. Retry
        # only those startup observations; every strict comparison stays below.
        deadline = self.monotonic() + 4
        while True:
            try:
                current = self._runtime(self.controller.snapshot(), target=target)
                break
            except UpgradeError as error:
                if error.code not in {"service_inactive", "service_arguments", "service_metadata"}:
                    raise
                remaining = deadline - self.monotonic()
                if remaining <= 0:
                    raise
                self.sleep(min(0.1, remaining))
        if (current.snapshot.argv != runtime.snapshot.argv
                or current.snapshot.working_directory != runtime.snapshot.working_directory
                or current.snapshot.execution_fingerprint != runtime.snapshot.execution_fingerprint
                or current.snapshot.uid != runtime.snapshot.uid or current.snapshot.gid != runtime.snapshot.gid
                or current.ledger != runtime.ledger or current.helper != runtime.helper or current.secret != runtime.secret
                or current.config_metadata != runtime.config_metadata):
            raise UpgradeError("service_changed")
        preserved = lambda env: tuple((key, value) for key, value in env
            if key not in VOLATILE_ENV and not key.startswith("GROCERY_AI_"))
        if preserved(current.snapshot.environment) != preserved(runtime.snapshot.environment):
            raise UpgradeError("environment_changed")
        self._identity(runtime)
        mode = dict(runtime.file_metadata)[SERVER][2:5]
        expected = self.artifacts if target else BASELINE_ARTIFACTS
        for name in expected:
            actual = root_file(runtime.entrypoint.parent / name)
            wanted = mode if target else dict(runtime.file_metadata)[name][2:5]
            if actual[2:5] != wanted:
                raise UpgradeError("runtime_metadata_drift")
        self._health()

    def _recover(self, runtime, initialization_started, cause):
        if initialization_started:
            try:
                self.controller.stop()
                self._off()
            except Exception:
                return self.report("attention", "stop_unconfirmed", cause=cause, compatible_runtime_required=True)
            return self.report("attention", "compatible_runtime_required", cause=cause,
                               unit_stopped=True, budget_marker_retained=runtime.marker.exists())
        try:
            # Code/config were not touched before initialization begins.
            for name, expected in BASELINE_ARTIFACTS.items():
                if digest(regular_bytes(runtime.entrypoint.parent / name)) != expected:
                    raise UpgradeError("runtime_drift")
            self._identity(runtime)
            self._state(runtime)
            if not self.controller.snapshot().active:
                self.controller.start()
            self._verify_started(runtime, target=False)
            return self.report("baseline_resumed", cause, backup_retained=self.backup.exists())
        except Exception:
            try:
                self.controller.stop()
                self._off()
            except Exception:
                return self.report("attention", "stop_unconfirmed", cause=cause)
            return self.report("attention", "recovery_failed", cause=cause, unit_stopped=True)

    def apply(self, *, initialize=False):
        if not initialize:
            raise UpgradeError("explicit_budget_initialization_required")
        original, _ = self._inspect()
        with self._lock():
            runtime, _ = self._inspect(health=False)
            if runtime != original:
                raise UpgradeError("service_changed")
            guard = self._drain(runtime)
            stop_attempted = initialization_started = False
            try:
                if self._runtime(self.controller.snapshot()) != runtime:
                    raise UpgradeError("service_changed")
                self._identity(runtime)
                self._state(runtime, guard)
                stop_attempted = True
                self.controller.stop()
                self._off()
                guard.rollback()
                guard.close()
                guard = None
                self._identity(runtime)
                self._state(runtime)
                # Recheck all four installed versions after the stop.
                for name, expected in BASELINE_ARTIFACTS.items():
                    if digest(regular_bytes(runtime.entrypoint.parent / name)) != expected:
                        raise UpgradeError("runtime_drift")
                self._backup(runtime)
                owner_mode = dict(runtime.file_metadata)[SERVER][2:5]
                with tempfile.TemporaryDirectory(prefix=".grocery-ai-stage-", dir=runtime.entrypoint.parent) as folder:
                    staging = Path(folder)
                    for name, raw in self.files.items():
                        self._write(staging / name, raw, owner_mode)
                    initialization_started = True
                    self.initializer(staging, runtime.journal)
                    # Preserve the inode of existing data, and never repair/remove
                    # an incomplete marker if the initializer fails.
                    self._identity(runtime)
                    self._state(runtime)
                    if not runtime.marker.exists():
                        raise UpgradeError("budget_initialization_failed")
                    for name in (*sorted(ARTIFACT_NAMES - {SERVER}), SERVER):
                        self._replace(staging / name, runtime.entrypoint.parent / name)
                for name, expected in self.artifacts.items():
                    if digest(regular_bytes(runtime.entrypoint.parent / name)) != expected:
                        raise UpgradeError("runtime_drift")
                self.paths.dropin.parent.mkdir(mode=0o755, exist_ok=True)
                secure_directory(self.paths.dropin.parent)
                sync_directory(self.paths.dropin_root)
                self._write(self.paths.dropin, self._dropin(), (EXPECTED_UID, runtime.snapshot.gid, 0o644))
                sync_directory(self.paths.dropin.parent)
                self.controller.reload()
                self.controller.start()
                self._verify_started(runtime, target=True)
                return self.report("installed", artifact_sha256=self.artifacts, backup_verified=True,
                                   budget_initialized=True, arguments_unchanged=True, health_profile_unchanged=True)
            except Exception as error:
                if guard is not None:
                    guard.rollback()
                    guard.close()
                    guard = None
                if not stop_attempted:
                    raise
                return self._recover(runtime, initialization_started,
                    error.code if isinstance(error, UpgradeError) else "upgrade_failed")
            finally:
                if guard is not None:
                    guard.rollback()
                    guard.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--initialize-budget", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        paths = Paths(args.package, args.backup_root, args.lock_file or args.backup_root / ".grocery-ai-upgrade.lock", args.key_file)
        upgrade = Upgrader(paths, SystemdController(), HTTPSHealth(args.health_url))
        report = upgrade.apply(initialize=args.initialize_budget) if args.apply else upgrade.preflight()
    except UpgradeError as error:
        report = {"status": "blocked", "code": error.code, "unit": UNIT, "data_restored": False}
    except Exception:
        report = {"status": "blocked", "code": "upgrade_failed", "unit": UNIT, "data_restored": False}
    print(json.dumps(report, sort_keys=True))
    if report["status"] not in {"preflight_ok", "installed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
