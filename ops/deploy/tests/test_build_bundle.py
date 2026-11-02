"""Offline release provenance tests; no working-tree secrets or network access."""

import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


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
        self.repo = Path(self.directory.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Synthetic Tests")
        self.git("config", "user.email", "synthetic@example.invalid")
        self.write("backend/mobile_capture_staging_server.py", "VALUE = 'reviewed'\n")
        self.write("ops/deploy/release-files.json", json.dumps({"bff": "backend/mobile_capture_staging_server.py"}))
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic release")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.builder = load_builder()

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.repo), *args], check=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        ).stdout

    def write(self, name, text):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def commit_mapping(self, mapping):
        self.write("ops/deploy/release-files.json", json.dumps(mapping))
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic change")
        return self.git("rev-parse", "HEAD").strip()

    def build(self, revision=None):
        return self.builder.build_bundle(self.repo, revision or self.revision)

    def assert_rejected(self, revision=None):
        with self.assertRaises(Exception):
            self.build(revision)

    def test_uses_exact_committed_bytes_and_excludes_worktree_secrets(self):
        self.write("backend/mobile_capture_staging_server.py", "VALUE = 'dirty'\n")
        self.write("backend/local-secret.py", "TOKEN = 'do-not-package'\n")
        self.write(".env", "TOKEN=private-test-sentinel\n")
        bundle = self.build()
        expected = b"VALUE = 'reviewed'\n"
        self.assertEqual(bundle["schema"], 1)
        self.assertEqual(bundle["revision"], self.revision)
        self.assertEqual(set(bundle["files"]), {"bff"})
        entry = bundle["files"]["bff"]
        self.assertEqual(base64.b64decode(entry["content_b64"], validate=True), expected)
        self.assertEqual(entry["sha256"], hashlib.sha256(expected).hexdigest())
        encoded = json.dumps(bundle)
        self.assertNotIn("private-test-sentinel", encoded)
        self.assertNotIn("do-not-package", encoded)
        self.assertNotIn("dirty", encoded)

    def test_bundle_is_reproducible_even_after_newer_commit(self):
        first = self.build()
        self.write("backend/mobile_capture_staging_server.py", "VALUE = 'newer'\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Newer synthetic release")
        self.assertEqual(self.build(), first)

    def test_ref_names_and_abbreviated_revision_are_rejected(self):
        for ref in ("HEAD", "main", self.revision[:12], self.revision.upper(), "../HEAD", "-h"):
            with self.subTest(ref=ref):
                self.assert_rejected(ref)

    def test_optional_reviewed_helper_is_included(self):
        self.write("runtime/apple_watch_capture.py", "HELPER = True\n")
        revision = self.commit_mapping({
            "bff": "backend/mobile_capture_staging_server.py",
            "aura_helper": "runtime/apple_watch_capture.py",
        })
        self.assertEqual(set(self.build(revision)["files"]), {"bff", "aura_helper"})

    def test_unknown_slot_and_missing_bff_are_rejected(self):
        for mapping in ({"secret": "backend/mobile_capture_staging_server.py"}, {"aura_helper": "backend/mobile_capture_staging_server.py"}):
            with self.subTest(mapping=mapping):
                revision = self.commit_mapping(mapping)
                self.assert_rejected(revision)

    def test_non_python_source_is_rejected(self):
        self.write("backend/settings.env", "TOKEN=synthetic\n")
        revision = self.commit_mapping({"bff": "backend/settings.env"})
        self.assert_rejected(revision)

    def test_source_symlink_is_rejected_without_reading_target(self):
        target = self.repo / "backend/mobile_capture_staging_server.py"
        target.unlink()
        target.symlink_to("../private.py")
        self.write("private.py", "SECRET = 'synthetic-secret'\n")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Synthetic symlink")
        self.assert_rejected(self.git("rev-parse", "HEAD").strip())

    def test_mapping_cannot_escape_committed_tree(self):
        for path in ("../private.py", "/tmp/private.py", "backend/../private.py", "backend/.env"):
            with self.subTest(path=path):
                revision = self.commit_mapping({"bff": path})
                self.assert_rejected(revision)

    def test_invalid_or_oversized_python_is_not_a_release(self):
        for source in ("def broken(:\n", "#" + "x" * 400_000):
            with self.subTest(size=len(source)):
                self.write("backend/mobile_capture_staging_server.py", source)
                self.git("add", ".")
                self.git("commit", "--quiet", "-m", "Invalid synthetic source")
                self.assert_rejected(self.git("rev-parse", "HEAD").strip())

    def test_two_slots_cannot_alias_the_same_source(self):
        revision = self.commit_mapping({
            "bff": "backend/mobile_capture_staging_server.py",
            "aura_helper": "backend/mobile_capture_staging_server.py",
        })
        self.assert_rejected(revision)


if __name__ == "__main__":
    unittest.main()
