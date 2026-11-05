"""Offline packaging tests against the exact accepted six-file grocery base.

Only earlier packaging tools are executed to reconstruct historical source.
Application sources are read, parsed and compiled; this suite never imports
them or contacts a provider. Runtime HTTP acceptance is a separate test suite.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


def packager(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


build = packager("_reminder_packager_under_test", Path(__file__).with_name("build_release.py"))
ROOT = build.ROOT


class ReminderReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prior = packager("_prior_grocery_packager_for_reminder_test", ROOT / "ops/hotfixes/grocery-multi-item/build_release.py")
        old_baseline = prior.baseline_source(ROOT)
        cls.grocery_server = prior.overlay_server(old_baseline, prior.OVERLAY.read_bytes())
        cls.base_manifest = build.baseline_manifest()
        build.require_digest(cls.grocery_server, build.BASELINE_SERVER_SHA256, "Reconstructed accepted server")
        cls.grocery_modules = {}
        for name in build.GROCERY_MODULES:
            cls.grocery_modules[name] = subprocess.check_output(["git", "-C", str(ROOT), "show", "f8738b9:backend/" + name])
            build.require_digest(cls.grocery_modules[name], cls.base_manifest["artifacts"][name], name)
        cls.reminder_modules = {name: (ROOT / "backend" / name).read_bytes() for name in build.REMINDER_MODULES}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(
            prefix="personal-watch-release-test-", dir=Path(tempfile.gettempdir()).resolve(),
        )
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.grocery = self.directory / "grocery-release"
        self.grocery.mkdir()
        (self.grocery / build.SERVER).write_bytes(self.grocery_server)
        for name, source in self.grocery_modules.items():
            (self.grocery / name).write_bytes(source)
        (self.grocery / "manifest.json").write_bytes(build.encoded(self.base_manifest))
        self.repo = self.directory / "source"
        (self.repo / "backend").mkdir(parents=True)
        for name, source in self.reminder_modules.items():
            (self.repo / "backend" / name).write_bytes(source)
        self.output = self.directory / "release"

    def prepare(self):
        expected, runtime = build.candidate(self.grocery, self.repo)
        build.build_release(self.grocery, self.output, self.repo, expected)
        return expected, runtime

    def test_pinned_manifest_has_exact_accepted_seal_and_six_pins(self):
        self.assertEqual(build.digest(build.BASELINE_MANIFEST.read_bytes()), "bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281")
        self.assertEqual(self.base_manifest["runtime_file_count"], 6)
        self.assertEqual(set(self.base_manifest["artifacts"]), set(build.GROCERY_ARTIFACTS))
        self.assertEqual(self.base_manifest["artifacts"][build.SERVER], build.BASELINE_SERVER_SHA256)

    def test_candidate_exact_count_byte_identical_groceries_and_baseline_hashes(self):
        expected, runtime = self.prepare()
        self.assertEqual(expected["runtime_file_count"], 12)
        self.assertEqual(set(runtime), set(build.ARTIFACTS))
        self.assertEqual(set(path.name for path in self.output.iterdir()), set((*build.ARTIFACTS, "manifest.json")))
        self.assertEqual(expected["baseline_artifacts"], self.base_manifest["artifacts"])
        self.assertEqual(expected["grocery_manifest_sha256"], build.GROCERY_MANIFEST_SHA256)
        for name in build.GROCERY_MODULES:
            self.assertEqual((self.output / name).read_bytes(), (self.grocery / name).read_bytes())
            self.assertEqual(expected["artifacts"][name], self.base_manifest["artifacts"][name])
        build.verify_release(self.output, expected)

    def test_exact_overlay_recovers_original_server_bytes(self):
        released = build.overlay_server(self.grocery_server)
        self.assertEqual(build.baseline_from_release(released), self.grocery_server)
        build.assert_scope(self.grocery_server, released)
        tree = ast.parse(released)
        handler = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MobileCaptureHandler")
        for name in ("do_GET", "do_POST"):
            old_tree = build._handler(ast.parse(self.grocery_server))
            original = copy.deepcopy(build._method(old_tree, name))
            renamed = copy.deepcopy(build._method(handler, "_grocery_" + name))
            renamed.name = name
            self.assertEqual(ast.dump(original), ast.dump(renamed))

    def test_config_cli_init_db_and_gate_source_segments_remain_byte_identical(self):
        released = build.overlay_server(self.grocery_server)
        old, new = self.grocery_server.decode(), released.decode()
        old_nodes, new_nodes = ast.parse(old).body, ast.parse(new).body
        for name in ("Config", "init_db", "parse_args", "execute_capture", "write_is_authorized", "run_server"):
            original = next(node for node in old_nodes if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name)
            changed = next(node for node in new_nodes if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name)
            self.assertEqual(ast.get_source_segment(old, original), ast.get_source_segment(new, changed), name)
        def gates(tree):
            return [ast.dump(node) for node in tree if isinstance(node, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id in {"SUPPORTED_WRITE_DOMAINS", "SUPPORTED_WRITE_ROUTES"} for target in node.targets)]
        self.assertEqual(gates(old_nodes), gates(new_nodes))

    def test_candidate_and_package_are_reproducible(self):
        first, runtime = self.prepare()
        second, second_runtime = build.candidate(self.grocery, self.repo)
        self.assertEqual(build.encoded(first), build.encoded(second))
        self.assertEqual(runtime, second_runtime)
        other = self.directory / "second-release"
        build.build_release(self.grocery, other, self.repo, first)
        for path in self.output.iterdir():
            self.assertEqual(path.read_bytes(), (other / path.name).read_bytes())

    def test_current_checkout_grocery_modules_are_never_substituted(self):
        for name in build.GROCERY_MODULES:
            (self.repo / "backend" / name).write_bytes(b"raise AssertionError('not the accepted grocery module')\n")
        _, runtime = build.candidate(self.grocery, self.repo)
        for name in build.GROCERY_MODULES:
            self.assertEqual(runtime[name], self.grocery_modules[name])

    def test_builder_compiles_but_does_not_execute_application_code(self):
        module = self.repo / "backend" / "reminder_runtime.py"
        module.write_bytes(module.read_bytes() + b"\nraise AssertionError('application executed during packaging')\n")
        expected, _ = build.candidate(self.grocery, self.repo)
        build.build_release(self.grocery, self.output, self.repo, expected)
        build.verify_release(self.output, expected)

    def test_tampering_any_runtime_file_or_manifest_fails_verification(self):
        expected, _ = self.prepare()
        for name in (*build.ARTIFACTS, "manifest.json"):
            with self.subTest(file=name):
                path = self.output / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n# synthetic tamper\n")
                with self.assertRaises((ValueError, SyntaxError)):
                    build.verify_release(self.output, expected)
                path.write_bytes(original)

    def test_wrong_baseline_blob_or_manifest_produces_no_candidate_output(self):
        for name in (*build.GROCERY_ARTIFACTS, "manifest.json"):
            with self.subTest(file=name):
                path = self.grocery / name
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaises(ValueError):
                    build.candidate(self.grocery, self.repo)
                self.assertFalse(self.output.exists())
                path.write_bytes(original)

    def test_altered_pinned_manifest_is_rejected(self):
        pinned = self.directory / "altered-baseline.json"
        pinned.write_bytes(build.BASELINE_MANIFEST.read_bytes() + b"\n")
        with mock.patch.object(build, "BASELINE_MANIFEST", pinned):
            with self.assertRaisesRegex(ValueError, "Pinned grocery manifest"):
                build.candidate(self.grocery, self.repo)

    def test_missing_baseline_or_runtime_files_fail(self):
        for path in (self.grocery / "grocery_ai.py", self.repo / "backend" / "reminder_capture.py"):
            with self.subTest(file=path.name):
                original = path.read_bytes()
                path.unlink()
                with self.assertRaises((ValueError, FileNotFoundError)):
                    build.candidate(self.grocery, self.repo)
                path.write_bytes(original)

    def test_input_output_and_source_symlinks_are_rejected(self):
        alias = self.directory / "grocery-alias"
        alias.symlink_to(self.grocery, target_is_directory=True)
        with self.assertRaises(ValueError):
            build.candidate(alias, self.repo)
        module = self.repo / "backend" / "reminder_capture.py"
        original = module.read_bytes()
        target = self.directory / "module.py"
        target.write_bytes(original)
        module.unlink()
        module.symlink_to(target)
        with self.assertRaises(ValueError):
            build.candidate(self.grocery, self.repo)
        module.unlink()
        module.write_bytes(original)
        expected, _ = self.prepare()
        output_alias = self.directory / "release-alias"
        output_alias.symlink_to(self.output, target_is_directory=True)
        with self.assertRaises(ValueError):
            build.verify_release(output_alias, expected)
        artifact = self.output / "reminder_capture.py"
        artifact.unlink()
        artifact.symlink_to(target)
        with self.assertRaises(ValueError):
            build.verify_release(self.output, expected)

    def test_input_and_output_hardlinks_are_rejected_even_with_correct_bytes(self):
        artifact = self.grocery / "grocery_ai.py"
        outside = self.directory / "grocery-hardlink.py"
        os.link(artifact, outside)
        with self.assertRaises(ValueError):
            build.candidate(self.grocery, self.repo)
        outside.unlink()
        expected, _ = self.prepare()
        os.link(self.output / "reminder_ai.py", self.directory / "reminder-hardlink.py")
        with self.assertRaises(ValueError):
            build.verify_release(self.output, expected)

    def test_extras_directories_and_manifest_aliases_are_rejected(self):
        for kind in ("file", "directory"):
            extra = self.grocery / "unexpected"
            extra.mkdir() if kind == "directory" else extra.write_text("synthetic")
            with self.assertRaises(ValueError):
                build.candidate(self.grocery, self.repo)
            extra.rmdir() if kind == "directory" else extra.unlink()
        expected, _ = self.prepare()
        (self.output / ".DS_Store").write_bytes(b"synthetic")
        with self.assertRaises(ValueError):
            build.verify_release(self.output, expected)

    def test_existing_output_and_racing_output_are_not_replaced(self):
        expected, _ = build.candidate(self.grocery, self.repo)
        self.output.mkdir()
        sentinel = self.output / "keep.txt"
        sentinel.write_text("keep")
        with self.assertRaises(ValueError):
            build.build_release(self.grocery, self.output, self.repo, expected)
        self.assertEqual(sentinel.read_text(), "keep")
        other = self.directory / "racing-output"
        original_mkdir = Path.mkdir
        def appearing(path, *args, **kwargs):
            if path == other:
                original_mkdir(path)
                (path / "keep.txt").write_text("racing keep")
                raise FileExistsError("synthetic race")
            return original_mkdir(path, *args, **kwargs)
        with mock.patch.object(Path, "mkdir", autospec=True, side_effect=appearing):
            with self.assertRaises(ValueError):
                build.build_release(self.grocery, other, self.repo, expected)
        self.assertEqual((other / "keep.txt").read_text(), "racing keep")

    def test_unsealed_default_build_fails_without_creating_output(self):
        with mock.patch.object(build, "MANIFEST", self.directory / "missing-seal.json"):
            with self.assertRaisesRegex(ValueError, "not sealed"):
                build.build_release(self.grocery, self.output, self.repo)
        self.assertFalse(self.output.exists())

    def test_explicit_test_local_seal_is_immutable_and_detects_source_change(self):
        seal = self.directory / "test-local-seal.json"
        first = build.seal_manifest(self.grocery, self.repo, seal)
        original = seal.read_bytes()
        self.assertEqual(build.seal_manifest(self.grocery, self.repo, seal), first)
        path = self.repo / "backend" / "reminder_runtime.py"
        path.write_bytes(path.read_bytes() + b"\n# synthetic source revision\n")
        with self.assertRaisesRegex(ValueError, "different release seal"):
            build.seal_manifest(self.grocery, self.repo, seal)
        self.assertEqual(seal.read_bytes(), original)
        with self.assertRaisesRegex(ValueError, "differs from sealed"):
            build.build_release(self.grocery, self.output, self.repo, first)
        self.assertFalse(self.output.exists())

    def test_invalid_manifest_cannot_change_base_hashes_count_or_grocery_blobs(self):
        expected, _ = build.candidate(self.grocery, self.repo)
        for field, value in (
            ("grocery_manifest_sha256", "0" * 64), ("runtime_file_count", 11),
            ("baseline_artifacts", {}), ("external_helper_sha256", "0" * 64),
            ("added_modules", []), ("scope", "broader scope"),
        ):
            altered = {**expected, field: value}
            with self.subTest(field=field), self.assertRaises(ValueError):
                build.build_release(self.grocery, self.output, self.repo, altered)
        altered = copy.deepcopy(expected)
        altered["artifacts"]["grocery_ai.py"] = "0" * 64
        with self.assertRaises(ValueError):
            build.build_release(self.grocery, self.output, self.repo, altered)

    def test_unrelated_server_changes_fail_scope_even_with_recomputed_package_hash(self):
        expected, runtime = self.prepare()
        server = self.output / build.SERVER
        for modified in (
            runtime[build.SERVER] + b"\n# unrelated server edit\n",
            runtime[build.SERVER].replace(b"8650", b"8651", 1),
            runtime[build.SERVER].replace(b"self._grocery_do_POST, globals()", b"self._grocery_do_POST, {}", 1),
            runtime[build.SERVER].replace(b"def _grocery_do_POST(self)", b"def _different_do_POST(self)", 1),
        ):
            with self.subTest(size=len(modified)):
                self.assertNotEqual(modified, runtime[build.SERVER])
                with self.assertRaises(ValueError):
                    build.assert_scope(self.grocery_server, modified)
                altered = copy.deepcopy(expected)
                altered["artifacts"][build.SERVER] = build.digest(modified)
                server.write_bytes(modified)
                (self.output / "manifest.json").write_bytes(build.encoded(altered))
                with self.assertRaises(ValueError):
                    build.verify_release(self.output, altered)

    def test_nonstdlib_relative_dynamic_and_missing_import_closure_fails(self):
        _, runtime = build.candidate(self.grocery, self.repo)
        for added in (b"\nimport requests\n", b"\nfrom . import hidden_dependency\n", b"\n__import__('hidden_dependency')\n", b"\nimport importlib\nimportlib.import_module('hidden_dependency')\n"):
            altered = dict(runtime)
            altered["reminder_runtime.py"] += added
            with self.subTest(added=added), self.assertRaises(ValueError):
                build.assert_import_closure(altered)
        with self.assertRaises(ValueError):
            build.assert_import_closure({key: value for key, value in runtime.items() if key != "reminder_ai_budget.py"})
        with self.assertRaises(ValueError):
            build.assert_import_closure({**runtime, "extra.py": b""})

    def test_syntax_error_is_found_before_output_creation(self):
        path = self.repo / "backend" / "reminder_runtime.py"
        path.write_bytes(b"def incomplete(:\n")
        with self.assertRaises(SyntaxError):
            build.candidate(self.grocery, self.repo)
        self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
