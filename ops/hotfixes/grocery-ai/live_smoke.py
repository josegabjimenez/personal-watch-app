#!/usr/bin/env python3
"""Authorized synthetic Luna inference only; never import a helper or write groceries.

Use the installed runtime, its separately reviewed manifest, and service journal.
All calls count against the same USD2 guard as the app. Reusing a run ID cannot
repeat a dispatched call, including one whose result was lost. No key is printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import time
import types


def cases():
    return [
        ("compound-synonym", "agrega patatas, pan de ajo y queso y dos huevos", [
            {"id": "synthetic-potatoes", "text": "papas", "checked": False, "parent_id": "synthetic-section"},
            {"id": "synthetic-bread", "text": "pan de ajo y queso", "checked": False, "parent_id": "synthetic-section"},
            {"id": "synthetic-eggs", "text": "dos huevos", "checked": False, "parent_id": "synthetic-section"},
        ], {"decision": "apply", "question": None, "items": [
            {"text": "patatas", "candidate_id": "synthetic-potatoes"},
            {"text": "pan de ajo y queso", "candidate_id": "synthetic-bread"},
            {"text": "dos huevos", "candidate_id": "synthetic-eggs"},
        ]}),
        ("ambiguous-variant", "agrega leche", [
            {"id": "synthetic-whole", "text": "leche entera", "checked": False, "parent_id": "synthetic-section"},
            {"id": "synthetic-lactose-free", "text": "leche sin lactosa", "checked": False, "parent_id": "synthetic-section"},
        ], {"decision": "needs_input"}),
        ("explicit-missing", "me falta otra vez leche sin lactosa", [
            {"id": "synthetic-bought", "text": "leche sin lactosa", "checked": True, "parent_id": "synthetic-section"},
        ], {"decision": "apply", "question": None, "items": [
            {"text": "leche sin lactosa", "candidate_id": "synthetic-bought"},
        ]}),
    ]


def verify_runtime(runtime: Path, manifest_sha256: str, *, manifest_path: Path | None = None) -> dict[str, bytes]:
    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise ValueError("invalid_manifest")
    manifest_path = manifest_path if manifest_path is not None else runtime / "manifest.json"
    raw = manifest_path.read_bytes()
    if manifest_path.is_symlink() or hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise ValueError("manifest_mismatch")
    manifest = json.loads(raw)
    expected = {"mobile_capture_staging_server.py", "grocery_capture.py", "grocery_helper_adapter.py",
                "grocery_item_parser.py", "grocery_ai.py", "grocery_ai_budget.py"}
    if manifest.get("runtime_file_count") != 6 or set(manifest.get("artifacts", {})) != expected:
        raise ValueError("invalid_manifest")
    sources = {}
    for name, digest in manifest["artifacts"].items():
        path = runtime / name
        source = path.read_bytes()
        if path.is_symlink() or hashlib.sha256(source).hexdigest() != digest:
            raise ValueError("runtime_mismatch")
        sources[name] = source
    return sources


def load_runtime(runtime: Path, sources: dict[str, bytes]):
    """Execute exactly verified bytes, bypassing .pyc/extension module selection."""
    modules = {}
    for name in ("grocery_item_parser", "grocery_ai_budget", "grocery_ai"):
        module = types.ModuleType(name)
        module.__file__ = str(runtime / (name + ".py"))
        sys.modules[name] = module
        exec(compile(sources[name + ".py"], module.__file__, "exec"), module.__dict__)
        modules[name] = module
    return modules["grocery_ai"], modules["grocery_ai_budget"]


def run_cases(ai, guard, api_key: str, run_id: str) -> dict:
    """No caller-supplied capture/catalog: only the three synthetic fixtures."""
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", run_id):
        raise ValueError("invalid_run_id")
    before = guard.snapshot()
    results = []
    for name, text, catalog, expected in cases():
        identity = "synthetic-luna-smoke-" + run_id + "-" + name
        canonical = json.dumps({"text": text, "catalog": catalog}, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"))
        payload_hash = hashlib.sha256(canonical.encode()).hexdigest()
        started = time.monotonic()
        try:
            proposal = ai.interpret(text, catalog, api_key=api_key, budget=guard,
                request_id=identity, payload_hash=payload_hash, model="gpt-6-luna", timeout=8)
            valid = (proposal == expected if expected["decision"] == "apply" else
                     proposal.get("decision") == "needs_input" and proposal.get("items") == []
                     and isinstance(proposal.get("question"), str) and bool(proposal["question"].strip()))
            result = {"case": name, "passed": valid,
                      "status": "matched" if valid else "unexpected_interpretation"}
        except ai.InterpretationUnavailable as error:
            result = {"case": name, "passed": False, "status": str(error)}
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        results.append(result)
        # A failure is enough to stop test spending; do not retry or continue.
        if not result["passed"]:
            break
    after = guard.snapshot()
    return {"model": "gpt-6-luna", "reasoning": "low", "synthetic_only": True,
            "grocery_writes": 0, "passed": len(results) == 3 and all(row["passed"] for row in results),
            "cases": results, "month_bogota": after.month,
            "accounted_nanodollars": after.charged_nanodollars,
            "unsettled_reserved_nanodollars": after.reserved_nanodollars,
            "remaining_nanodollars": after.remaining_nanodollars,
            "month_changed": before.month != after.month,
            "guard_accounting_delta_nanodollars": (after.charged_nanodollars - before.charged_nanodollars
                if before.month == after.month else None)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    try:
        sources = verify_runtime(args.runtime, args.manifest_sha256, manifest_path=args.manifest)
        ai, budget = load_runtime(args.runtime.resolve(), sources)
        guard = budget.GroceryAIBudget(args.journal)
        key = ai._read_key_file(args.key_file)
        result = run_cases(ai, guard, key, args.run_id)
    except Exception:
        # Raw exceptions may contain private paths or HTTP bodies.
        print(json.dumps({"passed": False, "status": "synthetic_smoke_unavailable", "grocery_writes": 0}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
