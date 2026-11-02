#!/usr/bin/env python3
"""Build code-only release bytes from one immutable Git commit; no checkout writes."""
import argparse
import base64
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess

ALLOWED_IDS = {"bff", "aura_helper", "expense_helper"}
MAX_FILE = 400_000
MAX_BUNDLE = 2_000_000


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=20).stdout


def committed_file(repo, revision, path):
    parts = PurePosixPath(path)
    if (not isinstance(path, str) or not path or parts.is_absolute()
            or ".." in parts.parts or str(parts) != path or "\\" in path
            or not path.endswith(".py") or path.startswith("-")):
        raise ValueError("invalid_source_path")
    entry = git(repo, "ls-tree", "-z", revision, "--", path).split(b"\0")[0]
    if not entry or entry.split(b"\t", 1)[0].split()[:2] != [b"100644", b"blob"]:
        # Executable Python files are regular blobs as well.
        if not entry or entry.split(b"\t", 1)[0].split()[:2] != [b"100755", b"blob"]:
            raise ValueError("source_must_be_regular_blob")
    content = git(repo, "show", f"{revision}:{path}")
    if not content or len(content) > MAX_FILE:
        raise ValueError("invalid_source_size")
    compile(content, path, "exec")  # Parse only: never import candidate production code.
    return content


def build_bundle(repo: Path, revision: str):
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("full_commit_sha_required")
    if git(repo, "rev-parse", f"{revision}^{{commit}}").decode().strip() != revision:
        raise ValueError("commit_mismatch")
    mapping = json.loads(git(repo, "show", f"{revision}:ops/deploy/release-files.json"))
    if (not isinstance(mapping, dict) or "bff" not in mapping
            or not set(mapping) <= ALLOWED_IDS or not all(isinstance(p, str) for p in mapping.values())
            or len(set(mapping.values())) != len(mapping)):
        raise ValueError("invalid_release_file_map")
    files = {}
    for name, path in sorted(mapping.items()):
        content = committed_file(repo, revision, path)
        files[name] = {"sha256": hashlib.sha256(content).hexdigest(),
                       "content_b64": base64.b64encode(content).decode("ascii")}
    bundle = {"schema": 1, "revision": revision, "files": files}
    if len(canonical(bundle)) > MAX_BUNDLE:
        raise ValueError("bundle_too_large")
    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        bundle = build_bundle(args.repo, args.revision)
        args.output.write_bytes(canonical(bundle))
    except (ValueError, OSError, SyntaxError, subprocess.SubprocessError):
        parser.exit(1, "release_build_failed\n")
    print(json.dumps({"revision": args.revision,
                      "release_id": hashlib.sha256(canonical(bundle)).hexdigest(),
                      "files": {k: v["sha256"] for k, v in bundle["files"].items()}}))


if __name__ == "__main__":
    main()
