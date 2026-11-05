#!/usr/bin/env python3
"""Build/verify a separate grocery-only AI candidate; never deploy or install.

The historical server and its unchanged grocery overlay are reconstructed by
the previously sealed multi-item builder. Application modules are compiled and
inspected, never imported by this packaging tool. The original package stays
immutable; this release has its own explicit seal.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
MANIFEST = FOLDER / "release_manifest.json"
PRIOR_FOLDER = ROOT / "ops/hotfixes/grocery-multi-item"
OVERLAY = PRIOR_FOLDER / "server_overlay.py"
BASELINE_SHA256 = "f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38"
EXTERNAL_HELPER_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
OVERLAY_SHA256 = "2c6e4a775cb599436c829507b650acb681351697bbb19c32c2fbeb8c71d1c441"
SERVER = "mobile_capture_staging_server.py"
RUNTIME_MODULES = ("grocery_capture.py", "grocery_helper_adapter.py", "grocery_item_parser.py", "grocery_ai_budget.py", "grocery_ai.py")
ARTIFACTS = (SERVER, *RUNTIME_MODULES)
STDLIB_IMPORTS = frozenset({
    "__future__", "argparse", "contextlib", "dataclasses", "datetime", "decimal",
    "hashlib", "hmac", "http", "importlib", "json", "math", "os", "pathlib",
    "queue", "re", "sqlite3", "stat", "subprocess", "sys", "threading", "time",
    "typing", "unicodedata", "urllib", "uuid", "zoneinfo",
})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def require_digest(data: bytes, expected: str, description: str) -> None:
    if digest(data) != expected:
        raise ValueError(f"{description} SHA256 mismatch")


def _prior_builder(repo: Path = ROOT):
    path = repo / "ops/hotfixes/grocery-multi-item/build_release.py"
    spec = importlib.util.spec_from_file_location("_grocery_ai_prior_packager", path)
    if spec is None or spec.loader is None:
        raise ValueError("Prior grocery packager is unavailable")
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    if module.BASELINE_SHA256 != BASELINE_SHA256 or module.EXTERNAL_HELPER_SHA256 != EXTERNAL_HELPER_SHA256:
        raise ValueError("Prior grocery package identity changed")
    return module


def baseline_source(repo: Path = ROOT) -> bytes:
    source = _prior_builder(repo).baseline_source(repo)
    require_digest(source, BASELINE_SHA256, "Historical baseline")
    return source


def assert_scope(baseline: bytes, released: bytes, overlay: bytes) -> None:
    require_digest(overlay, OVERLAY_SHA256, "Unchanged grocery overlay")
    _prior_builder().assert_scope(baseline, released, overlay)


def overlay_server(source: bytes, overlay: bytes) -> bytes:
    require_digest(source, BASELINE_SHA256, "Historical baseline")
    require_digest(overlay, OVERLAY_SHA256, "Unchanged grocery overlay")
    released = _prior_builder().overlay_server(source, overlay)
    assert_scope(source, released, overlay)
    return released


def assert_import_closure(runtime: dict[str, bytes]) -> None:
    if set(runtime) != set(ARTIFACTS):
        raise ValueError("Release must contain exactly the six grocery runtime files")
    allowed = STDLIB_IMPORTS | {Path(name).stem for name in ARTIFACTS}
    for name, source in runtime.items():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                dependencies = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    raise ValueError(f"Unpackaged relative runtime dependency in {name}")
                dependencies = [(node.module or "").split(".")[0]]
            else:
                continue
            if any(dependency not in allowed for dependency in dependencies):
                raise ValueError(f"Unpackaged runtime dependency in {name}")


def candidate(source: bytes, repo: Path = ROOT) -> tuple[dict, dict[str, bytes]]:
    overlay = OVERLAY.read_bytes()
    runtime = {SERVER: overlay_server(source, overlay)}
    for name in RUNTIME_MODULES:
        runtime[name] = (repo / "backend" / name).read_bytes()
        compile(runtime[name], name, "exec")
    assert_import_closure(runtime)
    helper_tree = ast.parse(runtime["grocery_helper_adapter.py"])
    pin = next((ast.literal_eval(node.value) for node in helper_tree.body
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
                and target.id == "PINNED_HELPER_SHA256" for target in node.targets)), None)
    if pin != EXTERNAL_HELPER_SHA256:
        raise ValueError("Unexpected external helper fingerprint")
    manifest = {
        "format_version": 1,
        "scope": "Historical grocery-only Luna AI with persistent USD2 monthly guard; original server gates and unrelated functions unchanged",
        "baseline_sha256": BASELINE_SHA256,
        "overlay_sha256": digest(overlay),
        "external_helper_sha256": EXTERNAL_HELPER_SHA256,
        "runtime_file_count": len(ARTIFACTS),
        "artifacts": {name: digest(runtime[name]) for name in sorted(runtime)},
        "journal_suffix": ".grocery.sqlite3",
        "activation": "Jose authorized grocery-only Luna/USD2 rollout; explicit budget initialization and private server key required",
        "rollback": "Retain compatible frozen batch/item state; never replay uncertain work or restore one database independently",
    }
    return manifest, runtime


def load_manifest(path: Path = MANIFEST) -> dict:
    try:
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise ValueError("AI candidate is not sealed; freeze/review runtime before --seal-manifest") from None
    if (not isinstance(manifest, dict) or manifest.get("format_version") != 1
            or manifest.get("baseline_sha256") != BASELINE_SHA256
            or manifest.get("overlay_sha256") != OVERLAY_SHA256
            or manifest.get("external_helper_sha256") != EXTERNAL_HELPER_SHA256
            or manifest.get("runtime_file_count") != len(ARTIFACTS)
            or not isinstance(manifest.get("artifacts"), dict)
            or set(manifest["artifacts"]) != set(ARTIFACTS)
            or any(not isinstance(value, str) or len(value) != 64
                   or any(char not in "0123456789abcdef" for char in value)
                   for value in manifest["artifacts"].values())):
        raise ValueError("Invalid grocery AI release manifest")
    return manifest


def verify_release(output: Path, expected_manifest: dict | None = None) -> dict:
    expected = load_manifest() if expected_manifest is None else expected_manifest
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Release must be an ordinary directory")
    if sorted(item.name for item in output.iterdir()) != sorted((*ARTIFACTS, "manifest.json")):
        raise ValueError("Release must contain exactly six runtime files and its manifest")
    for name in (*ARTIFACTS, "manifest.json"):
        path = output / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Release must contain ordinary files")
    if (output / "manifest.json").read_bytes() != encoded(expected):
        raise ValueError("Release manifest differs from sealed manifest")
    for name in ARTIFACTS:
        require_digest((output / name).read_bytes(), expected["artifacts"][name], name)
    return expected


def build_release(source: bytes, output: Path, repo: Path = ROOT, expected_manifest: dict | None = None) -> dict:
    expected = load_manifest() if expected_manifest is None else expected_manifest
    actual, runtime = candidate(source, repo)
    if actual != expected:
        raise ValueError("Runtime differs from sealed manifest for AI candidate; no package produced")
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".grocery-ai-release-", dir=output.parent) as temporary:
        staging = Path(temporary) / "package"
        staging.mkdir()
        for name, data in runtime.items():
            (staging / name).write_bytes(data)
        (staging / "manifest.json").write_bytes(encoded(expected))
        verify_release(staging, expected)
        if output.exists() or output.is_symlink():
            raise ValueError("Output appeared while staging; no replacement performed")
        os.rename(staging, output)
    return verify_release(output, expected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT)
    parser.add_argument("--source", type=Path, help="Optional exact historical f6eae64 baseline")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--seal-manifest", action="store_true", help="Explicitly seal frozen reviewed source; never deploy")
    args = parser.parse_args()
    if args.seal_manifest and (args.verify or args.output):
        parser.error("Manifest sealing is separate from build/verify")
    if not args.seal_manifest and args.output is None:
        parser.error("--output is required for build/verify")
    try:
        if args.verify:
            result = verify_release(args.output)
        else:
            source = args.source.read_bytes() if args.source else baseline_source(args.repo)
            if args.seal_manifest:
                result, _ = candidate(source, args.repo)
                MANIFEST.write_bytes(encoded(result))
            else:
                result = build_release(source, args.output, args.repo)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
