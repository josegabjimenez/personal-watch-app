"""Durable, single-purpose Reminder -> structured interpretation -> Todoist.

Per-request OS locks cover network I/O, never the server's global execution
lock or a SQLite transaction. The OS releases a lock on a crash; frozen plans,
command UUIDs, account binding and receipts survive in the existing ledger.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import hmac
import json
from pathlib import Path
import sqlite3
import stat
from typing import Any
import uuid

import reminder_ai
from todoist_reminders import TodoistGateway, RetryableTodoistError, PermanentTodoistError
from todoist_auth import CredentialStore
from reminder_ai_budget import ReminderBudget, BudgetBlocked

DOMAIN = "aura.reminder_capture"


class ReminderRetryable(RuntimeError):
    """No authoritative receipt yet; retain original outbox payload for retry."""


class ReminderOccurrencesPending(ReminderRetryable):
    """Keep the original outbox row retryable while exposing verified progress."""

    def __init__(self, request_id: str, occurrences: list[dict]):
        super().__init__("reminder_pending_retry")
        self.response = {
            "status": "error", "error": "reminder_pending_retry",
            "request_id": request_id, "domain": DOMAIN, "occurrences": occurrences,
        }


class ReminderConflict(RuntimeError):
    pass


def init_db(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        # Serialize CREATE/ALTER across distinct captures. Existing rows retain
        # the historical transport; only explicit new inserts choose REST.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""CREATE TABLE IF NOT EXISTS reminder_operation_ledger (
            request_id TEXT PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            status TEXT NOT NULL,
            command_uuid TEXT NOT NULL UNIQUE,
            temp_id TEXT NOT NULL UNIQUE,
            plan_json TEXT,
            identity_json TEXT,
            task_id TEXT,
            response_json TEXT,
            updated_at TEXT NOT NULL,
            transport TEXT NOT NULL DEFAULT 'sync_v1',
            rest_dispatched INTEGER NOT NULL DEFAULT 0
        )""")
        columns = {row[1] for row in conn.execute("PRAGMA table_info(reminder_operation_ledger)")}
        if "transport" not in columns:
            conn.execute("ALTER TABLE reminder_operation_ledger ADD COLUMN transport TEXT NOT NULL DEFAULT 'sync_v1'")
        if "rest_dispatched" not in columns:
            conn.execute("ALTER TABLE reminder_operation_ledger ADD COLUMN rest_dispatched INTEGER NOT NULL DEFAULT 0")
        conn.execute("""CREATE TABLE IF NOT EXISTS reminder_occurrence_ledger (
            request_id TEXT NOT NULL,
            due_start TEXT NOT NULL,
            command_uuid TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('pending','unverified','saved','elapsed')),
            task_id TEXT,
            PRIMARY KEY (request_id, due_start)
        )""")


def _timestamp() -> str:
    return datetime.now().astimezone().isoformat()


def _update(path: Path, request_id: str, **fields: Any) -> None:
    fields["updated_at"] = _timestamp()
    with sqlite3.connect(path) as conn:
        conn.execute(
            "UPDATE reminder_operation_ledger SET "
            + ", ".join(key + " = ?" for key in fields) + " WHERE request_id = ?",
            (*fields.values(), request_id),
        )


def _claim_rest_dispatch(path: Path, request_id: str, binding: str) -> None:
    """Consume the single REST dispatch before the gateway opens its POST."""
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute("""UPDATE reminder_operation_ledger
            SET rest_dispatched=1,status='reconciliation_required',updated_at=?
            WHERE request_id=? AND payload_hash=? AND transport='rest_v1'
              AND rest_dispatched=0 AND status IN ('ready','writing','uncertain')""",
            (_timestamp(), request_id, binding)).rowcount
        if changed != 1:
            raise ReminderRetryable("reminder_rest_dispatch_already_claimed")


