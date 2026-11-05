"""Durable, reminder-only admission budget. No credentials, environment or network.

Call ``reserve`` immediately before EACH model transport attempt; only its fresh
return authorizes one call. Every return permanently debits the fixed allowance,
including timeout, refusal, crash and unknown outcome. Excess is released only
after the caller verifies a completed response and supplies a valid usage receipt.
There is no reset. Unknown reservations carry into all future months.
Default allowance is zero, independent of grocery or account-wide spending.

Transport policy: only gpt-6-luna, service_tier=default, text only, no tools or
conversation history, max 32 KiB serialized request, max_output_tokens=2048,
prompt_cache_options={mode:implicit,ttl:30m}. The caller enforces this contract.
The debit does NOT infer token count from bytes: it covers the entire 1,050,000
model context at USD .30/M input plus 2048 output at USD 1/M, rounded to USD .32.
These rates exceed documented .10/.50 standard, long-context .20/.75 and 30m
cache-write/regional uplift as verified 2026-11-05:
https://developers.openai.com/api/docs/models/gpt-6-luna
Prices/model changes require policy review. This limits this app's admissions;
it is not an OpenAI account spending limit or a provider billing guarantee.

A separate witness is fsynced BEFORE each SQLite commit. Missing, truncated or
restored DB state (including restoring an older copy with the same ledger ID)
fails closed against that witness. An interrupted two-file update fails closed,
requiring operator reconciliation; it never silently repairs state. Restoring
BOTH files to a mutually consistent old copy cannot be detected locally: keep
the witness outside DB-only backup/restore and never bootstrap to repair loss.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any, Callable
import uuid
from zoneinfo import ZoneInfo

MODEL = "gpt-6-luna"
MAX_REQUEST_BYTES = 32 * 1024
MAX_OUTPUT_TOKENS = 2048
MAX_ATTEMPTS_PER_REQUEST = 2
CONTEXT_TOKENS = 1_050_000
INPUT_NANODOLLARS_PER_TOKEN = 300
OUTPUT_NANODOLLARS_PER_TOKEN = 1000
DEBIT_USD = Decimal("0.32")
_NANOS = 1_000_000_000
_DEBIT_NANOS = 320_000_000
_TZ = ZoneInfo("America/Bogota")
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\Z")
_STATE_TABLE = "reminder_ai_budget_state"
_ATTEMPT_TABLE = "reminder_ai_budget_attempts"
_POLICY = {"version": 1, "model": MODEL, "service_tier": "default", "tools": False,
           "prompt_cache_ttl": "30m", "max_request_bytes": MAX_REQUEST_BYTES,
           "max_output_tokens": MAX_OUTPUT_TOKENS, "timezone": "America/Bogota",
           "max_attempts": MAX_ATTEMPTS_PER_REQUEST, "debit_nanodollars": _DEBIT_NANOS,
           "context_tokens": CONTEXT_TOKENS, "input_nanodollars_per_token": INPUT_NANODOLLARS_PER_TOKEN,
           "output_nanodollars_per_token": OUTPUT_NANODOLLARS_PER_TOKEN,
           "refunds": "verified_completed_usage_only", "unsettled_carry": "all_future_months",
           "settled_charge": "admission_and_settlement_month"}


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


_POLICY_HASH = _digest(_POLICY)


class BudgetBlocked(RuntimeError):
    """Static safe reason; no model dispatch is allowed after this exception."""

    def __init__(self, reason: str):
        self.reason = reason
        self.code = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Reservation:
    reservation_id: int
    attempt: int
    month: str
    debit_usd: Decimal
    remaining_usd: Decimal


@dataclass(frozen=True)
class BudgetSnapshot:
    month: str
    attempts_this_month: int
    total_attempts: int
    debited_usd: Decimal
    remaining_usd: Decimal
    reserved_usd: Decimal
    charged_usd: Decimal


@dataclass(frozen=True)
class Settlement:
    settled: bool
    charged_usd: Decimal | None = None


def _limit(value: Decimal | str) -> int:
    try:
        if not isinstance(value, (Decimal, str)):
            raise ValueError("invalid_limit")
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount > Decimal("1000000"):
            raise ValueError("invalid_limit")
        nanos = amount * _NANOS
        if nanos != nanos.to_integral_value():
            raise ValueError("invalid_limit")
        return int(nanos)
    except (ValueError, InvalidOperation, TypeError, OverflowError):
        raise BudgetBlocked("reminder_ai_budget_limit_invalid") from None


def _identity(request_id: str, payload_hash: str, model: str) -> str:
    if (not isinstance(request_id, str) or not 0 < len(request_id) <= 512
            or not request_id.strip() or any(ord(c) < 32 or ord(c) == 127 for c in request_id)
            or not isinstance(payload_hash, str) or not _HEX.fullmatch(payload_hash)):
        raise BudgetBlocked("reminder_ai_budget_identity_invalid")
    if model != MODEL:
        raise BudgetBlocked("reminder_ai_budget_model_not_allowed")
    try:
        return hashlib.sha256(request_id.encode("utf-8")).hexdigest()
    except UnicodeError:
        raise BudgetBlocked("reminder_ai_budget_identity_invalid") from None


def _timestamp(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("invalid_clock")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _clock(now: Callable[[], datetime] | None) -> tuple[str, str]:
    try:
        current = now() if now else datetime.now(timezone.utc)
        stamp = _timestamp(current)
        month = current.astimezone(_TZ).strftime("%Y-%m")
        if not _MONTH.fullmatch(month):
            raise ValueError("invalid_month")
        return stamp, month
    except Exception:
        raise BudgetBlocked("reminder_ai_budget_clock_invalid") from None


def _valid_timestamp(value: Any) -> bool:
    try:
        return isinstance(value, str) and _timestamp(datetime.fromisoformat(value)) == value
    except (TypeError, ValueError, OverflowError):
        return False


def marker_path(path: Path | str) -> Path:
    """The witness is operational state; exclude it from automatic DB restores."""
    path = Path(path)
    return path.with_name(path.name + ".reminder-budget-initialized")


def _private_regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_uid != os.geteuid() or info.st_mode & 0o077):
        raise BudgetBlocked("reminder_ai_budget_state_invalid")
    return info


def _read_marker(path: Path) -> dict:
    descriptor = None
    try:
        witness = marker_path(path)
        before = _private_regular(witness)
        if not 0 < before.st_size <= 2048:
            raise ValueError("invalid_marker")
        descriptor = os.open(witness, os.O_RDONLY | os.O_NOFOLLOW)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise ValueError("changed_marker")
        value = json.loads(os.read(descriptor, 2049))
        if (not isinstance(value, dict)
                or set(value) != {"version", "ledger_id", "policy_hash", "sequence", "state_hash"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["ledger_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", value["ledger_id"])
                or value["policy_hash"] != _POLICY_HASH
                or type(value["sequence"]) is not int or value["sequence"] < 0
                or not isinstance(value["state_hash"], str) or not _HEX.fullmatch(value["state_hash"])):
            raise ValueError("invalid_marker")
        return value
    except BudgetBlocked:
        raise
    except FileNotFoundError:
        raise BudgetBlocked("reminder_ai_budget_state_missing") from None
    except (OSError, ValueError, TypeError):
        raise BudgetBlocked("reminder_ai_budget_state_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    directory = os.open(path, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _witness(state: dict) -> dict:
    return {"version": 1, "ledger_id": state["ledger_id"], "policy_hash": _POLICY_HASH,
            "sequence": state["sequence"], "state_hash": _digest(state)}


def _write_marker(path: Path, state: dict, *, previous: dict | None) -> None:
    """Persist rollback witness first; a crash can block, never restore allowance."""
    target = marker_path(path)
    if previous is not None and _read_marker(path) != previous:
        raise BudgetBlocked("reminder_ai_budget_state_changed")
    temporary = target if previous is None else target.with_name(target.name + ".tmp-" + uuid.uuid4().hex)
    raw = json.dumps(_witness(state), sort_keys=True, separators=(",", ":")).encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if previous is not None:
            os.replace(temporary, target)
        _fsync_directory(path.parent)
    except BaseException:
        # Keep incomplete initialization evidence. A stale temp file cannot be
        # used as an allowance and is safe for later manual cleanup.
        raise


@contextmanager
def _connection(path: Path, *, read_only: bool = False):
    conn = None
    try:
        before = _private_regular(path)
        mode = "ro" if read_only else "rw"
        conn = sqlite3.connect(path.absolute().as_uri() + "?mode=" + mode, uri=True, timeout=5)
        after = _private_regular(path)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise BudgetBlocked("reminder_ai_budget_state_changed")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA trusted_schema=OFF")
        if read_only:
            conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN" if read_only else "BEGIN IMMEDIATE")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BudgetBlocked("reminder_ai_budget_state_invalid")
        yield conn
        conn.commit()
    except BudgetBlocked:
        if conn is not None:
            conn.rollback()
        raise
    except FileNotFoundError:
        if conn is not None:
            conn.rollback()
        witness = marker_path(path)
        code = "reminder_ai_budget_state_missing" if witness.exists() or witness.is_symlink() else "reminder_ai_budget_uninitialized"
        raise BudgetBlocked(code) from None
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        if conn is not None:
            conn.rollback()
        raise BudgetBlocked("reminder_ai_budget_unavailable") from None
    finally:
        if conn is not None:
            conn.close()


def _validate(conn: sqlite3.Connection, path: Path, stamp: str) -> tuple[dict, list[dict], dict]:
    marker = _read_marker(path)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if tables != {_STATE_TABLE, _ATTEMPT_TABLE}:
        raise BudgetBlocked("reminder_ai_budget_state_invalid")
    states = conn.execute("SELECT * FROM reminder_ai_budget_state").fetchall()
    if len(states) != 1:
        raise BudgetBlocked("reminder_ai_budget_state_invalid")
    state = dict(states[0])
    if (set(state) != {"id", "version", "ledger_id", "policy_hash", "last_seen_utc", "sequence", "total_debit_nanos", "rows_hash"}
            or type(state.get("id")) is not int or state["id"] != 1
            or type(state.get("version")) is not int or state["version"] != 1
            or state.get("policy_hash") != _POLICY_HASH or _witness(state) != marker
            or not _valid_timestamp(state.get("last_seen_utc"))
            or any(type(state.get(k)) is not int or state[k] < 0 for k in ("sequence", "total_debit_nanos"))):
        raise BudgetBlocked("reminder_ai_budget_state_invalid")
    if stamp < state["last_seen_utc"]:
        raise BudgetBlocked("reminder_ai_budget_clock_rollback")
    rows = [dict(r) for r in conn.execute("SELECT * FROM reminder_ai_budget_attempts ORDER BY sequence")]
    requests: dict[str, tuple[str, int]] = {}
    for seq, row in enumerate(rows, 1):
        if (set(row) != {"sequence", "request_key", "payload_hash", "model", "attempt", "month", "debit_nanos", "created_at", "status", "settled_at", "settled_month", "input_tokens", "output_tokens", "charged_nanos"}
                or type(row["sequence"]) is not int or row["sequence"] != seq
                or not isinstance(row["request_key"], str) or not _HEX.fullmatch(row["request_key"])
                or not isinstance(row["payload_hash"], str) or not _HEX.fullmatch(row["payload_hash"])
                or row["model"] != MODEL or type(row["attempt"]) is not int
                or row["debit_nanos"] != _DEBIT_NANOS or type(row["debit_nanos"]) is not int
                or not _valid_timestamp(row["created_at"]) or row["created_at"] > state["last_seen_utc"]
                or row["month"] != datetime.fromisoformat(row["created_at"]).astimezone(_TZ).strftime("%Y-%m")):
            raise BudgetBlocked("reminder_ai_budget_state_invalid")
        if row["status"] == "reserved":
            if any(row[k] is not None for k in ("settled_at", "settled_month", "input_tokens", "output_tokens", "charged_nanos")):
                raise BudgetBlocked("reminder_ai_budget_state_invalid")
        elif row["status"] == "settled":
            if (not _valid_timestamp(row["settled_at"])
                    or not row["created_at"] <= row["settled_at"] <= state["last_seen_utc"]
                    or row["settled_month"] != datetime.fromisoformat(row["settled_at"]).astimezone(_TZ).strftime("%Y-%m")
                    or not _valid_usage(row["input_tokens"], row["output_tokens"], row["model"], "default")
                    or type(row["charged_nanos"]) is not int
                    or row["charged_nanos"] != _usage_charge(row["input_tokens"], row["output_tokens"])):
                raise BudgetBlocked("reminder_ai_budget_state_invalid")
        else:
            raise BudgetBlocked("reminder_ai_budget_state_invalid")
        old_hash, old_attempt = requests.get(row["request_key"], (row["payload_hash"], 0))
        if old_hash != row["payload_hash"] or row["attempt"] != old_attempt + 1 or row["attempt"] > MAX_ATTEMPTS_PER_REQUEST:
            raise BudgetBlocked("reminder_ai_budget_state_invalid")
        requests[row["request_key"]] = row["payload_hash"], row["attempt"]
    if (state["sequence"] != len(rows) or state["total_debit_nanos"] != sum(r["charged_nanos"] if r["status"] == "settled" else _DEBIT_NANOS for r in rows)
            or state["rows_hash"] != _digest(rows)):
        raise BudgetBlocked("reminder_ai_budget_state_invalid")
    return state, rows, marker


def _save(conn: sqlite3.Connection, path: Path, state: dict, rows: list[dict], marker: dict, stamp: str) -> None:
    state.update(last_seen_utc=stamp, sequence=len(rows),
                 total_debit_nanos=sum(r["charged_nanos"] if r["status"] == "settled" else _DEBIT_NANOS for r in rows),
                 rows_hash=_digest(rows))
    conn.execute("""UPDATE reminder_ai_budget_state SET last_seen_utc=:last_seen_utc,
        sequence=:sequence,total_debit_nanos=:total_debit_nanos,rows_hash=:rows_hash WHERE id=1""", state)
    _write_marker(path, state, previous=marker)


def _valid_usage(input_tokens: Any, output_tokens: Any, model: Any, service_tier: Any) -> bool:
    return (model == MODEL and service_tier == "default"
            and type(input_tokens) is int and type(output_tokens) is int
            and 0 <= input_tokens <= CONTEXT_TOKENS and 0 <= output_tokens <= MAX_OUTPUT_TOKENS
            and input_tokens + output_tokens <= CONTEXT_TOKENS)


def _usage_charge(input_tokens: int, output_tokens: int) -> int:
    return input_tokens * INPUT_NANODOLLARS_PER_TOKEN + output_tokens * OUTPUT_NANODOLLARS_PER_TOKEN


def _monthly_usage(rows: list[dict], month: str) -> tuple[int, int]:
    reserved = sum(r["debit_nanos"] for r in rows if r["status"] == "reserved")
    charged = sum(r["charged_nanos"] for r in rows if r["status"] == "settled"
                  and month in (r["month"], r["settled_month"]))
    return reserved, charged


class ReminderBudget:
    def __init__(self, path: Path | str, monthly_limit_usd: Decimal | str = "0", *, now: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.monthly_limit_nanos = _limit(monthly_limit_usd)
        self.now = now

    def initialize(self) -> None:
        """Explicit first installation only; never repair/reset request failures.

        Existing state is validated. An existing marker with a missing or empty
        database is refused. No constructor or request automatically bootstraps.
        """
        stamp, _ = _clock(self.now)
        path = self.path
        if path.exists() or path.is_symlink() or marker_path(path).exists() or marker_path(path).is_symlink():
            with _connection(path) as conn:
                state, rows, marker = _validate(conn, path, stamp)
                _save(conn, path, state, rows, marker, stamp)
            return
        try:
            # The operator chooses a private directory. Do not create/change
            # unrelated parent directories or accept a shared directory.
            parent = path.parent.stat()
            if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid() or parent.st_mode & 0o077:
                raise BudgetBlocked("reminder_ai_budget_directory_not_private")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            with _connection(path) as conn:
                conn.execute("""CREATE TABLE reminder_ai_budget_state (
                    id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL,
                    ledger_id TEXT NOT NULL, policy_hash TEXT NOT NULL, last_seen_utc TEXT NOT NULL,
                    sequence INTEGER NOT NULL, total_debit_nanos INTEGER NOT NULL, rows_hash TEXT NOT NULL)""")
                conn.execute("""CREATE TABLE reminder_ai_budget_attempts (
                    sequence INTEGER PRIMARY KEY, request_key TEXT NOT NULL, payload_hash TEXT NOT NULL,
                    model TEXT NOT NULL, attempt INTEGER NOT NULL, month TEXT NOT NULL,
                    debit_nanos INTEGER NOT NULL, created_at TEXT NOT NULL, status TEXT NOT NULL,
                    settled_at TEXT, settled_month TEXT, input_tokens INTEGER, output_tokens INTEGER,
                    charged_nanos INTEGER, UNIQUE(request_key,attempt))""")
                state = {"id": 1, "version": 1, "ledger_id": uuid.uuid4().hex, "policy_hash": _POLICY_HASH,
                         "last_seen_utc": stamp, "sequence": 0, "total_debit_nanos": 0, "rows_hash": _digest([])}
                conn.execute("INSERT INTO reminder_ai_budget_state VALUES (:id,:version,:ledger_id,:policy_hash,:last_seen_utc,:sequence,:total_debit_nanos,:rows_hash)", state)
                _write_marker(path, state, previous=None)
        except BudgetBlocked:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError):
            raise BudgetBlocked("reminder_ai_budget_initialization_failed") from None

    def snapshot(self) -> BudgetSnapshot:
        stamp, month = _clock(self.now)
        with _connection(self.path) as conn:
            state, rows, marker = _validate(conn, self.path, stamp)
            reserved, charged = _monthly_usage(rows, month)
            used = reserved + charged
            _save(conn, self.path, state, rows, marker, stamp)
            return BudgetSnapshot(month, sum(r["month"] == month for r in rows), len(rows), Decimal(used) / _NANOS,
                                  Decimal(max(0, self.monthly_limit_nanos - used)) / _NANOS,
                                  Decimal(reserved) / _NANOS, Decimal(charged) / _NANOS)

    def attempt_count(self, request_id: str, payload_hash: str) -> int:
        """Read remembered attempts without admission, settlement or state writes.

        Call before creating a new operation if its independent operation ledger
        has no record. A nonzero count is reconciliation evidence, not authority
        for another interpretation or task write. This uses a read-only SQLite
        snapshot, validates the durable witness and persisted clock high-water,
        and never advances that high-water or replaces/repairs the witness.
        """
        key = _identity(request_id, payload_hash, MODEL)
        stamp, _ = _clock(self.now)
        with _connection(self.path, read_only=True) as conn:
            _, rows, marker = _validate(conn, self.path, stamp)
            previous = [row for row in rows if row["request_key"] == key]
            if previous and previous[0]["payload_hash"] != payload_hash:
                raise BudgetBlocked("reminder_ai_budget_request_conflict")
            # A writer persists its witness before committing SQL. Refuse an
            # observed concurrent witness transition instead of trusting an old
            # SQL snapshot against newer independent state.
            if _read_marker(self.path) != marker:
                raise BudgetBlocked("reminder_ai_budget_state_changed")
            return len(previous)

    def reserve(self, request_id: str, payload_hash: str, model: str) -> Reservation:
        """Permanently debit one attempt; each successful call is a NEW debit.

        Same request/hash may reserve at most twice across its entire lifetime,
        including month changes. A new hash cannot reuse its request identity.
        Frozen interpretation reuse must bypass this method, since it makes no
        model transport attempt. Failure consumes any prior successful debits.
        """
        key = _identity(request_id, payload_hash, model)
        stamp, month = _clock(self.now)
        blocked = None
        reservation = None
        with _connection(self.path) as conn:
            state, rows, marker = _validate(conn, self.path, stamp)
            previous = [row for row in rows if row["request_key"] == key]
            used = sum(_monthly_usage(rows, month))
            if previous and previous[0]["payload_hash"] != payload_hash:
                blocked = "reminder_ai_budget_request_conflict"
            elif len(previous) >= MAX_ATTEMPTS_PER_REQUEST:
                blocked = "reminder_ai_budget_attempt_limit"
            elif used + _DEBIT_NANOS > self.monthly_limit_nanos:
                blocked = "reminder_ai_budget_monthly_limit"
            else:
                row = {"sequence": len(rows) + 1, "request_key": key, "payload_hash": payload_hash,
                       "model": MODEL, "attempt": len(previous) + 1, "month": month,
                       "debit_nanos": _DEBIT_NANOS, "created_at": stamp, "status": "reserved",
                       "settled_at": None, "settled_month": None, "input_tokens": None, "output_tokens": None, "charged_nanos": None}
                conn.execute("INSERT INTO reminder_ai_budget_attempts VALUES (:sequence,:request_key,:payload_hash,:model,:attempt,:month,:debit_nanos,:created_at,:status,:settled_at,:settled_month,:input_tokens,:output_tokens,:charged_nanos)", row)
                rows.append(row)
                reservation = Reservation(row["sequence"], row["attempt"], month, DEBIT_USD,
                                          Decimal(self.monthly_limit_nanos - used - _DEBIT_NANOS) / _NANOS)
            # Advance clock even for denied attempts without granting allowance.
            _save(conn, self.path, state, rows, marker, stamp)
        if blocked:
            raise BudgetBlocked(blocked)
        assert reservation is not None
        return reservation


    def settle(self, reservation_id: int, input_tokens: int, output_tokens: int,
               model: str, service_tier: str) -> Settlement:
        """Release excess only for a caller-verified COMPLETED Responses receipt.

        Caller must verify completed status, response identity, no refusal or
        error, valid total_tokens and bounded output before calling this method.
        Counts include reasoning output. Missing/invalid receipt never refunds;
        repeated exact settlement is idempotent, conflicting settlement blocks.
        This is conservative accounting at pinned upper rates, not invoice cost.
        """
        if type(reservation_id) is not int or reservation_id <= 0:
            raise BudgetBlocked("reminder_ai_budget_reservation_invalid")
        stamp, month = _clock(self.now)
        blocked = None
        result = Settlement(False)
        with _connection(self.path) as conn:
            state, rows, marker = _validate(conn, self.path, stamp)
            row = next((r for r in rows if r["sequence"] == reservation_id), None)
            if row is None:
                blocked = "reminder_ai_budget_reservation_missing"
            elif _valid_usage(input_tokens, output_tokens, model, service_tier):
                charge = _usage_charge(input_tokens, output_tokens)
                if row["status"] == "settled":
                    if (row["input_tokens"], row["output_tokens"]) != (input_tokens, output_tokens):
                        blocked = "reminder_ai_budget_settlement_conflict"
                    else:
                        result = Settlement(True, Decimal(row["charged_nanos"]) / _NANOS)
                else:
                    row.update(status="settled", settled_at=stamp, settled_month=month,
                               input_tokens=input_tokens, output_tokens=output_tokens, charged_nanos=charge)
                    conn.execute("""UPDATE reminder_ai_budget_attempts SET status=:status,
                        settled_at=:settled_at,settled_month=:settled_month,input_tokens=:input_tokens,
                        output_tokens=:output_tokens,charged_nanos=:charged_nanos WHERE sequence=:sequence""", row)
                    result = Settlement(True, Decimal(charge) / _NANOS)
            _save(conn, self.path, state, rows, marker, stamp)
        if blocked:
            raise BudgetBlocked(blocked)
        return result
