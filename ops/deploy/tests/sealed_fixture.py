"""Synthetic sealed grocery package shared by offline deployment tests."""

import base64
import hashlib
import json


FILENAMES = (
    "mobile_capture_staging_server.py", "grocery_capture.py",
    "grocery_helper_adapter.py", "grocery_item_parser.py",
)
INPUT_PATHS = (
    "ops/hotfixes/grocery-multi-item/build_release.py",
    "ops/hotfixes/grocery-multi-item/release_manifest.json",
    "ops/hotfixes/grocery-multi-item/server_overlay.py",
    "ops/hotfixes/grocery-live-clarification/build_release.py",
    "ops/hotfixes/grocery-live-clarification/legacy-970e14d.patch",
    "backend/grocery_capture.py", "backend/grocery_helper_adapter.py",
    "backend/grocery_item_parser.py",
)
LEGACY = b"print('synthetic-legacy-single-item')\n"
HELPER = b"HELPER = 'synthetic-external-helper'\n"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def digest(value):
    return hashlib.sha256(value).hexdigest()


def fixture():
    artifacts = {
        "mobile_capture_staging_server.py": b"import json\nimport grocery_capture\nif __name__ == '__main__':\n    print(json.dumps({'package': grocery_capture.VALUE}))\n",
        "grocery_capture.py": b"import grocery_helper_adapter\nVALUE = grocery_helper_adapter.VALUE\n",
        "grocery_helper_adapter.py": b"import grocery_item_parser\nVALUE = grocery_item_parser.VALUE\n",
        "grocery_item_parser.py": b"VALUE = 'synthetic-sealed-batch'\n",
    }
    manifest = {
        "artifacts": {name: digest(value) for name, value in artifacts.items()},
        "baseline_sha256": digest(LEGACY),
        "external_helper_sha256": digest(HELPER),
        "format_version": 1, "journal_suffix": ".grocery.sqlite3",
        "overlay_sha256": digest(b"# synthetic overlay\n"),
        "rollback": "Once batch state exists, use a compatible runtime or disable writes; never replay through the old single-item writer",
        "runtime_file_count": 4,
        "scope": "Historical grocery-only batch runtime; unrelated historical functions remain unchanged",
    }
    raw = (json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()
    profile = {
        "schema": 1, "package_revision": "1" * 40,
        "state_compatibility": "grocery-batch-v1",
        "manifest_path": "ops/hotfixes/grocery-multi-item/release_manifest.json",
        "manifest_sha256": digest(raw), "sealed_manifest": manifest,
        "inputs": {path: digest(("synthetic input: " + path).encode()) for path in INPUT_PATHS},
    }
    bundle = {
        "schema": 2, "revision": profile["package_revision"],
        "manifest_b64": base64.b64encode(raw).decode(),
        "files": {name: {"sha256": digest(value), "content_b64": base64.b64encode(value).decode()} for name, value in artifacts.items()},
    }
    return artifacts, manifest, raw, profile, bundle
