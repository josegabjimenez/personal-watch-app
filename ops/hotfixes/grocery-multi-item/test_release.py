"""Offline checks of the actual four-file historical release.

Notion is replaced with the existing synthetic AST-helper fixture/FakeNotion.
Handlers receive in-memory requests; no loopback listener or real HTTP runs.
"""
from __future__ import annotations

from contextlib import ExitStack
import copy
import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest import mock

import build_release as build

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "backend/tests"), str(ROOT / "backend")]
from fixtures import grocery_helper_contract as contract
from test_grocery_helper_adapter import FakeNotion, block


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # Historical Config is a dataclass.
    exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
    return module


source_fixture = load_module("_historical_grocery_source_fixture", Path(__file__).with_name("test_fixture.py"))


class HistoricalMultiItemReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.directory = Path(cls.temp.name)
        cls.snapshot = cls.directory / "snapshot"
        source_fixture.populate_snapshot(ROOT, cls.snapshot, build.RUNTIME_MODULES)
        cls.baseline = build.baseline_source(ROOT)
        # A test-local explicit candidate manifest does not seal repository pins.
        cls.expected, _ = build.candidate(cls.baseline, cls.snapshot)
        cls.release = cls.directory / "release"
        build.build_release(cls.baseline, cls.release, cls.snapshot, cls.expected)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_case.cleanup)
        self.case = Path(self.temp_case.name)
        self.saved = {name: sys.modules.get(name) for name in (
            "grocery_item_parser", "grocery_helper_adapter", "grocery_capture", "_historical_grocery_test_server")}
        self.addCleanup(self.restore_modules)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.helper_source = contract.source()
        self.helper = self.case / "synthetic_helper.py"
        self.helper.write_text(self.helper_source)
        self.fake = FakeNotion()
        self.stack.enter_context(mock.patch.dict("os.environ", {
            "AURA_CAPTURE_HOME": str(self.case / "synthetic-home"), "NOTION_API_TOKEN": "synthetic-token",
        }, clear=True))
        self.stack.enter_context(mock.patch("urllib.request.urlopen", side_effect=self.fake.open))
        self.restart()
        self.payload = {
            "event_type": "mobile_capture.v1", "schema_version": 1,
            "request_id": "synthetic-historical-batch",
            "route": {"agent": "aura", "intent": "grocery", "domain": "aura.grocery_capture"},
            "capture": {"text": "agrega leche y huevos al mercado"},
            "context": {"dry_run": False, "allow_write": True, "allow_firefly_write": False},
        }

    def restore_modules(self):
        for name, original in self.saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original

    def restart(self):
        for name in ("grocery_item_parser", "grocery_helper_adapter", "grocery_capture"):
            module = load_module(name, self.release / (name + ".py"))
            if name == "grocery_helper_adapter":
                self.adapter = module
                self.stack.enter_context(mock.patch.object(module, "PINNED_HELPER_SHA256",
                    hashlib.sha256(self.helper_source.encode()).hexdigest()))
            elif name == "grocery_capture":
                self.engine = module
        self.srv = load_module("_historical_grocery_test_server", self.release / build.SERVER)
        self.cfg = self.srv.Config(secret=b"synthetic-historical-secret", ledger_path=self.case / "mobile.sqlite3",
            host="127.0.0.1", port=0, write_enabled=True,
            write_domains=frozenset({"aura.grocery_capture"}), aura_helper=self.helper)

    def raw(self, payload=None):
        return json.dumps(self.payload if payload is None else payload, ensure_ascii=False,
                          sort_keys=True, separators=(",", ":")).encode()

    def invoke(self, payload=None, *, raw=None, signature=None, request_header=None, disconnect=False):
        payload = self.payload if payload is None else payload
        raw = self.raw(payload) if raw is None else raw
        timestamp = str(int(time.time()))
        signed = hmac.new(self.cfg.secret, timestamp.encode() + b"." + raw, hashlib.sha256).hexdigest()
        from types import SimpleNamespace
        handler = SimpleNamespace(path="/webhooks/mobile-capture-v1", cfg=self.cfg,
            headers={"Content-Length": str(len(raw)), "X-Webhook-Timestamp": timestamp,
                "X-Webhook-Signature-V2": signed if signature is None else signature,
                "X-Request-ID": payload["request_id"] if request_header is None else request_header},
            rfile=io.BytesIO(raw))
        with mock.patch.object(self.srv, "json_response") as reply:
            if disconnect:
                reply.side_effect = ConnectionError("synthetic client lost acknowledgement")
            self.srv.MobileCaptureHandler.do_POST(handler)
            reply.assert_called_once()
            return reply.call_args.args[1:]

    def parent(self):
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            return conn.execute("SELECT * FROM mobile_capture_ledger WHERE request_id=?", (self.payload["request_id"],)).fetchone()

    def test_actual_package_uses_only_four_pinned_runtime_files_and_no_held_imports(self):
        self.assertEqual(sorted(path.name for path in self.release.iterdir()), sorted((*build.ARTIFACTS, "manifest.json")))
        build.verify_release(self.release, self.expected)
        self.assertEqual(self.expected["baseline_sha256"], build.BASELINE_SHA256)
        self.assertEqual(self.expected["external_helper_sha256"], contract.ORIGINAL_SHA256)
        self.assertIs(self.srv.grocery_capture, self.engine)
        self.assertEqual(Path(self.engine.__file__).parent, self.release)
        self.assertEqual(Path(self.adapter.__file__).parent, self.release)
        for unwanted in ("reminder_ai", "reminder_capture", "todoist_reminders", "execute_expense_capture", "EXPENSE_CONFIRMATION_TTL_SECONDS"):
            self.assertNotIn(unwanted, self.srv.__dict__)
        build.assert_scope(self.baseline, (self.release / build.SERVER).read_bytes(), build.OVERLAY.read_bytes())

    def test_two_items_and_quantities_run_actual_extracted_helper_contract(self):
        self.payload["capture"]["text"] = "2 litros de leche y 6 huevos"
        code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(response["status"], "committed")
        self.assertIs(response["write_committed"], True)
        self.assertEqual(response["external_ids"]["item_count"], 2)
        self.assertEqual([receipt["requested_item"] for receipt in response["external_ids"]["grocery_items"]],
                         ["2 litros de leche", "6 huevos"])
        self.assertEqual(len(self.fake.writes), 2)
        self.assertTrue(all(self.fake.blocks[block_id]["to_do"]["checked"] is False
                            for block_id in response["external_ids"]["notion_block_ids"]))
        self.assertEqual(self.parent()[1], hashlib.sha256(self.raw()).hexdigest())
        with sqlite3.connect(self.engine.journal_path(self.cfg.ledger_path)) as conn:
            self.assertEqual(conn.execute("SELECT payload_hash,status FROM grocery_batches").fetchone(),
                             (hashlib.sha256(self.raw()).hexdigest(), "committed"))

    def test_parent_claim_is_durable_and_transactions_closed_before_every_notion_request(self):
        original = self.fake.open
        def assert_unlocked(req, timeout):
            row = self.parent()
            self.assertIsNotNone(row)
            self.assertEqual(row[3], "processing")
            for path in (self.cfg.ledger_path, self.engine.journal_path(self.cfg.ledger_path)):
                with sqlite3.connect(path, timeout=0) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    conn.rollback()
            return original(req, timeout)
        with mock.patch("urllib.request.urlopen", side_effect=assert_unlocked):
            code, _ = self.invoke()
        self.assertEqual(code, 200)

    def test_preflight_read_outage_can_restart_and_retry_without_writes_or_replanning_loss(self):
        self.fake.bad_cursor = True
        code, _ = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual(self.fake.writes, [])
        original = self.parent()
        self.assertEqual(original[3], "grocery_pending")
        self.fake.bad_cursor = False
        self.restart()
        code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(response["external_ids"]["item_count"], 2)
        self.assertEqual(self.parent()[1], original[1])
        self.assertEqual(self.parent()[5], original[5])
        self.assertEqual(len(self.fake.writes), 2)

    def test_partial_lost_ack_keeps_parent_progress_across_runtime_restart_without_replay(self):
        def fail_second():
            if len(self.fake.writes) == 1:
                self.fake.after_mutation_error = TimeoutError("synthetic lost item acknowledgement")
        self.fake.before_mutation = fail_second
        code, response = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual((response["completed_count"], response["total_count"]), (1, 2))
        self.assertNotIn("write_committed", response)
        original = self.parent()
        self.assertEqual(original[3], "grocery_pending")
        self.fake.before_mutation = self.fake.after_mutation_error = None
        self.restart()
        for _ in range(2):
            code, response = self.invoke()
            self.assertEqual(code, 503)
            self.assertEqual(response["completed_count"], 1)
            self.assertEqual(self.parent()[1], original[1])
            self.assertEqual(self.parent()[5], original[5])
        self.assertEqual(len(self.fake.writes), 2)

    def test_acknowledged_candidate_reconciles_read_only_then_finishes_remaining_item(self):
        self.fake.fail_readback = True
        code, _ = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual(len(self.fake.writes), 1)
        created = self.parent()[5]
        self.fake.fail_readback = False
        self.restart()
        code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(response["external_ids"]["created"], 2)
        self.assertEqual(len(self.fake.writes), 2)
        self.assertEqual(self.parent()[5], created)

    def test_lost_parent_ack_and_completed_cache_never_recheck_or_dispatch(self):
        with self.assertRaises(ConnectionError):
            self.invoke(disconnect=True)
        self.assertEqual(self.parent()[3], "committed")
        self.assertEqual(len(self.fake.writes), 2)
        calls = len(self.fake.calls)
        self.restart()
        with mock.patch.object(self.engine, "execute", side_effect=AssertionError("completed parent executed")):
            code, replay = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(replay["status"], "duplicate")
        self.assertEqual(len(self.fake.calls), calls)
        self.assertEqual(len(self.fake.writes), 2)

    def test_same_id_with_changed_raw_bytes_returns_409_without_touching_state(self):
        self.invoke()
        parent = self.parent()
        journal = self.engine.journal_path(self.cfg.ledger_path)
        before = journal.read_bytes()
        changed = self.raw() + b" "
        with mock.patch.object(self.engine, "execute", side_effect=AssertionError("hash conflict executed")):
            code, response = self.invoke(raw=changed)
        self.assertEqual(code, 409)
        self.assertEqual(response["error"], "request_id_conflict")
        self.assertEqual(self.parent(), parent)
        self.assertEqual(journal.read_bytes(), before)

    def test_restored_missing_parent_wrong_body_cannot_mask_original_batch_replay(self):
        self.invoke()
        journal = self.engine.journal_path(self.cfg.ledger_path)
        before = journal.read_bytes()
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.execute("DELETE FROM mobile_capture_ledger")  # Synthetic partial-restore simulation only.
        changed = copy.deepcopy(self.payload)
        changed["capture"]["text"] = "arroz"
        code, _ = self.invoke(changed)
        self.assertEqual(code, 409)
        self.assertIsNone(self.parent(), "Only the conflicting new processing claim must be removed")
        self.assertEqual(journal.read_bytes(), before)
        code, response = self.invoke()
        self.assertEqual(code, 200)
        self.assertEqual(response["status"], "duplicate")
        self.assertEqual(len(self.fake.writes), 2)

    def test_pending_parent_with_missing_batch_or_item_state_never_recreates_or_replays(self):
        self.fake.fail_readback = True
        self.assertEqual(self.invoke()[0], 503)
        journal = self.engine.journal_path(self.cfg.ledger_path)
        original_parent = self.parent()
        writes = len(self.fake.writes)
        backup = journal.read_bytes()
        journal.unlink()  # Synthetic state-loss simulation only.
        with mock.patch.object(self.engine, "GroceryAdapter", side_effect=AssertionError("missing journal reached helper")):
            code, _ = self.invoke()
        self.assertEqual(code, 503)
        self.assertFalse(journal.exists())
        self.assertEqual(self.parent()[1], original_parent[1])
        journal.write_bytes(backup)
        with sqlite3.connect(journal) as conn:
            conn.execute("DELETE FROM grocery_operations WHERE status='prepared'")
        self.fake.fail_readback = False
        code, _ = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual(len(self.fake.writes), writes)

    def test_missing_item_row_keeps_last_known_progress_without_replaying(self):
        def fail_second():
            if len(self.fake.writes) == 1:
                self.fake.after_mutation_error = TimeoutError("synthetic lost acknowledgement")
        self.fake.before_mutation = fail_second
        self.assertEqual(self.invoke()[1]["completed_count"], 1)
        journal = self.engine.journal_path(self.cfg.ledger_path)
        saved = journal.read_bytes()
        self.fake.blocks["synthetic-created-1"]["to_do"]["checked"] = True
        self.fake.before_mutation = self.fake.after_mutation_error = None
        calls = len(self.fake.calls)
        journal.unlink()  # Synthetic missing-journal recovery check.
        with mock.patch.object(self.engine, "GroceryAdapter", side_effect=AssertionError("missing journal reached helper")):
            code, response = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual((response["completed_count"], response["total_count"]), (1, 2))
        self.assertFalse(journal.exists())
        self.assertEqual(len(self.fake.calls), calls)
        self.assertIs(self.fake.blocks["synthetic-created-1"]["to_do"]["checked"], True)
        journal.write_bytes(saved)
        with sqlite3.connect(journal) as conn:
            conn.execute("DELETE FROM grocery_operations WHERE status='committed'")
        code, response = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual((response["completed_count"], response["total_count"]), (1, 2))
        self.assertEqual(len(self.fake.writes), 2)
        self.assertEqual(json.loads(self.parent()[4])["completed_count"], 1)
        self.assertIs(self.fake.blocks["synthetic-created-1"]["to_do"]["checked"], True)

    def test_pending_parent_gate_off_stays_503_and_preserves_progress(self):
        self.fake.fail_readback = True
        self.invoke()
        before = self.parent()
        self.cfg = self.srv.Config(secret=self.cfg.secret, ledger_path=self.cfg.ledger_path, host="127.0.0.1", port=0,
                                  write_enabled=False, write_domains=self.cfg.write_domains, aura_helper=self.helper)
        with mock.patch.object(self.engine, "execute", side_effect=AssertionError("disabled gate executed")):
            code, response = self.invoke()
        self.assertEqual(code, 503)
        self.assertEqual(response["error"], "grocery_server_gate_disabled")
        self.assertEqual(response["completed_count"], json.loads(before[4])["completed_count"])
        self.assertEqual(self.parent(), before)

    def test_hmac_client_server_and_cross_domain_gates_remain_closed(self):
        code, _ = self.invoke(signature="invalid")
        self.assertEqual(code, 401)
        self.assertFalse(self.cfg.ledger_path.exists())
        code, _ = self.invoke(request_header="different-header")
        self.assertEqual(code, 409)
        variants = []
        dry = copy.deepcopy(self.payload); dry["context"].update(dry_run=True, allow_write=False); variants.append(dry)
        for agent, intent, domain in (("aura", "reminder", "aura.reminder_capture"), ("megan", "expense", "megan.expense_capture"),
                                     ("argos", "general_capture", "argos.general_capture")):
            payload = copy.deepcopy(self.payload)
            payload["request_id"] += "-" + intent
            payload["route"] = {"agent": agent, "intent": intent, "domain": domain}
            variants.append(payload)
        with mock.patch.object(self.engine, "execute", side_effect=AssertionError("non-grocery or dry-run executed")):
            for payload in variants:
                self.assertIs(self.invoke(payload)[1]["dry_run"], True)
        finance = copy.deepcopy(self.payload); finance["request_id"] += "-broad-finance"
        finance["context"]["allow_firefly_write"] = True
        code, response = self.invoke(finance)
        self.assertEqual(code, 200)
        self.assertEqual(response["status"], "rejected")
        self.assertIs(response["write_committed"], False)
        self.assertEqual(self.fake.calls, [])

    def test_legacy_cached_no_write_clarification_and_ordinary_cache_are_unchanged(self):
        self.srv.init_db(self.cfg.ledger_path)
        legacy = self.srv.plan_capture(self.payload)
        legacy.update(status="needs_confirmation", display_message="No parece un artículo de mercado · no cambié nada",
                      question="¿Qué artículo quieres agregar o marcar como comprado?")
        original = (self.payload["request_id"], hashlib.sha256(self.raw()).hexdigest(), "aura.grocery_capture",
                    "needs_confirmation", json.dumps(legacy, ensure_ascii=False), "synthetic-created", "synthetic-updated")
        with sqlite3.connect(self.cfg.ledger_path) as conn:
            conn.execute("INSERT INTO mobile_capture_ledger VALUES (?,?,?,?,?,?,?)", original)
        before = self.cfg.ledger_path.read_bytes()
        with mock.patch.object(self.engine, "execute", side_effect=AssertionError("legacy cache executed")):
            for _ in range(2):
                code, response = self.invoke()
                self.assertEqual(code, 200)
                self.assertEqual(response["status"], "needs_input")
                self.assertIs(response["dry_run"], False)
                self.assertIs(response["write_committed"], False)
        self.assertEqual(self.parent(), original)
        self.assertEqual(self.cfg.ledger_path.read_bytes(), before)
        self.assertEqual(self.fake.calls, [])

    def test_ordinary_legacy_dry_run_cache_keeps_its_duplicate_envelope(self):
        payload = copy.deepcopy(self.payload)
        payload["route"] = {"agent": "argos", "intent": "general_capture", "domain": "argos.general_capture"}
        payload["context"].update(dry_run=True, allow_write=False)
        with mock.patch.object(self.engine, "execute", side_effect=AssertionError("ordinary legacy cache executed")):
            code, first = self.invoke(payload)
            self.assertEqual(code, 200)
            self.assertEqual(first["status"], "accepted")
            parent = self.parent()
            code, replay = self.invoke(payload)
        expected = dict(first, status="duplicate", duplicate=True)
        self.assertEqual((code, replay), (200, expected))
        self.assertEqual(self.parent(), parent)
        self.assertEqual(self.fake.calls, [])

    def test_builder_rejects_drift_tampering_and_extra_files(self):
        with self.assertRaisesRegex(ValueError, "baseline SHA256"):
            build.candidate(self.baseline + b"\n", self.snapshot)
        changed = self.case / "changed-snapshot"
        shutil.copytree(self.snapshot, changed)
        path = changed / "backend/grocery_item_parser.py"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaisesRegex(ValueError, "sealed manifest"):
            build.build_release(self.baseline, self.case / "bad-build", changed, self.expected)
        held = self.case / "held-import-snapshot"
        shutil.copytree(self.snapshot, held)
        path = held / "backend/grocery_capture.py"
        path.write_bytes(path.read_bytes() + b"\nimport reminder_ai\n")
        with self.assertRaisesRegex(ValueError, "Unpackaged runtime dependency"):
            build.candidate(self.baseline, held)
        for kind in ("runtime", "manifest", "extra", "symlink"):
            output = self.case / ("tampered-" + kind)
            shutil.copytree(self.release, output)
            if kind == "runtime":
                path = output / "grocery_capture.py"; path.write_bytes(path.read_bytes() + b"\n")
            elif kind == "manifest":
                (output / "manifest.json").write_bytes(b"{}")
            elif kind == "extra":
                (output / "unrelated.py").write_text("pass")
            else:
                path = output / "grocery_item_parser.py"; path.unlink(); path.symlink_to(self.release / "grocery_item_parser.py")
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                build.verify_release(output, self.expected)
        second = self.case / "second-release"
        build.build_release(self.baseline, second, self.snapshot, self.expected)
        for name in (*build.ARTIFACTS, "manifest.json"):
            self.assertEqual((self.release / name).read_bytes(), (second / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
