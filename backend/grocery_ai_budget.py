"""Durable, fail-closed USD 2/month admission guard for grocery Luna calls.

Only a fresh reservation authorizes one provider dispatch. There is intentionally
no release/reset operation: timeouts, crashes and unknown usage keep their full
reservation, including across calendar months. The caller supplies the existing
grocery journal; this module has no environment, credential or network access.

Pinned standard Luna prices are USD .10/.50 per million input/output tokens:
https://developers.openai.com/api/docs/models/gpt-6-luna
Accounting deliberately overestimates at .30/1.00, covering long-context,
30-minute cache-write and regional multipliers. The transport must pin 30-minute
cache writes. Output usage already includes reasoning.
Reserve the entire model context as possible input plus the requested output;
no unproven relationship between UTF-8 bytes and tokens is used. This guards this
application's admissions, not provider/account-wide billing or future pricing.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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
MONTHLY_CAP_NANODOLLARS = 2_000_000_000
CONTEXT_TOKENS = 1_050_000
MODEL_MAX_OUTPUT_TOKENS = 128_000
MAX_OUTPUT_TOKENS = 3000
# A nanodollar/token is .001 dollars/million tokens.
BASE_INPUT_NANODOLLARS_PER_TOKEN = 100
BASE_OUTPUT_NANODOLLARS_PER_TOKEN = 500
INPUT_NANODOLLARS_PER_TOKEN = 300
OUTPUT_NANODOLLARS_PER_TOKEN = 1000
_POLICY = {
    "version": 1, "model": MODEL, "service_tier": "default", "tools": False,
    "prompt_cache_ttl": "30m",
    "cap_nanodollars": MONTHLY_CAP_NANODOLLARS, "timezone": "America/Bogota",
    "context_tokens": CONTEXT_TOKENS, "max_output_tokens": MAX_OUTPUT_TOKENS,
    "base_input_nanodollars_per_token": BASE_INPUT_NANODOLLARS_PER_TOKEN,
    "base_output_nanodollars_per_token": BASE_OUTPUT_NANODOLLARS_PER_TOKEN,
    "input_nanodollars_per_token": INPUT_NANODOLLARS_PER_TOKEN,
    "output_nanodollars_per_token": OUTPUT_NANODOLLARS_PER_TOKEN,
    "unsettled_carry": "all_future_months", "settled_charge": "admission_and_settlement_month",
}
_POLICY_JSON = json.dumps(_POLICY, sort_keys=True, separators=(",", ":"))
_POLICY_HASH = hashlib.sha256(_POLICY_JSON.encode()).hexdigest()
_STATE_TABLE = "grocery_ai_budget_state"
_REQUEST_TABLE = "grocery_ai_budget_requests"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MONTH = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])\Z")
_TZ = ZoneInfo("America/Bogota")


class BudgetGuardError(RuntimeError):
    """Fixed safe code; the caller must not dispatch on any guard failure."""


@dataclass(frozen=True)
class Admission:
    allowed: bool
    fresh: bool
    request_id: str
    reservation_nanodollars: int
    reason: str


@dataclass(frozen=True)
class Settlement:
    settled: bool
    charged_nanodollars: int | None = None


@dataclass(frozen=True)
class BudgetSnapshot:
    month: str
    reserved_nanodollars: int
    charged_nanodollars: int
    remaining_nanodollars: int


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _month(clock: Callable[[], datetime] | None) -> str:
    try:
        now = clock() if clock else datetime.now(timezone.utc)
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("untrusted_clock")
        month = now.astimezone(_TZ).strftime("%Y-%m")
        if not _MONTH.fullmatch(month):
            raise ValueError("invalid_month")
        return month
    except Exception:
        raise BudgetGuardError("grocery_ai_budget_clock_invalid") from None


def _identity(request_id: str, body_hash: str) -> None:
    if (not isinstance(request_id, str) or not 0 < len(request_id) <= 512
            or not request_id.strip() or any(ord(c) < 32 for c in request_id)
            or not isinstance(body_hash, str) or not _HEX.fullmatch(body_hash)):
        raise BudgetGuardError("grocery_ai_budget_identity_invalid")


def _marker_path(path: Path) -> Path:
    return path.with_name(path.name + ".ai-budget-initialized")


def _read_marker(path: Path) -> dict:
    marker = _marker_path(path)
    descriptor = None
    try:
        info = marker.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024:
            raise ValueError("invalid_marker")
        descriptor = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW)
        actual = os.fstat(descriptor)
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("changed_marker")
        value = json.loads(os.read(descriptor, 1025))
        if (not isinstance(value, dict) or set(value) != {"version", "ledger_id", "policy_hash"}
                or type(value["version"]) is not int or value["version"] != 1
                or not isinstance(value["ledger_id"], str)
                or not re.fullmatch(r"[0-9a-f]{32}", value["ledger_id"])
                or value["policy_hash"] != _POLICY_HASH):
            raise ValueError("invalid_marker")
        return value
    except (OSError, ValueError, TypeError):
        raise BudgetGuardError("grocery_ai_budget_state_invalid") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write_marker(path: Path, ledger_id: str) -> None:
    value = json.dumps({"version": 1, "ledger_id": ledger_id, "policy_hash": _POLICY_HASH},
                       sort_keys=True, separators=(",", ":")).encode()
    # Marker precedes the SQL commit. An interrupted initialization fails closed
    # on restart; it can never silently initialize a fresh spending allowance.
    descriptor = os.open(_marker_path(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        # An incomplete marker is deliberately retained as evidence of state loss.
        raise


@contextmanager
def _connection(path: Path):
    conn = None
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise BudgetGuardError("grocery_ai_budget_state_invalid")
        # mode=rw forbids SQLite's normal implicit creation after deletion.
        conn = sqlite3.connect(path.absolute().as_uri() + "?mode=rw", uri=True, timeout=30)
        after = path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise BudgetGuardError("grocery_ai_budget_state_invalid")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA trusted_schema=OFF")
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BudgetGuardError("grocery_ai_budget_state_invalid")
        yield conn
        conn.commit()
    except BudgetGuardError:
        if conn is not None:
            conn.rollback()
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        if conn is not None:
            conn.rollback()
        raise BudgetGuardError("grocery_ai_budget_unavailable") from None
    finally:
        if conn is not None:
            conn.close()


def _checksum(row: dict) -> str:
    return _hash({key: value for key, value in row.items() if key != "integrity_hash"})


def _write_state(conn: sqlite3.Connection, state: dict) -> None:
    state["integrity_hash"] = _checksum(state)
    conn.execute("""UPDATE grocery_ai_budget_state SET
        high_water_month=:high_water_month, request_count=:request_count,
        settled_count=:settled_count, total_reserved_nanodollars=:total_reserved_nanodollars,
        total_charged_nanodollars=:total_charged_nanodollars, integrity_hash=:integrity_hash
        WHERE id=1""", state)


def _validate(conn: sqlite3.Connection, path: Path, month: str) -> tuple[dict, list[dict]]:
    marker = _read_marker(path)
    states = conn.execute("SELECT * FROM grocery_ai_budget_state").fetchall()
    if len(states) != 1:
        raise BudgetGuardError("grocery_ai_budget_state_invalid")
    state = dict(states[0])
    if (state.get("id") != 1 or state.get("policy_json") != _POLICY_JSON
            or state.get("policy_hash") != _POLICY_HASH or state.get("ledger_id") != marker["ledger_id"]
            or state.get("integrity_hash") != _checksum(state)
            or not isinstance(state.get("high_water_month"), str)
            or not _MONTH.fullmatch(state["high_water_month"])
            or any(type(state.get(key)) is not int or state[key] < 0 for key in
                   ("request_count", "settled_count", "total_reserved_nanodollars", "total_charged_nanodollars"))):
        raise BudgetGuardError("grocery_ai_budget_state_invalid")
    if month < state["high_water_month"]:
        raise BudgetGuardError("grocery_ai_budget_clock_rollback")
    rows = [dict(row) for row in conn.execute("SELECT * FROM grocery_ai_budget_requests")]
    reserved = charged = settled = 0
    for row in rows:
        _identity(row.get("request_id"), row.get("body_hash"))
        maximum = row.get("max_output_tokens")
        reserve = CONTEXT_TOKENS * INPUT_NANODOLLARS_PER_TOKEN + (maximum if type(maximum) is int else 0) * OUTPUT_NANODOLLARS_PER_TOKEN
        if (type(maximum) is not int or not 1 <= maximum <= MAX_OUTPUT_TOKENS
                or type(row.get("reserved_nanodollars")) is not int or row["reserved_nanodollars"] != reserve
                or row.get("integrity_hash") != _checksum(row)
                or not isinstance(row.get("admitted_month"), str)
                or not _MONTH.fullmatch(row["admitted_month"])
                or row["admitted_month"] > state["high_water_month"]):
            raise BudgetGuardError("grocery_ai_budget_state_invalid")
        if row["status"] == "reserved":
            if any(row[key] is not None for key in
                   ("settled_month", "input_tokens", "output_tokens", "usage_hash", "charged_nanodollars")):
                raise BudgetGuardError("grocery_ai_budget_state_invalid")
            reserved += reserve
        elif row["status"] == "settled":
            if (not isinstance(row["settled_month"], str) or not _MONTH.fullmatch(row["settled_month"])
                    or not row["admitted_month"] <= row["settled_month"] <= state["high_water_month"]
                    or type(row["input_tokens"]) is not int or not 0 <= row["input_tokens"] <= CONTEXT_TOKENS
                    or type(row["output_tokens"]) is not int or not 0 <= row["output_tokens"] <= maximum
                    or not isinstance(row["usage_hash"], str) or not _HEX.fullmatch(row["usage_hash"])
                    or type(row["charged_nanodollars"]) is not int
                    or row["charged_nanodollars"] != (row["input_tokens"] * INPUT_NANODOLLARS_PER_TOKEN
                                                      + row["output_tokens"] * OUTPUT_NANODOLLARS_PER_TOKEN)):
                raise BudgetGuardError("grocery_ai_budget_state_invalid")
            charged += row["charged_nanodollars"]
            settled += 1
        else:
            raise BudgetGuardError("grocery_ai_budget_state_invalid")
    if ((len(rows), settled, reserved, charged) !=
            (state["request_count"], state["settled_count"], state["total_reserved_nanodollars"], state["total_charged_nanodollars"])):
        raise BudgetGuardError("grocery_ai_budget_state_invalid")
    state["high_water_month"] = month
    _write_state(conn, state)
    return state, rows


def initialize_schema(path: Path | str, *, clock: Callable[[], datetime] | None = None) -> None:
    """Explicit installation only; never call to repair a failed request.

    Requires an existing grocery journal. Repeated initialization validates but
    never repairs. The independent marker prevents DB/table deletion from
    silently granting a new allowance. Unbudgeted prior AI history is refused.
    """
    path = Path(path)
    month = _month(clock)
    with _connection(path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        budget_tables = tables & {_STATE_TABLE, _REQUEST_TABLE}
        if budget_tables or _marker_path(path).exists() or _marker_path(path).is_symlink():
            _validate(conn, path, month)
            return
        if "grocery_batches" not in tables:
            raise BudgetGuardError("grocery_ai_budget_journal_missing")
        for row in conn.execute("SELECT plan_json FROM grocery_batches"):
            try:
                plan = json.loads(row[0])
                if not isinstance(plan, dict):
                    raise ValueError("invalid_plan")
            except (ValueError, TypeError):
                raise BudgetGuardError("grocery_ai_budget_history_invalid") from None
            if plan.get("version") == 2 and plan != {"version": 2, "phase": "preflight"}:
                raise BudgetGuardError("grocery_ai_budget_unaccounted_history")
        conn.execute("""CREATE TABLE grocery_ai_budget_state (
            id INTEGER PRIMARY KEY CHECK(id=1), policy_json TEXT NOT NULL,
            policy_hash TEXT NOT NULL, ledger_id TEXT NOT NULL, high_water_month TEXT NOT NULL,
            request_count INTEGER NOT NULL, settled_count INTEGER NOT NULL,
            total_reserved_nanodollars INTEGER NOT NULL, total_charged_nanodollars INTEGER NOT NULL,
            integrity_hash TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE grocery_ai_budget_requests (
            request_id TEXT PRIMARY KEY, body_hash TEXT NOT NULL, max_output_tokens INTEGER NOT NULL,
            admitted_month TEXT NOT NULL, reserved_nanodollars INTEGER NOT NULL, status TEXT NOT NULL,
            settled_month TEXT, input_tokens INTEGER, output_tokens INTEGER, usage_hash TEXT,
            charged_nanodollars INTEGER, integrity_hash TEXT NOT NULL)""")
        state = {"id": 1, "policy_json": _POLICY_JSON, "policy_hash": _POLICY_HASH,
                 "ledger_id": uuid.uuid4().hex, "high_water_month": month, "request_count": 0,
                 "settled_count": 0, "total_reserved_nanodollars": 0, "total_charged_nanodollars": 0}
        state["integrity_hash"] = _checksum(state)
        conn.execute("INSERT INTO grocery_ai_budget_state VALUES (:id,:policy_json,:policy_hash,:ledger_id,:high_water_month,:request_count,:settled_count,:total_reserved_nanodollars,:total_charged_nanodollars,:integrity_hash)", state)
        _write_marker(path, state["ledger_id"])


def _snapshot(month: str, rows: list[dict]) -> BudgetSnapshot:
    reserved = sum(row["reserved_nanodollars"] for row in rows if row["status"] == "reserved")
    charged = sum(row["charged_nanodollars"] for row in rows if row["status"] == "settled"
                  and month in (row["admitted_month"], row["settled_month"]))
    if reserved + charged > MONTHLY_CAP_NANODOLLARS:
        raise BudgetGuardError("grocery_ai_budget_state_invalid")
    return BudgetSnapshot(month, reserved, charged, MONTHLY_CAP_NANODOLLARS - reserved - charged)


def _usage(response: Any, maximum: int) -> tuple[int, int, str] | None:
    if (not isinstance(response, dict) or response.get("model") != MODEL
            or response.get("service_tier") != "default" or response.get("error") is not None
            or not isinstance(response.get("status"), str)
            or response.get("status") not in {"completed", "incomplete"}
            or (response["status"] == "completed" and response.get("incomplete_details") is not None)
            or (response["status"] == "incomplete" and not isinstance(response.get("incomplete_details"), dict))):
        return None
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return None
    input_tokens, output_tokens, total = (usage.get(key) for key in ("input_tokens", "output_tokens", "total_tokens"))
    if (any(type(value) is not int for value in (input_tokens, output_tokens, total))
            or not 0 <= input_tokens <= CONTEXT_TOKENS or not 0 <= output_tokens <= maximum
            or total != input_tokens + output_tokens):
        return None
    # All input is charged at the conservative non-cached rate. Cache hits can
    # never produce a discount here. Unknown billing detail fields retain reserve.
    if set(usage) - {"input_tokens", "output_tokens", "total_tokens", "input_tokens_details", "output_tokens_details"}:
        return None
    for field, child, bound in (("input_tokens_details", "cached_tokens", input_tokens),
                                ("output_tokens_details", "reasoning_tokens", output_tokens)):
        if field in usage:
            details = usage[field]
            allowed = ({child, "cache_write_tokens", "cache_write_tokens_details"}
                       if field == "input_tokens_details" else {child})
            if (not isinstance(details, dict) or set(details) - allowed
                    or type(details.get(child)) is not int or not 0 <= details[child] <= bound):
                return None
            if "cache_write_tokens" in details and (
                    type(details["cache_write_tokens"]) is not int
                    or not 0 <= details["cache_write_tokens"] <= input_tokens):
                return None
            if field == "input_tokens_details":
                writes = details.get("cache_write_tokens", 0)
                # Reads exclude writes in the official prompt-caching contract.
                if details[child] + writes > input_tokens:
                    return None
                if "cache_write_tokens_details" in details:
                    breakdown = details["cache_write_tokens_details"]
                    if ("cache_write_tokens" not in details or not isinstance(breakdown, dict)
                            or set(breakdown) != {"ttl_30m_tokens", "ttl_12h_tokens"}
                            or any(type(value) is not int or value < 0 for value in breakdown.values())
                            or breakdown["ttl_12h_tokens"] != 0
                            or sum(breakdown.values()) != writes):
                        return None
    return input_tokens, output_tokens, _hash(usage)


class GroceryAIBudget:
    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.clock = clock

    def snapshot(self) -> BudgetSnapshot:
        month = _month(self.clock)
        with _connection(self.path) as conn:
            _, rows = _validate(conn, self.path, month)
            return _snapshot(month, rows)

    def reserve(self, request_id: str, body_hash: str, *, max_output_tokens: int = MAX_OUTPUT_TOKENS) -> Admission:
        _identity(request_id, body_hash)
        if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= MAX_OUTPUT_TOKENS:
            raise BudgetGuardError("grocery_ai_budget_output_limit_invalid")
        month = _month(self.clock)
        reserve = CONTEXT_TOKENS * INPUT_NANODOLLARS_PER_TOKEN + max_output_tokens * OUTPUT_NANODOLLARS_PER_TOKEN
        with _connection(self.path) as conn:
            state, rows = _validate(conn, self.path, month)
            previous = next((row for row in rows if row["request_id"] == request_id), None)
            if previous is not None:
                if previous["body_hash"] != body_hash or previous["max_output_tokens"] != max_output_tokens:
                    raise BudgetGuardError("grocery_ai_budget_request_conflict")
                return Admission(True, False, request_id, previous["reserved_nanodollars"], "duplicate")
            if _snapshot(month, rows).remaining_nanodollars < reserve:
                return Admission(False, False, request_id, reserve, "monthly_budget_exhausted")
            row = {"request_id": request_id, "body_hash": body_hash, "max_output_tokens": max_output_tokens,
                   "admitted_month": month, "reserved_nanodollars": reserve, "status": "reserved",
                   "settled_month": None, "input_tokens": None, "output_tokens": None,
                   "usage_hash": None, "charged_nanodollars": None}
            row["integrity_hash"] = _checksum(row)
            conn.execute("INSERT INTO grocery_ai_budget_requests VALUES (:request_id,:body_hash,:max_output_tokens,:admitted_month,:reserved_nanodollars,:status,:settled_month,:input_tokens,:output_tokens,:usage_hash,:charged_nanodollars,:integrity_hash)", row)
            state["request_count"] += 1
            state["total_reserved_nanodollars"] += reserve
            _write_state(conn, state)
            return Admission(True, True, request_id, reserve, "reserved")

    def settle(self, request_id: str, body_hash: str, response: Any) -> Settlement:
        _identity(request_id, body_hash)
        month = _month(self.clock)
        with _connection(self.path) as conn:
            state, rows = _validate(conn, self.path, month)
            row = next((row for row in rows if row["request_id"] == request_id), None)
            if row is None or row["body_hash"] != body_hash:
                raise BudgetGuardError("grocery_ai_budget_request_conflict")
            usage = _usage(response, row["max_output_tokens"])
            if usage is None:
                return Settlement(False)
            input_tokens, output_tokens, usage_hash = usage
            charge = input_tokens * INPUT_NANODOLLARS_PER_TOKEN + output_tokens * OUTPUT_NANODOLLARS_PER_TOKEN
            if row["status"] == "settled":
                if row["usage_hash"] != usage_hash:
                    raise BudgetGuardError("grocery_ai_budget_settlement_conflict")
                return Settlement(True, row["charged_nanodollars"])
            row.update(status="settled", settled_month=month, input_tokens=input_tokens,
                       output_tokens=output_tokens, usage_hash=usage_hash, charged_nanodollars=charge)
            row["integrity_hash"] = _checksum(row)
            conn.execute("""UPDATE grocery_ai_budget_requests SET status=:status,settled_month=:settled_month,
                input_tokens=:input_tokens,output_tokens=:output_tokens,usage_hash=:usage_hash,
                charged_nanodollars=:charged_nanodollars,integrity_hash=:integrity_hash
                WHERE request_id=:request_id""", row)
            state["settled_count"] += 1
            state["total_reserved_nanodollars"] -= row["reserved_nanodollars"]
            state["total_charged_nanodollars"] += charge
            _write_state(conn, state)
            _snapshot(month, rows)
            return Settlement(True, charge)
