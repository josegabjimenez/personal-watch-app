#!/usr/bin/env python3
"""Reproduce the sealed November 5 base and assemble an offline Gasto candidate."""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
FOLDER = Path(__file__).resolve().parent
BASELINE = "9b2884671e74afc7865663dc1e29d25eed3d26bf"
BASELINE_MANIFEST_SHA256 = "55d14d53043179256791d14242c4f44b17bb33534dd7e18102c61c6bbc17b038"
GROCERY = "f8738b9a1529c09a7a577550706d7fb876ea6f89"
SERVER = "mobile_capture_staging_server.py"


def load_packager(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


def git_source(repo, revision, path):
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{revision}:{path}"])


def sealed_baseline(repo=ROOT):
    reminder = load_packager("_gasto_reminder_packager", repo / "ops/hotfixes/reminder-ai/build_release.py")
    grocery = load_packager("_gasto_grocery_packager", repo / "ops/hotfixes/grocery-multi-item/build_release.py")
    raw = git_source(repo, BASELINE, "ops/hotfixes/reminder-ai/release_manifest.json")
    reminder.require_digest(raw, BASELINE_MANIFEST_SHA256, "November 5 sealed manifest")
    manifest = json.loads(raw)
    grocery_server = grocery.overlay_server(grocery.baseline_source(repo), grocery.OVERLAY.read_bytes())
    runtime = {SERVER: reminder.overlay_server(grocery_server)}
    for name in reminder.GROCERY_MODULES:
        runtime[name] = git_source(repo, GROCERY, "backend/" + name)
    for name in reminder.REMINDER_MODULES:
        runtime[name] = git_source(repo, BASELINE, "backend/" + name)
    if set(runtime) != set(manifest["artifacts"]):
        raise ValueError("Runtime does not match the sealed file list")
    for name, source in runtime.items():
        reminder.require_digest(source, manifest["artifacts"][name], "November 5 sealed " + name)
    reminder.assert_import_closure(runtime)
    return reminder, manifest, runtime


def overlay_server(source):
    text = source.decode("utf-8")
    original_import = "import reminder_runtime\n"
    if text.count(original_import) != 1:
        raise ValueError("Missing sealed Reminder import")
    text = text.replace(original_import, original_import + "import expense_runtime\n", 1)
    for method in ("get", "post"):
        original_call = f"reminder_runtime.route_{method}(self, self._grocery_do_{method.upper()}, globals())"
        if text.count(original_call) != 1:
            raise ValueError("Missing sealed HTTP delegation")
        text = text.replace(
            original_call,
            f"expense_runtime.route_{method}(self, lambda: {original_call}, globals())",
            1,
        )
    return text.encode("utf-8")


def candidate(repo=ROOT):
    packager, baseline, runtime = sealed_baseline(repo)
    runtime[SERVER] = overlay_server(runtime[SERVER])
    engine_source = (repo / "backend" / SERVER).read_bytes()
    # This is an import-only executor behind the one reviewed entry point.
    cli = ast.parse(engine_source).body[-1]
    if not isinstance(cli, ast.If) or ast.unparse(cli.test) != "__name__ == '__main__'":
        raise ValueError("Missing expense engine CLI guard")
    runtime.update({
        "expense_runtime.py": (FOLDER / "expense_runtime.py").read_bytes(),
        "expense_engine.py": b"".join(engine_source.splitlines(keepends=True)[:cli.lineno - 1]),
        "mobile_expense_capture.py": (repo / "backend/scripts/mobile_expense_capture.py").read_bytes(),
    })
    allowed = packager.STDLIB_IMPORTS | {Path(name).stem for name in runtime}
    for name, code in runtime.items():
        compile(code, name, "exec")
        for node in ast.walk(ast.parse(code)):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
                if node.level:
                    raise ValueError(f"Unexpected relative runtime import in {name}")
            else:
                continue
            if names - allowed:
                raise ValueError(f"Unpackaged runtime import in {name}: {names - allowed}")

    manifest = {
        "format_version": 1,
        "baseline_revision": BASELINE,
        "baseline_manifest_sha256": BASELINE_MANIFEST_SHA256,
        "external_helper_sha256": baseline["external_helper_sha256"],
        "artifacts": {name: packager.digest(code) for name, code in sorted(runtime.items())},
    }
    return packager, manifest, runtime


def verify(directory, packager, manifest):
    directory = packager._directory(directory)
    if {path.name for path in directory.iterdir()} != {*manifest["artifacts"], "manifest.json"}:
        raise ValueError("Candidate directory differs from the manifest")
    if packager._read(directory / "manifest.json") != packager.encoded(manifest):
        raise ValueError("Candidate manifest differs from reviewed source")
    for name, expected in manifest["artifacts"].items():
        packager.require_digest(packager._read(directory / name), expected, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    try:
        packager, manifest, runtime = candidate()
        output = args.output.absolute()
        if not args.verify:
            packager._directory(output.parent)
            output.mkdir(mode=0o700)
            for name, code in runtime.items():
                packager._write_new(output / name, code)
            packager._write_new(output / "manifest.json", packager.encoded(manifest))
        verify(output, packager, manifest)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Gasto candidate verification failed: {error}\n")
    print(packager.digest(packager.encoded(manifest)))


if __name__ == "__main__":
    main()
