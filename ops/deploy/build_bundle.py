#!/usr/bin/env python3
"""Reproduce the pinned historical grocery package, never the branch's full BFF."""
import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

PROFILE = Path(__file__).with_name("grocery-package.json")
MANIFEST_PATH = "ops/hotfixes/grocery-multi-item/release_manifest.json"
BUILDER_PATH = "ops/hotfixes/grocery-multi-item/build_release.py"
SOURCE_PATHS = frozenset({
    MANIFEST_PATH, BUILDER_PATH,
    "ops/hotfixes/grocery-multi-item/server_overlay.py",
    "ops/hotfixes/grocery-live-clarification/build_release.py",
    "ops/hotfixes/grocery-live-clarification/legacy-970e14d.patch",
    "backend/grocery_capture.py", "backend/grocery_helper_adapter.py",
    "backend/grocery_item_parser.py",
})
ARTIFACTS = frozenset({"mobile_capture_staging_server.py", "grocery_capture.py",
                      "grocery_helper_adapter.py", "grocery_item_parser.py"})
TEST_PATHS = (
    "ops/hotfixes/grocery-multi-item/test_release.py",
    "backend/tests/test_grocery_helper_adapter.py",
    "backend/tests/fixtures/grocery_helper_contract.py",
)
MAX_FILE = 400_000
MAX_BUNDLE = 2_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sealed_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("duplicate_json_key")
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=20).stdout


def committed_file(repo, revision, path):
    if not isinstance(path, str):
        raise ValueError("invalid_source_path")
    parts = PurePosixPath(path)
    if (not path or parts.is_absolute() or ".." in parts.parts or str(parts) != path
            or "\\" in path or path.startswith("-")):
        raise ValueError("invalid_source_path")
    entries = git(repo, "ls-tree", "-z", revision, "--", path).split(b"\0")
    entry = entries[0]
    if (not entry or len(entries) != 2 or entries[1]
            or entry.split(b"\t", 1)[-1].decode() != path
            or entry.split(b"\t", 1)[0].split()[:2] not in
            ([b"100644", b"blob"], [b"100755", b"blob"])):
        raise ValueError("source_must_be_regular_blob")
    content = git(repo, "show", f"{revision}:{path}")
    if not content or len(content) > MAX_FILE:
        raise ValueError("invalid_source_size")
    return content


def load_profile(profile=None):
    value = strict_json(PROFILE.read_bytes()) if profile is None else profile
    if (not isinstance(value, dict) or set(value) != {
            "schema", "package_revision", "state_compatibility", "manifest_path",
            "manifest_sha256", "sealed_manifest", "inputs"}
            or type(value["schema"]) is not int or value["schema"] != 1
            or not isinstance(value["package_revision"], str)
            or not re.fullmatch(r"[0-9a-f]{40}", value["package_revision"])
            or value["state_compatibility"] != "grocery-batch-v1"
            or value["manifest_path"] != MANIFEST_PATH
            or not isinstance(value["inputs"], dict) or set(value["inputs"]) != SOURCE_PATHS
            or any(not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin)
                   for pin in value["inputs"].values())):
        raise ValueError("invalid_package_profile")
    manifest = value["sealed_manifest"]
    if (not isinstance(manifest, dict) or manifest.get("format_version") != 1
            or manifest.get("runtime_file_count") != 4
            or manifest.get("journal_suffix") != ".grocery.sqlite3"
            or not isinstance(manifest.get("artifacts"), dict)
            or set(manifest["artifacts"]) != ARTIFACTS
            or any(not isinstance(pin, str) or not re.fullmatch(r"[0-9a-f]{64}", pin)
                   for pin in manifest["artifacts"].values())
            or digest(sealed_bytes(manifest)) != value["manifest_sha256"]
            or value["inputs"][MANIFEST_PATH] != value["manifest_sha256"]):
        raise ValueError("invalid_seal")
    return value


