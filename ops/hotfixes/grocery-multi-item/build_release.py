#!/usr/bin/env python3
"""Build/verify a four-runtime-file historical grocery release, never deploy it.

Manifest sealing is explicit and must wait until the runtime files are frozen.
Server/helper code is parsed/compiled but never imported or executed here.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
OVERLAY = FOLDER / "server_overlay.py"
MANIFEST = FOLDER / "release_manifest.json"
BASELINE_SHA256 = "f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38"
EXTERNAL_HELPER_SHA256 = "24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0"
SERVER = "mobile_capture_staging_server.py"
RUNTIME_MODULES = ("grocery_capture.py", "grocery_helper_adapter.py", "grocery_item_parser.py")
ARTIFACTS = (SERVER, *RUNTIME_MODULES)
# Explicit reviewed stdlib roots also work on the installed Python3.9 toolchain.
STDLIB_IMPORTS = frozenset({
    "__future__", "argparse", "contextlib", "dataclasses", "datetime", "decimal",
    "hashlib", "hmac", "http", "importlib", "json", "os", "pathlib", "re",
    "sqlite3", "subprocess", "sys", "threading", "time", "typing", "unicodedata",
    "urllib", "zoneinfo",
})


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def require_digest(data: bytes, expected: str, description: str) -> None:
    if digest(data) != expected:
        raise ValueError(f"{description} SHA256 mismatch")


def baseline_source(repo: Path = ROOT) -> bytes:
    """Reconstruct only the previously pinned/deployed one-file release."""
    path = repo / "ops/hotfixes/grocery-live-clarification/build_release.py"
    spec = importlib.util.spec_from_file_location("_prior_grocery_release_builder", path)
    if spec is None or spec.loader is None:
        raise ValueError("Previous release builder is unavailable")
    previous = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), previous.__dict__)  # Packager only; no .pyc writes.
    source = subprocess.check_output([
        "git", "-C", str(repo), "show", f"{previous.BASELINE_COMMIT}:{previous.BASELINE_PATH}"
    ])
    previous.require_digest(source, previous.BASELINE_SHA256, "Historical source")
    patch = previous.PATCH_PATH.read_bytes()
    previous.require_digest(patch, previous.PATCH_SHA256, "Prior clarification patch")
    deployed = previous.apply_strict_patch(source, patch)
    require_digest(deployed, BASELINE_SHA256, "Deployed baseline")
    return deployed


def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    matches = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one function {name}")
    return matches[0]


def _handler(tree: ast.Module) -> ast.ClassDef:
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MobileCaptureHandler"]
    if len(matches) != 1:
        raise ValueError("Expected exactly one historical handler")
    return matches[0]


def assert_scope(baseline: bytes, released: bytes, overlay: bytes) -> None:
    old, new, instructions = ast.parse(baseline), ast.parse(released), ast.parse(overlay)
    expected_import = ast.dump(ast.parse("import grocery_capture").body[0])
    added = [node for node in new.body if ast.dump(node) == expected_import]
    if len(added) != 1:
        raise ValueError("Release must add only one grocery engine import")
    remaining = [node for node in new.body if ast.dump(node) != expected_import]
    if len(remaining) != len(old.body):
        raise ValueError("Historical top-level scope changed")
    for original, changed in zip(old.body, remaining):
        if isinstance(original, ast.FunctionDef) and original.name == "execute_capture":
            expected = _function(instructions, "execute_capture")
        elif isinstance(original, ast.ClassDef) and original.name == "MobileCaptureHandler":
            expected = copy.deepcopy(original)
            method = _function(expected, "do_POST")
            expected.body[expected.body.index(method)] = _function(instructions, "do_POST")
        else:
            expected = original
        if ast.dump(expected) != ast.dump(changed):
            raise ValueError("Release changed unrelated historical source")


def overlay_server(source: bytes, overlay: bytes) -> bytes:
    require_digest(source, BASELINE_SHA256, "Deployed baseline")
    tree, instruction_tree = ast.parse(source), ast.parse(overlay)
    allowed = {"execute_capture", "do_POST"}
    for node in instruction_tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, ast.ImportFrom) and node.module == "__future__":
            continue
        if isinstance(node, ast.Import) and ast.dump(node) == ast.dump(ast.parse("import grocery_capture").body[0]):
            continue
        if not isinstance(node, ast.FunctionDef) or node.name not in allowed or node.decorator_list:
            raise ValueError("Unexpected overlay scope")
    original_text, overlay_text = source.decode(), overlay.decode()
    lines = original_text.splitlines(keepends=True)
    old_execute, old_post = _function(tree, "execute_capture"), _function(_handler(tree), "do_POST")
    replacements = []
    for original, name, indent in ((old_execute, "execute_capture", ""), (old_post, "do_POST", "    ")):
        replacement = ast.get_source_segment(overlay_text, _function(instruction_tree, name))
        replacements.append((original.lineno - 1, original.end_lineno, textwrap.indent(replacement, indent) + "\n"))
    last_import = max(node.end_lineno for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom)))
    replacements.append((last_import, last_import, "import grocery_capture\n"))
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = [replacement]
    released = "".join(lines).encode()
    compile(released, SERVER, "exec")
    assert_scope(source, released, overlay)
    return released


def assert_import_closure(runtime: dict[str, bytes]) -> None:
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
                if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "PINNED_HELPER_SHA256" for target in node.targets)), None)
    if pin != EXTERNAL_HELPER_SHA256:
        raise ValueError("Unexpected external helper fingerprint")
    manifest = {
        "format_version": 1,
        "scope": "Historical grocery-only batch runtime; unrelated historical functions remain unchanged",
        "baseline_sha256": BASELINE_SHA256,
        "overlay_sha256": digest(overlay),
        "external_helper_sha256": EXTERNAL_HELPER_SHA256,
        "runtime_file_count": 4,
        "artifacts": {name: digest(runtime[name]) for name in sorted(runtime)},
        "journal_suffix": ".grocery.sqlite3",
        "rollback": "Once batch state exists, use a compatible runtime or disable writes; never replay through the old single-item writer",
    }
    return manifest, runtime


def load_manifest(path: Path = MANIFEST) -> dict:
    try:
        manifest = json.loads(path.read_bytes())
    except FileNotFoundError:
        raise ValueError("Release is not sealed; wait for the runtime freeze before --seal-manifest") from None
    if not isinstance(manifest, dict) or manifest.get("baseline_sha256") != BASELINE_SHA256 or manifest.get("external_helper_sha256") != EXTERNAL_HELPER_SHA256:
        raise ValueError("Invalid release manifest")
    return manifest


def verify_release(output: Path, expected_manifest: dict | None = None) -> dict:
    expected = load_manifest() if expected_manifest is None else expected_manifest
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Release must be an ordinary directory")
    if sorted(item.name for item in output.iterdir()) != sorted((*ARTIFACTS, "manifest.json")):
        raise ValueError("Release must contain exactly four runtime files and its manifest")
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
        raise ValueError("Runtime/overlay differs from sealed manifest; no package produced")
    if output.exists() or output.is_symlink():
        raise ValueError("Output must be a new directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".grocery-release-", dir=output.parent) as temporary:
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
    parser.add_argument("--source", type=Path, help="Optional exact deployed f6eae64 baseline source")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--seal-manifest", action="store_true", help="Explicitly pin frozen inputs; never deploy")
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
                # Sealing is a deliberate review step, never an automatic build side effect.
                MANIFEST.write_bytes(encoded(result))
            else:
                result = build_release(source, args.output, args.repo)
    except (OSError, ValueError, SyntaxError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
