"""Dedicated personal-app Todoist OAuth setup and rotating credential storage.

Importing this module does not read a secret, start a listener or contact a
provider. ``authorize_local`` is for an explicitly invoked local testing or
one-time setup flow; Todoist documents localhost redirects for testing, not as
a general production callback guarantee. No connector/CLI credentials are used.

Official contract: https://developer.todoist.com/api/v1/ (OAuth, public dynamic
clients, PKCE, refresh-token rotation). A durable refresh reservation deliberately
requires reauthorization after an uncertain refresh; the old token is never
replayed, even within Todoist's documented grace window.
"""
from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import math
import os
import queue
import re
import secrets
import socket
import stat
import threading
import time
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib import error, parse, request

REGISTER_URL = "https://api.todoist.com/oauth/register"
# Advertised by https://api.todoist.com/.well-known/oauth-authorization-server.
AUTHORIZE_URL = "https://api.todoist.com/oauth/authorize"
TOKEN_URL = "https://api.todoist.com/oauth/access_token"
SCOPES = frozenset({"task:add", "data:read"})
CALLBACK_PATH = "/todoist/oauth/callback"
MAX_JSON_BYTES = 32 * 1024
REFRESH_MARGIN = 60
_TRANSPORT_SLOTS = threading.BoundedSemaphore(2)
_CLIENT_RE = re.compile(r"tdd_[A-Za-z0-9_-]{1,200}\Z")
_BUNDLE_FIELDS = frozenset({
    "version", "client_id", "token_endpoint_auth_method", "scopes",
    "access_token", "refresh_token", "expires_at", "refresh_state", "refresh_started_at",
})


class TodoistAuthError(RuntimeError):
    """Only a fixed, safe code may be presented to the user."""


class CredentialFileError(TodoistAuthError):
    pass


class ReauthorizationRequired(TodoistAuthError):
    pass


class OAuthUnavailable(TodoistAuthError):
    pass


class AuthorizationRefused(TodoistAuthError):
    pass


class AuthorizationTimedOut(TodoistAuthError):
    pass


def _opaque(value: Any) -> bool:
    return (
        isinstance(value, str) and 1 <= len(value) <= 8192
        and all(33 <= ord(char) <= 126 for char in value)
    )


def _scope_set(value: Any) -> frozenset[str]:
    if not isinstance(value, str) or len(value) > 512:
        raise ReauthorizationRequired("todoist_scopes_not_verified")
    values = re.split(r"[\s,]+", value.strip())
    if not values or len(values) != len(set(values)) or frozenset(values) != SCOPES:
        raise ReauthorizationRequired("todoist_scopes_not_verified")
    return frozenset(values)


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _post(url: str, payload: dict[str, Any], *, form: bool = False, timeout: float = 8) -> dict[str, Any]:
    """One bounded provider call; no retry, redirect, raw-body error or logging."""
    if url not in {REGISTER_URL, TOKEN_URL} or not 0 < timeout <= 30:
        raise OAuthUnavailable("todoist_oauth_unavailable")
    if not _TRANSPORT_SLOTS.acquire(blocking=False):
        raise OAuthUnavailable("todoist_oauth_busy")
    try:
        encoded = parse.urlencode(payload).encode() if form else json.dumps(payload).encode()
        req = request.Request(url, data=encoded, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
            "Accept": "application/json", "Cache-Control": "no-store",
        })
    except (TypeError, ValueError, UnicodeError):
        _TRANSPORT_SLOTS.release()
        raise OAuthUnavailable("todoist_oauth_invalid_request") from None
    results: queue.Queue[Any] = queue.Queue(maxsize=1)
    deadline = time.monotonic() + timeout

    def transport() -> None:
        try:
            with request.build_opener(request.ProxyHandler({}), _NoRedirect()).open(req, timeout=timeout) as response:
                raw = response.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise OAuthUnavailable("todoist_oauth_invalid_response")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise OAuthUnavailable("todoist_oauth_invalid_response")
            results.put(value)
        except error.HTTPError as exc:
            exc.close()
            results.put(OAuthUnavailable("todoist_oauth_request_failed"))
        except TodoistAuthError as exc:
            results.put(exc)
        except Exception:
            # Provider exceptions and bodies may contain credentials or codes.
            results.put(OAuthUnavailable("todoist_oauth_unavailable"))
        finally:
            _TRANSPORT_SLOTS.release()

    try:
        threading.Thread(target=transport, name="todoist-oauth-http", daemon=True).start()
    except RuntimeError:
        _TRANSPORT_SLOTS.release()
        raise OAuthUnavailable("todoist_oauth_unavailable") from None
    try:
        result = results.get(timeout=max(0.001, deadline - time.monotonic()))
    except queue.Empty:
        raise OAuthUnavailable("todoist_oauth_timeout") from None
    if isinstance(result, BaseException):
        raise result
    return result


