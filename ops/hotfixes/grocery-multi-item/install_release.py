#!/usr/bin/env python3
"""Initial activation of the exact sealed grocery-only historical release.

Default: read-only preflight. --apply alone permits the bounded unit stop,
exclusive code/data backups and four-file replacement. Never restore databases.
This standalone installer imports no application/helper code and needs no Git.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
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
MANIFEST_SHA256 = "9d34903345cac05cf695bc2839640e9aa9c773dd7a7261e6d5de0d7159a21f0e"
BASELINE_SHA256 = "f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38"
HELPER_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
DEFAULT_HELPER = Path("/root/.hermes/scripts/apple_watch_capture.py")
SERVER = "mobile_capture_staging_server.py"
ARTIFACTS = {
    SERVER: "309602498c2a9ccf3186e7c336ed9ff5cc2fe3b693866fc1fbfdb18c1b0d5100",
    "grocery_capture.py": "30638a74cc96c4ab71a7d3df8e89552ca1180798715fe6c04be33b4dd58ef943",
    "grocery_helper_adapter.py": "2894998b54a22bbc3b398a801f8cf5a080a22bcd5fec51678ccd4d8f7baacbde",
    "grocery_item_parser.py": "bf0097c86e43ca8b3349a96bc2ebf65f8d5d2e93eca4e9e3856ff6a2d134fdc7",
}
EXPECTED_HEALTH = {"mode": "allowlisted-write", "service": "mobile-capture-v1-staging",
                   "status": "ok", "write_domains": [DOMAIN]}


class InstallError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)  # Never interpolate private paths, arguments or reply bodies.


@dataclass(frozen=True)
class Paths:
    package: Path
    backup_root: Path
    lock_file: Path


@dataclass(frozen=True)
class ServiceSnapshot:
    active: bool
    pid: int
    argv: tuple[str, ...]
    working_directory: Path


@dataclass(frozen=True)
class Runtime:
    snapshot: ServiceSnapshot
    entrypoint: Path
    ledger: Path
    helper: Path
    helper_env: str | None
    ledger_identity: tuple[int, int]
    metadata: tuple[int, int, int]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def regular_bytes(path: Path) -> bytes:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode) or path.is_symlink():
                raise InstallError("invalid_path")
            return handle.read()
    except InstallError:
        raise
    except OSError:
        raise InstallError("invalid_path") from None


def select_helper_env(raw: bytes) -> str | None:
    values = [part.partition(b"=")[2] for part in raw.split(b"\0")
              if part.partition(b"=")[0] == b"MOBILE_CAPTURE_AURA_HELPER"]
    if len(values) > 1:
        raise InstallError("helper_ambiguous")
    if not values:
        return None
    try:
        value = values[0].decode("utf-8")
    except UnicodeDecodeError:
        raise InstallError("helper_ambiguous") from None
    if not value:
        raise InstallError("helper_ambiguous")
    return value


def option(argv: tuple[str, ...], name: str, *, required: bool = True) -> str | None:
    values = []
    for index, arg in enumerate(argv):
        if arg == name:
            if index + 1 >= len(argv) or argv[index + 1].startswith("--"):
                raise InstallError("service_arguments")
            values.append(argv[index + 1])
        elif arg.startswith(name + "="):
            values.append(arg.partition("=")[2])
    if len(values) > 1 or (required and not values) or (values and not values[0]):
        raise InstallError("service_arguments")
    return values[0] if values else None


def readonly_db(path: Path):
    conn = sqlite3.connect("file:" + quote(str(path), safe="/") + "?mode=ro", uri=True, timeout=5)
    conn.execute("PRAGMA query_only=ON")
    return conn


def quick_check(conn) -> None:
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise InstallError("ledger_invalid")


def pending_count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM mobile_capture_ledger WHERE domain=? AND status IN ('processing','grocery_pending')",
                        (DOMAIN,)).fetchone()[0]


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class SystemdController:
    def snapshot(self) -> ServiceSnapshot:
        try:
            result = subprocess.run(["systemctl", "show", UNIT,
                "--property=MainPID,ActiveState,SubState,WorkingDirectory", "--no-pager"],
                capture_output=True, text=True, check=True, timeout=5)
            fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
            pid = int(fields.get("MainPID", "0"))
            active = fields.get("ActiveState") == "active" and fields.get("SubState") == "running"
            argv = tuple(part.decode("utf-8") for part in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if part) if active and pid > 0 else ()
            return ServiceSnapshot(active, pid, argv, Path(fields.get("WorkingDirectory") or "/"))
        except (OSError, ValueError, subprocess.SubprocessError):
            raise InstallError("service_metadata") from None

    def helper_environment(self, pid: int) -> str | None:
        try:
            # Select exactly this setting; no environment values enter reports.
            return select_helper_env(Path(f"/proc/{pid}/environ").read_bytes())
        except OSError:
            raise InstallError("service_metadata") from None

    def stop(self) -> None:
        try:
            subprocess.run(["systemctl", "stop", UNIT], capture_output=True, check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise InstallError("stop_failed") from None

    def start(self) -> None:
        try:
            subprocess.run(["systemctl", "start", UNIT], capture_output=True, check=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            raise InstallError("start_failed") from None


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        raise InstallError("health_redirect")


class HTTPSHealth:
    def __init__(self, url: str):
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path != "/health"):
            raise InstallError("health_url_invalid")
        self.url = url
        # Do not read proxy settings/credentials. TLS uses the normal CA trust.
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())

    def __call__(self) -> dict:
        with self.opener.open(Request(self.url, method="GET"), timeout=3) as reply:
            if reply.status != 200:
                raise InstallError("health_mismatch")
            return json.loads(reply.read(16385))


class Installer:
    def __init__(self, paths: Paths, controller, health, *, helper_sha256: str = HELPER_SHA256,
                 lock_timeout: float = 5.0, drain_timeout: float = 5.0):
        self.paths, self.controller, self.health = paths, controller, health
        self.helper_sha256 = helper_sha256
        self.lock_timeout = min(max(lock_timeout, 0), 5.0)
        self.drain_timeout = min(max(drain_timeout, 0), 5.0)
        self.backup = paths.backup_root / ("release-" + MANIFEST_SHA256[:16])
        self.package_bytes: dict[str, bytes] = {}

    def _report(self, status: str, code: str = "ok", **extra) -> dict:
        return {"status": status, "code": code, "unit": UNIT,
                "manifest_sha256": MANIFEST_SHA256, "data_restored": False, **extra}

    def _verify_package(self) -> None:
        package = self.paths.package
        if not package.is_absolute() or package.is_symlink() or not package.is_dir():
            raise InstallError("package_invalid")
        try:
            if sorted(path.name for path in package.iterdir()) != sorted((*ARTIFACTS, "manifest.json")):
                raise InstallError("package_invalid")
            raw = regular_bytes(package / "manifest.json")
            if digest(raw) != MANIFEST_SHA256:
                raise InstallError("package_invalid")
            manifest = json.loads(raw)
            if manifest["artifacts"] != ARTIFACTS or manifest["external_helper_sha256"] != HELPER_SHA256:
                raise InstallError("package_invalid")
            files = {name: regular_bytes(package / name) for name in ARTIFACTS}
            for name, raw in files.items():
                if digest(raw) != ARTIFACTS[name]:
                    raise InstallError("package_invalid")
                compile(raw, name, "exec")
            self.package_bytes = files
        except (OSError, ValueError, KeyError, SyntaxError, InstallError):
            raise InstallError("package_invalid") from None

    def _runtime(self, snapshot: ServiceSnapshot, expected_source: str) -> Runtime:
        if not snapshot.active or snapshot.pid <= 0 or not snapshot.working_directory.is_absolute():
            raise InstallError("service_inactive")
        candidates = [Path(arg) for arg in snapshot.argv if Path(arg).name == SERVER]
        if len(candidates) != 1 or not candidates[0].is_absolute():
            raise InstallError("service_arguments")
        source = candidates[0]
        if digest(regular_bytes(source)) != expected_source:
            raise InstallError("baseline_drift")
        if option(snapshot.argv, "--host") != "127.0.0.1" or option(snapshot.argv, "--port") != "8650":
            raise InstallError("service_arguments")
        ledger = Path(option(snapshot.argv, "--ledger"))
        # Source is pinned before selecting the one allowed environment setting.
        explicit = option(snapshot.argv, "--aura-helper", required=False)
        environment = None if explicit else getattr(self.controller, "helper_environment", lambda pid: None)(snapshot.pid)
        helper = Path(explicit or environment or DEFAULT_HELPER)
        if not ledger.is_absolute() or not helper.is_absolute():
            raise InstallError("service_arguments")
        if digest(regular_bytes(helper)) != self.helper_sha256:
            raise InstallError("helper_drift")
        if ledger.is_symlink() or not ledger.is_file():
            raise InstallError("invalid_path")
        metadata = source.stat()
        if not stat.S_IMODE(metadata.st_mode) & 0o444:
            raise InstallError("runtime_unreadable")
        ledger_stat = ledger.stat()
        return Runtime(snapshot, source, ledger, helper, environment,
                       (ledger_stat.st_dev, ledger_stat.st_ino),
                       (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)))

    def _wait_health(self) -> None:
        # Three <=3s HTTPS attempts and two 0.25s pauses stay below15s total.
        for attempt in range(3):
            try:
                if self.health() == EXPECTED_HEALTH:
                    return
            except Exception:
                pass
            if attempt < 2:
                time.sleep(0.25)
        raise InstallError("health_mismatch")

    def _initial_state(self, runtime: Runtime, conn=None) -> None:
        journal = Path(str(runtime.ledger) + ".grocery.sqlite3")
        if journal.exists() or journal.is_symlink():
            raise InstallError("journal_present")
        for name in ARTIFACTS:
            sibling = runtime.entrypoint.parent / name
            if name != SERVER and (sibling.exists() or sibling.is_symlink()):
                raise InstallError("runtime_present")
        if self.backup.exists() or self.backup.is_symlink():
            raise InstallError("backup_exists")
        own = conn is None
        try:
            if own:
                conn = readonly_db(runtime.ledger)
            quick_check(conn)
            if pending_count(conn):
                raise InstallError("pending_state")
        except sqlite3.Error:
            raise InstallError("ledger_invalid") from None
        finally:
            if own and conn is not None:
                conn.close()

    def _inspect(self, *, check_health: bool = True) -> Runtime:
        if not self.paths.backup_root.is_absolute() or not self.paths.lock_file.is_absolute():
            raise InstallError("invalid_path")
        self._verify_package()
        runtime = self._runtime(self.controller.snapshot(), BASELINE_SHA256)
        self._initial_state(runtime)
        if check_health:
            self._wait_health()
        return runtime

    def preflight(self) -> dict:
        self._inspect()
        return self._report("preflight_ok", journal_absent=True, runtime_siblings_absent=True)

    @contextmanager
    def _deployment_lock(self):
        self.paths.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.paths.backup_root.is_symlink() or self.paths.lock_file.is_symlink():
            raise InstallError("invalid_path")
        descriptor = os.open(self.paths.lock_file, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise InstallError("invalid_path")
            deadline = time.monotonic() + self.lock_timeout
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise InstallError("deployment_busy") from None
                    time.sleep(0.05)
            yield
        finally:
            os.close(descriptor)

    def _drain(self, runtime: Runtime):
        conn = None
        try:
            conn = sqlite3.connect("file:" + quote(str(runtime.ledger), safe="/") + "?mode=rw", uri=True,
                                   timeout=self.drain_timeout, isolation_level=None)
            conn.execute("BEGIN IMMEDIATE")
            return conn
        except sqlite3.Error:
            if conn:
                conn.close()
            raise InstallError("drain_busy") from None

    def _assert_same_active(self, original: Runtime, guard) -> None:
        snapshot = self.controller.snapshot()
        if snapshot != original.snapshot:
            raise InstallError("service_changed")
        current = self._runtime(snapshot, BASELINE_SHA256)
        if (current.ledger_identity != original.ledger_identity or current.helper != original.helper
                or current.helper_env != original.helper_env or current.metadata != original.metadata):
            raise InstallError("service_changed")
        self._initial_state(current, guard)

    def _ensure_off(self) -> None:
        snapshot = self.controller.snapshot()
        if snapshot.active or snapshot.pid:
            raise InstallError("stop_failed")

    def _write_exclusive(self, path: Path, raw: bytes, metadata=None) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            if metadata:
                os.fchown(handle.fileno(), metadata[0], metadata[1])
                os.fchmod(handle.fileno(), metadata[2])
            handle.flush()
            os.fsync(handle.fileno())

    def _backup(self, runtime: Runtime) -> None:
        self.backup.mkdir(mode=0o700, exist_ok=False)
        original_code = self.backup / "previous-mobile_capture_staging_server.py"
        self._write_exclusive(original_code, regular_bytes(runtime.entrypoint), runtime.metadata)
        if digest(regular_bytes(original_code)) != BASELINE_SHA256:
            raise InstallError("backup_failed")
        source_hash = digest(regular_bytes(runtime.ledger))
        backup_path = self.backup / "mobile-ledger.sqlite3"
        self._write_exclusive(backup_path, b"")
        # The service is off and the drain reservation is already released.
        # This source is a separate read-only connection, never the guard.
        source, target = readonly_db(runtime.ledger), sqlite3.connect(backup_path)
        try:
            deadline = time.monotonic() + 5.0
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise InstallError("backup_failed")
            source.backup(target, pages=128, sleep=0.01, progress=progress)
            quick_check(target)
        finally:
            source.close()
            target.close()
        if source_hash != digest(regular_bytes(runtime.ledger)):
            raise InstallError("backup_failed")
        record = {"manifest_sha256": MANIFEST_SHA256, "source_sha256": BASELINE_SHA256,
                  "mobile_ledger_source_sha256": source_hash,
                  "mobile_ledger_backup_sha256": digest(regular_bytes(backup_path)),
                  "grocery_journal": "absent", "data_restore_permitted": False}
        self._write_exclusive(self.backup / "backup.json", (json.dumps(record, sort_keys=True) + "\n").encode())
        sync_directory(self.backup)

    def _replace_file(self, staged: Path, destination: Path, metadata) -> None:
        os.replace(staged, destination)
        sync_directory(destination.parent)

    def _target_files_match(self, runtime: Runtime) -> bool:
        try:
            return all(digest(regular_bytes(runtime.entrypoint.parent / name)) == expected
                       for name, expected in ARTIFACTS.items())
        except InstallError:
            return False

    def _rollback_allowed(self, runtime: Runtime) -> bool:
        try:
            conn = readonly_db(runtime.ledger)
            try:
                quick_check(conn)
                if pending_count(conn):
                    return False
            finally:
                conn.close()
            journal = Path(str(runtime.ledger) + ".grocery.sqlite3")
            if not journal.exists():
                return not journal.is_symlink()
            if not regular_bytes(journal).startswith(b"SQLite format 3\0"):
                return False
            conn = readonly_db(journal)
            try:
                quick_check(conn)
                known = {
                    "grocery_batches": (
                        ("request_id", "TEXT", 0, None, 1), ("payload_hash", "TEXT", 1, None, 0),
                        ("plan_json", "TEXT", 1, None, 0), ("plan_hash", "TEXT", 1, None, 0),
                        ("response_json", "TEXT", 0, None, 0), ("status", "TEXT", 1, None, 0),
                        ("created_at", "TEXT", 1, "CURRENT_TIMESTAMP", 0),
                        ("updated_at", "TEXT", 1, "CURRENT_TIMESTAMP", 0),
                    ),
                    "grocery_operations": (
                        ("operation_id", "TEXT", 0, None, 1), ("plan_hash", "TEXT", 1, None, 0),
                        ("plan_json", "TEXT", 1, None, 0), ("destination", "TEXT", 1, None, 0),
                        ("status", "TEXT", 1, None, 0), ("candidate_id", "TEXT", 0, None, 0),
                        ("receipt_json", "TEXT", 0, None, 0), ("error_code", "TEXT", 0, None, 0),
                    ),
                }
                objects = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
                tables = {name for kind, name, _, _ in objects if kind == "table"}
                if not tables <= set(known):
                    return False
                for kind, name, table, sql in objects:
                    if kind == "table" and name in known:
                        continue
                    if kind == "index" and table in known and name == "sqlite_autoindex_" + table + "_1" and sql is None:
                        continue
                    return False  # Unknown views, triggers, indexes or schema are not empty known state.
                for name in tables:
                    columns = tuple((row[1], row[2].upper(), row[3], row[4], row[5])
                                    for row in conn.execute("PRAGMA table_info(" + name + ")"))
                    if columns != known[name] or conn.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] != 0:
                        return False
                return True
            finally:
                conn.close()
        except (InstallError, OSError, sqlite3.Error):
            return False

    def _verify_started(self, runtime: Runtime, source_hash: str, *, target: bool) -> None:
        current = self._runtime(self.controller.snapshot(), source_hash)
        if (current.snapshot.argv != runtime.snapshot.argv or current.snapshot.working_directory != runtime.snapshot.working_directory
                or current.ledger != runtime.ledger or current.helper != runtime.helper or current.helper_env != runtime.helper_env):
            raise InstallError("service_changed")
        if target:
            if not self._target_files_match(runtime):
                raise InstallError("runtime_drift")
            for name in ARTIFACTS:
                metadata = (runtime.entrypoint.parent / name).stat()
                if (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)) != runtime.metadata:
                    raise InstallError("runtime_metadata_drift")
        self._wait_health()

    def _recover(self, runtime: Runtime, changed: bool, code: str) -> dict:
        try:
            if changed:
                self.controller.stop()
                self._ensure_off()
            if not self._rollback_allowed(runtime):
                if self.controller.snapshot().active:
                    self.controller.stop()
                    self._ensure_off()
                return self._report("attention", "unsafe_rollback", cause=code,
                                    compatible_code_valid=self._target_files_match(runtime))
            if changed:
                for name in ARTIFACTS:
                    destination = runtime.entrypoint.parent / name
                    if name == SERVER:
                        if digest(regular_bytes(destination)) not in {BASELINE_SHA256, ARTIFACTS[SERVER]}:
                            raise InstallError("rollback_code_drift")
                        with tempfile.TemporaryDirectory(prefix=".grocery-restore-", dir=runtime.entrypoint.parent) as temporary:
                            old = Path(temporary) / SERVER
                            self._write_exclusive(old, regular_bytes(self.backup / "previous-mobile_capture_staging_server.py"), runtime.metadata)
                            if digest(regular_bytes(old)) != BASELINE_SHA256:
                                raise InstallError("backup_failed")
                            self._replace_file(old, destination, runtime.metadata)
                    elif destination.exists() or destination.is_symlink():
                        if digest(regular_bytes(destination)) != ARTIFACTS[name]:
                            raise InstallError("rollback_code_drift")
                        destination.unlink()  # Only initially absent files created by this activation.
                sync_directory(runtime.entrypoint.parent)
            if not self.controller.snapshot().active:
                self.controller.start()
            self._verify_started(runtime, BASELINE_SHA256, target=False)
            return self._report("rolled_back", code, backup_retained=self.backup.exists())
        except Exception:
            try:
                self.controller.stop()
            except Exception:
                pass
            return self._report("attention", "recovery_failed", cause=code)

    def apply(self) -> dict:
        original = self._inspect()
        with self._deployment_lock():
            runtime = self._inspect(check_health=False)
            if runtime != original:
                raise InstallError("service_changed")
            guard = self._drain(runtime)
            stop_attempted = changed = False
            try:
                self._assert_same_active(runtime, guard)
                stop_attempted = True
                self.controller.stop()
                self._ensure_off()
                guard.rollback()
                guard.close()
                guard = None
                # Nothing changed while draining/stopping. No backup is attempted
                # on an active guard or while the unit can accept captures.
                if digest(regular_bytes(runtime.entrypoint)) != BASELINE_SHA256 or digest(regular_bytes(runtime.helper)) != self.helper_sha256:
                    raise InstallError("baseline_drift")
                self._initial_state(runtime)
                self._backup(runtime)
                with tempfile.TemporaryDirectory(prefix=".grocery-stage-", dir=runtime.entrypoint.parent) as temporary:
                    staging = Path(temporary)
                    for name, raw in self.package_bytes.items():
                        self._write_exclusive(staging / name, raw, runtime.metadata)
                    for name in (*[name for name in ARTIFACTS if name != SERVER], SERVER):
                        changed = True  # A failure may occur after an atomic replace.
                        self._replace_file(staging / name, runtime.entrypoint.parent / name, runtime.metadata)
                if not self._target_files_match(runtime):
                    raise InstallError("runtime_drift")
                self.controller.start()
                self._verify_started(runtime, ARTIFACTS[SERVER], target=True)
                return self._report("installed", artifact_sha256=ARTIFACTS, backup_verified=True,
                                    arguments_unchanged=True, health_profile_unchanged=True)
            except Exception as error:
                if guard is not None:
                    guard.rollback()
                    guard.close()
                    guard = None
                if not stop_attempted:
                    raise
                code = error.code if isinstance(error, InstallError) else "install_failed"
                return self._recover(runtime, changed, code)
            finally:
                if guard is not None:
                    guard.rollback()
                    guard.close()


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--lock-file", type=Path)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        paths = Paths(args.package, args.backup_root, args.lock_file or args.backup_root / ".grocery-install.lock")
        installer = Installer(paths, SystemdController(), HTTPSHealth(args.health_url))
        report = installer.apply() if args.apply else installer.preflight()
    except InstallError as error:
        report = {"status": "blocked", "code": error.code, "unit": UNIT, "data_restored": False}
    except Exception:
        report = {"status": "blocked", "code": "installer_failed", "unit": UNIT, "data_restored": False}
    print(json.dumps(report, sort_keys=True))
    if report["status"] not in {"preflight_ok", "installed"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
