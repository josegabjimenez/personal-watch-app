#!/usr/bin/env python3
"""Build or verify the one-file historical grocery compatibility release.

This script never fetches, deploys, opens credentials, or touches service data.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

BASELINE_COMMIT = "970e14dfd6d3788996135a933cc0909c3049f076"
BASELINE_PATH = "backend/mobile_capture_staging_server.py"
BASELINE_SHA256 = "3746e4c73689cc8ae5ab80958db51802b6a51491238a5c68c0b6585125c9f7a4"
PATCH_SHA256 = "8eac63e556d84a6706b5b4f6c26670a328bec16c1aea10566a4b01a18dc7dc46"
RELEASE_SHA256 = "f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38"
ARTIFACT_NAME = "mobile_capture_staging_server.py"
PATCH_PATH = Path(__file__).with_name("legacy-970e14d.patch")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_digest(data: bytes, expected: str, description: str) -> None:
    if digest(data) != expected:
        raise ValueError(f"{description} SHA256 mismatch; no release was produced")


def apply_strict_patch(source: bytes, patch: bytes) -> bytes:
    """Apply exact unified hunks, with no offset, fuzz, or shell patch tool."""
    original = source.decode("utf-8").splitlines(keepends=True)
    changes = patch.decode("utf-8").splitlines(keepends=True)
    expected_headers = [f"--- a/{BASELINE_PATH}\n", f"+++ b/{BASELINE_PATH}\n"]
    if changes[:2] != expected_headers:
        raise ValueError("Unexpected patch file headers")
    output: list[str] = []
    cursor = 0
    index = 2
    hunks = 0
    while index < len(changes):
        header = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@.*\n", changes[index])
        if header is None:
            raise ValueError("Invalid unified hunk header")
        old_start, old_count = int(header[1]) - 1, int(header[2] or 1)
        new_start, new_count = int(header[3]) - 1, int(header[4] or 1)
        if old_start < cursor or old_start > len(original):
            raise ValueError("Unexpected hunk offset")
        output.extend(original[cursor:old_start])
        cursor = old_start
        if len(output) != new_start:
            raise ValueError("Unexpected new-file hunk position")
        index += 1
        old_seen = new_seen = 0
        while index < len(changes) and not changes[index].startswith("@@ "):
            line = changes[index]
            marker, content = line[:1], line[1:]
            if marker not in {" ", "+", "-"}:
                raise ValueError("Unexpected unified patch line")
            if marker in {" ", "-"}:
                if cursor >= len(original) or original[cursor] != content:
                    raise ValueError("Patch context does not match exactly")
                cursor += 1
                old_seen += 1
            if marker in {" ", "+"}:
                output.append(content)
                new_seen += 1
            index += 1
        if (old_seen, new_seen) != (old_count, new_count):
            raise ValueError("Unified hunk counts do not match")
        hunks += 1
    if not hunks:
        raise ValueError("Patch contains no hunks")
    output.extend(original[cursor:])
    return "".join(output).encode("utf-8")


def manifest() -> dict[str, str | int]:
    return {
        "format_version": 1,
        "baseline_commit": BASELINE_COMMIT,
        "baseline_path": BASELINE_PATH,
        "baseline_sha256": BASELINE_SHA256,
        "patch_sha256": PATCH_SHA256,
        "artifact": ARTIFACT_NAME,
        "artifact_sha256": RELEASE_SHA256,
        "scope": "Historical grocery clarification envelope and exact legacy cached presentation only",
    }


def manifest_bytes() -> bytes:
    return (json.dumps(manifest(), sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_release(source: bytes, output: Path) -> dict[str, str | int]:
    require_digest(source, BASELINE_SHA256, "Baseline")
    patch = PATCH_PATH.read_bytes()
    require_digest(patch, PATCH_SHA256, "Patch")
    released = apply_strict_patch(source, patch)
    require_digest(released, RELEASE_SHA256, "Patched backend")
    compile(released, ARTIFACT_NAME, "exec")  # Syntax only; do not import/run it.
    output.mkdir(parents=True, exist_ok=False)
    (output / ARTIFACT_NAME).write_bytes(released)
    (output / "manifest.json").write_bytes(manifest_bytes())
    return verify_release(output)


def verify_release(output: Path) -> dict[str, str | int]:
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Release must be a real directory")
    if sorted(item.name for item in output.iterdir()) != ["manifest.json", ARTIFACT_NAME]:
        raise ValueError("Release must contain exactly the backend file and manifest")
    for name in (ARTIFACT_NAME, "manifest.json"):
        if not (output / name).is_file() or (output / name).is_symlink():
            raise ValueError("Release files must be ordinary files")
    require_digest(PATCH_PATH.read_bytes(), PATCH_SHA256, "Patch")
    require_digest((output / ARTIFACT_NAME).read_bytes(), RELEASE_SHA256, "Release backend")
    if (output / "manifest.json").read_bytes() != manifest_bytes():
        raise ValueError("Release manifest does not match the pinned release")
    return manifest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="New release directory, or existing directory with --verify")
    parser.add_argument("--source", type=Path, help="Optional exact baseline copy; otherwise git show reads the pinned commit")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--verify", action="store_true", help="Verify only; never write the output")
    args = parser.parse_args()
    try:
        if args.verify:
            result = verify_release(args.output)
        else:
            source = args.source.read_bytes() if args.source else subprocess.check_output(
                ["git", "-C", str(args.repo), "show", f"{BASELINE_COMMIT}:{BASELINE_PATH}"]
            )
            result = build_release(source, args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