def _client_id(value: Any) -> str:
    if not isinstance(value, str) or _CLIENT_RE.fullmatch(value) is None:
        raise ReauthorizationRequired("todoist_client_not_verified")
    return value


def _new_bundle(client_id: str, tokens: dict[str, Any], now: float, *, previous_refresh: str | None = None) -> dict[str, Any]:
    if not isinstance(tokens, dict):
        raise ReauthorizationRequired("todoist_tokens_not_verified")
    _scope_set(tokens.get("scope"))
    if (
        not isinstance(tokens.get("token_type"), str) or tokens["token_type"].lower() != "bearer"
        or not _opaque(tokens.get("access_token")) or not _opaque(tokens.get("refresh_token"))
        or type(tokens.get("expires_in")) is not int or not REFRESH_MARGIN < tokens["expires_in"] <= 86400
        or tokens["refresh_token"] == previous_refresh
    ):
        raise ReauthorizationRequired("todoist_tokens_not_verified")
    return {
        "version": 1, "client_id": _client_id(client_id), "token_endpoint_auth_method": "none",
        "scopes": sorted(SCOPES), "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"], "expires_at": now + tokens["expires_in"],
        "refresh_state": "ready", "refresh_started_at": None,
    }


def _validate_bundle(bundle: Any) -> dict[str, Any]:
    if not isinstance(bundle, dict) or frozenset(bundle) != _BUNDLE_FIELDS:
        raise ReauthorizationRequired("todoist_credential_bundle_invalid")
    _client_id(bundle.get("client_id"))
    expiry = bundle.get("expires_at")
    scopes = bundle.get("scopes")
    started = bundle.get("refresh_started_at")
    if (
        type(bundle.get("version")) is not int or bundle["version"] != 1
        or bundle.get("token_endpoint_auth_method") != "none"
        or not isinstance(scopes, list) or not all(isinstance(scope, str) for scope in scopes)
        or len(scopes) != len(SCOPES) or frozenset(scopes) != SCOPES
        or not _opaque(bundle.get("access_token")) or not _opaque(bundle.get("refresh_token"))
        or type(expiry) not in {int, float} or not math.isfinite(expiry) or expiry < 0
        or bundle.get("refresh_state") not in ("ready", "refreshing")
        or (bundle.get("refresh_state") == "ready" and started is not None)
        or (bundle.get("refresh_state") == "refreshing" and (
            type(started) not in {int, float} or not math.isfinite(started) or started < 0
        ))
    ):
        raise ReauthorizationRequired("todoist_credential_bundle_invalid")
    return bundle