def _mark_rest_failure(path: Path, request_id: str, binding: str, *, terminal_response: dict | None = None) -> bool:
    # A timed-out HTTP worker can still finish its durable callback. Serialize
    # this read/status write with that callback so it cannot erase the old-code
    # reconciliation fence after observing the previous unclaimed state.
    with sqlite3.connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT rest_dispatched FROM reminder_operation_ledger
            WHERE request_id=? AND payload_hash=? AND transport='rest_v1'""", (request_id, binding)).fetchone()
        if row is None or type(row[0]) is not int or row[0] not in (0, 1):
            raise ReminderRetryable("reminder_operation_reconciliation_required")
        dispatched = row[0] == 1
        if not dispatched and terminal_response is not None:
            conn.execute("""UPDATE reminder_operation_ledger SET status='terminal',response_json=?,updated_at=?
                WHERE request_id=? AND payload_hash=? AND transport='rest_v1'""",
                (json.dumps(terminal_response, ensure_ascii=False), _timestamp(), request_id, binding))
        else:
            conn.execute("""UPDATE reminder_operation_ledger SET status=?,updated_at=?
                WHERE request_id=? AND payload_hash=? AND transport='rest_v1'""",
                ("reconciliation_required" if dispatched else "ready", _timestamp(), request_id, binding))
    return dispatched


@contextmanager
def _request_lock(path: Path, request_id: str):
    directory = path.with_suffix(".reminder-locks")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    filename = hashlib.sha256(request_id.encode()).hexdigest() + ".lock"
    with (directory / filename).open("a+b") as lock:
        (directory / filename).chmod(0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ReminderRetryable("reminder_in_progress") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _token(path: Path | None) -> str:
    try:
        if path is None or stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise ValueError("private credential file required")
        value = path.read_text(encoding="utf-8").strip()
        if not value or len(value) > 4096 or any(c.isspace() for c in value):
            raise ValueError("invalid credential")
        return value
    except (OSError, ValueError, UnicodeError):
        raise ReminderRetryable("reminder_configuration_unavailable") from None


def _no_write(payload: dict, *, rejected: bool = False, question: str | None = None) -> dict:
    return {
        "status": "rejected" if rejected else "needs_input",
        "dry_run": False, "write_committed": False,
        "request_id": payload["request_id"], "domain": DOMAIN,
        "display_message": "No creé el recordatorio · " + (question or "indica una tarea con fecha y hora claras en Bogotá"),
        "question": question,
        "gates_missing": [],
        "plan": {"would_write": False, "side_effects": [], "notes": "No external write dispatched."},
    }


def _saved(payload: dict, plan: dict, receipt: dict) -> dict:
    title = " ".join(plan["title"].split())[:96]
    return {
        "status": "committed", "dry_run": False, "write_committed": True,
        "request_id": payload["request_id"], "domain": DOMAIN,
        "display_message": f"Guardado en Todoist ✅ · {title} · {plan['due']['display']} · alertas pendientes de verificar",
        "question": None, "duplicate": bool(receipt.get("duplicate")),
        "interpreted_due": plan["due"],
        "external_ids": {"todoist_task_id": receipt["task_id"]},
        "notification_verified": False, "delivery_status": "pending_verification",
        "auto_reminder_requested": True, "alert_configured": None,
        "gates_missing": [],
        "plan": {"would_write": True, "side_effects": ["todoist.task.write"],
                 "notes": "Inbox task and exact intended task time read back. Default auto-reminder requested; alert configuration and phone delivery unverified."},
    }


def _command_uuid(secret: bytes, kind: str, request_id: str, binding: str) -> str:
    # Same original capture + server signing identity survives a main-ledger
    # restore. Existing persisted UUIDs stay unchanged; no migration is made.
    material = ("reminder-v1\0" + kind + "\0" + request_id + "\0" + binding).encode("utf-8")
    return str(uuid.UUID(bytes=hmac.new(secret, material, hashlib.sha256).digest()[:16], version=5))


def _freeze_occurrences(path: Path, request_id: str, binding: str, secret: bytes, plan: dict) -> None:
    """Journal the whole plan and all its independent REST identities together."""
    with sqlite3.connect(path) as conn:
        changed = conn.execute("""UPDATE reminder_operation_ledger
            SET status='reconciliation_required',plan_json=?,updated_at=?
            WHERE request_id=? AND payload_hash=? AND plan_json IS NULL AND transport='rest_v1'
              AND status='interpreting' AND rest_dispatched=0 AND task_id IS NULL""",
            (json.dumps(plan, ensure_ascii=False), _timestamp(), request_id, binding)).rowcount
        if changed != 1:
            raise ReminderRetryable("reminder_operation_reconciliation_required")
        conn.executemany("""INSERT INTO reminder_occurrence_ledger
            (request_id,due_start,command_uuid,status) VALUES (?,?,?,'pending')""",
            ((request_id, due["start"], _command_uuid(secret, "occurrence:" + due["start"], request_id, binding))
             for due in plan["dues"]))


def _occurrence_rows(path: Path, request_id: str) -> dict:
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        return {row["due_start"]: row for row in conn.execute(
            "SELECT * FROM reminder_occurrence_ledger WHERE request_id=?", (request_id,))}


def _change_occurrence(path: Path, row: sqlite3.Row, status: str, *,
                       prior_status: str, task_id: str | None = None) -> bool:
    with sqlite3.connect(path) as conn:
        return conn.execute("""UPDATE reminder_occurrence_ledger
            SET status=?,task_id=COALESCE(?,task_id)
            WHERE request_id=? AND due_start=? AND command_uuid=? AND status=?""",
            (status, task_id, row["request_id"], row["due_start"], row["command_uuid"], prior_status)).rowcount == 1


def _advance_occurrence(path: Path, row: sqlite3.Row, plan: dict, due: dict,
                        gateway: TodoistGateway, identity: dict, now: Any) -> None:
    old_status = row["status"]
    if old_status in {"elapsed", "saved"}:
        return

    def claim() -> None:
        if not _change_occurrence(path, row, "unverified", prior_status="pending"):
            raise RetryableTodoistError("reminder_rest_dispatch_already_claimed")

    def remember_id(task_id: str) -> None:
        # Preflight can find a known matching task before a dispatch claim.
        # A returned ID is a reason for read-only recovery if we lose readback.
        if not _change_occurrence(path, row, "unverified", prior_status="unverified", task_id=task_id):
            _change_occurrence(path, row, "unverified", prior_status="pending", task_id=task_id)

    occurrence_plan = {"title": plan["title"], "labels": plan["labels"], "due": due}
    try:
        if old_status == "unverified":
            receipt = gateway.reconcile(occurrence_plan, row["command_uuid"], identity, external_id=row["task_id"])
        elif datetime.fromisoformat(due["start"]) <= now():
            _change_occurrence(path, row, "elapsed", prior_status="pending")
            return
        else:
            receipt = gateway.create_rest_or_reconcile(
                occurrence_plan, row["command_uuid"], identity, on_before_post=claim, on_external_id=remember_id)
    except (RetryableTodoistError, PermanentTodoistError) as error:
        if isinstance(error, PermanentTodoistError) and str(error) == "todoist_due_elapsed_before_dispatch":
            _change_occurrence(path, row, "elapsed", prior_status="pending")
        return
    if receipt is not None:
        _change_occurrence(path, row, "saved", prior_status="unverified", task_id=receipt["task_id"])


def _execute_occurrences(payload: dict, cfg: Any, plan: dict,
                         gateway: TodoistGateway, identity: dict, now: Any) -> dict:
    request_id, path = payload["request_id"], cfg.ledger_path
    rows = _occurrence_rows(path, request_id)
    starts = {due["start"] for due in plan["dues"]}
    if not 2 <= len(starts) == len(plan["dues"]) <= reminder_ai.MAX_SCHEDULES or rows.keys() != starts:
        # Do not reconstruct a missing dispatch journal from the frozen plan.
        raise ReminderRetryable("reminder_operation_reconciliation_required")

    for due in plan["dues"]:
        _advance_occurrence(path, rows[due["start"]], plan, due, gateway, identity, now)

    rows = _occurrence_rows(path, request_id)
    occurrences = []
    for due in plan["dues"]:
        row = rows[due["start"]]
        occurrence = {"due": due, "status": row["status"]}
        if row["status"] == "saved":
            occurrence["todoist_task_id"] = row["task_id"]
        occurrences.append(occurrence)
    if any(occurrence["status"] in {"pending", "unverified"} for occurrence in occurrences):
        raise ReminderOccurrencesPending(request_id, occurrences)

    saved = [occurrence for occurrence in occurrences if occurrence["status"] == "saved"]
    if saved:
        first = saved[0]
        response = _saved(payload, {"title": plan["title"], "due": first["due"]}, {"task_id": first["todoist_task_id"]})
        # Keep the established committed response while avoiding a singular
        # ID/due that could be mistaken for the whole reminder.
        del response["interpreted_due"]
        response["external_ids"] = {"todoist_task_ids": [occurrence["todoist_task_id"] for occurrence in saved]}
        title = " ".join(plan["title"].split())[:96]
        details = " · ".join(f"{item['due']['display']}: {'guardado' if item['status'] == 'saved' else 'hora pasada'}"
                             for item in occurrences)
        response["display_message"] = f"{title} · {details} · alertas pendientes de verificar"
        response["plan"]["notes"] = "Each saved Inbox task and intended time was read back. Default auto-reminders requested."
    else:
        response = _no_write(payload, question="Las horas ya pasaron. ¿Para cuándo lo programo en Bogotá?")
    response["occurrences"] = occurrences
    _update(path, request_id, status="committed" if saved else "terminal",
            response_json=json.dumps(response, ensure_ascii=False))
    return response


def _gateway(cfg: Any) -> TodoistGateway:
    credentials = getattr(cfg, "reminder_todoist_credentials_file", None)
    if credentials is not None:
        # The private bundle is maintained by the dedicated app OAuth flow.
        bearer = CredentialStore(credentials).bearer
    else:
        bearer = _token(cfg.reminder_todoist_token_file)
    return TodoistGateway(bearer, timeout=cfg.reminder_todoist_timeout)


def execute(payload: dict, cfg: Any, *, now: Any) -> dict:
    """Called only after the server's exact Reminder route/authority checks."""
    request_id = payload["request_id"]
    binding = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
    init_db(cfg.ledger_path)
    with _request_lock(cfg.ledger_path, request_id):
        with sqlite3.connect(cfg.ledger_path) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM reminder_operation_ledger WHERE request_id = ?", (request_id,)).fetchone()
            if row is not None and row["payload_hash"] != binding:
                raise ReminderConflict("request_id_conflict")
            if row is None:
                conn.execute("""INSERT INTO reminder_operation_ledger
                    (request_id,payload_hash,status,command_uuid,temp_id,updated_at,transport)
                    VALUES (?,?,'unbound',?,?,?,'rest_v1')""", (request_id, binding,
                        _command_uuid(cfg.secret, "command", request_id, binding),
                        _command_uuid(cfg.secret, "temporary", request_id, binding), _timestamp()))
                row = conn.execute("SELECT * FROM reminder_operation_ledger WHERE request_id = ?", (request_id,)).fetchone()
            operation = dict(row)
        transport = operation["transport"]
        reconciling = operation["status"] == "reconciliation_required"
        # Old executors already deny this parent state before any write. New
        # executors can proceed only with the frozen plan and separate journal.
        occurrence_parent = reconciling and transport == "rest_v1" and operation["rest_dispatched"] == 0
        if reconciling and not (occurrence_parent or (transport == "rest_v1" and operation["rest_dispatched"] == 1)):
            raise ReminderRetryable("reminder_operation_reconciliation_required")
        if operation["response_json"]:
            result = json.loads(operation["response_json"])
            if result.get("write_committed"):
                result.update(status="duplicate", duplicate=True)
            return result
        if transport not in {"sync_v1", "rest_v1"} or type(operation["rest_dispatched"]) is not int or operation["rest_dispatched"] not in (0, 1):
            raise ReminderRetryable("reminder_operation_reconciliation_required")
        if operation["rest_dispatched"] == 1 and (
                transport != "rest_v1" or not operation["plan_json"] or not operation["identity_json"]
                or operation["status"] in {"unbound", "interpreting"}):
            # Lost/contradictory frozen state is not authority to interpret or
            # dispatch again, including after partial application-state loss.
            raise ReminderRetryable("reminder_operation_reconciliation_required")

        # Missing server credentials are retryable configuration gates. They are
        # never sourced from a desktop connector or another process environment.
        plan = json.loads(operation["plan_json"]) if operation["plan_json"] else None
        has_occurrences = plan is not None and "dues" in plan
        if has_occurrences != occurrence_parent:
            raise ReminderRetryable("reminder_operation_reconciliation_required")
        gateway = None
        if plan is None:
            try:
                reminder_ai.validate_capture_timestamp(payload.get("created_at"), now())
            except ValueError:
                response = _no_write(payload, question="Necesito la fecha original de captura válida con zona horaria.")
                _update(cfg.ledger_path, request_id, status="terminal", response_json=json.dumps(response, ensure_ascii=False))
                return response
            if cfg.reminder_provider != "openai":
                raise ReminderRetryable("reminder_provider_not_configured")
            try:
                budget = ReminderBudget(
                    cfg.reminder_budget_path or Path(str(cfg.ledger_path) + ".reminder-ai-budget.sqlite3"),
                    monthly_limit_usd=cfg.reminder_monthly_limit_usd, now=now,
                )
                if operation["status"] == "unbound":
                    if budget.attempt_count(request_id, binding):
                        _update(cfg.ledger_path, request_id, status="reconciliation_required")
                        raise ReminderRetryable("reminder_operation_reconciliation_required")
                    _update(cfg.ledger_path, request_id, status="interpreting")
                    operation["status"] = "interpreting"
            except BudgetBlocked:
                raise ReminderRetryable("reminder_budget_pending") from None
            api_key = _token(cfg.reminder_openai_key_file)
            # Resolve app-server authorization and existing labels before any
            # paid call. Native connector credentials are never exported here.
            gateway = _gateway(cfg)
            try:
                identity = gateway.identity()
                if not cfg.reminder_todoist_inbox_id or identity.get("inbox_project_id") != cfg.reminder_todoist_inbox_id:
                    raise PermanentTodoistError("todoist_destination_not_verified")
                old_identity = json.loads(operation["identity_json"]) if operation["identity_json"] else None
                if old_identity is not None and old_identity != identity:
                    raise PermanentTodoistError("todoist_account_changed")
                available_labels = gateway.label_catalog()
            except (RetryableTodoistError, PermanentTodoistError):
                raise ReminderRetryable("reminder_authorization_pending") from None
            _update(cfg.ledger_path, request_id, identity_json=json.dumps(identity))
            operation["identity_json"] = json.dumps(identity)
            try:
                reservation = budget.reserve(request_id, binding, cfg.reminder_model)
            except BudgetBlocked:
                raise ReminderRetryable("reminder_budget_pending") from None
            def settle_usage(usage):
                budget.settle(reservation.reservation_id, usage["input_tokens"], usage["output_tokens"], cfg.reminder_model, "default")
            try:
                proposal = reminder_ai.interpret(
                    payload["capture"]["text"], payload.get("created_at"),
                    api_key=api_key, model=cfg.reminder_model, timeout=cfg.reminder_ai_timeout,
                    available_labels=available_labels, on_usage=settle_usage,
                )
                validated = reminder_ai.validate(proposal, payload.get("created_at"), now(), available_labels=available_labels,
                                                 capture_text=payload["capture"]["text"])
            except reminder_ai.InterpretationUnavailable as exc:
                if str(exc) in {"interpreter_refused", "interpreter_incomplete", "interpreter_invalid_output", "interpreter_invalid_schema", "invalid_capture_text"}:
                    response = _no_write(payload, question="No pude interpretar la captura completa. Indica una sola tarea con fecha y hora claras en Bogotá.")
                    _update(cfg.ledger_path, request_id, status="terminal", response_json=json.dumps(response, ensure_ascii=False))
                    return response
                raise ReminderRetryable("reminder_interpretation_unavailable") from None
            if validated["status"] != "ready":
                response = _no_write(payload, rejected=validated["status"] == "unsupported", question=validated.get("question"))
                _update(cfg.ledger_path, request_id, status="terminal", response_json=json.dumps(response, ensure_ascii=False))
                return response
            plan = {"title": validated["title"], "labels": validated["labels"]}
            # Freeze the exact plan before any task preflight. Retrying must
            # reuse the original dates, even after an occurrence has elapsed.
            if "dues" in validated:
                if transport != "rest_v1":
                    response = _no_write(payload, question="Vuelve a dictar los horarios en un recordatorio nuevo.")
                    _update(cfg.ledger_path, request_id, status="terminal", response_json=json.dumps(response, ensure_ascii=False))
                    return response
                plan["dues"] = validated["dues"]
                _freeze_occurrences(cfg.ledger_path, request_id, binding, cfg.secret, plan)
            else:
                plan["due"] = validated["due"]
                _update(cfg.ledger_path, request_id, status="ready", plan_json=json.dumps(plan, ensure_ascii=False))
                operation["status"] = "ready"

        gateway = gateway or _gateway(cfg)
        identity = json.loads(operation["identity_json"]) if operation["identity_json"] else None
        if not cfg.reminder_todoist_inbox_id or (identity is not None and identity.get("inbox_project_id") != cfg.reminder_todoist_inbox_id):
            raise ReminderRetryable("reminder_authorization_pending")
        if "dues" in plan:
            if (transport != "rest_v1" or identity is None or operation["rest_dispatched"] != 0
                    or operation["task_id"] is not None):
                raise ReminderRetryable("reminder_operation_reconciliation_required")
            return _execute_occurrences(payload, cfg, plan, gateway, identity, now)
        dispatched = (operation["rest_dispatched"] == 1 or operation["task_id"] is not None
                      if transport == "rest_v1" else operation["status"] in {"writing", "uncertain"})
        was_dispatched = dispatched
        try:
            if identity is None:
                identity = gateway.identity()
                _update(cfg.ledger_path, request_id, identity_json=json.dumps(identity))
            was_dispatched = dispatched
            if was_dispatched:
                # Reconcile even after task time elapsed. A missing active task
                # does not prove a previous POST failed (it may be completed).
                receipt = gateway.reconcile(plan, operation["command_uuid"], identity, external_id=operation["task_id"])
                if receipt is not None:
                    response = _saved(payload, plan, receipt)
                    _update(cfg.ledger_path, request_id, status="committed", task_id=receipt["task_id"], response_json=json.dumps(response, ensure_ascii=False))
                    return response
                if transport == "rest_v1":
                    # No active match is not proof an earlier REST POST failed.
                    # A consumed claim never permits another task creation.
                    _update(cfg.ledger_path, request_id, status="reconciliation_required")
                    raise ReminderRetryable("reminder_rest_reconciliation_required")
            if datetime.fromisoformat(plan["due"]["start"]) <= now():
                if was_dispatched:
                    raise ReminderRetryable("reminder_elapsed_requires_reconciliation")
                response = _no_write(payload, question="La hora ya pasó. ¿Para cuándo lo programo en Bogotá?")
                if transport == "rest_v1":
                    if _mark_rest_failure(cfg.ledger_path, request_id, binding, terminal_response=response):
                        raise ReminderRetryable("reminder_elapsed_requires_reconciliation")
                else:
                    _update(cfg.ledger_path, request_id, status="terminal", response_json=json.dumps(response, ensure_ascii=False))
                return response
            # Commit intent before any POST. The same UUID is reused after every
            # timeout/crash; neither a retry nor a lost ack creates a new command.
            if transport == "rest_v1":
                receipt = gateway.create_rest_or_reconcile(
                    plan, operation["command_uuid"], identity, external_id=operation["task_id"],
                    on_before_post=lambda: _claim_rest_dispatch(cfg.ledger_path, request_id, binding),
                    on_external_id=lambda task_id: _update(cfg.ledger_path, request_id, task_id=task_id),
                )
            else:
                _update(cfg.ledger_path, request_id, status="writing")
                dispatched = True
                receipt = gateway.create_or_reconcile(
                    plan, operation["command_uuid"], operation["temp_id"], identity,
                    external_id=operation["task_id"],
                    on_external_id=lambda task_id: _update(cfg.ledger_path, request_id, task_id=task_id),
                )
        except (RetryableTodoistError, PermanentTodoistError) as exc:
            # The gateway can positively attest this first call stopped before
            # POST. Prior uncertain attempts must remain uncertain even then.
            elapsed = isinstance(exc, PermanentTodoistError) and str(exc) == "todoist_due_elapsed_before_dispatch" and not was_dispatched
            response = _no_write(payload, question="La hora ya pasó. ¿Para cuándo lo programo en Bogotá?") if elapsed else None
            if transport == "rest_v1":
                dispatched = _mark_rest_failure(cfg.ledger_path, request_id, binding, terminal_response=response)
                if response is not None and not dispatched:
                    return response
            elif response is not None:
                _update(cfg.ledger_path, request_id, status="terminal", response_json=json.dumps(response, ensure_ascii=False))
                return response
            else:
                _update(cfg.ledger_path, request_id, status="uncertain" if dispatched else "ready")
            raise ReminderRetryable("reminder_receipt_unverified") from None
        response = _saved(payload, plan, receipt)
        _update(cfg.ledger_path, request_id, status="committed", task_id=receipt["task_id"], response_json=json.dumps(response, ensure_ascii=False))
        return response
