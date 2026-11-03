"""Offline installation faults against the sealed four-file release.

All service operations and health checks are injected. Databases, code files,
helper and backups are synthetic/local; no controller subprocess or HTTP runs.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import ssl
import tempfile
import time
import unittest
from unittest import mock
from urllib.request import HTTPSHandler, ProxyHandler, Request

import build_release as build
import install_release as install


ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def file_snapshot(root):
    return {
        str(path.relative_to(root)): (path.read_bytes(), path.stat().st_mode & 0o777)
        for path in root.rglob('*') if path.is_file()
    }


def create_grocery_journal(path, *, with_batch=False):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE grocery_batches (
            request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL, response_json TEXT,
            status TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")
        conn.execute("""CREATE TABLE grocery_operations (
            operation_id TEXT PRIMARY KEY, plan_hash TEXT NOT NULL,
            plan_json TEXT NOT NULL, destination TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('prepared','dispatching','uncertain','committed','rejected')),
            candidate_id TEXT, receipt_json TEXT, error_code TEXT
        )""")
        if with_batch:
            conn.execute("INSERT INTO grocery_batches(request_id,payload_hash,plan_json,plan_hash,status) VALUES (?,?,?,?,?)",
                         ('synthetic-new-batch', 'b' * 64, '{"private":"synthetic-state"}', 'c' * 64, 'pending'))


class FakeController:
    def __init__(self, entrypoint, ledger, helper, working_directory):
        self.entrypoint, self.ledger = entrypoint, ledger
        self.active, self.pid = True, 100
        self.argv = (
            '/usr/bin/python3', str(entrypoint), '--host', '127.0.0.1', '--port', '8650',
            '--ledger', str(ledger), '--aura-helper', str(helper),
            '--secret-file', str(working_directory / 'must-not-read-route-secret.txt'),
        )
        self.working_directory = working_directory
        self.events = []
        self.reservation_seen_at_stop = []
        self.on_stop = None
        self.on_start = None
        self.on_snapshot = None
        self.helper_env = None
        self.helper_reads = 0
        self.snapshot_count = 0

    def snapshot(self):
        self.snapshot_count += 1
        if self.on_snapshot:
            self.on_snapshot(self)
        return install.ServiceSnapshot(self.active, self.pid, self.argv, self.working_directory)

    def helper_environment(self, pid):
        self.helper_reads += 1
        return self.helper_env

    def stop(self):
        self.events.append('stop')
        locked = False
        with sqlite3.connect(self.ledger, timeout=0) as conn:
            try:
                conn.execute('BEGIN IMMEDIATE')
            except sqlite3.OperationalError:
                locked = True
            else:
                conn.rollback()
        self.reservation_seen_at_stop.append(locked)
        self.active, self.pid = False, 0
        if self.on_stop:
            self.on_stop(self)

    def start(self):
        self.events.append('start')
        if self.on_start:
            self.on_start(self)
        self.active, self.pid = True, 100 + self.events.count('start')


class InstallReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.baseline = build.baseline_source(ROOT)
        cls.package = cls.root / 'sealed-package'
        build.build_release(cls.baseline, cls.package, ROOT)
        cls.manifest = json.loads((cls.package / 'manifest.json').read_bytes())

    def setUp(self):
        self.temp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_case.cleanup)
        self.case = Path(self.temp_case.name)
        self.runtime = self.case / 'runtime'
        self.runtime.mkdir()
        self.entrypoint = self.runtime / build.SERVER
        self.entrypoint.write_bytes(self.baseline)
        self.entrypoint.chmod(0o640)
        self.helper = self.case / 'synthetic-helper.py'
        self.helper.write_text('# Exclusively synthetic helper; installer must never execute it.\n')
        self.ledger = self.case / 'mobile-ledger.sqlite3'
        with sqlite3.connect(self.ledger) as conn:
            conn.execute("""CREATE TABLE mobile_capture_ledger (
                request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
                domain TEXT NOT NULL, status TEXT NOT NULL, response_json TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            for index, status in enumerate(['accepted'] * 5 + ['committed'] * 5 + ['needs_confirmation'] * 2):
                conn.execute('INSERT INTO mobile_capture_ledger VALUES (?,?,?,?,?,?,?)', (
                    f'synthetic-private-id-{index}', 'a' * 64, 'aura.grocery_capture', status,
                    '{"display_message":"synthetic captured text must not enter install report"}',
                    'synthetic-created', 'synthetic-updated'))
        self.original_ledger = self.ledger.read_bytes()
        self.package = self.case / 'package'
        shutil.copytree(type(self).package, self.package)
        self.backup_root = self.case / 'backups'
        self.lock_file = self.case / 'locks' / 'install.lock'
        self.lock_file.parent.mkdir()
        self.paths = install.Paths(self.package, self.backup_root, self.lock_file)
        self.controller = FakeController(self.entrypoint, self.ledger, self.helper, self.case)
        self.health_calls = 0
        self.health_hook = None
        self.new_health_bad = False
        self.addCleanup(mock.patch.stopall)
        # The real controller and HTTP implementation must never be reachable.
        mock.patch('subprocess.run', side_effect=AssertionError('offline tests cannot execute a service command')).start()
        mock.patch('urllib.request.urlopen', side_effect=AssertionError('offline tests cannot make HTTP requests')).start()

    @property
    def journal(self):
        return Path(str(self.ledger) + '.grocery.sqlite3')

    @property
    def backup(self):
        return self.backup_root / ('release-' + install.MANIFEST_SHA256[:16])

    def health(self):
        self.health_calls += 1
        if self.health_hook:
            self.health_hook()
        self.assertTrue(self.controller.active, 'health must not probe a stopped fake service')
        result = copy.deepcopy(install.EXPECTED_HEALTH)
        if self.new_health_bad and digest(self.entrypoint) != install.BASELINE_SHA256:
            result['write_domains'] = ['unexpected-domain']
        return result

    def installer(self, **kwargs):
        return install.Installer(self.paths, self.controller, self.health,
                                 helper_sha256=digest(self.helper), lock_timeout=0.05, drain_timeout=0.05, **kwargs)

    def assert_old_runtime(self):
        self.assertEqual(self.entrypoint.read_bytes(), self.baseline)
        for name in build.RUNTIME_MODULES:
            self.assertFalse((self.runtime / name).exists(), name)

    def assert_new_runtime(self):
        for name, expected in self.manifest['artifacts'].items():
            self.assertEqual(digest(self.runtime / name), expected, name)

    def assert_private_report(self, report):
        text = json.dumps(report, sort_keys=True)
        for private in ('synthetic-private-id', 'synthetic captured text', 'must-not-read-route-secret', str(self.helper)):
            self.assertNotIn(private, text)

    def test_preflight_is_default_read_only_with_private_safe_report(self):
        before = file_snapshot(self.case)
        report = self.installer().preflight()
        self.assertEqual(report['status'], 'preflight_ok')
        self.assertEqual(file_snapshot(self.case), before)
        self.assertEqual(self.controller.events, [])
        self.assertFalse(self.backup_root.exists())
        self.assertFalse(self.lock_file.exists())
        self.assert_private_report(report)

    def test_manifest_and_runtime_tampering_fail_before_service_actions(self):
        for name in ('manifest.json', 'grocery_capture.py'):
            path = self.package / name
            original = path.read_bytes()
            path.write_bytes(original + b'\n')
            with self.subTest(name=name):
                with self.assertRaises(install.InstallError) as caught:
                    self.installer().apply()
                self.assertEqual(caught.exception.code, 'package_invalid')
                self.assertEqual(self.controller.events, [])
                self.assert_old_runtime()
            path.write_bytes(original)

    def test_source_and_helper_drift_are_blocked_without_stop_or_environment_read(self):
        original = self.entrypoint.read_bytes()
        self.entrypoint.write_bytes(original + b'\n')
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'baseline_drift')
        self.assertEqual(self.controller.helper_reads, 0)
        self.entrypoint.write_bytes(original)
        installer = self.installer()
        self.helper.write_text('# Changed helper\n')
        with self.assertRaises(install.InstallError) as caught:
            installer.apply()
        self.assertEqual(caught.exception.code, 'helper_drift')
        self.assertEqual(self.controller.events, [])

    def test_initial_activation_rejects_existing_journal_or_module(self):
        self.journal.write_bytes(b'not even an initialized journal')
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'journal_present')
        self.journal.unlink()
        module = self.runtime / 'grocery_capture.py'
        module.write_text('# Existing file must not be overwritten\n')
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'runtime_present')
        self.assertEqual(module.read_text(), '# Existing file must not be overwritten\n')
        module.unlink()
        self.journal.symlink_to(self.case / 'absent-journal-target')
        self.assertFalse(self.journal.exists())
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'journal_present')
        self.assertTrue(self.journal.is_symlink())
        self.journal.unlink()
        module.symlink_to(self.case / 'absent-module-target')
        self.assertFalse(module.exists())
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'runtime_present')
        self.assertTrue(module.is_symlink())
        self.assertEqual(self.controller.events, [])

    def test_pending_transport_state_blocks_initial_activation(self):
        with sqlite3.connect(self.ledger) as conn:
            conn.execute("UPDATE mobile_capture_ledger SET status='processing' WHERE request_id='synthetic-private-id-0'")
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'pending_state')
        self.assertEqual(self.controller.events, [])
        self.assert_old_runtime()

    def test_explicit_loopback_port_and_absolute_ledger_are_required(self):
        for flag, replacement in (('--host', '0.0.0.0'), ('--port', '8651'), ('--ledger', 'relative.sqlite3')):
            original = self.controller.argv
            argv = list(original); argv[argv.index(flag) + 1] = replacement
            self.controller.argv = tuple(argv)
            with self.subTest(flag=flag):
                with self.assertRaises(install.InstallError):
                    self.installer().apply()
                self.assertEqual(self.controller.events, [])
            self.controller.argv = original

    def test_busy_database_drain_is_bounded_and_never_stops_service(self):
        with sqlite3.connect(self.ledger, timeout=0) as writer:
            writer.execute('BEGIN IMMEDIATE')
            started = time.monotonic()
            with self.assertRaises(install.InstallError) as caught:
                self.installer().apply()
            elapsed = time.monotonic() - started
            writer.rollback()
        self.assertEqual(caught.exception.code, 'drain_busy')
        self.assertLess(elapsed, 1.0)
        self.assertEqual(self.controller.events, [])
        self.assert_old_runtime()

    def test_deployment_lock_is_bounded_and_never_stops_service(self):
        with self.lock_file.open('a+') as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(install.InstallError) as caught:
                self.installer().apply()
        self.assertEqual(caught.exception.code, 'deployment_busy')
        self.assertEqual(self.controller.events, [])
        self.assert_old_runtime()

    def test_pid_and_source_are_rechecked_after_preflight_before_stop(self):
        for change, code in (('pid', 'service_changed'), ('source', 'baseline_drift')):
            self.controller.on_snapshot = None
            self.entrypoint.write_bytes(self.baseline)
            changed = False
            def arm_change():
                nonlocal changed
                if changed:
                    return
                changed = True
                if change == 'pid':
                    self.controller.on_snapshot = lambda control: setattr(control, 'pid', 222)
                else:
                    self.entrypoint.write_bytes(self.baseline + b'\n')
            self.health_hook = arm_change
            with self.subTest(change=change):
                with self.assertRaises(install.InstallError) as caught:
                    self.installer().apply()
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(self.controller.events, [])
            self.controller.on_snapshot = None
            self.controller.pid = 100
        self.health_hook = None

    def test_success_installs_all_four_preserves_metadata_and_creates_one_verified_backup(self):
        old_metadata = self.entrypoint.stat()
        installer = self.installer()
        original_replace = installer._replace_file
        inspected = False
        def inspect_before_replace(staged, destination, metadata):
            nonlocal inspected
            self.assertFalse(self.controller.active)
            self.assertTrue((self.backup / 'backup.json').is_file())
            self.assertEqual((self.backup / 'previous-mobile_capture_staging_server.py').read_bytes(), self.baseline)
            with sqlite3.connect('file:' + str(self.backup / 'mobile-ledger.sqlite3') + '?mode=ro', uri=True) as conn:
                self.assertEqual(conn.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM mobile_capture_ledger').fetchone()[0], 12)
            # Separate backup source connection must run only after drain guard is released.
            with sqlite3.connect(self.ledger, timeout=0) as conn:
                conn.execute('BEGIN IMMEDIATE'); conn.rollback()
            inspected = True
            return original_replace(staged, destination, metadata)
        with mock.patch.object(installer, '_replace_file', side_effect=inspect_before_replace):
            report = installer.apply()
        self.assertTrue(inspected)
        self.assertEqual(report['status'], 'installed')
        self.assertEqual(self.controller.events, ['stop', 'start'])
        self.assertEqual(self.controller.reservation_seen_at_stop, [True])
        self.assertTrue(self.controller.active)
        self.assert_new_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertFalse(self.journal.exists())
        for name in build.ARTIFACTS:
            metadata = (self.runtime / name).stat()
            self.assertEqual((metadata.st_uid, metadata.st_gid, metadata.st_mode & 0o777),
                             (old_metadata.st_uid, old_metadata.st_gid, old_metadata.st_mode & 0o777))
        self.assertEqual(len([path for path in self.backup_root.iterdir() if path.is_dir()]), 1)
        self.assertEqual(digest(self.backup / 'previous-mobile_capture_staging_server.py'), install.BASELINE_SHA256)
        self.assert_private_report(report)

    def test_backup_creation_failure_restarts_unchanged_service_before_code_change(self):
        self.controller.on_stop = lambda _: self.backup.mkdir(parents=True, exist_ok=False)
        report = self.installer().apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertEqual(self.controller.events.count('start'), 1)

    def test_partial_copy_failure_restores_baseline_and_removes_only_created_modules(self):
        installer = self.installer()
        original = installer._replace_file
        replacements = 0
        def fail_after_replace(staged, destination, metadata):
            nonlocal replacements
            replacements += 1
            result = original(staged, destination, metadata)
            if replacements == 1:
                raise OSError('synthetic failure after atomic replacement')
            return result
        with mock.patch.object(installer, '_replace_file', side_effect=fail_after_replace):
            report = installer.apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_post_start_module_metadata_drift_is_not_reported_as_success(self):
        def change_new_mode(_):
            if digest(self.entrypoint) != install.BASELINE_SHA256:
                (self.runtime / 'grocery_item_parser.py').chmod(0o600)
        self.controller.on_start = change_new_mode
        report = self.installer().apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_startup_failure_restores_baseline_without_restoring_data(self):
        def fail_new_start(_):
            if digest(self.entrypoint) != install.BASELINE_SHA256:
                raise OSError('synthetic startup failure')
        self.controller.on_start = fail_new_start
        report = self.installer().apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_bad_new_health_rolls_back_only_code_when_journal_is_absent(self):
        self.new_health_bad = True
        report = self.installer().apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertEqual(self.controller.events, ['stop', 'start', 'stop', 'start'])

    def test_verified_empty_new_journal_can_remain_during_code_only_rollback(self):
        self.new_health_bad = True
        created = None
        def empty_journal_on_new_start(_):
            nonlocal created
            if digest(self.entrypoint) != install.BASELINE_SHA256:
                create_grocery_journal(self.journal)
                created = self.journal.read_bytes()
        self.controller.on_start = empty_journal_on_new_start
        report = self.installer().apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assert_old_runtime()
        self.assertTrue(self.controller.active)
        self.assertEqual(self.journal.read_bytes(), created)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_new_batch_state_refuses_legacy_downgrade_and_never_restores_databases(self):
        self.new_health_bad = True
        state = None
        def create_batch_on_start(_):
            nonlocal state
            create_grocery_journal(self.journal, with_batch=True)
            state = self.journal.read_bytes()
        self.controller.on_start = create_batch_on_start
        report = self.installer().apply()
        self.assertEqual((report['status'], report['code']), ('attention', 'unsafe_rollback'))
        self.assertFalse(self.controller.active)
        self.assert_new_runtime()
        self.assertEqual(self.journal.read_bytes(), state)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertEqual(self.controller.events.count('start'), 1)
        self.assertEqual(self.controller.events[-1], 'stop')
        self.assert_private_report(report)

    def test_unknown_new_journal_refuses_legacy_downgrade(self):
        self.new_health_bad = True
        self.controller.on_start = lambda _: self.journal.write_bytes(b'synthetic unknown state must remain')
        report = self.installer().apply()
        self.assertEqual((report['status'], report['code']), ('attention', 'unsafe_rollback'))
        self.assertFalse(self.controller.active)
        self.assert_new_runtime()
        self.assertEqual(self.journal.read_bytes(), b'synthetic unknown state must remain')

    def test_wrong_column_journal_and_unknown_view_refuse_legacy_downgrade(self):
        self.new_health_bad = True
        state = None
        def create_unrecognized_schema(_):
            nonlocal state
            with sqlite3.connect(self.journal) as conn:
                conn.execute('CREATE TABLE grocery_batches(unrecognized_state TEXT)')
                conn.execute('CREATE VIEW unknown_state AS SELECT unrecognized_state FROM grocery_batches')
            state = self.journal.read_bytes()
        self.controller.on_start = create_unrecognized_schema
        report = self.installer().apply()
        self.assertEqual((report['status'], report['code']), ('attention', 'unsafe_rollback'))
        self.assertFalse(self.controller.active)
        self.assert_new_runtime()
        self.assertEqual(self.journal.read_bytes(), state)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)
        self.assertEqual(self.controller.events.count('start'), 1)
        self.assertEqual(self.controller.events[-1], 'stop')

    def test_pending_parent_without_journal_refuses_legacy_downgrade(self):
        self.new_health_bad = True
        def create_pending(_):
            with sqlite3.connect(self.ledger) as conn:
                conn.execute("UPDATE mobile_capture_ledger SET status='grocery_pending' WHERE request_id='synthetic-private-id-0'")
        self.controller.on_start = create_pending
        report = self.installer().apply()
        self.assertEqual((report['status'], report['code']), ('attention', 'unsafe_rollback'))
        self.assertFalse(self.controller.active)
        self.assert_new_runtime()
        with sqlite3.connect(self.ledger) as conn:
            self.assertEqual(conn.execute("SELECT status FROM mobile_capture_ledger WHERE request_id='synthetic-private-id-0'").fetchone()[0], 'grocery_pending')

    def test_one_exclusive_backup_per_release_is_never_overwritten(self):
        self.backup.mkdir(parents=True)
        sentinel = self.backup / 'must-remain'
        sentinel.write_text('synthetic previous backup')
        with self.assertRaises(install.InstallError) as caught:
            self.installer().apply()
        self.assertEqual(caught.exception.code, 'backup_exists')
        self.assertEqual(sentinel.read_text(), 'synthetic previous backup')
        self.assertEqual(self.controller.events, [])
        self.assert_old_runtime()

    def test_helper_environment_selection_is_narrow_and_rejects_duplicates(self):
        raw = b'UNRELATED_SECRET=must-not-return\0MOBILE_CAPTURE_AURA_HELPER=/synthetic/helper.py\0PATH=/synthetic/bin\0'
        self.assertEqual(install.select_helper_env(raw), '/synthetic/helper.py')
        self.assertIsNone(install.select_helper_env(b'UNRELATED_SECRET=must-not-return\0'))
        with self.assertRaises(install.InstallError) as caught:
            install.select_helper_env(b'MOBILE_CAPTURE_AURA_HELPER=/one\0MOBILE_CAPTURE_AURA_HELPER=/two\0')
        self.assertEqual(caught.exception.code, 'helper_ambiguous')

    def test_https_health_rejects_redirects_credentials_and_noncanonical_urls_without_network(self):
        for url in (
            'http://synthetic.invalid/health', 'https://user:password@synthetic.invalid/health',
            'https://synthetic.invalid/health?secret=value', 'https://synthetic.invalid/health#fragment',
            'https://synthetic.invalid/other', 'https://synthetic.invalid/health/',
        ):
            with self.subTest(url=url):
                with self.assertRaises(install.InstallError) as caught:
                    install.HTTPSHealth(url)
                self.assertEqual(caught.exception.code, 'health_url_invalid')
        with mock.patch.object(install, 'build_opener') as opener:
            install.HTTPSHealth('https://synthetic.invalid/health')
        handlers = opener.call_args.args
        self.assertTrue(any(isinstance(handler, install.NoRedirect) for handler in handlers))
        proxies = [handler for handler in handlers if isinstance(handler, ProxyHandler)]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0].proxies, {})
        tls = [handler for handler in handlers if isinstance(handler, HTTPSHandler)]
        self.assertEqual(len(tls), 1)
        self.assertTrue(tls[0]._context.check_hostname)
        self.assertEqual(tls[0]._context.verify_mode, ssl.CERT_REQUIRED)
        request = Request('https://synthetic.invalid/health', method='GET')
        for destination in ('http://synthetic.invalid/health', 'https://other.invalid/health',
                            'https://synthetic.invalid/health'):
            with self.subTest(destination=destination):
                with self.assertRaises(install.InstallError) as caught:
                    install.NoRedirect().redirect_request(request, None, 302, 'Found', {}, destination)
                self.assertEqual(caught.exception.code, 'health_redirect')

    def test_explicit_helper_argument_takes_precedence_over_selected_environment(self):
        self.controller.helper_env = str(self.case / 'nonexistent-unused-helper.py')
        self.assertEqual(self.installer().preflight()['status'], 'preflight_ok')
        self.assertEqual(self.controller.helper_reads, 0)
        argv = list(self.controller.argv)
        index = argv.index('--aura-helper'); del argv[index:index + 2]
        self.controller.argv = tuple(argv)
        self.controller.helper_env = str(self.helper)
        self.assertEqual(self.installer().preflight()['status'], 'preflight_ok')
        self.assertGreater(self.controller.helper_reads, 0)


if __name__ == '__main__':
    unittest.main()
