"""Test-only source snapshots for the immutable four-file grocery release.

The historical writer must not acquire newer backend dependencies merely
because the current checkout also contains the six-file AI implementation.
The AI suite intentionally reuses the legacy HTTP scenarios with its own
five-module builder; that case continues to snapshot current AI source.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

HISTORICAL_SOURCE_COMMIT = "1fe67948051f1d25140e13900d51275c498e0770"
LEGACY_MODULE_HASHES = {
    "grocery_capture.py": "30638a74cc96c4ab71a7d3df8e89552ca1180798715fe6c04be33b4dd58ef943",
    "grocery_helper_adapter.py": "2894998b54a22bbc3b398a801f8cf5a080a22bcd5fec51678ccd4d8f7baacbde",
    "grocery_item_parser.py": "bf0097c86e43ca8b3349a96bc2ebf65f8d5d2e93eca4e9e3856ff6a2d134fdc7",
}
AI_MODULES = frozenset((*LEGACY_MODULE_HASHES, "grocery_ai.py", "grocery_ai_budget.py"))


def populate_snapshot(repo: Path, destination: Path, modules) -> None:
    """Prepare only temporary test inputs; never modify a builder or seal."""
    names = tuple(modules)
    if len(names) != len(set(names)):
        raise ValueError("Duplicate grocery test fixture module")
    sources = {}
    if set(names) == set(LEGACY_MODULE_HASHES):
        for name in names:
            raw = subprocess.check_output([
                "git", "-C", str(repo), "show", f"{HISTORICAL_SOURCE_COMMIT}:backend/{name}",
            ])
            if hashlib.sha256(raw).hexdigest() != LEGACY_MODULE_HASHES[name]:
                raise ValueError("Historical sealed grocery fixture SHA256 mismatch")
            sources[name] = raw
    elif set(names) == AI_MODULES:
        # The six-file package's inherited HTTP scenarios must still test its
        # actual AI modules, rather than silently testing the older writer.
        sources = {name: (repo / "backend" / name).read_bytes() for name in names}
    else:
        raise ValueError("Unsupported grocery test fixture module set")
    backend = destination / "backend"
    backend.mkdir(parents=True)
    for name, raw in sources.items():
        (backend / name).write_bytes(raw)
