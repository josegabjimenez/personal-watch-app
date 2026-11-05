"""Reminder-only adapter over the recorded historical Mercado service.

The two thin HTTP wrappers pass all non-reminder traffic to the original
methods. No grocery implementation, cost state, service argument or secret is
rewritten here. Environment settings add only this explicit timed-reminder gate.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from datetime import datetime, timezone
from typing import Any

import reminder_capture

DOMAIN = "aura.reminder_capture"
ROUTE = {"agent": "aura", "intent": "reminder", "domain": DOMAIN}
WAITING = "waiting_for_server_gate"


def requested(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("route"), dict):
        return False
    route = payload["route"]
    intent = route.get("intent")
    return route.get("domain") == DOMAIN or (route.get("agent") == "aura" and isinstance(intent, str) and intent in {"reminder", "reminder_capture"})


def enabled(cfg: Any) -> bool:
    return cfg.write_enabled is True and DOMAIN in cfg.write_domains


def authorized(payload: dict, cfg: Any) -> bool:
    route, context = payload.get("route"), payload.get("context")
    return (
        enabled(cfg) and isinstance(route, dict) and all(route.get(key) == value for key, value in ROUTE.items())
        and isinstance(context, dict) and context.get("dry_run") is False and context.get("allow_write") is True
        and all(context.get(key) is None or context.get(key) is False for key in
                ("allow_firefly_write", "user_confirmation", "user_cancellation", "requires_confirmation"))
        and context.get("operation_id") is None and context.get("confirmation_token") is None
    )


def _path(name: str) -> Path | None:
    value = os.getenv(name)
    return Path(value) if value else None


def settings(cfg: Any) -> Any:
    values = vars(cfg).copy()
    values.update(
        reminder_openai_key_file=_path("MOBILE_CAPTURE_REMINDER_OPENAI_KEY_FILE"),
        reminder_todoist_token_file=_path("MOBILE_CAPTURE_REMINDER_TODOIST_TOKEN_FILE"),
        reminder_todoist_credentials_file=_path("MOBILE_CAPTURE_REMINDER_TODOIST_CREDENTIALS_FILE"),
        reminder_todoist_inbox_id=os.getenv("MOBILE_CAPTURE_REMINDER_TODOIST_INBOX_ID", ""),
        reminder_budget_path=_path("MOBILE_CAPTURE_REMINDER_BUDGET_PATH"),
        reminder_monthly_limit_usd=os.getenv("MOBILE_CAPTURE_REMINDER_MONTHLY_LIMIT_USD", "0"),
        reminder_provider=os.getenv("MOBILE_CAPTURE_REMINDER_PROVIDER", "openai"),
        reminder_model=os.getenv("MOBILE_CAPTURE_REMINDER_MODEL", "gpt-6-luna"),
        reminder_ai_timeout=float(os.getenv("MOBILE_CAPTURE_REMINDER_AI_TIMEOUT", "8")),
        reminder_todoist_timeout=float(os.getenv("MOBILE_CAPTURE_REMINDER_TODOIST_TIMEOUT", "5")),
    )
    if not 0.1 <= values["reminder_ai_timeout"] <= 30 or not 0.1 <= values["reminder_todoist_timeout"] <= 15:
        raise ValueError("invalid_reminder_timeout")
    return SimpleNamespace(**values)


def plan(payload: dict) -> dict:
    return {
        "status": "accepted", "dry_run": True, "write_committed": False,
        "request_id": payload["request_id"], "domain": DOMAIN,
        "display_message": "Recordatorio pendiente de configuración · captura conservada",
        "question": None, "gates_missing": ["explicit server reminder domain", "private server authorization and independent reminder cost guard"],
        "plan": {"would_write": False, "side_effects": [], "notes": "No AI or external task write before server gates."},
    }


def _reject(payload: dict, question: str) -> dict:
    response = plan(payload)
    response.update(status="rejected", dry_run=False, question=question,
                    display_message="No creé el recordatorio · " + question, gates_missing=[])
    return response


def route_get(handler: Any, legacy: Any, api: dict) -> None:
    if handler.path != "/health" or not enabled(handler.cfg):
        return legacy()
    domains = sorted((handler.cfg.write_domains & api["SUPPORTED_WRITE_DOMAINS"]) | {DOMAIN})
    return api["json_response"](handler, 200, {
        "status": "ok", "service": "mobile-capture-v1-staging", "mode": "allowlisted-write", "write_domains": domains,
    })


def route_post(handler: Any, legacy: Any, api: dict) -> None:
    """Read bounded bytes once; unrelated requests retain the historical path."""
    if handler.path != "/webhooks/mobile-capture-v1":
        return legacy()
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except (ValueError, TypeError):
        return legacy()
    if not 0 < length <= api["MAX_BODY_BYTES"]:
        return legacy()
    original = handler.rfile
    raw = original.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        payload = None
    if not requested(payload):
        handler.rfile = io.BytesIO(raw)
        try:
            return legacy()
        finally:
            handler.rfile = original
    return _handle_reminder(handler, api, raw, payload)


def _handle_reminder(handler: Any, api: dict, raw: bytes, payload: dict) -> None:
    reply = lambda status, value: api["json_response"](handler, status, value)
    valid, reason = api["verify_hmac_v2"](handler.headers, raw, handler.cfg.secret)
    if not valid:
        return reply(401, {"status": "rejected", "error": reason})
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return reply(400, {"status": "rejected", "error": "invalid_request_id"})
    if handler.headers.get("X-Request-ID", "") != request_id:
        return reply(409, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
    if payload.get("event_type") != "mobile_capture.v1" or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
        return reply(400, {"status": "rejected", "error": "invalid_reminder_envelope", "request_id": request_id})
    for name in ("capture", "route", "context", "source", "entities"):
        if name in payload and not isinstance(payload[name], dict):
            return reply(400, {"status": "rejected", "error": "invalid_reminder_envelope", "request_id": request_id})
    capture = payload.get("capture") or {}
    if not isinstance(capture.get("text"), str):
        return reply(400, {"status": "rejected", "error": "invalid_reminder_text", "request_id": request_id})
    binding = hashlib.sha256(raw).hexdigest()
    cfg = handler.cfg
    api["init_db"](cfg.ledger_path)
    early = None
    with closing(sqlite3.connect(cfg.ledger_path)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload_hash,status,response_json FROM mobile_capture_ledger WHERE request_id=?", (request_id,)).fetchone()
        if row and row[0] != binding:
            early = (409, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
        elif row and row[1] not in {"processing", WAITING}:
            try:
                cached = json.loads(row[2])
                if not isinstance(cached, dict) or cached.get("request_id") != request_id or cached.get("domain") != DOMAIN:
                    raise ValueError("invalid_cached_reminder")
                if cached.get("write_committed") is True:
                    cached.update(status="duplicate", duplicate=True)
                early = (200, cached)
            except (ValueError, TypeError):
                early = (502, {"status": "error", "error": "reminder_pending_retry", "request_id": request_id, "domain": DOMAIN})
        elif not row:
            timestamp = api["now_iso"]()
            conn.execute("INSERT INTO mobile_capture_ledger VALUES(?,?,?,'processing','{}',?,?)", (request_id, binding, DOMAIN, timestamp, timestamp))
        if early is not None:
            conn.rollback()
    if early is not None:
        return reply(*early)
    try:
        context = payload.get("context") or {}
        if not capture["text"].strip():
            response = _reject(payload, "Indica una sola tarea con fecha y hora claras en Bogotá.")
        elif not enabled(cfg) or context.get("dry_run") is True:
            response = plan(payload)
        elif not authorized(payload, cfg):
            response = _reject(payload, "Esta captura no autoriza crear un recordatorio nuevo.")
        else:
            active = settings(cfg)
            if active.reminder_openai_key_file is None or (active.reminder_todoist_token_file is None and active.reminder_todoist_credentials_file is None):
                response = plan(payload)
            else:
                response = reminder_capture.execute(payload, active, now=lambda: datetime.now(timezone.utc))
    except reminder_capture.ReminderConflict:
        return reply(409, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
    except (reminder_capture.ReminderRetryable, ValueError) as error:
        configuration = str(error) in {"reminder_budget_pending", "reminder_configuration_unavailable", "reminder_authorization_pending", "invalid_reminder_timeout"}
        return reply(502, {"status": "error", "error": "reminder_configuration_pending" if configuration else "reminder_pending_retry", "request_id": request_id, "domain": DOMAIN})
    status = WAITING if response["status"] == "accepted" and response.get("dry_run") is True else response["status"]
    with closing(sqlite3.connect(cfg.ledger_path)) as conn, conn:
        conn.execute("UPDATE mobile_capture_ledger SET status=?,response_json=?,updated_at=? WHERE request_id=? AND payload_hash=?", (status, json.dumps(response, ensure_ascii=False), api["now_iso"](), request_id, binding))
    return reply(200, response)
