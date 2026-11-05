"""Offline installation faults against the sealed four-file release.

All service operations and health checks are injected. Databases, code files,
helper and backups are synthetic/local; no controller subprocess or HTTP runs.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.util
import io
import json
import os
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


class TransientStartController(FakeController):
    """Simulate systemd Type=simple before its launcher execs Python."""
    def __init__(self, *args):
        super().__init__(*args)
        self.startup_snapshots = 0
        self.transients_seen = 0

    def start(self):
        super().start()
        self.startup_snapshots = 2

    def snapshot(self):
        snapshot = super().snapshot()
        if snapshot.active and self.startup_snapshots:
            remaining = self.startup_snapshots
            self.startup_snapshots -= 1
            self.transients_seen += 1
            if remaining == 2:
                return install.ServiceSnapshot(False, 0, (), self.working_directory)
            return install.ServiceSnapshot(True, self.pid, ('/bin/sh', '-c', 'synthetic launch wrapper'),
                                           self.working_directory / 'temporary-wrapper-directory')
        return snapshot


class InstallReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.baseline = build.baseline_source(ROOT)
        cls.package = cls.root / 'sealed-package'
        fixture_path = Path(__file__).with_name('test_fixture.py')
        spec = importlib.util.spec_from_file_location('_grocery_installer_source_fixture', fixture_path)
        source_fixture = importlib.util.module_from_spec(spec)
        exec(compile(fixture_path.read_bytes(), str(fixture_path), 'exec'), source_fixture.__dict__)
        cls.snapshot = cls.root / 'historical-source'
        source_fixture.populate_snapshot(ROOT, cls.snapshot, build.RUNTIME_MODULES)
        build.build_release(cls.baseline, cls.package, cls.snapshot)
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
        options = dict(helper_sha256=digest(self.helper), lock_timeout=0.05,
                       drain_timeout=0.05, startup_timeout=0.05)
        options.update(kwargs)
        return install.Installer(self.paths, self.controller, self.health, **options)

    def prepare_verified_backup(self):
        self.new_health_bad = True
        report = self.installer().apply()
        self.assertEqual(report['status'], 'rolled_back')
        self.assert_old_runtime()
        self.assertTrue(self.controller.active)
        self.assertEqual(sorted(member.name for member in self.backup.iterdir()),
                         ['backup.json', 'mobile-ledger.sqlite3', 'previous-mobile_capture_staging_server.py'])
        self.new_health_bad = False
        self.controller.events.clear()
        self.controller.reservation_seen_at_stop.clear()
        return self.backup_snapshot()

    def backup_snapshot(self):
        folder = self.backup.lstat()
        return {
            'directory': (folder.st_dev, folder.st_ino, folder.st_uid, folder.st_gid, folder.st_mode & 0o777),
            'members': {
                member.name: (member.read_bytes(), member.stat().st_dev, member.stat().st_ino,
                              member.stat().st_nlink, member.stat().st_uid, member.stat().st_gid,
                              member.stat().st_mode & 0o777)
                for member in self.backup.iterdir()
            },
        }

    def assert_resume_blocked(self, code='resume_backup_invalid'):
        with self.assertRaises(install.InstallError) as caught:
            self.installer(resume_verified_backup=True).apply()
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(self.controller.events, [])
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()

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

    def test_transient_launch_wrapper_is_waited_for_before_new_runtime_verification(self):
        self.controller = TransientStartController(self.entrypoint, self.ledger, self.helper, self.case)
        report = self.installer(startup_timeout=0.3).apply()
        self.assertEqual(report['status'], 'installed')
        self.assertEqual(self.controller.transients_seen, 2)
        self.assertTrue(self.controller.active)
        self.assert_new_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_transient_launch_wrapper_is_also_waited_for_during_legacy_recovery(self):
        self.controller = TransientStartController(self.entrypoint, self.ledger, self.helper, self.case)
        self.new_health_bad = True
        report = self.installer(startup_timeout=0.3).apply()
        self.assertEqual((report['status'], report['code']), ('rolled_back', 'health_mismatch'))
        self.assertEqual(self.controller.transients_seen, 4)
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_persistent_post_start_argument_mismatch_fails_with_bounded_readiness_wait(self):
        original_snapshot = self.controller.snapshot
        def mismatched_new_runtime():
            snapshot = original_snapshot()
            if snapshot.active and digest(self.entrypoint) != install.BASELINE_SHA256:
                return install.ServiceSnapshot(True, snapshot.pid, snapshot.argv + ('--synthetic-unexpected', 'value'),
                                               snapshot.working_directory)
            return snapshot
        self.controller.snapshot = mismatched_new_runtime
        started = time.monotonic()
        report = self.installer(startup_timeout=0.05).apply()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(report['status'], 'rolled_back')
        self.assertIn(report['code'], {'service_arguments', 'service_changed'})
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()

    def test_persistent_post_start_working_directory_mismatch_is_not_hidden_by_healthy_reply(self):
        original_snapshot = self.controller.snapshot
        def mismatched_new_runtime():
            snapshot = original_snapshot()
            if snapshot.active and digest(self.entrypoint) != install.BASELINE_SHA256:
                return install.ServiceSnapshot(True, snapshot.pid, snapshot.argv, self.case / 'wrong-working-directory')
            return snapshot
        self.controller.snapshot = mismatched_new_runtime
        started = time.monotonic()
        report = self.installer(startup_timeout=0.05).apply()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual((report['status'], report['code']), ('rolled_back', 'service_changed'))
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()

    def test_existing_verified_backup_stays_blocked_by_default_but_explicit_preflight_is_read_only(self):
        backup = self.prepare_verified_backup()
        for operation in ('preflight', 'apply'):
            with self.subTest(operation=operation):
                with self.assertRaises(install.InstallError) as caught:
                    getattr(self.installer(), operation)()
                self.assertEqual(caught.exception.code, 'backup_exists')
        before = file_snapshot(self.case)
        report = self.installer(resume_verified_backup=True).preflight()
        self.assertEqual(report['status'], 'preflight_ok')
        self.assertIs(report['backup_reused'], True)
        self.assertEqual(file_snapshot(self.case), before)
        self.assertEqual(self.backup_snapshot(), backup)
        self.assertEqual(self.controller.events, [])
        self.assert_private_report(report)

    def test_explicit_resume_reuses_identical_backup_without_backup_or_data_restore(self):
        backup = self.prepare_verified_backup()
        current = self.ledger.read_bytes()
        installer = self.installer(resume_verified_backup=True)
        with mock.patch.object(installer, '_backup', side_effect=AssertionError('resume must not create another backup')):
            report = installer.apply()
        self.assertEqual(report['status'], 'installed')
        self.assertIs(report['backup_reused'], True)
        self.assertEqual(self.backup_snapshot(), backup)
        self.assertEqual(self.ledger.read_bytes(), current)
        self.assertEqual(len([entry for entry in self.backup_root.iterdir() if entry.is_dir()]), 1)
        self.assert_new_runtime()
        self.assertTrue(self.controller.active)
        self.assert_private_report(report)

    def test_explicit_resume_without_existing_backup_never_creates_one(self):
        self.assert_resume_blocked()
        self.assertFalse(self.backup.exists())
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_resume_rejects_missing_and_extra_backup_members_before_stop(self):
        self.prepare_verified_backup()
        marker = self.backup / 'backup.json'
        raw = marker.read_bytes()
        marker.unlink()
        self.assert_resume_blocked()
        marker.write_bytes(raw); marker.chmod(0o600)
        extra = self.backup / 'unexpected-member'
        extra.write_text('synthetic unrelated file')
        self.assert_resume_blocked()
        self.assertEqual(extra.read_text(), 'synthetic unrelated file')
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_resume_rejects_nonprivate_backup_directory_members_and_wrong_executor_owner(self):
        self.prepare_verified_backup()
        self.backup.chmod(0o755)
        self.assert_resume_blocked()
        self.backup.chmod(0o700)
        for name in ('backup.json', 'mobile-ledger.sqlite3', 'previous-mobile_capture_staging_server.py'):
            member = self.backup / name
            original = member.stat().st_mode & 0o777
            member.chmod(0o600 if original != 0o600 else 0o640)
            with self.subTest(member=name):
                self.assert_resume_blocked()
            member.chmod(original)
        with mock.patch.object(install.os, 'geteuid', return_value=os.geteuid() + 1):
            self.assert_resume_blocked()

    def test_resume_rejects_symlinked_backup_directory_without_rewriting_it(self):
        self.prepare_verified_backup()
        target = self.backup.with_name('synthetic-symlink-target')
        self.backup.rename(target)
        self.backup.symlink_to(target, target_is_directory=True)
        self.assert_resume_blocked()
        self.assertTrue(self.backup.is_symlink())
        self.assertTrue((target / 'backup.json').is_file())

    def test_resume_rejects_symlinked_and_hardlinked_backup_members(self):
        self.prepare_verified_backup()
        marker = self.backup / 'backup.json'
        target = self.case / 'synthetic-marker-target'
        target.write_bytes(marker.read_bytes()); target.chmod(0o600)
        marker.unlink(); marker.symlink_to(target)
        self.assert_resume_blocked()
        self.assertTrue(marker.is_symlink())
        marker.unlink(); marker.write_bytes(target.read_bytes()); marker.chmod(0o600)
        code = self.backup / 'previous-mobile_capture_staging_server.py'
        os.link(code, self.case / 'synthetic-code-hardlink')
        self.assertEqual(code.stat().st_nlink, 2)
        self.assert_resume_blocked()

    def test_resume_requires_exact_canonical_six_key_marker_and_release_pins(self):
        self.prepare_verified_backup()
        path = self.backup / 'backup.json'
        original_raw = path.read_bytes()
        original = json.loads(original_raw)
        mutations = []
        extra = dict(original, unknown_key=False); mutations.append(extra)
        missing = dict(original); del missing['grocery_journal']; mutations.append(missing)
        for field, value in (
            ('manifest_sha256', '0' * 64), ('source_sha256', '0' * 64),
            ('grocery_journal', 'empty'), ('data_restore_permitted', True),
            ('mobile_ledger_source_sha256', 'not-a-digest'), ('mobile_ledger_backup_sha256', True),
        ):
            mutated = dict(original); mutated[field] = value; mutations.append(mutated)
        for index, marker in enumerate(mutations):
            path.write_bytes((json.dumps(marker, sort_keys=True) + '\n').encode())
            with self.subTest(mutation=index):
                self.assert_resume_blocked()
        for raw in (json.dumps(original, sort_keys=True, indent=2).encode(), original_raw + b' '):
            path.write_bytes(raw)
            self.assert_resume_blocked()
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_resume_checks_old_code_hash_and_backup_database_hash_and_quick_check(self):
        self.prepare_verified_backup()
        code = self.backup / 'previous-mobile_capture_staging_server.py'
        original_code = code.read_bytes()
        code.write_bytes(original_code + b'\n')
        self.assert_resume_blocked()
        code.write_bytes(original_code)
        saved = self.backup / 'mobile-ledger.sqlite3'
        original_database = saved.read_bytes()
        with sqlite3.connect(saved) as conn:
            conn.execute("UPDATE mobile_capture_ledger SET status='synthetic-changed-backup' WHERE request_id='synthetic-private-id-0'")
        self.assert_resume_blocked()
        saved.write_bytes(b'synthetic-invalid-SQLite-backup')
        marker = self.backup / 'backup.json'
        value = json.loads(marker.read_bytes())
        value['mobile_ledger_backup_sha256'] = digest(saved)
        marker.write_bytes((json.dumps(value, sort_keys=True) + '\n').encode())
        self.assert_resume_blocked()
        self.assertNotEqual(saved.read_bytes(), original_database)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_resume_detects_changed_live_database_without_restoring_it(self):
        self.prepare_verified_backup()
        with sqlite3.connect(self.ledger) as conn:
            conn.execute("UPDATE mobile_capture_ledger SET status='synthetic-new-terminal-state' WHERE request_id='synthetic-private-id-0'")
        changed = self.ledger.read_bytes()
        self.assert_resume_blocked('resume_data_changed')
        self.assertEqual(self.ledger.read_bytes(), changed)

    def test_resume_rejects_wal_mode_or_sidecars_without_creating_reader_side_effects(self):
        self.prepare_verified_backup()
        for suffix in ('-wal', '-shm'):
            path = Path(str(self.ledger) + suffix)
            path.write_bytes(b'synthetic-sidecar')
            before = file_snapshot(self.case)
            self.assert_resume_blocked('resume_storage_unsupported')
            self.assertEqual(file_snapshot(self.case), before)
            path.unlink()
        conn = sqlite3.connect(self.ledger)
        try:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            conn.close()
        self.assertEqual(self.ledger.read_bytes()[18:20], b'\x02\x02')
        before = file_snapshot(self.case)
        self.assert_resume_blocked('resume_storage_unsupported')
        self.assertEqual(file_snapshot(self.case), before)

    def test_resume_rejects_checkpointed_wal_saved_backup_without_reader_side_effects(self):
        self.prepare_verified_backup()
        saved = self.backup / 'mobile-ledger.sqlite3'
        # Construct checkpointed WAL main-file bytes away from the backup. Some
        # SQLite builds retain WAL/SHM files on close; the malformed backup must
        # start with exactly its three members so the reader guard is exercised.
        fixture = self.case / 'synthetic-checkpointed-wal.sqlite3'
        shutil.copyfile(saved, fixture)
        conn = sqlite3.connect(fixture)
        try:
            self.assertEqual(conn.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            self.assertEqual(conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0], 0)
        finally:
            conn.close()
        saved.write_bytes(fixture.read_bytes())
        self.assertEqual(saved.read_bytes()[18:20], b'\x02\x02')
        for suffix in ('-wal', '-shm'):
            self.assertFalse(Path(str(saved) + suffix).exists())
        marker = self.backup / 'backup.json'
        value = json.loads(marker.read_bytes())
        value['mobile_ledger_backup_sha256'] = digest(saved)
        marker.write_bytes((json.dumps(value, sort_keys=True) + '\n').encode())
        before = self.backup_snapshot()
        self.assert_resume_blocked('resume_backup_invalid')
        self.assertEqual(self.backup_snapshot(), before)
        for suffix in ('-wal', '-shm'):
            self.assertFalse(Path(str(saved) + suffix).exists())
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_resume_revalidates_backup_under_drain_before_stopping_service(self):
        self.prepare_verified_backup()
        marker = self.backup / 'backup.json'
        corrupted = False
        def mutate_when_reserved(_):
            nonlocal corrupted
            if corrupted:
                return
            with sqlite3.connect(self.ledger, timeout=0) as conn:
                try:
                    conn.execute('BEGIN IMMEDIATE')
                except sqlite3.OperationalError:
                    marker.write_bytes(marker.read_bytes() + b' ')
                    corrupted = True
                else:
                    conn.rollback()
        self.controller.on_snapshot = mutate_when_reserved
        self.assert_resume_blocked()
        self.assertTrue(corrupted)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_resume_revalidates_backup_after_stop_and_recovers_unchanged_code(self):
        self.prepare_verified_backup()
        marker = self.backup / 'backup.json'
        damaged = marker.read_bytes() + b' '
        self.controller.on_stop = lambda _: marker.write_bytes(damaged)
        installer = self.installer(resume_verified_backup=True)
        with mock.patch.object(installer, '_backup', side_effect=AssertionError('resume must not create another backup')):
            report = installer.apply()
        self.assertEqual((report['status'], report['code']), ('rolled_back', 'resume_backup_invalid'))
        self.assertTrue(self.controller.active)
        self.assert_old_runtime()
        self.assertEqual(marker.read_bytes(), damaged)
        self.assertEqual(self.ledger.read_bytes(), self.original_ledger)

    def test_cli_resume_flag_can_select_preflight_without_apply_or_live_controller(self):
        argv = ['install_release.py', '--package', str(self.package), '--backup-root', str(self.backup_root),
                '--lock-file', str(self.lock_file), '--health-url', 'https://synthetic.invalid/health',
                '--resume-verified-backup']
        with mock.patch('sys.argv', argv), mock.patch.object(install, 'Installer') as factory, \
                mock.patch.object(install, 'SystemdController', return_value=self.controller), \
                mock.patch.object(install, 'HTTPSHealth', return_value=self.health), \
                mock.patch('sys.stdout', new_callable=io.StringIO) as output:
            factory.return_value.preflight.return_value = {'status': 'preflight_ok'}
            install.main()
        self.assertIs(factory.call_args.kwargs['resume_verified_backup'], True)
        factory.return_value.preflight.assert_called_once()
        factory.return_value.apply.assert_not_called()
        self.assertEqual(json.loads(output.getvalue()), {'status': 'preflight_ok'})
        self.assertEqual(self.controller.events, [])

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