class CredentialStore:
    """Private JSON plus a separate OS lock; no secret appears in repr/status."""
    def __init__(self, path: Path | str, *, timeout: float = 8, lock_timeout: float = 3, now: Callable[[], float] = time.time):
        candidate = Path(path)
        if not candidate.is_absolute() or ".." in candidate.parts or candidate.name in {"", ".", ".."}:
            raise CredentialFileError("todoist_credentials_path_invalid")
        if (
            type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 30
            or type(lock_timeout) not in {int, float} or not math.isfinite(lock_timeout) or not 0 < lock_timeout <= 30
        ):
            raise CredentialFileError("todoist_credentials_configuration_invalid")
        self.path = candidate
        self.timeout = float(timeout)
        self.lock_timeout = float(lock_timeout)
        self._now = now

    @staticmethod
    def _private_file(info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1
        ):
            raise CredentialFileError("todoist_credentials_file_not_private")

    @contextmanager
    def _directory(self):
        # Reject symlinks, including directory components. Use /private/tmp,
        # rather than macOS's /tmp alias, when preparing a temporary destination.
        try:
            current = Path(self.path.anchor)
            for part in self.path.parent.parts[1:]:
                current = current / part
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise CredentialFileError("todoist_credentials_symlink_rejected")
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except TodoistAuthError:
            raise
        except OSError:
            raise CredentialFileError("todoist_credentials_directory_unavailable") from None
        try:
            info = os.fstat(directory)
            if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
                raise CredentialFileError("todoist_credentials_directory_not_private")
            yield directory
        finally:
            os.close(directory)

    def _check_existing(self, directory: int) -> bool:
        try:
            info = os.stat(self.path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            raise CredentialFileError("todoist_credentials_unavailable") from None
        self._private_file(info)
        return True

    def _read(self, directory: int) -> dict[str, Any]:
        try:
            fd = os.open(self.path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                self._private_file(info)
                if info.st_size > MAX_JSON_BYTES:
                    raise ReauthorizationRequired("todoist_credential_bundle_invalid")
                raw = stream.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise ReauthorizationRequired("todoist_credential_bundle_invalid")
            return _validate_bundle(json.loads(raw))
        except FileNotFoundError:
            raise ReauthorizationRequired("todoist_authorization_required") from None
        except TodoistAuthError:
            raise
        except (OSError, ValueError, UnicodeError):
            raise CredentialFileError("todoist_credentials_unavailable") from None

    def _write(self, directory: int, bundle: dict[str, Any]) -> None:
        _validate_bundle(bundle)
        temporary = "." + self.path.name + "." + secrets.token_hex(12) + ".tmp"
        try:
            self._check_existing(directory)
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(json.dumps(bundle, separators=(",", ":"), sort_keys=True).encode())
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path.name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        except TodoistAuthError:
            raise
        except OSError:
            raise CredentialFileError("todoist_credentials_save_failed") from None
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except OSError:
                pass

    @contextmanager
    def _locked(self):
        with self._directory() as directory:
            fd = None
            try:
                lock_name = self.path.name + ".lock"
                flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
                try:
                    fd = os.open(lock_name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=directory)
                    os.fchmod(fd, 0o600)
                except FileExistsError:
                    fd = os.open(lock_name, flags, dir_fd=directory)
            except OSError:
                if fd is not None:
                    os.close(fd)
                raise CredentialFileError("todoist_credentials_lock_unavailable") from None
            try:
                self._private_file(os.fstat(fd))
                deadline = time.monotonic() + self.lock_timeout
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= deadline:
                            raise OAuthUnavailable("todoist_credentials_busy") from None
                        time.sleep(0.01)
                yield directory
            finally:
                os.close(fd)

    def status(self) -> dict[str, Any]:
        """Read-only local inspection: no lock creation, refresh or provider call."""
        with self._directory() as directory:
            if not self._check_existing(directory):
                return {"configured": False, "status": "not_configured"}
            bundle = self._read(directory)
        if bundle["refresh_state"] != "ready":
            state = "reauthorization_required"
        elif bundle["expires_at"] <= self._now() + REFRESH_MARGIN:
            state = "refresh_required"
        else:
            state = "ready"
        return {"configured": True, "status": state, "scopes": sorted(SCOPES), "expires_at": bundle["expires_at"]}

    def bearer(self, *, rejected_token: str | None = None) -> str:
        """Get/refresh one token; pass a rejected token to recover one API 401.

        A concurrent caller that already replaced ``rejected_token`` wins; this
        method returns that replacement without issuing another refresh.
        """
        if rejected_token is not None and not _opaque(rejected_token):
            raise ReauthorizationRequired("todoist_rejected_token_invalid")
        with self._locked() as directory:
            bundle = self._read(directory)
            if bundle["refresh_state"] != "ready":
                raise ReauthorizationRequired("todoist_refresh_uncertain_reauthorize")
            rejected = rejected_token is not None and hmac.compare_digest(bundle["access_token"], rejected_token)
            if bundle["expires_at"] > self._now() + REFRESH_MARGIN and not rejected:
                return bundle["access_token"]
            reserved = {**bundle, "refresh_state": "refreshing", "refresh_started_at": self._now()}
            # This reservation and its directory entry must be durable before
            # a token that may be consumed is sent over the network.
            self._write(directory, reserved)
            try:
                response = _post(TOKEN_URL, {
                    "client_id": bundle["client_id"], "grant_type": "refresh_token",
                    "refresh_token": bundle["refresh_token"],
                }, form=True, timeout=self.timeout)
                replacement = _new_bundle(bundle["client_id"], response, self._now(), previous_refresh=bundle["refresh_token"])
                self._write(directory, replacement)
            except TodoistAuthError:
                # Retain refreshing after transport ambiguity, refusal or an
                # invalid rotation response. Never resend the old refresh token.
                raise ReauthorizationRequired("todoist_refresh_uncertain_reauthorize") from None
            return replacement["access_token"]


class CallbackState:
    """Pure callback validation; neither incoming query strings nor codes log."""
    def __init__(self, state: str, port: int):
        self.state = state
        self.host = f"localhost:{port}"
        self.result: str | None = None
        self.refused = False
        self.finished = threading.Event()
        self._lock = threading.Lock()

    def accept(self, host: str, target: str, client_ip: str) -> bool:
        with self._lock:
            if self.finished.is_set() or host != self.host or client_ip != "127.0.0.1" or len(target) > 4096:
                return False
            try:
                parts = parse.urlsplit(target)
            except ValueError:
                return False
            if parts.scheme or parts.netloc or parts.fragment or parts.path != CALLBACK_PATH:
                return False
            try:
                fields = parse.parse_qs(parts.query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
            except ValueError:
                return False
            if any(len(value) != 1 for value in fields.values()) or set(fields) not in ({"state", "code"}, {"state", "error"}):
                return False
            supplied_state = fields["state"][0]
            if not _opaque(supplied_state) or not hmac.compare_digest(supplied_state, self.state):
                return False
            if "error" in fields:
                self.refused = True
            elif _opaque(fields["code"][0]) and len(fields["code"][0]) <= 1024:
                self.result = fields["code"][0]
            else:
                return False
            self.finished.set()
            return True


class _CallbackHandler(BaseHTTPRequestHandler):
    # Slow or malicious local connections must not hold the setup listener.
    timeout = 1
    server_version = "PersonalWatchTodoistSetup"
    sys_version = ""

    def log_message(self, *_args):
        pass

    def do_GET(self):  # noqa: N802
        hosts = self.headers.get_all("Host", [])
        accepted = len(hosts) == 1 and self.server.callback.accept(hosts[0], self.path, self.client_address[0])
        body = b"Authorization received. You may close this window." if accepted else b"Invalid callback. Continue in the authorization window."
        self.send_response(200 if accepted else 400)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler's default error can echo request text. Keep
        # unsupported methods and malformed requests generic and unlogged.
        body = b"Invalid callback request."
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class _CallbackServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    block_on_close = False

    def __init__(self, *args, **kwargs):
        self._slots = threading.BoundedSemaphore(4)
        self._requests = set()
        self._requests_lock = threading.Lock()
        self._closing = False
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        with self._requests_lock:
            if self._closing:
                self._slots.release()
                self.shutdown_request(request)
                return
            self._requests.add(request)
        try:
            super().process_request(request, client_address)
        except Exception:
            with self._requests_lock:
                self._requests.discard(request)
            self._slots.release()
            self.shutdown_request(request)

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self._requests_lock:
                self._requests.discard(request)
            self._slots.release()

    def server_close(self):
        # Do not leave slow local connections alive after the overall setup
        # deadline. This also releases the bounded daemon request workers.
        with self._requests_lock:
            self._closing = True
            active = tuple(self._requests)
        for connection in active:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            connection.close()
        super().server_close()

    def handle_error(self, request, client_address):
        # A disconnect must not print a traceback containing the request line.
        pass


class LoopbackCallback:
    def __init__(self, state: str, port: int = 0):
        try:
            self.server = _CallbackServer(("127.0.0.1", port), _CallbackHandler)
        except OSError:
            raise OAuthUnavailable("todoist_callback_unavailable") from None
        self.server.timeout = 0.2
        self.callback = CallbackState(state, self.server.server_port)
        self.server.callback = self.callback
        self.redirect_uri = f"http://localhost:{self.server.server_port}{CALLBACK_PATH}"
        self._thread = None
        self._failed = threading.Event()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        if self._thread is not None and self._thread.is_alive():
            self.server.shutdown()
        self.server.server_close()

    def wait(self, timeout: float) -> str:
        if not self.callback.finished.is_set():
            def serve():
                try:
                    self.server.serve_forever(poll_interval=0.05)
                except Exception:
                    self._failed.set()
                    self.callback.finished.set()
            self._thread = threading.Thread(target=serve, name="todoist-oauth-callback", daemon=True)
            self._thread.start()
            try:
                finished = self.callback.finished.wait(timeout)
            finally:
                # The accept loop only starts bounded daemon request workers;
                # fragmented HTTP headers cannot block this overall wait.
                self.server.shutdown()
            if not finished:
                raise AuthorizationTimedOut("todoist_authorization_timeout")
        if self._failed.is_set():
            raise OAuthUnavailable("todoist_callback_unavailable")
        if self.callback.refused:
            raise AuthorizationRefused("todoist_authorization_refused")
        if self.callback.result is None:
            raise OAuthUnavailable("todoist_callback_invalid")
        return self.callback.result


def authorize_local(path: Path | str, *, port: int = 0, timeout: float = 120,
                    open_browser: Callable[[str], Any] = webbrowser.open) -> dict[str, Any]:
    """Explicit local setup only; never call automatically from a capture."""
    if type(port) is not int or not 0 <= port <= 65535 or type(timeout) not in {int, float} or not math.isfinite(timeout) or not 1 <= timeout <= 600:
        raise OAuthUnavailable("todoist_authorization_configuration_invalid")
    store = CredentialStore(path)
    with store._locked() as directory:
        store._check_existing(directory)
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        with LoopbackCallback(state, port) as callback:
            registration = _post(REGISTER_URL, {
                "client_name": "Personal Watch App local setup",
                "redirect_uris": [callback.redirect_uri], "scope": " ".join(sorted(SCOPES)),
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"], "token_endpoint_auth_method": "none",
            })
            _scope_set(registration.get("scope"))
            client_id = _client_id(registration.get("client_id"))
            if (
                registration.get("token_endpoint_auth_method") != "none"
                or registration.get("redirect_uris") != [callback.redirect_uri]
                or not isinstance(registration.get("grant_types"), list)
                or not all(isinstance(value, str) for value in registration["grant_types"])
                or len(registration["grant_types"]) != 2
                or any(value not in {"authorization_code", "refresh_token"} for value in registration["grant_types"])
                or "authorization_code" not in registration["grant_types"]
                or "refresh_token" not in registration["grant_types"]
                or registration.get("response_types") != ["code"]
                or registration.get("client_secret") not in (None, "")
            ):
                raise ReauthorizationRequired("todoist_client_not_verified")
            url = AUTHORIZE_URL + "?" + parse.urlencode({
                "client_id": client_id, "scope": ",".join(sorted(SCOPES)),
                "state": state, "redirect_uri": callback.redirect_uri, "response_type": "code",
                "code_challenge": challenge, "code_challenge_method": "S256",
            })
            try:
                opened = open_browser(url)
            except Exception:
                raise OAuthUnavailable("todoist_browser_unavailable") from None
            if not opened:
                raise OAuthUnavailable("todoist_browser_unavailable")
            code = callback.wait(float(timeout))
            tokens = _post(TOKEN_URL, {
                "client_id": client_id, "grant_type": "authorization_code", "code": code,
                "redirect_uri": callback.redirect_uri, "code_verifier": verifier,
            }, form=True)
            bundle = _new_bundle(client_id, tokens, store._now())
            store._write(directory, bundle)
    return store.status()
