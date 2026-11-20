"""Offline OAuth, callback validation and credential-rotation safety tests."""
from __future__ import annotations

import base64
import concurrent.futures
import copy
import hashlib
import importlib.util
import io
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib import error, parse

import todoist_auth as auth

NOW = 2_000_000_000
CLIENT = "tdd_synthetic_personal_app"
TOKENS = {"access_token": "synthetic-access-token", "refresh_token": "synthetic-refresh-token", "token_type": "Bearer", "expires_in": 3600, "scope": "task:add data:read"}
REPLACEMENT = {**TOKENS, "access_token": "synthetic-new-access-token", "refresh_token": "synthetic-new-refresh-token"}
STATE = "synthetic-state-for-offline-test"


class FakeResponse:
    def __init__(self, value=None, *, raw=None, wait=None):
        self.raw = json.dumps(value).encode() if raw is None else raw
        self.closed = False
        self.wait = wait

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def read(self, amount):
        if self.wait:
            self.wait.wait(2)
        return self.raw[:amount]


class FakeOpener:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def open(self, req, timeout):
        self.calls.append((req, timeout))
        if isinstance(self.value, BaseException):
            raise self.value
        return self.value


class FakeCallback:
    redirect_uri = "http://localhost:45678" + auth.CALLBACK_PATH

    def __init__(self, state, port=0):
        self.state = state
        self.port = port
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def wait(self, timeout):
        return "synthetic-authorization-code"


class FakeSocket:
    def __init__(self, incoming):
        self.incoming = io.BytesIO(incoming)
        self.outgoing = io.BytesIO()

    def makefile(self, *_args, **_kwargs):
        return self.incoming

    def sendall(self, value):
        self.outgoing.write(value)

    def settimeout(self, _timeout):
        pass


class TodoistAuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(
            prefix="personal-watch-auth-test-", dir=Path(tempfile.gettempdir()).resolve(),
        )
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "credentials.json"
        self.store = auth.CredentialStore(self.path, now=lambda: NOW)

    def seed(self, *, expired=False, **changes):
        bundle = auth._new_bundle(CLIENT, TOKENS, NOW)
        if expired:
            bundle["expires_at"] = NOW - 1
        bundle.update(changes)
        self.path.write_text(json.dumps(bundle))
        self.path.chmod(0o600)
        return bundle

    @staticmethod
    def registration(payload, **changes):
        return {"client_id": CLIENT, **payload, **changes}

    def flow(self, *, token_response=None, registration_changes=None):
        callback = mock.patch.object(auth, "LoopbackCallback", FakeCallback)
        callback.start()
        self.addCleanup(callback.stop)
        def post(url, payload, **_kwargs):
            if url == auth.REGISTER_URL:
                return self.registration(payload, **(registration_changes or {}))
            self.assertEqual(url, auth.TOKEN_URL)
            return token_response or copy.deepcopy(TOKENS)
        provider = mock.patch.object(auth, "_post", side_effect=post)
        mocked = provider.start()
        self.addCleanup(provider.stop)
        return mocked

    def test_import_and_construction_do_not_create_or_contact(self):
        with mock.patch.object(auth, "_post") as post, mock.patch.object(auth, "LoopbackCallback") as callback:
            self.assertEqual(self.store.status(), {"configured": False, "status": "not_configured"})
            post.assert_not_called()
            callback.assert_not_called()
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_valid_bearer_needs_no_provider_call_and_status_is_safe(self):
        self.seed()
        with mock.patch.object(auth, "_post") as post:
            self.assertEqual(self.store.bearer(), TOKENS["access_token"])
            status = self.store.status()
        post.assert_not_called()
        self.assertEqual(status["status"], "ready")
        self.assertEqual(status["scopes"], ["data:read", "task:add"])
        self.assertNotIn("token", json.dumps(status))
        self.assertNotIn(CLIENT, json.dumps(status))

    def test_status_on_expiry_does_not_refresh_or_create_lock(self):
        self.seed(expired=True)
        before = self.path.read_bytes()
        with mock.patch.object(auth, "_post") as post:
            self.assertEqual(self.store.status()["status"], "refresh_required")
        post.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_name(self.path.name + ".lock").exists())

    def test_refresh_reservation_precedes_one_call_and_rotates_atomically(self):
        self.seed(expired=True)
        def refresh(url, payload, **kwargs):
            reserved = json.loads(self.path.read_text())
            self.assertEqual(reserved["refresh_state"], "refreshing")
            self.assertEqual(reserved["refresh_started_at"], NOW)
            self.assertEqual(url, auth.TOKEN_URL)
            self.assertEqual(payload, {"client_id": CLIENT, "grant_type": "refresh_token", "refresh_token": TOKENS["refresh_token"]})
            self.assertEqual(kwargs, {"form": True, "timeout": 8})
            return REPLACEMENT
        with mock.patch.object(auth, "_post", side_effect=refresh) as post:
            self.assertEqual(self.store.bearer(), REPLACEMENT["access_token"])
            self.assertEqual(self.store.bearer(), REPLACEMENT["access_token"])
        self.assertEqual(post.call_count, 1)
        saved = json.loads(self.path.read_text())
        self.assertEqual(saved["refresh_token"], REPLACEMENT["refresh_token"])
        self.assertEqual(saved["refresh_state"], "ready")
        self.assertIsNone(saved["refresh_started_at"])
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.path.parent.iterdir()))

    def test_concurrent_stores_refresh_once(self):
        self.seed(expired=True)
        def refresh(*_args, **_kwargs):
            time.sleep(0.03)
            return REPLACEMENT
        with mock.patch.object(auth, "_post", side_effect=refresh) as post:
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                values = list(pool.map(lambda _index: auth.CredentialStore(self.path, now=lambda: NOW).bearer(), [1, 2]))
        self.assertEqual(values, [REPLACEMENT["access_token"]] * 2)
        self.assertEqual(post.call_count, 1)

    def test_uncertain_refresh_never_replays_consumed_token(self):
        self.seed(expired=True)
        with mock.patch.object(auth, "_post", side_effect=auth.OAuthUnavailable("safe_failure")) as post:
            for _ in range(2):
                with self.assertRaisesRegex(auth.ReauthorizationRequired, "refresh_uncertain"):
                    self.store.bearer()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.store.status()["status"], "reauthorization_required")

    def test_crash_before_post_retains_reservation(self):
        self.seed(expired=True)
        with mock.patch.object(auth, "_post", side_effect=SystemExit("synthetic process death")) as post:
            with self.assertRaises(SystemExit):
                self.store.bearer()
            with self.assertRaises(auth.ReauthorizationRequired):
                self.store.bearer()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(json.loads(self.path.read_text())["refresh_state"], "refreshing")

    def test_cross_process_refresh_is_serialized(self):
        self.seed(expired=True)
        count_path = self.path.parent / "synthetic-refresh-count"
        code = """
import os,sys,time
from pathlib import Path
from unittest import mock
sys.path.insert(0,sys.argv[1])
import todoist_auth as auth
def refresh(*args,**kwargs):
    fd=os.open(sys.argv[3],os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'w') as stream:stream.write('refresh\\n')
    time.sleep(0.08)
    return {'access_token':'synthetic-new-access-token','refresh_token':'synthetic-new-refresh-token','token_type':'Bearer','expires_in':3600,'scope':'data:read task:add'}
with mock.patch.object(auth,'_post',side_effect=refresh):
    result=auth.CredentialStore(Path(sys.argv[2]),now=lambda:2000000000).bearer()
    assert result=='synthetic-new-access-token'
"""
        args = [sys.executable, "-B", "-c", code, str(Path(auth.__file__).parent), str(self.path), str(count_path)]
        children = [subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
        try:
            for child in children:
                stdout, stderr = child.communicate(timeout=5)
                self.assertEqual(child.returncode, 0, stderr.decode())
                self.assertEqual(stdout, b"")
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()
        self.assertEqual(count_path.read_text().splitlines(), ["refresh"])

    def test_crash_after_token_response_does_not_reuse_old_refresh(self):
        self.seed(expired=True)
        actual_write = self.store._write
        def write(directory, bundle):
            if bundle["refresh_state"] == "ready":
                raise auth.CredentialFileError("synthetic_disk_failure")
            actual_write(directory, bundle)
        with mock.patch.object(auth, "_post", return_value=REPLACEMENT) as post, mock.patch.object(self.store, "_write", side_effect=write):
            with self.assertRaises(auth.ReauthorizationRequired):
                self.store.bearer()
        with mock.patch.object(auth, "_post") as retry:
            with self.assertRaises(auth.ReauthorizationRequired):
                self.store.bearer()
        self.assertEqual(post.call_count, 1)
        retry.assert_not_called()

    def test_reservation_save_failure_never_posts(self):
        self.seed(expired=True)
        original = self.path.read_bytes()
        with mock.patch.object(self.store, "_write", side_effect=auth.CredentialFileError("safe_failure")), mock.patch.object(auth, "_post") as post:
            with self.assertRaises(auth.CredentialFileError):
                self.store.bearer()
        post.assert_not_called()
        self.assertEqual(self.path.read_bytes(), original)

    def test_missing_rotated_refresh_scope_or_invalid_tokens_require_reauth(self):
        for tokens in (
            {key: value for key, value in REPLACEMENT.items() if key != "refresh_token"},
            {**REPLACEMENT, "refresh_token": TOKENS["refresh_token"]},
            {**REPLACEMENT, "scope": "data:read_write"},
            {**REPLACEMENT, "scope": "task:add"},
            {**REPLACEMENT, "scope": "data:read task:add task:add"},
            {**REPLACEMENT, "expires_in": True},
            {**REPLACEMENT, "expires_in": 1},
            {**REPLACEMENT, "token_type": "Other"},
            {**REPLACEMENT, "access_token": "secret\nheader"},
        ):
            with self.subTest(fields=list(tokens)):
                self.seed(expired=True)
                with mock.patch.object(auth, "_post", return_value=tokens):
                    with self.assertRaises(auth.ReauthorizationRequired):
                        self.store.bearer()
                self.assertEqual(self.store.status()["status"], "reauthorization_required")

    def test_401_recovery_refreshes_rejected_token_once(self):
        self.seed()
        with mock.patch.object(auth, "_post", return_value=REPLACEMENT) as post:
            self.assertEqual(self.store.bearer(rejected_token=TOKENS["access_token"]), REPLACEMENT["access_token"])
            self.assertEqual(self.store.bearer(rejected_token=TOKENS["access_token"]), REPLACEMENT["access_token"])
        self.assertEqual(post.call_count, 1)

    def test_scope_comma_separator_is_exactly_supported(self):
        self.assertEqual(auth._scope_set("task:add,data:read"), auth.SCOPES)
        with self.assertRaises(auth.ReauthorizationRequired):
            auth._scope_set("task:add,data:read,data:delete")

    def test_private_file_directory_symlink_and_hardlink_checks(self):
        self.seed()
        for mode in (0o644, 0o640, 0o660):
            with self.subTest(mode=mode):
                self.path.chmod(mode)
                with self.assertRaises(auth.CredentialFileError):
                    self.store.status()
        self.path.chmod(0o600)
        other = self.path.with_name("hardlink.json")
        os.link(self.path, other)
        with self.assertRaises(auth.CredentialFileError):
            self.store.status()
        other.unlink()
        target = self.path.with_name("target.json")
        self.path.rename(target)
        self.path.symlink_to(target)
        with self.assertRaises(auth.CredentialFileError):
            self.store.status()
        self.path.unlink()
        target.rename(self.path)
        self.path.parent.chmod(0o755)
        try:
            with self.assertRaises(auth.CredentialFileError):
                self.store.status()
        finally:
            self.path.parent.chmod(0o700)

    def test_directory_symlink_and_non_owner_are_rejected(self):
        linked = self.path.parent / "link"
        linked.symlink_to(self.path.parent, target_is_directory=True)
        with self.assertRaises(auth.CredentialFileError):
            auth.CredentialStore(linked / "credentials.json").status()
        self.seed()
        with mock.patch.object(auth.os, "geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(auth.CredentialFileError):
                self.store.status()

    def test_lock_symlink_rejected_without_touching_target(self):
        self.seed()
        target = self.path.parent / "target"
        target.write_text("synthetic unrelated data")
        lock = self.path.with_name(self.path.name + ".lock")
        lock.symlink_to(target)
        with self.assertRaises(auth.CredentialFileError):
            self.store.bearer()
        self.assertEqual(target.read_text(), "synthetic unrelated data")

    def test_lock_timeout_is_bounded_and_never_posts(self):
        self.seed(expired=True)
        with self.store._locked(), mock.patch.object(auth, "_post") as post:
            other = auth.CredentialStore(self.path, lock_timeout=0.025)
            began = time.monotonic()
            with self.assertRaisesRegex(auth.OAuthUnavailable, "credentials_busy"):
                other.bearer()
            self.assertLess(time.monotonic() - began, 0.2)
        post.assert_not_called()

    def test_malformed_bundle_extra_keys_and_nonfinite_expiry_fail_closed(self):
        for changes in ({"expires_at": float("nan")}, {"version": True}, {"client_secret": "synthetic-secret"}, {"refresh_state": "unknown"}, {"refresh_state": {}}, {"scopes": ["data:read_write"]}):
            with self.subTest(changes=list(changes)):
                self.seed(**changes)
                with mock.patch.object(auth, "_post") as post:
                    with self.assertRaises(auth.ReauthorizationRequired):
                        self.store.bearer()
                post.assert_not_called()

    def test_callback_validates_exact_host_path_state_and_local_peer(self):
        valid = auth.CALLBACK_PATH + "?" + parse.urlencode({"state": STATE, "code": "synthetic-code"})
        for host, target, peer in (
            ("attacker.invalid:45678", valid, "127.0.0.1"),
            ("127.0.0.1:45678", valid, "127.0.0.1"),
            ("localhost:45678", valid, "192.0.2.1"),
            ("localhost:45678", valid.replace(auth.CALLBACK_PATH, "/other"), "127.0.0.1"),
            ("localhost:45678", valid.replace(STATE, "wrong-state"), "127.0.0.1"),
            ("localhost:45678", valid + "&state=" + STATE, "127.0.0.1"),
            ("localhost:45678", valid + "&extra=value", "127.0.0.1"),
            ("localhost:45678", "http://localhost:45678" + valid, "127.0.0.1"),
            ("localhost:45678", auth.CALLBACK_PATH + "?state=%C3%A9&code=synthetic", "127.0.0.1"),
            ("localhost:45678", "http://[", "127.0.0.1"),
        ):
            with self.subTest(host=host):
                callback = auth.CallbackState(STATE, 45678)
                self.assertFalse(callback.accept(host, target, peer))
                self.assertFalse(callback.finished.is_set())
        callback = auth.CallbackState(STATE, 45678)
        self.assertTrue(callback.accept("localhost:45678", valid, "127.0.0.1"))
        self.assertEqual(callback.result, "synthetic-code")
        self.assertFalse(callback.accept("localhost:45678", valid, "127.0.0.1"))

    def test_valid_refusal_ends_flow_without_token(self):
        callback = auth.CallbackState(STATE, 45678)
        target = auth.CALLBACK_PATH + "?" + parse.urlencode({"state": STATE, "error": "access_denied"})
        self.assertTrue(callback.accept("localhost:45678", target, "127.0.0.1"))
        self.assertTrue(callback.refused)
        self.assertIsNone(callback.result)

    def test_callback_handler_does_not_log_or_echo_query(self):
        target = auth.CALLBACK_PATH + "?" + parse.urlencode({"state": STATE, "code": "synthetic-sensitive-code"})
        socket = FakeSocket(f"GET {target} HTTP/1.1\r\nHost: localhost:45678\r\n\r\n".encode())
        server = SimpleNamespace(callback=auth.CallbackState(STATE, 45678))
        output = io.StringIO()
        with mock.patch("sys.stderr", output):
            auth._CallbackHandler(socket, ("127.0.0.1", 12345), server)
        self.assertEqual(output.getvalue(), "")
        response = socket.outgoing.getvalue().decode()
        self.assertIn("200 OK", response)
        self.assertIn("no-store", response)
        self.assertNotIn(STATE, response)
        self.assertNotIn("synthetic-sensitive-code", response)

    def test_callback_rejects_duplicate_host_and_unsupported_method_without_echo(self):
        for raw in (
            f"GET {auth.CALLBACK_PATH}?state={STATE}&code=secret HTTP/1.1\r\nHost: localhost:45678\r\nHost: localhost:45678\r\n\r\n",
            "POST /private-sensitive-query HTTP/1.1\r\nHost: localhost:45678\r\n\r\n",
        ):
            socket = FakeSocket(raw.encode())
            server = SimpleNamespace(callback=auth.CallbackState(STATE, 45678))
            with mock.patch("sys.stderr", io.StringIO()) as output:
                auth._CallbackHandler(socket, ("127.0.0.1", 12345), server)
            response = socket.outgoing.getvalue().decode()
            self.assertNotIn("200 OK", response)
            self.assertNotIn("private-sensitive", response)
            self.assertNotIn(STATE, response)
            self.assertEqual(output.getvalue(), "")

    def test_loopback_listener_binds_ipv4_and_closes_without_real_socket(self):
        server = mock.Mock(server_port=45678)
        with mock.patch.object(auth, "_CallbackServer", return_value=server) as factory:
            with auth.LoopbackCallback(STATE) as receiver:
                self.assertEqual(receiver.redirect_uri, "http://localhost:45678" + auth.CALLBACK_PATH)
                receiver.callback.result = "synthetic-code"
                receiver.callback.finished.set()
                self.assertEqual(receiver.wait(1), "synthetic-code")
        factory.assert_called_once_with(("127.0.0.1", 0), auth._CallbackHandler)
        server.server_close.assert_called_once()

    def test_loopback_timeout_and_refusal_are_safe(self):
        server = mock.Mock(server_port=45678)
        with mock.patch.object(auth, "_CallbackServer", return_value=server):
            with auth.LoopbackCallback(STATE) as receiver:
                with self.assertRaises(auth.AuthorizationTimedOut):
                    receiver.wait(0.01)
                receiver.callback.refused = True
                receiver.callback.finished.set()
                with self.assertRaises(auth.AuthorizationRefused):
                    receiver.wait(1)

    def test_real_loopback_slow_fragmented_headers_cannot_extend_overall_timeout(self):
        release = threading.Event()
        with auth.LoopbackCallback(STATE) as receiver:
            connection = socket.create_connection(("127.0.0.1", receiver.server.server_port), timeout=0.5)
            self.addCleanup(connection.close)
            connection.sendall(b"GET /todoist/oauth/callback HTTP/1.1\r\nHost: localhost:")
            def drip():
                while not release.wait(0.015):
                    try:
                        connection.sendall(b"x")
                    except OSError:
                        return
            thread = threading.Thread(target=drip, daemon=True)
            thread.start()
            began = time.monotonic()
            try:
                with self.assertRaises(auth.AuthorizationTimedOut):
                    receiver.wait(0.1)
                self.assertLess(time.monotonic() - began, 0.5)
            finally:
                release.set()
                connection.close()
                thread.join(timeout=0.5)

    def test_real_loopback_bounds_simultaneous_request_workers(self):
        connections = []
        with auth.LoopbackCallback(STATE) as receiver:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(receiver.wait, 0.4)
                try:
                    for _ in range(12):
                        connection = socket.create_connection(("127.0.0.1", receiver.server.server_port), timeout=0.5)
                        connections.append(connection)
                        try:
                            connection.sendall(b"GET / HTTP/1.1\r\nX-Slow: ")
                        except OSError:
                            pass
                    time.sleep(0.025)
                    with receiver.server._requests_lock:
                        self.assertLessEqual(len(receiver.server._requests), 4)
                    with self.assertRaises(auth.AuthorizationTimedOut):
                        pending.result(timeout=1)
                finally:
                    for connection in connections:
                        connection.close()

    def test_authorization_prepares_public_client_exact_scopes_and_pkce(self):
        post = self.flow()
        browser = mock.Mock(return_value=True)
        result = auth.authorize_local(self.path, open_browser=browser)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(post.call_count, 2)
        url, payload = post.call_args_list[0].args
        self.assertEqual(url, auth.REGISTER_URL)
        self.assertEqual(payload["token_endpoint_auth_method"], "none")
        self.assertEqual(payload["scope"], "data:read task:add")
        self.assertEqual(payload["grant_types"], ["authorization_code", "refresh_token"])
        auth_url = parse.urlsplit(browser.call_args.args[0])
        self.assertEqual(
            auth_url.scheme + "://" + auth_url.netloc + auth_url.path,
            "https://api.todoist.com/oauth/authorize",
        )
        query = parse.parse_qs(auth_url.query)
        self.assertEqual(query["scope"], ["data:read,task:add"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        exchange = post.call_args_list[1].args[1]
        challenge = base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [challenge])
        self.assertEqual(exchange["code"], "synthetic-authorization-code")
        self.assertNotIn("client_secret", exchange)
        saved = json.loads(self.path.read_text())
        self.assertNotIn("code", saved)
        self.assertNotIn("code_verifier", saved)
        self.assertNotIn("state", saved)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_refusal_timeout_and_failed_exchange_preserve_existing_credentials(self):
        self.seed()
        old = self.path.read_bytes()
        post = self.flow()
        for failure in (auth.AuthorizationRefused("refused"), auth.AuthorizationTimedOut("timed_out")):
            with self.subTest(kind=type(failure).__name__), mock.patch.object(FakeCallback, "wait", side_effect=failure):
                with self.assertRaises(type(failure)):
                    auth.authorize_local(self.path, open_browser=lambda _url: True)
                self.assertEqual(self.path.read_bytes(), old)
        with mock.patch.object(auth, "_new_bundle", side_effect=auth.ReauthorizationRequired("invalid_tokens")):
            with self.assertRaises(auth.ReauthorizationRequired):
                auth.authorize_local(self.path, open_browser=lambda _url: True)
        self.assertEqual(self.path.read_bytes(), old)

    def test_broader_scope_confidential_client_or_wrong_callback_fails_before_browser(self):
        for changes in (
            {"scope": "data:read_write"}, {"token_endpoint_auth_method": "client_secret_post"},
            {"client_secret": "synthetic-secret"}, {"redirect_uris": ["https://elsewhere.invalid/callback"]},
            {"grant_types": ["authorization_code"]}, {"client_id": "unknown-public-client"},
            {"grant_types": [{}, "refresh_token"]},
        ):
            with self.subTest(fields=list(changes)):
                self.flow(registration_changes=changes)
                browser = mock.Mock(return_value=True)
                with self.assertRaises(auth.ReauthorizationRequired):
                    auth.authorize_local(self.path, open_browser=browser)
                browser.assert_not_called()
                self.assertFalse(self.path.exists())

    def test_browser_failure_preserves_existing_file(self):
        self.seed()
        original = self.path.read_bytes()
        self.flow()
        for browser in (mock.Mock(return_value=False), mock.Mock(side_effect=RuntimeError("private authorization URL"))):
            with self.assertRaisesRegex(auth.OAuthUnavailable, "browser_unavailable"):
                auth.authorize_local(self.path, open_browser=browser)
        self.assertEqual(self.path.read_bytes(), original)

    def test_atomic_replace_failure_preserves_original_and_removes_temporary(self):
        self.seed()
        original = self.path.read_bytes()
        bundle = auth._new_bundle(CLIENT, REPLACEMENT, NOW)
        with self.store._locked() as directory, mock.patch.object(auth.os, "replace", side_effect=OSError("synthetic replace failure")):
            with self.assertRaises(auth.CredentialFileError):
                self.store._write(directory, bundle)
        self.assertEqual(self.path.read_bytes(), original)
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.path.parent.iterdir()))

    def test_fixed_transport_content_types_no_redirect_and_safe_errors(self):
        response = FakeResponse(TOKENS)
        opener = FakeOpener(response)
        with mock.patch.object(auth.request, "build_opener", return_value=opener):
            self.assertEqual(auth._post(auth.TOKEN_URL, {"code": "synthetic-code"}, form=True), TOKENS)
        req, timeout = opener.calls[0]
        self.assertEqual(req.full_url, auth.TOKEN_URL)
        self.assertEqual(req.get_header("Content-type"), "application/x-www-form-urlencoded")
        self.assertEqual(parse.parse_qs(req.data.decode()), {"code": ["synthetic-code"]})
        self.assertTrue(response.closed)
        self.assertIsNone(auth._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid"))
        for value in (
            error.HTTPError("https://private.invalid/secret", 400, "secret-code", {}, io.BytesIO(b"secret-body")),
            FakeResponse(raw=b"private bad json"),
            FakeResponse(raw=b"x" * (auth.MAX_JSON_BYTES + 1)),
            error.URLError("secret-token"),
        ):
            with self.subTest(kind=type(value).__name__), mock.patch.object(auth.request, "build_opener", return_value=FakeOpener(value)):
                with self.assertRaises(auth.OAuthUnavailable) as raised:
                    auth._post(auth.REGISTER_URL, {})
                self.assertNotIn("secret", str(raised.exception))
                self.assertNotIn("private", str(raised.exception))

    def test_transport_timeout_is_bounded_and_does_not_retry(self):
        release = threading.Event()
        opener = FakeOpener(FakeResponse(TOKENS, wait=release))
        began = time.monotonic()
        try:
            with mock.patch.object(auth.request, "build_opener", return_value=opener):
                with self.assertRaises(auth.OAuthUnavailable):
                    auth._post(auth.TOKEN_URL, {}, form=True, timeout=0.03)
            self.assertLess(time.monotonic() - began, 0.2)
            self.assertEqual(len(opener.calls), 1)
        finally:
            release.set()

    def test_unknown_endpoint_relative_path_and_unsafe_configuration_never_network(self):
        with mock.patch.object(auth.request, "build_opener") as opener:
            with self.assertRaises(auth.OAuthUnavailable):
                auth._post("https://other.invalid/token", {})
        opener.assert_not_called()
        for path in (Path("relative.json"), self.path.parent / ".." / "invalid.json"):
            with self.assertRaises(auth.CredentialFileError):
                auth.CredentialStore(path)
        with mock.patch.object(auth, "_post") as post:
            with self.assertRaises(auth.OAuthUnavailable):
                auth.authorize_local(self.path, port=-1)
        post.assert_not_called()

    def test_cli_status_is_read_only_and_errors_do_not_print_secrets(self):
        spec = importlib.util.spec_from_file_location("authorize_todoist_script", Path(auth.__file__).parent / "scripts" / "authorize_todoist.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with mock.patch.object(auth, "_post") as post, mock.patch("sys.stdout", io.StringIO()) as output:
            result = script.main(["status", "--credentials-file", str(self.path)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), {"configured": False, "status": "not_configured"})
        post.assert_not_called()
        self.assertEqual(list(self.path.parent.iterdir()), [])
        with mock.patch.object(script, "authorize_local", side_effect=auth.AuthorizationRefused("todoist_authorization_refused")), mock.patch("sys.stderr", io.StringIO()) as output, mock.patch("sys.stdout", io.StringIO()):
            result = script.main(["authorize", "--credentials-file", str(self.path)])
        self.assertEqual(result, 1)
        self.assertEqual(output.getvalue().strip(), "Todoist setup: todoist_authorization_refused")


if __name__ == "__main__":
    unittest.main()
