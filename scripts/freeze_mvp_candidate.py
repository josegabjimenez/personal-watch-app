#!/usr/bin/env python3
"""Verify source hashes and freeze a clean Git commit without local/private data."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/verification/2026-10-28/SOURCE_MANIFEST.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / ".private/personal-watch-app-mvp.tar.gz")
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text())
    for source in manifest["implementation_sources"]:
        name, expected = source["path"], source["sha256"]
        actual = hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
        if actual != expected:
            raise SystemExit(f"Source changed: {name}")
    if subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT).strip():
        raise SystemExit("Commit the reviewed changes before freezing a candidate")
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    archive = subprocess.check_output(
        ["git", "archive", "--format=tar", "--prefix=personal-watch-app/", commit], cwd=ROOT
    )
    compressed = gzip.compress(archive, mtime=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(compressed)
    print(json.dumps({"commit": commit, "sha256": hashlib.sha256(compressed).hexdigest(),
                      "bytes": len(compressed), "verified_sources": len(manifest["implementation_sources"]),
                      "output": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()
