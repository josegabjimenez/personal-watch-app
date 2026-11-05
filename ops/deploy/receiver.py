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
PACKAGE_REVISION = "1fe67948051f1d25140e13900d51275c498e0770"
MANIFEST_SHA256 = "9d34903345cac05cf695bc2839640e9aa9c773dd7a7261e6d5de0d7159a21f0e"
EXTERNAL_HELPER_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
STATE_COMPATIBILITY = "grocery-batch-v1"
FILENAMES = ("mobile_capture_staging_server.py", "grocery_capture.py",
             "grocery_helper_adapter.py", "grocery_item_parser.py")
ENTRYNAME = FILENAMES[0]
SOURCE_PATHS = {"backend/grocery_capture.py", "backend/grocery_helper_adapter.py", "backend/grocery_item_parser.py",
                "ops/hotfixes/grocery-multi-item/build_release.py", "ops/hotfixes/grocery-multi-item/release_manifest.json",
                "ops/hotfixes/grocery-multi-item/server_overlay.py", "ops/hotfixes/grocery-live-clarification/build_release.py",
                "ops/hotfixes/grocery-live-clarification/legacy-970e14d.patch"}


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
            required = {"service", "entrypoint", "code_dir", "state_dir", "expected_cmdline",
                        "health_urls", "expected_health", "keep", "package_profile"}
            if not isinstance(config, dict) or set(config) != required:
                raise ValueError()
            if config["service"] != SERVICE or type(config["keep"]) is not int or not 2 <= config["keep"] <= 10:
                raise ValueError()
            self._profile(config["package_profile"])
            self.entrypoint, self.code, self.state = (Path(config[key]) for key in ("entrypoint", "code_dir", "state_dir"))
            self.inhibitor = self.code / ".activation-blocked" / ENTRYNAME
            if self.entrypoint.name != ENTRYNAME or not self.entrypoint.is_absolute():
                raise ValueError()
            for path in (self.entrypoint.parent, self.code, self.state):
                if not path.is_absolute() or path.resolve() != path:
                    raise ValueError()
            if self.code.is_relative_to(self.state) or self.state.is_relative_to(self.code) or self.entrypoint.is_relative_to(self.code):
                raise ValueError()
            argv = config["expected_cmdline"]
            if not isinstance(argv, list) or not all(isinstance(x, str) and x and "\0" not in x for x in argv):
                raise ValueError()
            if len(argv) < 6 or not Path(argv[0]).is_absolute() or argv[1] != str(self.entrypoint):
                raise ValueError()
            if argv.count("--ledger") != 1 or argv.count("--aura-helper") != 1:
                raise ValueError()
            self.ledger = Path(argv[argv.index("--ledger") + 1])
            self.helper = Path(argv[argv.index("--aura-helper") + 1])
            self.grocery_journal = Path(str(self.ledger) + ".grocery.sqlite3")
            if not self.ledger.is_absolute() or not self.ledger.is_file() or not self.helper.is_absolute():
                raise ValueError()
            for path in (self.ledger, self.grocery_journal, self.helper):
                if path in {self.entrypoint, self.code, self.state} or path.is_relative_to(self.code) or path.is_relative_to(self.state):
                    raise ValueError()
            if self.state.is_relative_to(self.ledger.parent) or self.code.is_relative_to(self.ledger.parent):
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
            self.code.mkdir(mode=0o755, parents=False, exist_ok=True)
            for path, mode in ((self.state, 0o700), (self.releases, 0o700), (self.code, 0o755)):
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != mode:
                    raise ReleaseError("unsafe_state")
            if os.path.lexists(self.inhibitor.parent):
                raise ReleaseError("unsafe_inhibitor")
        except (KeyError, ValueError, IndexError, TypeError, OSError) as exc:
            raise ReleaseError("invalid_config") from exc

    def _profile(self, profile):
        keys = {"schema", "package_revision", "state_compatibility", "manifest_path",
                "manifest_sha256", "sealed_manifest", "inputs"}
        if not isinstance(profile, dict) or set(profile) != keys or type(profile["schema"]) is not int or profile["schema"] != 1:
            raise ValueError()
        if profile["package_revision"] != PACKAGE_REVISION or profile["manifest_sha256"] != MANIFEST_SHA256:
            raise ValueError()
        if profile["state_compatibility"] != STATE_COMPATIBILITY or profile["manifest_path"] != "ops/hotfixes/grocery-multi-item/release_manifest.json":
            raise ValueError()
        self.manifest = profile["sealed_manifest"]
        self.manifest_bytes = (json.dumps(self.manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
        if digest(self.manifest_bytes) != MANIFEST_SHA256 or set(self.manifest["artifacts"]) != set(FILENAMES):
            raise ValueError()
        if self.manifest["format_version"] != 1 or self.manifest["runtime_file_count"] != 4 or self.manifest["journal_suffix"] != ".grocery.sqlite3":
            raise ValueError()
        if self.manifest["external_helper_sha256"] != EXTERNAL_HELPER_SHA256:
            raise ValueError()
        if not HEX64.fullmatch(self.manifest["baseline_sha256"]) or any(not HEX64.fullmatch(x) for x in self.manifest["artifacts"].values()):
            raise ValueError()
        if not isinstance(profile["inputs"], dict) or set(profile["inputs"]) != SOURCE_PATHS or any(not isinstance(x, str) or not HEX64.fullmatch(x) for x in profile["inputs"].values()):
            raise ValueError()
        self.manifest_b64 = base64.b64encode(self.manifest_bytes).decode("ascii")

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

    def _code_file(self, path):
        info = regular(path)
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if not data or len(data) > MAX_FILE:
            raise ReleaseError("code_too_large")
        return {"sha256": digest(data), "content_b64": base64.b64encode(data).decode("ascii")}, info

    def _external_helper(self):
        value, _ = self._code_file(self.helper)
        if value["sha256"] != EXTERNAL_HELPER_SHA256:
            raise ReleaseError("external_helper_mismatch")

    def _bundle(self, bundle):
        if not isinstance(bundle, dict) or set(bundle) != {"schema", "revision", "manifest_b64", "files"}:
            raise ReleaseError("invalid_bundle")
        if type(bundle["schema"]) is not int or bundle["schema"] != 2 or bundle["revision"] != PACKAGE_REVISION or bundle["manifest_b64"] != self.manifest_b64:
            raise ReleaseError("invalid_bundle")
        if not isinstance(bundle["files"], dict) or set(bundle["files"]) != set(FILENAMES):
            raise ReleaseError("invalid_bundle")
        for name, value in bundle["files"].items():
            if not isinstance(value, dict) or set(value) != {"sha256", "content_b64"}:
                raise ReleaseError("invalid_bundle")
            try:
                data = base64.b64decode(value["content_b64"], validate=True)
                if not data or len(data) > MAX_FILE or digest(data) != value["sha256"] or value["sha256"] != self.manifest["artifacts"][name]:
                    raise ValueError()
                if base64.b64encode(data).decode("ascii") != value["content_b64"]:
                    raise ValueError()
            except (ValueError, TypeError, binascii.Error) as exc:
                raise ReleaseError("invalid_bundle") from exc
        return digest(canonical(bundle))

    def _package_from_files(self, files):
        bundle = {"schema": 2, "revision": PACKAGE_REVISION, "manifest_b64": self.manifest_b64, "files": files}
        self._bundle(bundle)
        return bundle

    def _validate_stage(self, path, release_id):
        if path != self.code / release_id or path.resolve() != path:
            raise ReleaseError("unsafe_stage")
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o755:
            raise ReleaseError("unsafe_stage")
        names = {x.name for x in path.iterdir()}
        if names - {"__pycache__"} != set(FILENAMES) | {"manifest.json"}:
            raise ReleaseError("unsafe_stage")
        self._check_cache(path)
        files = {}
        for name in FILENAMES:
            value, info = self._code_file(path / name)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o644:
                raise ReleaseError("unsafe_stage")
            files[name] = value
        value, info = self._code_file(path / "manifest.json")
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o644 or value["sha256"] != MANIFEST_SHA256 or base64.b64decode(value["content_b64"]) != self.manifest_bytes:
            raise ReleaseError("unsafe_stage")
        bundle = self._package_from_files(files)
        if self._bundle(bundle) != release_id:
            raise ReleaseError("unsafe_stage")
        return {"kind": "package", "bundle": bundle}

    def _check_cache(self, directory):
        cache = directory / "__pycache__"
        if not os.path.lexists(cache):
            return
        info = cache.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ReleaseError("unsafe_cache")
        modules = "|".join(re.escape(Path(name).stem) for name in FILENAMES)
        pattern = re.compile(r"(?:" + modules + r")\.cpython-[0-9]{2,3}(?:\.opt-[012])?\.pyc\Z")
        for item in cache.iterdir():
            info = regular(item)
            if not pattern.fullmatch(item.name) or info.st_uid != os.geteuid() or info.st_mode & 0o022 or info.st_size > MAX_FILE * 4:
                raise ReleaseError("unsafe_cache")

    def _purge_cache(self, directory):
        self._check_cache(directory)
        cache = directory / "__pycache__"
        if cache.exists():
            for item in cache.iterdir():
                item.unlink()
            cache.rmdir()
            fsync_dir(directory)

    def _clear_staging(self, path):
        if path.parent != self.code or not re.fullmatch(r"\.staging-[0-9a-f]{64}", path.name):
            raise ReleaseError("unsafe_stage")
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) not in {0o700, 0o755}:
            raise ReleaseError("unsafe_stage")
        snapshot = self._load(path.name[len(".staging-"):])
        if snapshot["kind"] != "package":
            raise ReleaseError("unsafe_stage")
        for item in path.iterdir():
            if item.name not in set(FILENAMES) | {"manifest.json"} or regular(item).st_uid != os.geteuid():
                raise ReleaseError("unsafe_stage")
            expected = self.manifest_bytes if item.name == "manifest.json" else base64.b64decode(snapshot["bundle"]["files"][item.name]["content_b64"])
            with item.open("rb") as stream:
                actual = stream.read(len(expected) + 1)
            if not expected.startswith(actual):
                raise ReleaseError("unsafe_stage")
        for item in path.iterdir():
            item.unlink()
        path.rmdir()
        fsync_dir(self.code)

    def _stage(self, bundle):
        release_id = self._bundle(bundle)
        destination = self.code / release_id
        if destination.exists() or destination.is_symlink():
            self._validate_stage(destination, release_id)
            return release_id
        staging = self.code / (".staging-" + release_id)
        if staging.exists() or staging.is_symlink():
            self._clear_staging(staging)
        staging.mkdir(mode=0o700)
        try:
            for name in (*FILENAMES, "manifest.json"):
                fd = os.open(staging / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(self.manifest_bytes if name == "manifest.json" else base64.b64decode(bundle["files"][name]["content_b64"]))
                    stream.flush()
                    os.fchmod(stream.fileno(), 0o644)
                    os.fsync(stream.fileno())
            staging.chmod(0o755)
            fsync_dir(staging)
            os.rename(staging, destination)
            fsync_dir(self.code)
        except BaseException:
            if staging.exists():
                self._clear_staging(staging)
            raise
        self._validate_stage(destination, release_id)
        return release_id

    def _current(self):
        info = self.entrypoint.lstat()
        if stat.S_ISLNK(info.st_mode):
            target = Path(os.readlink(self.entrypoint))
            if target == self.inhibitor:
                if os.path.lexists(self.inhibitor.parent):
                    raise ReleaseError("unsafe_inhibitor")
                return {"kind": "inhibited"}
            if not target.is_absolute() or target.name != ENTRYNAME or target.parent.parent != self.code or not HEX64.fullmatch(target.parent.name):
                raise ReleaseError("unsafe_entrypoint")
            return self._validate_stage(target.parent, target.parent.name)
        value, info = self._code_file(self.entrypoint)
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ReleaseError("unsafe_entrypoint")
        files = {ENTRYNAME: value}
        if value["sha256"] == self.manifest["artifacts"][ENTRYNAME]:
            for name in FILENAMES[1:]:
                files[name], sibling_info = self._code_file(self.entrypoint.parent / name)
                if sibling_info.st_uid != os.geteuid() or sibling_info.st_mode & 0o022:
                    raise ReleaseError("unsafe_entrypoint")
            self._package_from_files(files)
            self._check_cache(self.entrypoint.parent)
        elif value["sha256"] != self.manifest["baseline_sha256"]:
            raise ReleaseError("unrecognized_live_code")
        return {"kind": "regular", "files": files,
                "metadata": {"uid": info.st_uid, "gid": info.st_gid, "mode": stat.S_IMODE(info.st_mode)}}

    def _files(self, snapshot):
        if snapshot["kind"] == "inhibited":
            return {}
        return snapshot["bundle"]["files"] if snapshot["kind"] == "package" else snapshot["files"]

    def _fingerprint(self):
        return digest(canonical({key: item["sha256"] for key, item in self._files(self._current()).items()}))

    def _compatible(self, snapshot):
        files = self._files(snapshot)
        return set(files) == set(FILENAMES) and {key: value["sha256"] for key, value in files.items()} == self.manifest["artifacts"]

    def _floor_active(self):
        path = self.state / "compatibility.json"
        if path.exists():
            if self._read(path) != {"state_compatibility": STATE_COMPATIBILITY}:
                raise ReleaseError("invalid_state")
            return True
        if self.grocery_journal.exists() or self.grocery_journal.is_symlink():
            regular(self.grocery_journal)  # Metadata only; never read either database.
            return True
        return False

    def _raise_floor(self):
        path = self.state / "compatibility.json"
        if path.exists():
            if self._read(path) != {"state_compatibility": STATE_COMPATIBILITY}:
                raise ReleaseError("invalid_state")
        else:
            self._write(path, {"state_compatibility": STATE_COMPATIBILITY})

    def _read(self, path):
        regular(path)
        with path.open("rb") as stream:
            data = stream.read(MAX_REQUEST + 1)
        if len(data) > MAX_REQUEST:
            raise ReleaseError("invalid_state")
        return strict_json(data)

    def _write(self, path, value):
        atomic_bytes(path, canonical(value))

    def _validate_receipt(self, value):
        if not isinstance(value, dict) or set(value) != {"active", "previous", "baseline"}:
            raise ReleaseError("invalid_state")
        if any(item is not None and (not isinstance(item, str) or not HEX64.fullmatch(item)) for item in value.values()):
            raise ReleaseError("invalid_state")
        if (value["active"] is None) != (value["baseline"] is None) or value["active"] is None and value["previous"] is not None:
            raise ReleaseError("invalid_state")

    def _receipt(self):
        path = self.state / "state.json"
        value = self._read(path) if path.exists() else {"active": None, "previous": None, "baseline": None}
        self._validate_receipt(value)
        return value

    def _snapshot_id(self, snapshot):
        if not isinstance(snapshot, dict):
            raise ReleaseError("invalid_state")
        if snapshot.get("kind") == "package" and set(snapshot) == {"kind", "bundle"}:
            return self._bundle(snapshot["bundle"])
        if snapshot.get("kind") != "regular" or set(snapshot) != {"kind", "files", "metadata"}:
            raise ReleaseError("invalid_state")
        metadata = snapshot["metadata"]
        if not isinstance(metadata, dict) or set(metadata) != {"uid", "gid", "mode"} or not all(type(x) is int and x >= 0 for x in metadata.values()) or metadata["mode"] & ~0o777:
            raise ReleaseError("invalid_state")
        files = snapshot["files"]
        if not isinstance(files, dict) or set(files) not in ({ENTRYNAME}, set(FILENAMES)):
            raise ReleaseError("invalid_state")
        for name, value in files.items():
            if not isinstance(value, dict) or set(value) != {"sha256", "content_b64"}:
                raise ReleaseError("invalid_state")
            try:
                data = base64.b64decode(value["content_b64"], validate=True)
                if not data or len(data) > MAX_FILE or digest(data) != value["sha256"]:
                    raise ValueError()
            except (ValueError, TypeError, binascii.Error) as exc:
                raise ReleaseError("invalid_state") from exc
        if set(files) == set(FILENAMES):
            self._package_from_files(files)
        elif files[ENTRYNAME]["sha256"] != self.manifest["baseline_sha256"]:
            raise ReleaseError("invalid_state")
        return digest(canonical(snapshot))

    def _snapshot(self, snapshot):
        release_id = self._snapshot_id(snapshot)
        path = self.releases / (release_id + ".json")
        if path.exists():
            if self._read(path) != snapshot:
                raise ReleaseError("snapshot_conflict")
        else:
            self._write(path, snapshot)
        return release_id

    def _load(self, release_id):
        if not isinstance(release_id, str) or not HEX64.fullmatch(release_id):
            raise ReleaseError("unknown_release")
        path = self.releases / (release_id + ".json")
        if not path.exists():
            raise ReleaseError("unknown_release")
        snapshot = self._read(path)
        if self._snapshot_id(snapshot) != release_id:
            raise ReleaseError("invalid_state")
        return snapshot

    def _replace_link(self, target):
        fd, temporary = tempfile.mkstemp(prefix=".entry-", dir=self.entrypoint.parent)
        os.close(fd)
        os.unlink(temporary)
        try:
            os.symlink(str(target), temporary)
            os.replace(temporary, self.entrypoint)
            fsync_dir(self.entrypoint.parent)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)

    def _inhibit(self):
        if os.path.lexists(self.inhibitor.parent):
            raise ReleaseError("unsafe_inhibitor")
        self._replace_link(self.inhibitor)

    def _switch(self, snapshot):
        if snapshot["kind"] == "package":
            release_id = self._stage(snapshot["bundle"])
            self._purge_cache(self.code / release_id)
            self._replace_link(self.code / release_id / ENTRYNAME)
        else:
            if self._compatible(snapshot):
                for name in FILENAMES[1:]:
                    value, info = self._code_file(self.entrypoint.parent / name)
                    if value != snapshot["files"][name] or info.st_uid != os.geteuid() or info.st_mode & 0o022:
                        raise ReleaseError("recovery_code_drift")
                self._purge_cache(self.entrypoint.parent)
            atomic_bytes(self.entrypoint, base64.b64decode(snapshot["files"][ENTRYNAME]["content_b64"]), snapshot["metadata"])

    def _install(self, snapshot):
        self.service("stop")
        if self._floor_active() and not self._compatible(snapshot):
            raise ReleaseError("incompatible_rollback_recovery_required")
        if self._compatible(snapshot):
            self._inhibit()
            self._raise_floor()
        self._switch(snapshot)
        self._external_helper()
        if self._floor_active() and not self._compatible(self._current()):
            raise ReleaseError("incompatible_rollback_recovery_required")
        self.service("start")

    def _verify(self, snapshot, before_identity=None):
        identity = self.running_identity()
        if before_identity is not None and identity == before_identity:
            raise ReleaseError("restart_unverified")
        if self._current() != snapshot:
            raise ReleaseError("code_mismatch")
        self._external_helper()
        self.check_health()
        self._external_helper()
        if self.running_identity() != identity or self._current() != snapshot:
            raise ReleaseError("runtime_changed")

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
        try:
            actual = self._current()
        except Exception:
            self.service("stop")
            raise ReleaseError("recovery_code_drift") from None
        if actual not in (before, after, {"kind": "inhibited"}):
            self.service("stop")
            raise ReleaseError("recovery_code_drift")
        # A durable pending activation is conservatively batch-capable even if
        # it died between inhibition and the compatibility-marker write.
        try:
            self.service("stop")
        except BaseException as stop_error:
            # The process may still be alive. Inhibit a future boot/restart and
            # retain the journal rather than claiming that stop succeeded.
            try:
                self._inhibit()
                self._raise_floor()
            except BaseException:
                pass
            raise ReleaseError("stop_failed_recovery_required") from stop_error
        self._inhibit()
        self._raise_floor()
        if self._floor_active() and not self._compatible(before):
            raise ReleaseError("incompatible_rollback_recovery_required")
        self._install(before)
        self._verify(before)
        self._write(self.state / "state.json", journal["old_state"])
        self._remove_journal()
        retention_pending = self._safe_prune(journal["old_state"])
        return {"status": "recovered", "current": self._fingerprint(), "active": journal["before"], "retention_pending": retention_pending}

    def _prune(self, receipt):
        protected = {receipt[key] for key in ("active", "previous", "baseline")}
        journal = self.state / "transaction.json"
        if journal.exists():
            transaction = self._read(journal)
            protected.update({transaction["before"], transaction["after"]})
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
                snapshot = self._load(path.stem)
                stage = self.code / path.stem
                if snapshot["kind"] == "package" and stage.exists():
                    self._validate_stage(stage, path.stem)
                    self._purge_cache(stage)
                    for name in (*FILENAMES, "manifest.json"):
                        (stage / name).unlink()
                    stage.rmdir()
                path.unlink()
        fsync_dir(self.releases)
        fsync_dir(self.code)

    def _safe_prune(self, receipt):
        try:
            self._prune(receipt)
            return False
        except Exception:
            return True

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
                actual = self._current()
                self._external_helper()
                return {"status": "ok", "current": self._fingerprint(), "active": receipt["active"], "previous": receipt["previous"],
                        "recovery_required": journal.exists(), "inhibited": actual["kind"] == "inhibited",
                        "state_compatibility": STATE_COMPATIBILITY if self._floor_active() or self._compatible(actual) or journal.exists() else None}
            if action == "recover":
                if not journal.exists():
                    raise ReleaseError("no_recovery_pending")
                return self._recover()
            if journal.exists():
                raise ReleaseError("recovery_required")
            if action == "deploy":
                self._bundle(request["bundle"])
            current = request["expected_current"]
            if not isinstance(current, str) or not HEX64.fullmatch(current) or self._fingerprint() != current:
                raise ReleaseError("stale_current")
            actual = self._current()
            if actual["kind"] == "inhibited":
                raise ReleaseError("recovery_required")
            if not self._compatible(actual) and self._floor_active():
                raise ReleaseError("legacy_with_batch_state")
            if receipt["active"] is not None and self._load(receipt["active"]) != actual:
                raise ReleaseError("unmanaged_code_drift")
            self._external_helper()
            before_identity = self.running_identity()
            self.check_health()
            if self._fingerprint() != current:
                raise ReleaseError("code_mismatch")
            if receipt["active"] is None:
                baseline_id = self._snapshot(actual)
                receipt = {"active": baseline_id, "previous": None, "baseline": baseline_id}
            if action == "rollback":
                release_id = request["release_id"]
                if release_id not in {receipt["previous"], receipt["baseline"]}:
                    raise ReleaseError("rollback_not_retained")
                candidate = self._load(release_id)
                if self._floor_active() and not self._compatible(candidate):
                    raise ReleaseError("incompatible_rollback_recovery_required")
            else:
                candidate = {"kind": "package", "bundle": request["bundle"]}
                release_id = self._snapshot(candidate)
                self._stage(request["bundle"])
            if release_id == receipt["active"]:
                if self._compatible(candidate):
                    self._raise_floor()
                return {"status": "unchanged", "revision": PACKAGE_REVISION if self._compatible(candidate) else None,
                        "release_id": release_id, "current": current, "previous": receipt["previous"]}
            self._write(journal, {"before": receipt["active"], "after": release_id, "old_state": receipt})
            try:
                self._install(candidate)
                self._verify(candidate, before_identity)
                updated = {"active": release_id, "previous": receipt["active"], "baseline": receipt["baseline"]}
                self._write(self.state / "state.json", updated)
                self._remove_journal()
            except BaseException as exc:
                try:
                    self._recover()
                except ReleaseError as recovery_error:
                    if str(recovery_error) in {"incompatible_rollback_recovery_required", "stop_failed_recovery_required"}:
                        raise recovery_error from exc
                    raise ReleaseError("rollback_failed_recovery_required") from recovery_error
                except BaseException as recovery_error:
                    raise ReleaseError("rollback_failed_recovery_required") from recovery_error
                raise ReleaseError("release_failed_rolled_back") from exc
            retention_pending = self._safe_prune(updated)
            return {"status": "deployed" if action == "deploy" else "rolled_back", "revision": PACKAGE_REVISION if self._compatible(candidate) else None,
                    "release_id": release_id, "current": self._fingerprint(), "previous": updated["previous"], "retention_pending": retention_pending}
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
        secure_path(receiver.code, directory=True)
        secure_path(receiver.entrypoint.parent, directory=True)
        secure_path(receiver.helper)
        if not receiver.entrypoint.is_symlink():
            secure_path(receiver.entrypoint)
        receiver._current()
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