def stage_inputs(repo, revision, profile, snapshot):
    if not isinstance(revision, str) or revision != profile["package_revision"]:
        raise ValueError("pinned_package_revision_required")
    if git(repo, "rev-parse", f"{revision}^{{commit}}").decode().strip() != revision:
        raise ValueError("commit_mismatch")
    for path, pin in profile["inputs"].items():
        data = committed_file(repo, revision, path)
        if digest(data) != pin:
            raise ValueError("package_input_drift")
        if path == MANIFEST_PATH and data != sealed_bytes(profile["sealed_manifest"]):
            raise ValueError("manifest_bytes_mismatch")
        destination = snapshot / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)


def build_environment(repo, snapshot):
    git_dir = git(repo, "rev-parse", "--absolute-git-dir").decode().strip()
    # The reviewed packager uses only `git show` against historical source. No
    # user credentials/environment or full branch source enters this snapshot.
    return {"PATH": "/usr/bin:/bin", "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_DIR": git_dir, "GIT_WORK_TREE": str(snapshot)}


def build_bundle(repo: Path, revision: str, profile=None):
    profile = load_profile(profile)
    with tempfile.TemporaryDirectory(prefix="watch-sealed-build-") as directory:
        root = Path(directory)
        snapshot, output = root / "source", root / "package"
        snapshot.mkdir()
        stage_inputs(repo, revision, profile, snapshot)
        subprocess.run([sys.executable, "-I", str(snapshot / BUILDER_PATH),
                        "--repo", str(snapshot), "--output", str(output)],
                       check=True, timeout=60, env=build_environment(repo, snapshot),
                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if (output.is_symlink() or not output.is_dir()
                or {p.name for p in output.iterdir()} != ARTIFACTS | {"manifest.json"}):
            raise ValueError("invalid_package_paths")
        for path in output.iterdir():
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink != 1:
                raise ValueError("invalid_package_file")
        raw_manifest = (output / "manifest.json").read_bytes()
        if (digest(raw_manifest) != profile["manifest_sha256"]
                or raw_manifest != sealed_bytes(profile["sealed_manifest"])):
            raise ValueError("package_seal_mismatch")
        files = {}
        for name, pin in sorted(profile["sealed_manifest"]["artifacts"].items()):
            data = (output / name).read_bytes()
            if not data or len(data) > MAX_FILE or digest(data) != pin:
                raise ValueError("package_artifact_mismatch")
            compile(data, name, "exec")  # No application import or startup.
            files[name] = {"sha256": pin, "content_b64": base64.b64encode(data).decode("ascii")}
    bundle = {"schema": 2, "revision": revision,
              "manifest_b64": base64.b64encode(raw_manifest).decode("ascii"), "files": files}
    if len(canonical(bundle)) > MAX_BUNDLE:
        raise ValueError("bundle_too_large")
    return bundle


def verify_package_tests(repo: Path, revision: str, profile=None):
    profile = load_profile(profile)
    with tempfile.TemporaryDirectory(prefix="watch-sealed-tests-") as directory:
        snapshot = Path(directory)
        stage_inputs(repo, revision, profile, snapshot)
        for name in TEST_PATHS:
            destination = snapshot / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(committed_file(repo, revision, name))
        subprocess.run([sys.executable, "-I", "-m", "unittest", "discover", "-s",
                        str(snapshot / "ops/hotfixes/grocery-multi-item"), "-p", "test_release.py"],
                       check=True, timeout=90, cwd=snapshot,
                       env=build_environment(repo, snapshot), stdin=subprocess.DEVNULL)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True, help="Exact package revision pinned by the trusted profile")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-tests", action="store_true")
    args = parser.parse_args()
    try:
        bundle = build_bundle(args.repo, args.revision)
        if args.verify_tests:
            verify_package_tests(args.repo, args.revision)
        args.output.write_bytes(canonical(bundle))
    except (ValueError, KeyError, TypeError, OSError, SyntaxError, subprocess.SubprocessError):
        parser.exit(1, "sealed_release_build_failed\n")
    print(json.dumps({"revision": args.revision,
                      "release_id": digest(canonical(bundle)),
                      "manifest_sha256": digest(base64.b64decode(bundle["manifest_b64"])),
                      "files": {k: v["sha256"] for k, v in bundle["files"].items()}}))


if __name__ == "__main__":
    main()
