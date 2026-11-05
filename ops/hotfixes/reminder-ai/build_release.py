#!/usr/bin/env python3
"""Build/verify a minimal Reminder candidate over the accepted grocery release.

No application module is imported or executed. The five grocery modules are
copied byte-for-byte. The accepted server only gains a reminder_runtime import,
renamed original HTTP methods and two thin wrappers. No installer or service
operation is provided. Seal only after the six new runtime modules are frozen.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
BASELINE_MANIFEST = FOLDER / "baseline_manifest.json"
MANIFEST = FOLDER / "release_manifest.json"
GROCERY_MANIFEST_SHA256 = "bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281"
BASELINE_SERVER_SHA256 = "309602498c2a9ccf3186e7c336ed9ff5cc2fe3b693866fc1fbfdb18c1b0d5100"
SERVER = "mobile_capture_staging_server.py"
GROCERY_MODULES = (
    "grocery_capture.py", "grocery_helper_adapter.py", "grocery_item_parser.py",
    "grocery_ai.py", "grocery_ai_budget.py",
)
REMINDER_MODULES = (
    "reminder_runtime.py", "reminder_capture.py", "reminder_ai.py",
    "reminder_ai_budget.py", "todoist_reminders.py", "todoist_auth.py",
)
GROCERY_ARTIFACTS = (SERVER, *GROCERY_MODULES)
ARTIFACTS = (*GROCERY_ARTIFACTS, *REMINDER_MODULES)
STDLIB_IMPORTS = frozenset({
    "__future__", "argparse", "ast", "base64", "collections", "contextlib", "copy",
    "dataclasses", "datetime", "decimal", "fcntl", "functools", "hashlib", "hmac",
    "http", "importlib", "io", "json", "math", "os", "pathlib", "queue", "re",
    "secrets", "socket", "sqlite3", "stat", "subprocess", "sys", "tempfile",
    "threading", "time", "types", "typing", "unicodedata", "urllib", "uuid",
    "webbrowser", "zoneinfo",
})
SCOPE = "Reminder-only HTTP wrappers over the exact accepted grocery AI release; grocery modules and original server bodies preserved"
SERVER_CHANGE = "one reminder_runtime import; original GET/POST renamed; two thin delegation methods"
WRAPPERS = (
    "\n"
    "    def do_POST(self):\n"
    "        return reminder_runtime.route_post(self, self._grocery_do_POST, globals())\n"
    "\n"
    "    def do_GET(self):\n"
    "        return reminder_runtime.route_get(self, self._grocery_do_GET, globals())\n"
)
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def require_digest(data: bytes, expected: str, description: str) -> None:
    if digest(data) != expected:
        raise ValueError(f"{description} SHA256 mismatch")


def _canonical(path: Path) -> Path:
    path = Path(path)
    absolute = path.absolute()
    if ".." in path.parts or absolute.resolve(strict=True) != absolute:
        raise ValueError("Package paths must be canonical and contain no symlinks")
    return absolute


def _directory(path: Path) -> Path:
    path = _canonical(path)
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError("Package must be an ordinary directory")
    return path


def _read(path: Path) -> bytes:
    path = _canonical(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise ValueError("Package must contain ordinary files without hardlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, "rb") as stream:
        current = os.fstat(stream.fileno())
        if not stat.S_ISREG(current.st_mode) or current.st_nlink != 1 or (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            raise ValueError("Package file identity changed while reading")
        data = stream.read(2 * 1024 * 1024 + 1)
    if len(data) > 2 * 1024 * 1024:
        raise ValueError("Package source exceeds size bound")
    return data


def baseline_manifest() -> dict:
    raw = _read(BASELINE_MANIFEST)
    require_digest(raw, GROCERY_MANIFEST_SHA256, "Pinned grocery manifest")
    value = json.loads(raw)
    if (
        not isinstance(value, dict) or value.get("format_version") != 1
        or value.get("runtime_file_count") != len(GROCERY_ARTIFACTS)
        or set(value.get("artifacts", {})) != set(GROCERY_ARTIFACTS)
        or value["artifacts"].get(SERVER) != BASELINE_SERVER_SHA256
        or encoded(value) != raw
    ):
        raise ValueError("Invalid pinned grocery manifest")
    return value


def load_grocery_release(directory: Path) -> tuple[dict, dict[str, bytes]]:
    expected = baseline_manifest()
    directory = _directory(directory)
    if {path.name for path in directory.iterdir()} != set((*GROCERY_ARTIFACTS, "manifest.json")):
        raise ValueError("Baseline must contain exactly six grocery runtime files and its manifest")
    raw_manifest = _read(directory / "manifest.json")
    require_digest(raw_manifest, GROCERY_MANIFEST_SHA256, "Accepted grocery release manifest")
    if raw_manifest != encoded(expected):
        raise ValueError("Baseline manifest differs from accepted grocery release")
    runtime = {}
    for name in GROCERY_ARTIFACTS:
        runtime[name] = _read(directory / name)
        require_digest(runtime[name], expected["artifacts"][name], "Accepted grocery " + name)
    return expected, runtime


def _handler(tree: ast.Module) -> ast.ClassDef:
    handlers = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MobileCaptureHandler"]
    if len(handlers) != 1:
        raise ValueError("Expected one accepted HTTP handler")
    return handlers[0]


def _method(handler: ast.ClassDef, name: str) -> ast.FunctionDef:
    matches = [node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise ValueError("Expected one historical HTTP method")
    return matches[0]


def _rename(lines: list[str], method: ast.FunctionDef, old: str, new: str) -> None:
    index = method.lineno - 1
    prefix = " " * method.col_offset + "def " + old + "("
    if not lines[index].startswith(prefix):
        raise ValueError("Unexpected HTTP method declaration")
    start = method.col_offset + len("def ")
    lines[index] = lines[index][:start] + new + lines[index][start + len(old):]


def _render_server(source: bytes) -> bytes:
    tree = ast.parse(source)
    handler = _handler(tree)
    lines = source.decode("utf-8").splitlines(keepends=True)
    for old, new in (("do_POST", "_grocery_do_POST"), ("do_GET", "_grocery_do_GET")):
        _rename(lines, _method(handler, old), old, new)
    last_import = max(node.end_lineno for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)))
    for index, addition in sorted(((handler.end_lineno, WRAPPERS), (last_import, "import reminder_runtime\n")), reverse=True):
        lines[index:index] = [addition]
    return "".join(lines).encode("utf-8")


def assert_scope(baseline: bytes, released: bytes) -> None:
    require_digest(baseline, BASELINE_SERVER_SHA256, "Accepted grocery server")
    old, new = ast.parse(baseline), ast.parse(released)
    reminder_import = ast.dump(ast.parse("import reminder_runtime").body[0])
    if sum(ast.dump(node) == reminder_import for node in new.body) != 1:
        raise ValueError("Server must add exactly one reminder_runtime import")
    remaining = [node for node in new.body if ast.dump(node) != reminder_import]
    if len(remaining) != len(old.body):
        raise ValueError("Unrelated server top-level scope changed")
    wrapper_nodes = ast.parse("class Wrapper:\n" + WRAPPERS).body[0].body
    for original, changed in zip(old.body, remaining):
        expected = original
        if isinstance(original, ast.ClassDef) and original.name == "MobileCaptureHandler":
            expected = copy.deepcopy(original)
            for old_name, new_name in (("do_POST", "_grocery_do_POST"), ("do_GET", "_grocery_do_GET")):
                _method(expected, old_name).name = new_name
            expected.body.extend(copy.deepcopy(wrapper_nodes))
        if ast.dump(expected) != ast.dump(changed):
            raise ValueError("Unrelated server function, Config, gates or handler body changed")
    if released != _render_server(baseline):
        raise ValueError("Server bytes changed beyond the exact handler delegation overlay")


def overlay_server(source: bytes) -> bytes:
    require_digest(source, BASELINE_SERVER_SHA256, "Accepted grocery server")
    result = _render_server(source)
    compile(result, SERVER, "exec")
    assert_scope(source, result)
    return result


def baseline_from_release(released: bytes) -> bytes:
    """Reverse only this exact overlay and recover the pinned server bytes."""
    text = released.decode("utf-8")
    if text.count(WRAPPERS) != 1 or text.count("import reminder_runtime\n") != 1:
        raise ValueError("Server lacks the exact reviewed handler wrappers")
    text = text.replace(WRAPPERS, "", 1).replace("import reminder_runtime\n", "", 1)
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    handler = _handler(tree)
    for old, new in (("_grocery_do_POST", "do_POST"), ("_grocery_do_GET", "do_GET")):
        _rename(lines, _method(handler, old), old, new)
    baseline = "".join(lines).encode("utf-8")
    assert_scope(baseline, released)
    return baseline


def assert_import_closure(runtime: dict[str, bytes]) -> None:
    if set(runtime) != set(ARTIFACTS):
        raise ValueError("Release must contain exactly twelve runtime files")
    allowed = STDLIB_IMPORTS | {Path(name).stem for name in ARTIFACTS}
    for name, source in runtime.items():
        compile(source, name, "exec")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                dependencies = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError("Relative runtime import is not packaged")
                dependencies = [(node.module or "").split(".")[0]]
            else:
                if name in REMINDER_MODULES and isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Name) and node.func.id == "__import__"
                    or isinstance(node.func, ast.Attribute) and node.func.attr in {"import_module", "spec_from_file_location", "exec_module"}
                ):
                    raise ValueError("New reminder runtime must use static packaged imports")
                continue
            if any(dependency not in allowed for dependency in dependencies):
                raise ValueError("Unpackaged non-stdlib runtime dependency in " + name)


def candidate(grocery_release: Path, repo: Path = ROOT) -> tuple[dict, dict[str, bytes]]:
    base, runtime = load_grocery_release(grocery_release)
    repo = _directory(repo)
    runtime[SERVER] = overlay_server(runtime[SERVER])
    for name in REMINDER_MODULES:
        runtime[name] = _read(repo / "backend" / name)
    assert_import_closure(runtime)
    manifest = {
        "format_version": 1, "scope": SCOPE, "server_change": SERVER_CHANGE,
        "grocery_manifest_sha256": GROCERY_MANIFEST_SHA256,
        "baseline_artifacts": base["artifacts"],
        "external_helper_sha256": base["external_helper_sha256"],
        "added_modules": sorted(REMINDER_MODULES), "runtime_file_count": len(ARTIFACTS),
        "artifacts": {name: digest(runtime[name]) for name in sorted(runtime)},
    }
    return manifest, runtime


def _validate_manifest(value: dict) -> dict:
    baseline = baseline_manifest()
    if (
        not isinstance(value, dict) or set(value) != {
            "format_version", "scope", "server_change", "grocery_manifest_sha256", "baseline_artifacts",
            "external_helper_sha256", "added_modules", "runtime_file_count", "artifacts",
        }
        or type(value.get("format_version")) is not int or value["format_version"] != 1
        or value.get("scope") != SCOPE or value.get("server_change") != SERVER_CHANGE
        or value.get("grocery_manifest_sha256") != GROCERY_MANIFEST_SHA256
        or value.get("baseline_artifacts") != baseline["artifacts"]
        or value.get("external_helper_sha256") != baseline["external_helper_sha256"]
        or value.get("added_modules") != sorted(REMINDER_MODULES)
        or type(value.get("runtime_file_count")) is not int or value["runtime_file_count"] != len(ARTIFACTS)
        or not isinstance(value.get("artifacts"), dict) or set(value["artifacts"]) != set(ARTIFACTS)
        or any(not isinstance(item, str) or _DIGEST_RE.fullmatch(item) is None for item in value["artifacts"].values())
        or any(value["artifacts"][name] != baseline["artifacts"][name] for name in GROCERY_MODULES)
    ):
        raise ValueError("Invalid Reminder release manifest")
    return value


def load_manifest(path: Path | None = None) -> dict:
    path = MANIFEST if path is None else path
    try:
        raw = _read(path)
    except FileNotFoundError:
        raise ValueError("Reminder candidate is not sealed; freeze/review runtime before --seal-manifest") from None
    manifest = _validate_manifest(json.loads(raw))
    if encoded(manifest) != raw:
        raise ValueError("Sealed manifest must use canonical JSON bytes")
    return manifest


def verify_release(output: Path, expected_manifest: dict | None = None) -> dict:
    expected = load_manifest() if expected_manifest is None else _validate_manifest(expected_manifest)
    output = _directory(output)
    if {path.name for path in output.iterdir()} != set((*ARTIFACTS, "manifest.json")):
        raise ValueError("Release must contain exactly twelve runtime files and its manifest")
    if _read(output / "manifest.json") != encoded(expected):
        raise ValueError("Release manifest differs from sealed manifest")
    runtime = {}
    for name in ARTIFACTS:
        runtime[name] = _read(output / name)
        require_digest(runtime[name], expected["artifacts"][name], name)
    baseline_from_release(runtime[SERVER])
    assert_import_closure(runtime)
    return expected


def _write_new(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)


def build_release(grocery_release: Path, output: Path, repo: Path = ROOT, expected_manifest: dict | None = None) -> dict:
    expected = load_manifest() if expected_manifest is None else _validate_manifest(expected_manifest)
    actual, runtime = candidate(grocery_release, repo)
    if actual != expected:
        raise ValueError("Runtime differs from sealed Reminder manifest; no package produced")
    output = Path(output).absolute()
    if ".." in output.parts or output.exists() or output.is_symlink():
        raise ValueError("Output must be a new canonical directory")
    _directory(output.parent)
    # Exclusive mkdir is the no-replacement claim. Unlike rename on an empty
    # directory, it cannot silently replace an output appearing in a race.
    try:
        output.mkdir(mode=0o700)
    except FileExistsError:
        raise ValueError("Output appeared before creation; no replacement performed") from None
    for name, source in runtime.items():
        _write_new(output / name, source)
    _write_new(output / "manifest.json", encoded(expected))
    return verify_release(output, expected)


def seal_manifest(grocery_release: Path, repo: Path = ROOT, path: Path | None = None) -> dict:
    """Explicit freeze step; never overwrite a different preexisting seal."""
    path = MANIFEST if path is None else Path(path)
    manifest, _ = candidate(grocery_release, repo)
    if path.exists() or path.is_symlink():
        if _read(path) != encoded(manifest):
            raise ValueError("A different release seal already exists; review it before replacement")
        return load_manifest(path)
    _directory(path.parent)
    _write_new(path, encoded(manifest))
    return load_manifest(path)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--grocery-release", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--seal-manifest", action="store_true", help="Freeze reviewed runtime separately; never deploy")
    args = parser.parse_args(argv)
    if args.seal_manifest and (args.verify or args.output is not None):
        parser.error("Manifest sealing is separate from build/verify")
    if not args.seal_manifest and args.output is None:
        parser.error("--output is required for build/verify")
    if not args.verify and args.grocery_release is None:
        parser.error("--grocery-release is required for build/seal")
    if args.verify and args.grocery_release is not None:
        parser.error("--verify consumes the package and existing seal only")
    try:
        if args.verify:
            result = verify_release(args.output)
        elif args.seal_manifest:
            result = seal_manifest(args.grocery_release, args.repo)
        else:
            result = build_release(args.grocery_release, args.output, args.repo)
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
