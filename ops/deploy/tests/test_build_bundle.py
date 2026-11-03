"""Offline checks for the sealed four-artifact package builder."""

import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from sealed_fixture import FILENAMES, INPUT_PATHS, digest, fixture

MODULE_PATH = Path(__file__).resolve().parents[1] / "build_bundle.py"


def load_builder():
    spec = importlib.util.spec_from_file_location("watch_build_bundle_test", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildBundleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name).resolve()
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Tests")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.artifacts, self.manifest, self.raw_manifest, self.profile, self.bundle = fixture()
        for path in INPUT_PATHS:
            self.write(path, b"# synthetic input\n")
        for name in FILENAMES[1:]:
            self.write("backend/" + name, self.artifacts[name])
        self.write(self.profile["manifest_path"], self.raw_manifest)
        self.write("ops/hotfixes/grocery-multi-item/build_release.py", self.packager())
        self.write("backend/mobile_capture_staging_server.py", b"raise RuntimeError('paused-unrelated-mvp')\n")
        self.write(".env", b"TOKEN=synthetic-private-sentinel\n")
        self.commit()
        self.builder = load_builder()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.repo), *args], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True).stdout

    def write(self, name, data):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data if isinstance(data, bytes) else data.encode())
        return path

    def commit(self):
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic sealed package")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.profile["package_revision"] = self.revision
        self.profile["inputs"] = {name: digest((self.repo / name).read_bytes()) for name in INPUT_PATHS}

    def packager(self, alteration=""):
        encoded = {name: base64.b64encode(content).decode() for name, content in self.artifacts.items()}
        return ("import argparse,base64,json,os\nfrom pathlib import Path\n"
                "p=argparse.ArgumentParser();p.add_argument('--repo');p.add_argument('--output',required=True);a=p.parse_args()\n"
                "assert 'SYNTHETIC_PRIVATE_TOKEN' not in os.environ\n"
                "out=Path(a.output);out.mkdir(parents=True,exist_ok=True)\n"
                "files=" + repr(encoded) + "\n"
                "for name,data in files.items(): (out/name).write_bytes(base64.b64decode(data))\n"
                "(out/'manifest.json').write_bytes(base64.b64decode(" + repr(base64.b64encode(self.raw_manifest).decode()) + "))\n" + alteration).encode()

    def build(self, revision=None, profile=None):
        return self.builder.build_bundle(self.repo, revision or self.revision, profile=profile or self.profile)

    def rejected(self, **kwargs):
        with self.assertRaises((ValueError, OSError, SyntaxError, subprocess.SubprocessError)):
            self.build(**kwargs)

    def test_builds_exact_sealed_artifacts_and_never_the_standalone_mvp(self):
        with mock.patch.dict(os.environ, {"SYNTHETIC_PRIVATE_TOKEN": "private-test-value"}):
            bundle = self.build()
        self.assertEqual(bundle["schema"], 2)
        self.assertEqual(bundle["revision"], self.revision)
        self.assertEqual(base64.b64decode(bundle["manifest_b64"], validate=True), self.raw_manifest)
        self.assertEqual(set(bundle["files"]), set(FILENAMES))
        for name, expected in self.artifacts.items():
            self.assertEqual(base64.b64decode(bundle["files"][name]["content_b64"], validate=True), expected)
            self.assertEqual(bundle["files"][name]["sha256"], self.manifest["artifacts"][name])
        text = json.dumps(bundle)
        self.assertNotIn("private-test-value", text)
        self.assertNotIn("synthetic-private-sentinel", text)
        self.assertNotIn("paused-unrelated-mvp", text)

    def test_dirty_or_untracked_inputs_cannot_change_the_pinned_release(self):
        expected = self.build()
        self.write("backend/grocery_capture.py", b"VALUE='unreviewed-dirty'\n")
        self.write("ops/hotfixes/grocery-multi-item/build_release.py", b"raise RuntimeError('dirty')\n")
        self.write("backend/untracked-private.py", b"TOKEN='untracked-sentinel'\n")
        self.assertEqual(self.build(), expected)

    def test_revision_must_be_exactly_the_trusted_package_commit(self):
        for revision in ("HEAD", self.revision[:12], "0" * 40, self.revision.upper(), "-h"):
            with self.subTest(revision=revision):
                self.rejected(revision=revision)
        self.write("unrelated.txt", b"newer commit\n")
        previous = self.revision
        profile = copy.deepcopy(self.profile)
        self.commit()
        self.rejected(profile=profile)
        self.assertEqual(self.build(previous, profile)["revision"], previous)

    def test_trusted_input_hashes_detect_changed_pinned_source(self):
        profile = copy.deepcopy(self.profile)
        self.write("backend/grocery_capture.py", b"VALUE='changed-after-review'\n")
        self.commit()
        profile["package_revision"] = self.revision
        self.rejected(profile=profile)

    def test_input_path_set_cannot_include_secrets_or_omit_a_dependency(self):
        for mutate in (lambda p: p["inputs"].update({".env": digest((self.repo / ".env").read_bytes())}), lambda p: p["inputs"].pop(INPUT_PATHS[0])):
            profile = copy.deepcopy(self.profile)
            mutate(profile)
            self.rejected(profile=profile)

    def test_source_manifest_requires_exact_raw_and_decoded_seal_agreement(self):
        self.write(self.profile["manifest_path"], self.raw_manifest + b"\n")
        self.commit()
        self.rejected()
        changed = copy.deepcopy(self.manifest)
        changed["artifacts"][FILENAMES[0]] = "0" * 64
        self.write(self.profile["manifest_path"], json.dumps(changed, sort_keys=True, indent=2) + "\n")
        self.commit()
        self.rejected()

    def test_symlinked_packager_or_runtime_input_is_rejected(self):
        for path in (INPUT_PATHS[0], "backend/grocery_capture.py"):
            with self.subTest(path=path):
                source = self.repo / path
                original = source.read_bytes()
                source.unlink()
                self.write("synthetic-target.py", original)
                source.symlink_to(self.repo / "synthetic-target.py")
                self.commit()
                self.rejected()
                source.unlink()
                source.write_bytes(original)
                self.commit()

    def test_packager_output_rejects_extra_missing_changed_or_symlinked_artifacts(self):
        alterations = (
            "(out/'extra.py').write_text('extra')\n",
            "(out/'grocery_capture.py').unlink()\n",
            "(out/'grocery_capture.py').write_text('VALUE=999')\n",
            "(out/'grocery_capture.py').unlink();(out/'grocery_capture.py').symlink_to(out/'grocery_item_parser.py')\n",
            "(out/'manifest.json').write_text('{}')\n",
        )
        for alteration in alterations:
            with self.subTest(alteration=alteration):
                self.write(INPUT_PATHS[0], self.packager(alteration))
                self.commit()
                self.rejected()


if __name__ == "__main__":
    unittest.main()
