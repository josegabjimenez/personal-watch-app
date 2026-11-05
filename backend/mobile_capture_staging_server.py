#!/usr/bin/env python3
"""Personal Watch App mobile-capture v1 synchronous BFF.

A small synchronous server for Watch/iPhone capture:
- verifies HMAC V2: HMAC_SHA256(secret, f"{timestamp}.{raw_body}")
- validates the mobile_capture.v1 envelope
- stores an idempotency ledger in SQLite
- returns short JSON immediately to the client
- permits only explicitly allowlisted server-side write adapters

This is intentionally separate from Hermes generic webhook subscriptions because
the current webhook adapter returns HTTP 202 immediately and runs the agent in
background. The native Watch app needs a synchronous response for good UX.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import unicodedata
from contextlib import nullcontext

import reminder_capture
import grocery_capture
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

DEFAULT_HERMES_HOME = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes")))
DEFAULT_SECRET_FILE = DEFAULT_HERMES_HOME / "mobile-capture-v1-secret.txt"
DEFAULT_LEDGER_PATH = Path(__file__).resolve().parent / "data" / "mobile_capture_ledger.sqlite"
REPLAY_TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 64 * 1024
# A stale confirmation can be refreshed against the same durable operation ID,
# so the signing window can remain short without creating a replacement write.
EXPENSE_CONFIRMATION_TTL_SECONDS = 5 * 60
# An unconfirmed preview must not pin a dated plan indefinitely. Keep its
# active lifetime aligned with the signed-confirmation window; writing and
# uncertain operations never expire automatically because they require
# external-id reconciliation.
EXPENSE_PREVIEW_TTL_SECONDS = EXPENSE_CONFIRMATION_TTL_SECONDS
EXPENSE_MAX_AMOUNT_COP = 100_000_000
BOGOTA = ZoneInfo("America/Bogota")
EXPENSE_OPERATION_ID_RE = re.compile(r"exp_[0-9a-f]{32}")
CAPTURE_EXECUTION_LOCK = threading.Lock()
GROCERY_HELPER_WRITE_INTENTS = frozenset({"grocery"})
REMINDER_HELPER_WRITE_INTENTS = frozenset({"reminder"})
EXPENSE_HELPER_WRITE_DECISIONS = frozenset({"committed", "duplicate"})
EXPENSE_BLOCKED_RE = re.compile(
    r"\b(?:fp|futuro\s+pago|tc\s*[123]|tarjeta|cr[eé]dito|cuotas?|"
    r"transferencia|transferir|transfiere|dep[oó]sito|ingreso|salario|pr[eé]stamo)\b",
    re.I,
)
REMINDER_BARE_TASK_PREFIX_RE = re.compile(
    r"^\s*(?:por\s+favor\s+)?(?:comprar|llamar|pagar|enviar|revisar|hacer|sacar|llevar|"
    r"recoger|pedir|agendar|reservar|solicitar|preparar|limpiar|lavar|visitar|buscar|"
    r"entregar|renovar|actualizar|responder|escribir|estudiar|tomar|ir|terminar|"
    r"organizar|presentar)\b",
    re.I,
)
REMINDER_CREATE_WRAPPER_RE = re.compile(
    r"^(?:por\s+favor\s+)?(?:recuerdame|recordarme|no\s+me\s+dejes\s+olvidar)\b",
    re.I,
)
REMINDER_DIRECT_QUERY_RE = re.compile(
    r"^(?:por\s+favor\s+)?(?:"
    r"(?:que|cuales|cuantos?|dime|lista|listar|muestrame|muestra|mostrar|consulta|consultar)\b"
    r".{0,100}\b(?:recordatorios?|tareas?|pendientes?)\b|"
    r"(?:tengo|hay|existen)\b.{0,100}\b(?:recordatorios?|tareas?|pendientes?)\b|"
    r"(?:recordatorios?|tareas?|pendientes?)\b(?:$|.{0,100}\b(?:tengo|hay|existen)\b)"
    r")",
    re.I,
)
REMINDER_DIRECT_MUTATION_RE = re.compile(
    r"^(?:por\s+favor\s+)?(?:puedes?\s+)?(?:"
    r"cancela(?:me|r)?|elimina(?:me|r)?|borra(?:me|r)?|quita(?:me|r)?|"
    r"pospon(?:er)?|reprograma(?:r)?|cambia(?:r)?|edita(?:r)?|mueve(?:me|r)?|"
    r"marca(?:me|r)?|completa(?:r)?|termina(?:r)?"
    r")\b",
    re.I,
)
REMINDER_DIRECT_CROSS_DOMAIN_RE = re.compile(
    r"^(?:por\s+favor\s+)?(?:puedes?\s+)?(?:"
    r"anota(?:r)?|nota|guarda(?:r)?|registra(?:r)?|"
    r"enciende(?:r)?|prende(?:r)?|apaga(?:r)?|abre|abrir|cierra|cerrar|"
    r"sube|subir|baja|bajar|pon|poner|ajusta(?:r)?|"
    r"pague|compre|falta|faltan|mercado|comprado|disponible|gasto|compra"
    r")\b|"
    r"^(?:temperatura|termostato|luces?|cortinas?|puerta|alarma|aire\s+acondicionado)\b|"
    r"^(?:\$\s*\d|\d[\d.,]*\s*(?:mil|k|cop|pesos?)\b)",
    re.I,
)
LEDGER_WAITING_FOR_SERVER_GATE = "waiting_for_server_gate"
SUPPORTED_WRITE_ROUTES = {
    ("aura", "grocery", "aura.grocery_capture"): GROCERY_HELPER_WRITE_INTENTS,
    ("aura", "reminder", "aura.reminder_capture"): REMINDER_HELPER_WRITE_INTENTS,
    ("megan", "expense", "megan.expense_capture"): EXPENSE_HELPER_WRITE_DECISIONS,
}
SUPPORTED_WRITE_DOMAINS = frozenset(route[2] for route in SUPPORTED_WRITE_ROUTES)

SPANISH_HOURS = {
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
}


@dataclass(frozen=True)
class Config:
    secret: bytes
    ledger_path: Path
    host: str
    port: int
    write_enabled: bool = False
    write_domains: frozenset[str] = frozenset()
    aura_helper: Path | None = None
    expense_helper: Path | None = None
    reminder_openai_key_file: Path | None = None
    reminder_todoist_token_file: Path | None = None
    reminder_todoist_credentials_file: Path | None = None
    reminder_todoist_inbox_id: str = ""
    reminder_budget_path: Path | None = None
    reminder_monthly_limit_usd: str = "0"
    reminder_provider: str = "openai"
    reminder_model: str = "gpt-6-luna"
    reminder_ai_timeout: float = 8.0
    reminder_todoist_timeout: float = 5.0


class WriteExecutionError(RuntimeError):
    pass


def load_secret(path: Path) -> bytes:
    secret = path.read_text(encoding="utf-8").strip()
    if not secret:
        raise RuntimeError(f"empty secret file: {path}")
    return secret.encode("utf-8")


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mobile_capture_ledger (
                request_id TEXT PRIMARY KEY,
                payload_hash TEXT NOT NULL,
                domain TEXT NOT NULL,
                status TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS expense_operation_ledger (
                operation_id TEXT PRIMARY KEY,
                capture_fingerprint TEXT NOT NULL,
                plan_hash TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                external_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL,
                response_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS expense_operation_fingerprint_idx ON expense_operation_ledger(capture_fingerprint, status, updated_at)"
        )
        conn.commit()
    path.chmod(0o600)
    reminder_capture.init_db(path)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def json_response(handler: BaseHTTPRequestHandler, status: int, body: dict[str, Any]) -> None:
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def verify_hmac_v2(headers: Any, raw_body: bytes, secret: bytes) -> tuple[bool, str]:
    sig = headers.get("X-Webhook-Signature-V2", "")
    ts_raw = headers.get("X-Webhook-Timestamp", "")
    if not sig:
        return False, "missing X-Webhook-Signature-V2"
    if not ts_raw:
        return False, "missing X-Webhook-Timestamp"
    try:
        ts = int(ts_raw)
    except ValueError:
        return False, "invalid timestamp"
    if abs(int(time.time()) - ts) > REPLAY_TOLERANCE_SECONDS:
        return False, "timestamp outside replay window"
    expected = hmac.new(secret, ts_raw.encode("utf-8") + b"." + raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return False, "invalid signature"
    return True, "ok"


def canonical_domain(payload: dict[str, Any]) -> tuple[str, str, str]:
    route = payload.get("route") or {}
    agent = str(route.get("agent") or "argos").strip().lower()
    intent = str(route.get("intent") or "general_capture").strip().lower()
    domain = str(route.get("domain") or "").strip().lower()
    if domain:
        return domain, agent, intent
    mapping = {
        ("megan", "expense"): "megan.expense_capture",
        ("megan", "expense_capture"): "megan.expense_capture",
        ("aura", "reminder"): "aura.reminder_capture",
        ("aura", "reminder_capture"): "aura.reminder_capture",
        ("aura", "grocery"): "aura.grocery_capture",
        ("aura", "grocery_capture"): "aura.grocery_capture",
        ("aura", "home_action"): "aura.home_action",
        ("aura", "general_life_capture"): "aura.general_life_capture",
        ("argos", "general_capture"): "argos.general_capture",
        ("argos", "agent_status"): "argos.agent_status",
        ("pipo", "coding_task"): "pipo.coding_task_capture",
        ("atenea", "research"): "atenea.research_capture",
        ("horacio", "design_brief"): "horacio.design_brief_capture",
    }
    return mapping.get((agent, intent), f"{agent}.{intent}"), agent, intent


def parse_amount_cop(text: str) -> int | None:
    normalized = text.lower().replace("$", " ")
    match = re.search(r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*(mil|k)?", normalized)
    if not match:
        return None
    number = match.group(1)
    suffix = match.group(2)
    if suffix in {"mil", "k"} and not re.search(r"[.,]\d{3}", number):
        try:
            return int(float(number.replace(",", ".")) * 1000)
        except ValueError:
            return None
    digits = re.sub(r"[^0-9]", "", number)
    return int(digits) if digits else None


def parse_created_at(payload: dict[str, Any]) -> datetime:
    raw = str(payload.get("created_at") or "")
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(BOGOTA)
        except ValueError:
            pass
    return datetime.now(BOGOTA)


def reminder_dispatch_now() -> datetime:
    return datetime.now(BOGOTA)


def reminder_due_is_future(due: dict[str, Any]) -> bool:
    return datetime.fromisoformat(due["start"]) > reminder_dispatch_now()


def parse_spanish_due(text: str, base: datetime) -> dict[str, Any] | None:
    """Resolve only explicit, future schedules in Bogotá without guessing AM/PM."""
    t = "".join(char for char in unicodedata.normalize("NFKD", text or "") if not unicodedata.combining(char)).lower()
    t = re.sub(r"\s+", " ", t).strip()
    # Unsupported calendar dates remain unsupported even if the dictation also
    # contains a relative phrase. Never discard one schedule to choose another.
    if re.search(r"\b(?:lunes|martes|miercoles|jueves|viernes|sabado|domingo)\b|\d{4}-\d|\d/\d", t):
        return None
    relative_pattern = r"\ben\s+(\d+|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+(minutos?|horas?)\b"
    relative_count = len(re.findall(relative_pattern, t)) + len(re.findall(r"\ben\s+media\s+hora\b", t))
    date_text = re.sub(r"\bde\s+la\s+(?:manana|tarde|noche)\b", " ", t)
    day_mentions = re.findall(r"\b(?:pasado\s+manana|manana|hoy)\b", date_text)
    alternative_time = r"\b(?:o|u)\s+(?:a\s+las?\s+)?(?:\d{1,2}|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce|media)\b"
    if relative_count > 1 or len(day_mentions) > 1 or re.search(alternative_time, t):
        return None
    relative = re.search(relative_pattern, t)
    if relative_count and (day_mentions or re.search(r"\ba\s+las?\b", t)):
        return None
    if len(re.findall(r"\ba\s+las?\b", t)) > 1:
        return None
    if "en media hora" in t:
        due = base + timedelta(minutes=30)
    elif relative:
        value = relative.group(1)
        amount = int(value) if value.isdigit() else SPANISH_HOURS.get(value, 0)
        if not 0 < amount <= 1440:
            return None
        due = base + timedelta(**({"minutes": amount} if relative.group(2).startswith("min") else {"hours": amount}))
    else:
        # Weekdays and calendar dates need an authoritative scheduler; this
        # small parser deliberately accepts only hoy/mañana/pasado mañana.
        if "pasado manana" in t:
            due = base + timedelta(days=2)
        elif re.search(r"\bmanana\b", t):
            due = base + timedelta(days=1)
        elif re.search(r"\bhoy\b", t):
            due = base
        else:
            return None
        clock = re.search(r"\ba\s+las?\s+(\d{1,2}|una|uno|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez|once|doce)(?::(\d{2}))?\s*(a\.?\s*m\.?|p\.?\s*m\.?|de\s+la\s+manana|de\s+la\s+tarde|de\s+la\s+noche)?", t)
        if not clock:
            return None
        raw_hour = clock.group(1)
        hour = int(raw_hour) if raw_hour.isdigit() else SPANISH_HOURS[raw_hour]
        minute = int(clock.group(2) or 0)
        marker = re.sub(r"[.\s]", "", clock.group(3) or "")
        if minute > 59 or hour > 23:
            return None
        if marker:
            if not 1 <= hour <= 12:
                return None
            if marker in {"pm", "delatarde", "delanoche"} and hour < 12:
                hour += 12
            elif marker in {"am", "delamanana"} and hour == 12:
                hour = 0
        elif 1 <= hour <= 12:
            return None
        due = due.replace(hour=hour, minute=minute, second=0, microsecond=0)
    due = due.replace(microsecond=0)
    if due <= base:
        return None
    return {
        "start": due.isoformat(),
        "timezone": "America/Bogota",
        "display": due.strftime("%Y-%m-%d %I:%M %p America/Bogota"),
    }


def plan_capture(payload: dict[str, Any]) -> dict[str, Any]:
    request_id = str(payload.get("request_id") or "")
    domain, agent, intent = canonical_domain(payload)
    capture = payload.get("capture") or {}
    text = str(capture.get("text") or "").strip()

    # Planning is always side-effect free. execute_capture may separately invoke
    # an exact server-allowlisted adapter; client context never grants authority.
    base: dict[str, Any] = {
        "status": "accepted",
        "dry_run": True,
        "write_committed": False,
        "request_id": request_id,
        "domain": domain,
        "display_message": "Captura validada · dry-run ✅",
        "question": None,
        "plan": {"side_effects": [], "would_write": False, "notes": "Planning response; no external write committed."},
        "gates_missing": [
            "server domain allowlist",
            "external write adapter",
        ],
    }
    if not text:
        base.update(status="needs_confirmation", display_message="No entendí · intenta otra vez", question="¿Qué quieres capturar?")
        return base

    lowered = text.lower()
    if "ignora reglas" in lowered or "crea categoría" in lowered or "salta" in lowered:
        base.update(status="rejected", display_message="No registré: regla insegura")
        base["plan"]["notes"] = "Rejected prompt-injection/safety-bypass style input."
        return base

    if domain == "megan.expense_capture":
        amount = parse_amount_cop(text)
        is_card = any(token in lowered for token in [" fp ", "tc1", "tc2", "tc3", "futuro pago", "tarjeta"])
        if amount is None:
            base.update(status="needs_confirmation", display_message="Falta monto", question="¿Cuánto fue el gasto?")
            return base
        if "tarjeta" in lowered and not any(token in lowered for token in ["tc1", "tc2", "tc3"]):
            base.update(status="needs_confirmation", display_message="¿TC1, TC2 o TC3?", question="¿Con qué tarjeta fue: TC1, TC2 o TC3?")
            return base
        base["display_message"] = f"Dry-run Megan ${amount:,} COP".replace(",", ".")
        base["plan"] = {
            "side_effects": ["firefly.plan_only"],
            "would_write": False,
            "amount_cop": amount,
            "transaction_kind": "fp_card_purchase" if is_card else "cash_expense",
            "notes": "Would query Firefly live for existing account/category/budget/tags before any write.",
        }
        if is_card:
            base["plan"]["expected_movements"] = 2
        return base

    if domain == "aura.reminder_capture":
        base["display_message"] = "Recordatorio pendiente · configuración de AI y Todoist requerida"
        base["plan"] = {
            "side_effects": ["todoist.task.plan_only"],
            "would_write": False,
            "notes": "Structured AI interpretation and deterministic Bogotá validation required before a direct Todoist Inbox write. No AI call or external write in dry-run.",
        }
        base["gates_missing"] = ["server reminder domain allowlist", "private OpenAI and Todoist server credential files"]
        return base

    if domain == "aura.grocery_capture":
        base["display_message"] = "Mercado dry-run ✅"
        base["plan"] = {
            "side_effects": ["notion.official_grocery_list.plan_only"],
            "would_write": False,
            "notes": "Would update official grocery list; unchecked means missing.",
        }
        return base

    if domain == "aura.home_action":
        sensitive = any(word in lowered for word in ["puerta", "cerradura", "alarma"])
        if sensitive:
            base.update(status="needs_confirmation", display_message="Confirma en iPhone", question="Esta acción de casa es sensible. ¿Confirmas desde iPhone?")
        else:
            base["display_message"] = "Casa dry-run ✅"
        base["plan"] = {
            "side_effects": ["homeassistant.plan_only"],
            "would_write": False,
            "notes": "Would execute only if target/action are clear and safe.",
        }
        return base

    base["display_message"] = "Ruta dry-run ✅"
    return base


def client_requests_write(payload: dict[str, Any]) -> bool:
    context = payload.get("context") or {}
    if not isinstance(context, dict):
        return False
    return context.get("dry_run") is False and context.get("allow_write") is True


def exact_write_route(payload: dict[str, Any]) -> tuple[str, str, str] | None:
    """Return the unmodified route triple used for server write gates."""
    route = payload.get("route") or {}
    if not isinstance(route, dict):
        return None
    agent = route.get("agent")
    intent = route.get("intent")
    domain = route.get("domain")
    if not isinstance(agent, str) or not isinstance(intent, str) or not isinstance(domain, str):
        return None
    return agent, intent, domain


def valid_expense_cancellation_request(payload: dict[str, Any]) -> bool:
    """Validate a no-write cancellation independently of new-write gates."""
    if exact_write_route(payload) != ("megan", "expense", "megan.expense_capture"):
        return False
    context = payload.get("context") or {}
    if not isinstance(context, dict) or not client_requests_write(payload):
        return False
    operation_id = context.get("operation_id")
    return (
        context.get("user_cancellation") is True
        and context.get("user_confirmation") is False
        and context.get("requires_confirmation") is not True
        and context.get("allow_firefly_write") is False
        and context.get("confirmation_token") is None
        and isinstance(operation_id, str)
        and EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is not None
    )


def write_is_authorized(payload: dict[str, Any], cfg: Config) -> bool:
    route = exact_write_route(payload)
    context = payload.get("context") or {}
    if route is None or not isinstance(context, dict):
        return False
    if not (
        client_requests_write(payload)
        and cfg.write_enabled
        and route in SUPPORTED_WRITE_ROUTES
        and route[2] in cfg.write_domains
    ):
        return False
    confirmation_token = context.get("confirmation_token")
    operation_id = context.get("operation_id")
    confirmed = context.get("user_confirmation") is True
    cancelling = context.get("user_cancellation") is True
    cancellation_value = context.get("user_cancellation")
    if cancellation_value is not None and not isinstance(cancellation_value, bool):
        return False
    has_confirmation_token = isinstance(confirmation_token, str) and bool(confirmation_token)
    has_operation_id = isinstance(operation_id, str) and bool(operation_id)
    if route == ("megan", "expense", "megan.expense_capture"):
        if cancelling:
            return valid_expense_cancellation_request(payload)
        if context.get("allow_firefly_write") is not True:
            return False
        if confirmed:
            return (
                has_confirmation_token
                and isinstance(operation_id, str)
                and EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is not None
            )
        return not has_confirmation_token and not has_operation_id
    # Every non-Megan adapter rejects broad Firefly and financial-confirmation
    # capabilities explicitly; these fields cannot hitchhike on Capturar.
    return (
        context.get("allow_firefly_write") is not True
        and not confirmed
        and not cancelling
        and not has_confirmation_token
        and not has_operation_id
    )


def run_aura_helper(
    payload: dict[str, Any],
    cfg: Config,
    *,
    dry_run: bool,
    text_override: str | None = None,
    requested_due: dict[str, Any] | None = None,
) -> dict[str, Any]:
    helper = cfg.aura_helper
    if helper is None or not helper.is_file():
        raise WriteExecutionError("aura_helper_unavailable")
    capture = payload.get("capture") or {}
    helper_payload = {
        "text": text_override or str(capture.get("text") or "").strip(),
        "request_id": str(payload.get("request_id") or ""),
    }
    if exact_write_route(payload) == ("aura", "reminder", "aura.reminder_capture"):
        helper_payload.update(
            created_at=payload.get("created_at"),
            timezone="America/Bogota",
            interpreted_due=requested_due or parse_spanish_due(str(capture.get("text") or ""), parse_created_at(payload)),
        )
    command = [
        sys.executable,
        str(helper),
        "--payload-file",
        "-",
        "--no-notify",
        "--no-ai-title",
    ]
    if dry_run:
        command.append("--dry-run")
    try:
        process = subprocess.run(
            command,
            input=json.dumps(helper_payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise WriteExecutionError("aura_helper_failed") from error
    try:
        result = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise WriteExecutionError("aura_helper_invalid_response") from error
    if process.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        raise WriteExecutionError("aura_helper_rejected")
    return result


def run_expense_helper(
    payload: dict[str, Any],
    cfg: Config,
    *,
    dry_run: bool,
    operation_id: str,
    expected_plan_hash: str | None = None,
    confirmed_plan: dict[str, Any] | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    helper = cfg.expense_helper
    if helper is None or not helper.is_file():
        raise WriteExecutionError("expense_helper_unavailable")
    if EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise WriteExecutionError("expense_helper_invalid_operation_id")
    helper_payload: dict[str, Any] = {
        "request_id": str(payload.get("request_id") or ""),
        "operation_id": operation_id,
        "created_at": payload.get("created_at"),
        "capture": payload.get("capture") or {},
        "entities": payload.get("entities") or {},
    }
    if expected_plan_hash:
        helper_payload["expected_plan_hash"] = expected_plan_hash
    if confirmed_plan is not None:
        helper_payload["confirmed_plan"] = confirmed_plan
    command = [sys.executable, str(helper), "--payload-file", "-"]
    if dry_run:
        command.append("--dry-run")
    if mode:
        if mode not in {"plan", "reconcile", "commit"}:
            raise WriteExecutionError("expense_helper_invalid_mode")
        command.extend(("--mode", mode))
    try:
        process = subprocess.run(
            command,
            input=json.dumps(helper_payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise WriteExecutionError("expense_helper_failed") from error
    try:
        result = json.loads(process.stdout)
    except (json.JSONDecodeError, TypeError) as error:
        raise WriteExecutionError("expense_helper_invalid_response") from error
    if process.returncode != 0 or not isinstance(result, dict) or result.get("ok") is not True:
        raise WriteExecutionError("expense_helper_rejected")
    return result


def reject_live_expense_without_write(
    planned: dict[str, Any],
    question: str,
    notes: str,
    *,
    status: str = "rejected",
) -> dict[str, Any]:
    planned.update(
        status=status,
        dry_run=False,
        write_committed=False,
        display_message=watch_safe_text(question, "Megan necesita confirmación"),
        question=question if status == "needs_input" else None,
        gates_missing=[],
    )
    planned["plan"].update(would_write=False, side_effects=[], notes=notes)
    return planned


def cancelled_expense_response(
    planned: dict[str, Any],
    operation_id: str,
    *,
    duplicate: bool = False,
    expired: bool = False,
) -> dict[str, Any]:
    planned.update(
        status="cancelled",
        dry_run=False,
        write_committed=False,
        operation_id=operation_id,
        display_message=(
            "El preview ya había vencido · no se registró ningún gasto"
            if expired
            else "Gasto cancelado · no se registró nada"
        ),
        question=None,
        duplicate=duplicate,
        gates_missing=[],
    )
    planned["plan"].update(
        would_write=False,
        side_effects=[],
        notes="The durable expense preview is inactive; no Firefly POST was attempted.",
    )
    return planned


def current_cached_expense_preview(
    conn: sqlite3.Connection,
    cached: dict[str, Any],
) -> dict[str, Any]:
    """Expose a cached confirmation token only while its operation is preview."""
    if (
        cached.get("status") != "needs_confirmation"
        or cached.get("domain") != "megan.expense_capture"
    ):
        return cached
    operation_id = cached.get("operation_id")
    if not isinstance(operation_id, str) or EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is None:
        operation_status = None
    else:
        expire_stale_expense_previews(conn)
        row = conn.execute(
            "SELECT status FROM expense_operation_ledger WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        operation_status = str(row[0]) if row else None
    if operation_status == "preview":
        return cached
    scrubbed = dict(cached)
    scrubbed.pop("confirmation_token", None)
    scrubbed.pop("confirmation", None)
    scrubbed.update(
        status="rejected",
        dry_run=False,
        write_committed=False,
        operation_id=operation_id,
        duplicate=True,
        display_message="Ese preview ya no está activo. Consulta el estado del gasto antes de volver a dictarlo.",
        question=None,
        gates_missing=[],
    )
    plan = scrubbed.get("plan")
    if not isinstance(plan, dict):
        plan = {}
        scrubbed["plan"] = plan
    plan.update(
        would_write=False,
        side_effects=[],
        notes=f"Cached confirmation suppressed for inactive operation state {operation_status or 'missing'}.",
    )
    return scrubbed


def expense_operation_binding(payload: dict[str, Any]) -> bytes:
    request_id = str(payload.get("request_id") or "")
    source = payload.get("source") or {}
    capture = payload.get("capture") or {}
    device_id = str(source.get("device_id") or "") if isinstance(source, dict) else ""
    text = str(capture.get("text") or "").strip() if isinstance(capture, dict) else ""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"expense-operation.v1\n{request_id}\n{device_id}\n{text_hash}".encode("utf-8")


def make_expense_operation_id(payload: dict[str, Any], secret: bytes) -> str:
    digest = hmac.new(secret, expense_operation_binding(payload), hashlib.sha256).hexdigest()
    return f"exp_{digest[:32]}"


def expense_confirmation_binding(
    payload: dict[str, Any],
    expires_at: int,
    operation_id: str,
    plan_hash: str,
) -> bytes:
    source = payload.get("source") or {}
    capture = payload.get("capture") or {}
    device_id = str(source.get("device_id") or "") if isinstance(source, dict) else ""
    text = str(capture.get("text") or "").strip() if isinstance(capture, dict) else ""
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return (
        f"expense-confirmation.v2\n{expires_at}\n{operation_id}\n{device_id}\n{text_hash}\n{plan_hash}"
    ).encode("utf-8")


def make_expense_confirmation_token(
    payload: dict[str, Any],
    operation_id: str,
    plan_hash: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> str:
    if EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is None:
        raise WriteExecutionError("expense_confirmation_invalid_operation_id")
    if re.fullmatch(r"[0-9a-f]{64}", plan_hash) is None:
        raise WriteExecutionError("expense_confirmation_invalid_plan_hash")
    expires_at = (int(time.time()) if now is None else now) + EXPENSE_CONFIRMATION_TTL_SECONDS
    signature = hmac.new(
        secret,
        expense_confirmation_binding(payload, expires_at, operation_id, plan_hash),
        hashlib.sha256,
    ).hexdigest()
    return f"v2.{expires_at}.{operation_id}.{plan_hash}.{signature}"


def expense_confirmation_state(
    payload: dict[str, Any],
    operation_id: str,
    plan_hash: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> str:
    """Return ``valid``, ``expired``, or ``invalid`` for a signed preview.

    Expired signatures remain cryptographically authenticated so the server can
    reconcile the same durable operation before offering a refreshed token.
    """
    context = payload.get("context") or {}
    if not isinstance(context, dict) or context.get("user_confirmation") is not True:
        return "invalid"
    if context.get("operation_id") != operation_id:
        return "invalid"
    token = str(context.get("confirmation_token") or "")
    parts = token.split(".")
    if (
        len(parts) != 5
        or parts[0] != "v2"
        or parts[2] != operation_id
        or parts[3] != plan_hash
        or EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is None
    ):
        return "invalid"
    try:
        expires_at = int(parts[1])
    except ValueError:
        return "invalid"
    current = int(time.time()) if now is None else now
    if expires_at <= 0 or expires_at > current + EXPENSE_CONFIRMATION_TTL_SECONDS:
        return "invalid"
    expected = hmac.new(
        secret,
        expense_confirmation_binding(payload, expires_at, operation_id, plan_hash),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(parts[4], expected):
        return "invalid"
    return "expired" if expires_at < current else "valid"


def expense_confirmation_is_valid(
    payload: dict[str, Any],
    operation_id: str,
    plan_hash: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> bool:
    return expense_confirmation_state(
        payload,
        operation_id,
        plan_hash,
        secret,
        now=now,
    ) == "valid"


def renew_expense_preview_lease(cfg: Config, operation_id: str) -> bool:
    """Renew the durable lease before issuing a fresh confirmation token."""
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE expense_operation_ledger SET updated_at = ? WHERE operation_id = ? AND status = 'preview'",
            (now_iso(), operation_id),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return False
        conn.commit()
        return True


def request_expense_confirmation(
    payload: dict[str, Any],
    cfg: Config,
    planned: dict[str, Any],
    expense_plan: dict[str, Any],
    operation_id: str,
    plan_hash: str,
) -> dict[str, Any]:
    if not renew_expense_preview_lease(cfg, operation_id):
        raise WriteExecutionError("expense_preview_inactive_before_confirmation")
    amount = f"${expense_plan['amount_cop']:,}".replace(",", ".")
    source_name = watch_safe_text(expense_plan.get("source_name"), "Cuenta", 42)
    description = watch_safe_text(expense_plan.get("description"), "Gasto", 48)
    planned.update(
        status="needs_confirmation",
        dry_run=False,
        write_committed=False,
        display_message=f"Confirma {amount} · {source_name}",
        question=f"¿Registrar {amount} por {description} desde {source_name}?",
        gates_missing=[],
        operation_id=operation_id,
        confirmation_token=make_expense_confirmation_token(
            payload,
            operation_id,
            plan_hash,
            cfg.secret,
        ),
        confirmation={
            "amount_cop": expense_plan["amount_cop"],
            "currency_code": "COP",
            "description": description,
            "source_name": source_name,
            "category_name": watch_safe_text(expense_plan.get("category_name"), "Categoría", 42),
            "budget_name": watch_safe_text(expense_plan.get("budget_name"), "Presupuesto", 42),
        },
    )
    planned["plan"].update(
        would_write=False,
        side_effects=[],
        notes="GET-only expense plan is awaiting a short-lived server-signed Watch confirmation.",
    )
    return planned


def expense_capture_fingerprint(payload: dict[str, Any], secret: bytes) -> str:
    source = payload.get("source") or {}
    capture = payload.get("capture") or {}
    device_id = str(source.get("device_id") or "") if isinstance(source, dict) else ""
    text = str(capture.get("text") or "").strip() if isinstance(capture, dict) else ""
    binding = f"expense-capture.v1\n{device_id}\n{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
    return hmac.new(secret, binding.encode("utf-8"), hashlib.sha256).hexdigest()


def _expense_operation_from_row(row: tuple[Any, ...] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    keys = (
        "operation_id",
        "capture_fingerprint",
        "plan_hash",
        "plan_json",
        "external_id",
        "status",
        "response_json",
        "created_at",
        "updated_at",
    )
    result: dict[str, Any] = dict(zip(keys, row))
    try:
        result["plan"] = json.loads(str(result.pop("plan_json")))
    except (TypeError, json.JSONDecodeError) as error:
        raise WriteExecutionError("expense_operation_corrupt_plan") from error
    return result


def load_expense_operation(cfg: Config, operation_id: str) -> dict[str, Any] | None:
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path) as conn:
        row = conn.execute(
            "SELECT operation_id, capture_fingerprint, plan_hash, plan_json, external_id, status, response_json, created_at, updated_at FROM expense_operation_ledger WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
    return _expense_operation_from_row(row)


def expire_stale_expense_previews(conn: sqlite3.Connection) -> int:
    """Expire only unclaimed previews older than the confirmation window."""
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=EXPENSE_PREVIEW_TTL_SECONDS)).isoformat()
    cursor = conn.execute(
        "UPDATE expense_operation_ledger SET status = 'expired', updated_at = ? WHERE status = 'preview' AND updated_at <= ?",
        (now_iso(), cutoff),
    )
    return cursor.rowcount


def load_active_expense_operation(cfg: Config, capture_fingerprint: str) -> dict[str, Any] | None:
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_stale_expense_previews(conn)
        row = conn.execute(
            """
            SELECT operation_id, capture_fingerprint, plan_hash, plan_json, external_id, status, response_json, created_at, updated_at
            FROM expense_operation_ledger
            WHERE capture_fingerprint = ? AND status IN ('preview', 'writing', 'uncertain')
            ORDER BY CASE status WHEN 'uncertain' THEN 0 WHEN 'writing' THEN 1 ELSE 2 END, updated_at DESC
            LIMIT 1
            """,
            (capture_fingerprint,),
        ).fetchone()
        conn.commit()
    return _expense_operation_from_row(row)


def persist_expense_preview(
    cfg: Config,
    operation_id: str,
    capture_fingerprint: str,
    plan_hash: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    external_id = f"hermes-watch:{operation_id}"
    if plan.get("operation_id") != operation_id or plan.get("external_id") != external_id:
        raise WriteExecutionError("expense_helper_operation_mismatch")
    ts = now_iso()
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        expire_stale_expense_previews(conn)
        existing = conn.execute(
            "SELECT operation_id, capture_fingerprint, plan_hash, plan_json, external_id, status, response_json, created_at, updated_at FROM expense_operation_ledger WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        if existing is None:
            # Different delivery IDs intentionally derive different candidate
            # operation IDs. Serialize preview creation and reuse the first
            # active operation for the same device/text fingerprint so two
            # concurrent previews cannot later authorize two withdrawals.
            active = conn.execute(
                """
                SELECT operation_id, capture_fingerprint, plan_hash, plan_json, external_id, status, response_json, created_at, updated_at
                FROM expense_operation_ledger
                WHERE capture_fingerprint = ? AND status IN ('preview', 'writing', 'uncertain')
                ORDER BY CASE status WHEN 'uncertain' THEN 0 WHEN 'writing' THEN 1 ELSE 2 END, updated_at DESC
                LIMIT 1
                """,
                (capture_fingerprint,),
            ).fetchone()
            if active is not None:
                conn.commit()
                resolved = _expense_operation_from_row(active)
                if resolved is None:
                    raise WriteExecutionError("expense_operation_missing_after_active_lookup")
                return resolved
            try:
                conn.execute(
                    "INSERT INTO expense_operation_ledger(operation_id, capture_fingerprint, plan_hash, plan_json, external_id, status, response_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'preview', NULL, ?, ?)",
                    (
                        operation_id,
                        capture_fingerprint,
                        plan_hash,
                        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                        external_id,
                        ts,
                        ts,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise WriteExecutionError("expense_operation_identity_conflict") from error
            conn.commit()
        else:
            parsed = _expense_operation_from_row(existing)
            if not parsed or any(
                (
                    parsed["capture_fingerprint"] != capture_fingerprint,
                    parsed["plan_hash"] != plan_hash,
                    parsed["external_id"] != external_id,
                    parsed["plan"] != plan,
                )
            ):
                raise WriteExecutionError("expense_operation_identity_conflict")
            conn.commit()
    operation = load_expense_operation(cfg, operation_id)
    if operation is None:
        raise WriteExecutionError("expense_operation_missing_after_insert")
    return operation


def set_expense_operation_status(
    cfg: Config,
    operation_id: str,
    status: str,
    response: dict[str, Any] | None = None,
) -> None:
    # Preview creation, the writing claim, and uncertain recovery each have
    # dedicated conditional helpers. This setter is intentionally terminal-only
    # so no caller can accidentally move ``committed`` backwards.
    if status != "committed" or not isinstance(response, dict):
        raise WriteExecutionError("expense_operation_invalid_transition")
    init_db(cfg.ledger_path)
    response_json = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE expense_operation_ledger SET status = 'committed', response_json = ?, updated_at = ? WHERE operation_id = ? AND status IN ('preview', 'writing', 'uncertain')",
            (response_json, now_iso(), operation_id),
        )
        if cursor.rowcount != 1:
            row = conn.execute(
                "SELECT status FROM expense_operation_ledger WHERE operation_id = ?",
                (operation_id,),
            ).fetchone()
            if row is None:
                raise WriteExecutionError("expense_operation_missing")
            if row[0] != "committed":
                raise WriteExecutionError("expense_operation_invalid_transition")
        conn.commit()


def claim_expense_operation_for_write(cfg: Config, operation_id: str) -> bool:
    """Atomically grant the one allowed transition into the POST boundary.

    Multiple delivery IDs may carry the same signed operation confirmation.  A
    plain status assignment lets two threads read ``preview`` and both enter the
    helper's commit mode.  The conditional update below is the durable
    compare-and-set: every loser must reconcile and may never issue a POST.
    """
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE expense_operation_ledger SET status = 'writing', updated_at = ? WHERE operation_id = ? AND status = 'preview'",
            (now_iso(), operation_id),
        )
        conn.commit()
    return cursor.rowcount == 1


def cancel_expense_operation_if_preview(cfg: Config, operation_id: str) -> bool:
    """Atomically revoke an unclaimed preview; later states are immutable here."""
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE expense_operation_ledger SET status = 'cancelled', updated_at = ? WHERE operation_id = ? AND status = 'preview'",
            (now_iso(), operation_id),
        )
        conn.commit()
    return cursor.rowcount == 1


def mark_expense_operation_uncertain_if_uncommitted(cfg: Config, operation_id: str) -> bool:
    """Move only nonterminal states to ``uncertain``.

    A slow concurrent reconciliation may have observed ``writing`` before
    another worker proved and persisted ``committed``. Its stale result must
    never move that terminal state backwards.
    """
    init_db(cfg.ledger_path)
    with sqlite3.connect(cfg.ledger_path, timeout=65.0) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "UPDATE expense_operation_ledger SET status = 'uncertain', updated_at = ? WHERE operation_id = ? AND status IN ('preview', 'writing', 'uncertain')",
            (now_iso(), operation_id),
        )
        conn.commit()
    return cursor.rowcount == 1


def expense_helper_identity_is_exact(
    result: dict[str, Any],
    operation_id: str,
    plan_hash: str,
    plan: dict[str, Any],
) -> bool:
    external_id = f"hermes-watch:{operation_id}"
    return (
        result.get("intent") == "expense"
        and result.get("transaction_kind") == "cash_expense"
        and result.get("operation_id") == operation_id
        and result.get("external_id") == external_id
        and result.get("plan_hash") == plan_hash
        and result.get("plan") == plan
        and plan.get("operation_id") == operation_id
        and plan.get("external_id") == external_id
    )


def committed_expense_response(
    payload: dict[str, Any],
    operation: dict[str, Any],
    committed: dict[str, Any],
) -> dict[str, Any]:
    operation_id = str(operation["operation_id"])
    plan = operation["plan"]
    if not expense_helper_identity_is_exact(
        committed,
        operation_id,
        str(operation["plan_hash"]),
        plan,
    ):
        raise WriteExecutionError("expense_helper_unproven_commit")
    if (
        committed.get("decision") not in EXPENSE_HELPER_WRITE_DECISIONS
        or committed.get("write_committed") is not True
    ):
        raise WriteExecutionError("expense_helper_unproven_commit")
    journal_ids = committed.get("journal_ids")
    if not (
        isinstance(journal_ids, list)
        and len(journal_ids) == 1
        and isinstance(journal_ids[0], str)
        and journal_ids[0].strip()
    ):
        raise WriteExecutionError("expense_helper_missing_journal_id")
    group_ids = committed.get("group_ids")
    if not isinstance(group_ids, list):
        group_ids = []
    amount = f"${plan['amount_cop']:,}".replace(",", ".")
    description = watch_safe_text(plan.get("description"), "Gasto", 48)
    status = "duplicate" if committed.get("decision") == "duplicate" else "committed"
    return {
        "status": status,
        "dry_run": False,
        "write_committed": True,
        "request_id": str(payload.get("request_id") or ""),
        "operation_id": operation_id,
        "domain": "megan.expense_capture",
        "display_message": f"Gasto registrado ✅ · {amount} · {description}",
        "question": None,
        "duplicate": status == "duplicate",
        "plan": {
            "side_effects": ["firefly.withdrawal.write"],
            "would_write": True,
            "notes": "Exactly one canonical Firefly withdrawal was reconciled for this durable operation ID.",
        },
        "external_ids": {
            "expense_operation_id": operation_id,
            "firefly_external_id": operation["external_id"],
            "firefly_journal_ids": [journal_ids[0].strip()],
            "firefly_group_ids": [str(value) for value in group_ids if str(value).strip()],
        },
        "gates_missing": [],
    }


def expense_pending_review_response(planned: dict[str, Any], operation_id: str, reason: str) -> dict[str, Any]:
    response = reject_live_expense_without_write(
        planned,
        "El resultado de Firefly no es concluyente; Megan debe revisarlo.",
        f"Operation {operation_id} is durable and reconciliation-only: {reason}.",
    )
    response["operation_id"] = operation_id
    response["display_message"] = "Gasto pendiente de revisión · no reintentaré la escritura"
    return response


def reconcile_expense_operation(
    payload: dict[str, Any],
    cfg: Config,
    planned: dict[str, Any],
    operation: dict[str, Any],
) -> tuple[str, dict[str, Any] | None]:
    result = run_expense_helper(
        payload,
        cfg,
        dry_run=False,
        operation_id=str(operation["operation_id"]),
        expected_plan_hash=str(operation["plan_hash"]),
        confirmed_plan=operation["plan"],
        mode="reconcile",
    )
    if not expense_helper_identity_is_exact(
        result,
        str(operation["operation_id"]),
        str(operation["plan_hash"]),
        operation["plan"],
    ):
        if result.get("decision") == "needs_confirmation":
            mark_expense_operation_uncertain_if_uncommitted(cfg, str(operation["operation_id"]))
            return "conflict", expense_pending_review_response(
                planned,
                str(operation["operation_id"]),
                watch_safe_text(result.get("error"), "identity_conflict", 80),
            )
        raise WriteExecutionError("expense_helper_reconciliation_identity_mismatch")
    if result.get("decision") in EXPENSE_HELPER_WRITE_DECISIONS and result.get("write_committed") is True:
        response = committed_expense_response(payload, operation, result)
        set_expense_operation_status(cfg, str(operation["operation_id"]), "committed", response)
        return "committed", response
    if result.get("decision") == "absent" and result.get("post_attempted") is False:
        return "absent", None
    if result.get("decision") == "reconciliation_error" and result.get("post_attempted") is False:
        return "unavailable", reject_live_expense_without_write(
            planned,
            "No pude verificar Firefly; intenta nuevamente.",
            "Read-only external-id reconciliation was unavailable; no POST was attempted.",
        )
    mark_expense_operation_uncertain_if_uncommitted(cfg, str(operation["operation_id"]))
    return "uncertain", expense_pending_review_response(
        planned,
        str(operation["operation_id"]),
        watch_safe_text(result.get("error"), "reconciliation_failed", 80),
    )


def execute_expense_capture(
    payload: dict[str, Any],
    cfg: Config,
    planned: dict[str, Any],
) -> dict[str, Any]:
    capture_text = str((payload.get("capture") or {}).get("text") or "").strip()
    if EXPENSE_BLOCKED_RE.search(capture_text):
        return reject_live_expense_without_write(
            planned,
            "Usa Megan para tarjetas, FP, transferencias o movimientos múltiples.",
            "Blocked unsupported financial flow before invoking the expense adapter.",
        )

    context = payload.get("context") or {}
    confirmed = isinstance(context, dict) and context.get("user_confirmation") is True
    cancelling = isinstance(context, dict) and context.get("user_cancellation") is True
    capture_fingerprint = expense_capture_fingerprint(payload, cfg.secret)

    if cancelling:
        operation_id = str(context.get("operation_id") or "")
        if EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is None:
            return reject_live_expense_without_write(
                planned,
                "La cancelación no tiene una identidad válida; no cambié nada.",
                "Rejected malformed expense cancellation identity before invoking the adapter.",
            )
        operation = load_expense_operation(cfg, operation_id)
        if operation is None or operation["capture_fingerprint"] != capture_fingerprint:
            return reject_live_expense_without_write(
                planned,
                "No encontré ese preview pendiente; no cambié nada.",
                "Rejected cancellation without an exact durable operation binding.",
            )
        planned["operation_id"] = operation_id
        if cancel_expense_operation_if_preview(cfg, operation_id):
            return cancelled_expense_response(planned, operation_id)
        current = load_expense_operation(cfg, operation_id)
        if current is None:
            raise WriteExecutionError("expense_operation_missing_after_cancel")
        if current["status"] in {"cancelled", "expired"}:
            return cancelled_expense_response(
                planned,
                operation_id,
                duplicate=True,
                expired=current["status"] == "expired",
            )
        return reject_live_expense_without_write(
            planned,
            "Ese gasto ya no puede cancelarse automáticamente; revisa con Megan.",
            f"Cancellation refused for monotonic operation state {current['status']}.",
        )

    if confirmed:
        operation_id = str(context.get("operation_id") or "")
        if EXPENSE_OPERATION_ID_RE.fullmatch(operation_id) is None:
            return reject_live_expense_without_write(
                planned,
                "La confirmación no tiene una identidad válida; revisa el gasto nuevamente.",
                "Rejected malformed expense operation identity before invoking the adapter.",
            )
        operation = load_expense_operation(cfg, operation_id)
        if operation is None or operation["capture_fingerprint"] != capture_fingerprint:
            return reject_live_expense_without_write(
                planned,
                "No encontré el preview firmado para esta confirmación.",
                "Rejected confirmation without an exact durable operation binding.",
            )
    else:
        operation_id = make_expense_operation_id(payload, cfg.secret)
        operation = load_expense_operation(cfg, operation_id)
        if operation is not None and operation["capture_fingerprint"] != capture_fingerprint:
            raise WriteExecutionError("expense_operation_identity_conflict")
        if operation is None:
            operation = load_active_expense_operation(cfg, capture_fingerprint)
            if operation is not None:
                operation_id = str(operation["operation_id"])

    if operation is not None:
        # Retain identity only after its durable device/text binding is proven.
        planned["operation_id"] = operation_id
        if not confirmed and operation["status"] != "preview":
            return reject_live_expense_without_write(
                planned,
                "Ese preview ya no está activo. Consulta el estado del gasto antes de volver a dictarlo.",
                f"Unconfirmed delivery recovered inactive durable operation state {operation['status']}; no adapter invoked.",
            )
        if operation["status"] in {"cancelled", "expired"}:
            return reject_live_expense_without_write(
                planned,
                "Ese preview ya no está activo; dicta el gasto nuevamente si aún lo necesitas.",
                f"Rejected confirmation for inactive operation state {operation['status']}.",
            )
        if operation["status"] == "committed" and operation.get("response_json"):
            cached = json.loads(str(operation["response_json"]))
            cached.update(
                status="duplicate",
                duplicate=True,
                request_id=str(payload.get("request_id") or ""),
            )
            return cached

        if operation["plan"].get("currency_code") != "COP" and operation["status"] == "preview":
            cancel_expense_operation_if_preview(cfg, operation_id)
            return reject_live_expense_without_write(
                planned,
                "Ese preview no verifica COP; dicta el gasto nuevamente para revisarlo.",
                "Legacy currencyless preview invalidated before any Firefly POST.",
            )

        if confirmed or operation["status"] in {"writing", "uncertain"}:
            reconciliation, reconciled_response = reconcile_expense_operation(
                payload,
                cfg,
                planned,
                operation,
            )
            if reconciled_response is not None:
                return reconciled_response
            if operation["status"] in {"writing", "uncertain"}:
                mark_expense_operation_uncertain_if_uncommitted(cfg, operation_id)
                return expense_pending_review_response(planned, operation_id, reconciliation)

        if not confirmed:
            return request_expense_confirmation(
                payload,
                cfg,
                planned,
                operation["plan"],
                operation_id,
                str(operation["plan_hash"]),
            )

        confirmation_state = expense_confirmation_state(
            payload,
            operation_id,
            str(operation["plan_hash"]),
            cfg.secret,
        )
        if confirmation_state == "expired":
            return request_expense_confirmation(
                payload,
                cfg,
                planned,
                operation["plan"],
                operation_id,
                str(operation["plan_hash"]),
            )
        if confirmation_state != "valid":
            return reject_live_expense_without_write(
                planned,
                "La confirmación no coincide; revisa el gasto nuevamente.",
                "Rejected invalid server-signed expense confirmation after read-only reconciliation.",
            )

        # This durable compare-and-set is the point of no automatic return:
        # exactly one confirmation delivery may cross into helper commit mode.
        # Every concurrent loser and every later retry is reconciliation-only,
        # even if the winner crashes before its HTTP response is stored.
        if not claim_expense_operation_for_write(cfg, operation_id):
            current = load_expense_operation(cfg, operation_id)
            if current is None:
                raise WriteExecutionError("expense_operation_missing_after_claim")
            if current["status"] == "committed" and current.get("response_json"):
                cached = json.loads(str(current["response_json"]))
                cached.update(
                    status="duplicate",
                    duplicate=True,
                    request_id=str(payload.get("request_id") or ""),
                )
                return cached
            if current["status"] in {"cancelled", "expired"}:
                return reject_live_expense_without_write(
                    planned,
                    "Ese preview ya no está activo. Consulta el estado del gasto antes de volver a dictarlo.",
                    f"Confirmation lost the preview compare-and-set to {current['status']}.",
                )
            reconciliation, reconciled_response = reconcile_expense_operation(
                payload,
                cfg,
                planned,
                current,
            )
            if reconciled_response is not None:
                return reconciled_response
            if current["status"] in {"writing", "uncertain"}:
                mark_expense_operation_uncertain_if_uncommitted(cfg, operation_id)
                return expense_pending_review_response(planned, operation_id, reconciliation)
            raise WriteExecutionError("expense_operation_claim_conflict")
        committed = run_expense_helper(
            payload,
            cfg,
            dry_run=False,
            operation_id=operation_id,
            expected_plan_hash=str(operation["plan_hash"]),
            confirmed_plan=operation["plan"],
            mode="commit",
        )
        if (
            expense_helper_identity_is_exact(
                committed,
                operation_id,
                str(operation["plan_hash"]),
                operation["plan"],
            )
            and committed.get("decision") in EXPENSE_HELPER_WRITE_DECISIONS
            and committed.get("write_committed") is True
        ):
            response = committed_expense_response(payload, operation, committed)
            set_expense_operation_status(cfg, operation_id, "committed", response)
            return response
        mark_expense_operation_uncertain_if_uncommitted(cfg, operation_id)
        return expense_pending_review_response(
            planned,
            operation_id,
            watch_safe_text(committed.get("error"), "unproven_commit", 80),
        )

    preflight = run_expense_helper(
        payload,
        cfg,
        dry_run=True,
        operation_id=operation_id,
        mode="plan",
    )
    if preflight.get("intent") != "expense":
        raise WriteExecutionError("expense_helper_domain_mismatch")
    if preflight.get("decision") == "needs_confirmation":
        return reject_live_expense_without_write(
            planned,
            watch_safe_text(preflight.get("question"), "Megan necesita más información", 180),
            f"Expense adapter preflight stopped safely: {watch_safe_text(preflight.get('error'), 'needs_input', 80)}.",
            status="needs_input",
        )
    if preflight.get("decision") != "auto_write" or preflight.get("transaction_kind") != "cash_expense":
        raise WriteExecutionError("expense_helper_unsafe_preflight")
    plan_hash = str(preflight.get("plan_hash") or "")
    expense_plan = preflight.get("plan")
    if not re.fullmatch(r"[0-9a-f]{64}", plan_hash) or not isinstance(expense_plan, dict):
        raise WriteExecutionError("expense_helper_invalid_plan")
    if not expense_helper_identity_is_exact(preflight, operation_id, plan_hash, expense_plan):
        raise WriteExecutionError("expense_helper_operation_mismatch")
    if not (
        expense_plan.get("currency_code") == "COP"
        and type(expense_plan.get("amount_cop")) is int
        and 1000 <= expense_plan["amount_cop"] <= EXPENSE_MAX_AMOUNT_COP
        and all(
            isinstance(expense_plan.get(key), str) and expense_plan[key].strip()
            for key in ("description", "source_name", "category_name", "budget_name")
        )
    ):
        raise WriteExecutionError("expense_helper_incomplete_plan")
    operation = persist_expense_preview(
        cfg,
        operation_id,
        capture_fingerprint,
        plan_hash,
        expense_plan,
    )
    return request_expense_confirmation(
        payload,
        cfg,
        planned,
        operation["plan"],
        str(operation["operation_id"]),
        str(operation["plan_hash"]),
    )


def _live_grocery_clarification_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "needs_input",
        "dry_run": False,
        "write_committed": False,
        "request_id": payload["request_id"],
        "domain": "aura.grocery_capture",
        "display_message": "No identifiqué el artículo · no cambié nada",
        "question": "¿Qué artículo quieres agregar al mercado?",
        "plan": {
            "side_effects": [],
            "would_write": False,
            "notes": "Live grocery clarification; no external write was attempted.",
        },
        "gates_missing": [],
    }


def correct_legacy_grocery_clarification(
    payload: dict[str, Any], cached: dict[str, Any], ledger_status: str
) -> dict[str, Any]:
    """Present one proven old no-write reply safely; never alter its ledger row."""
    route = payload.get("route")
    context = payload.get("context")
    request_id = payload.get("request_id")
    if (
        ledger_status != "needs_confirmation"
        or not isinstance(route, dict)
        or (route.get("agent"), route.get("intent"), route.get("domain"))
        != ("aura", "grocery", "aura.grocery_capture")
        or not isinstance(context, dict)
        or context.get("dry_run") is not False
        or context.get("allow_write") is not True
        or context.get("allow_firefly_write") is not False
        or any(context.get(field, False) is not False for field in (
            "user_confirmation", "user_cancellation", "requires_confirmation"
        ))
        or context.get("confirmation_token") is not None
        or context.get("operation_id") is not None
        or not isinstance(request_id, str)
        or not request_id.strip()
        or not isinstance(cached, dict)
        or set(cached) != {
            "status", "dry_run", "write_committed", "request_id", "domain",
            "display_message", "question", "plan", "gates_missing"
        }
    ):
        return cached
    plan = cached["plan"]
    if (
        cached["status"] != "needs_confirmation"
        or cached["request_id"] != request_id
        or cached["domain"] != "aura.grocery_capture"
        or cached["dry_run"] is not True
        or cached["write_committed"] is not False
        or cached["display_message"] != "No parece un artículo de mercado · no cambié nada"
        or cached["question"] != "¿Qué artículo quieres agregar o marcar como comprado?"
        or cached["gates_missing"] != ["server domain allowlist", "external write adapter"]
        or not isinstance(plan, dict)
        or set(plan) != {"side_effects", "would_write", "notes"}
        or plan["would_write"] is not False
        or plan["side_effects"] != ["notion.official_grocery_list.plan_only"]
        or plan["notes"] != "Would update official grocery list; unchecked means missing."
    ):
        return cached
    return _live_grocery_clarification_response(payload)


def execute_grocery_capture(
    payload: dict[str, Any],
    cfg: Config,
    planned: dict[str, Any],
    *,
    raw_payload_hash: str | None = None,
    require_existing_batch: bool = False,
) -> dict[str, Any]:
    return grocery_capture.execute(payload, cfg, raw_payload_hash=raw_payload_hash, require_existing_batch=require_existing_batch)


def watch_safe_text(value: Any, fallback: str, limit: int = 96) -> str:
    compact = re.sub(r"\s+", " ", str(value or "")).strip()
    return (compact or fallback)[:limit]


def normalize_reminder_command_text(capture_text: str) -> str:
    folded = "".join(
        char
        for char in unicodedata.normalize("NFKD", capture_text or "")
        if not unicodedata.combining(char)
    ).lower()
    folded = re.sub(r"[¿¡?!,;:()\[\]{}\"']", " ", folded)
    return re.sub(r"\s+", " ", folded).strip()


def reminder_text_is_blocked(capture_text: str) -> bool:
    """Reject known direct non-create/cross-domain language before the helper."""
    text = normalize_reminder_command_text(capture_text)
    if not text or REMINDER_CREATE_WRAPPER_RE.search(text):
        return False
    if REMINDER_BARE_TASK_PREFIX_RE.search(text):
        return False
    return any(
        pattern.search(text) is not None
        for pattern in (
            REMINDER_DIRECT_QUERY_RE,
            REMINDER_DIRECT_MUTATION_RE,
            REMINDER_DIRECT_CROSS_DOMAIN_RE,
        )
    )


def should_add_reminder_route_context(capture_text: str, helper_intent: Any) -> bool:
    """Allow only explicit task-like bare actions onto the live reminder path."""
    normalized = normalize_reminder_command_text(capture_text)
    return (
        helper_intent == "note"
        and REMINDER_BARE_TASK_PREFIX_RE.search(normalized) is not None
        and not reminder_text_is_blocked(capture_text)
    )


def reject_live_reminder_without_write(planned: dict[str, Any], notes: str) -> dict[str, Any]:
    """Return the terminal client-safe contract for a blocked live reminder."""
    planned.update(
        status="rejected",
        dry_run=False,
        write_committed=False,
        display_message="Solo creo recordatorios nuevos · no cambié nada",
        question=None,
        gates_missing=[],
    )
    planned["plan"].update(would_write=False, notes=notes)
    return planned


def execute_reminder_capture(
    payload: dict[str, Any], cfg: Config, planned: dict[str, Any]
) -> dict[str, Any]:
    # Credentials are an explicit server gate; desktop connector credentials
    # never authorize this backend. There is no legacy helper fallback.
    if cfg.reminder_openai_key_file is None or (cfg.reminder_todoist_token_file is None and cfg.reminder_todoist_credentials_file is None):
        return planned
    try:
        return reminder_capture.execute(payload, cfg, now=reminder_dispatch_now)
    except reminder_capture.ReminderConflict:
        raise WriteExecutionError("request_id_conflict") from None
    except reminder_capture.ReminderRetryable as error:
        code = "reminder_configuration_pending" if str(error) in {"reminder_budget_pending", "reminder_configuration_unavailable", "reminder_authorization_pending"} else "reminder_pending_retry"
        raise WriteExecutionError(code) from None


def ledger_status_for_response(payload: dict[str, Any], response: dict[str, Any]) -> str:
    """Reserve request/hash identity while allowing a server-gate retry."""
    plan = response.get("plan") or {}
    live_request_waiting_for_server = (
        client_requests_write(payload)
        and response.get("status") == "accepted"
        and response.get("dry_run") is True
        and response.get("write_committed") is False
        and isinstance(plan, dict)
        and plan.get("would_write") is False
    )
    if live_request_waiting_for_server:
        return LEDGER_WAITING_FOR_SERVER_GATE
    return str(response.get("status", "accepted"))


def execute_capture(payload: dict[str, Any], cfg: Config, *, raw_payload_hash: str | None = None, require_existing_grocery_batch: bool = False) -> dict[str, Any]:
    planned = plan_capture(payload)
    if planned.get("status") == "rejected":
        if client_requests_write(payload):
            planned.update(dry_run=False, gates_missing=[])
            planned["plan"].update(would_write=False, side_effects=[])
        return planned
    route = exact_write_route(payload)
    # Disabling new writes must never resurrect an already-issued financial
    # preview. A correctly bound cancellation is a monotonic no-write action.
    if route == ("megan", "expense", "megan.expense_capture") and valid_expense_cancellation_request(payload):
        return execute_expense_capture(payload, cfg, planned)
    if not write_is_authorized(payload, cfg):
        return planned

    if route == ("aura", "grocery", "aura.grocery_capture"):
        return execute_grocery_capture(payload, cfg, planned, raw_payload_hash=raw_payload_hash, require_existing_batch=require_existing_grocery_batch)
    if route == ("aura", "reminder", "aura.reminder_capture"):
        return execute_reminder_capture(payload, cfg, planned)
    if route == ("megan", "expense", "megan.expense_capture"):
        return execute_expense_capture(payload, cfg, planned)
    return planned


def validate_payload(payload: Any, header_request_id: str) -> tuple[bool, int, dict[str, Any]]:
    if not isinstance(payload, dict):
        return False, HTTPStatus.BAD_REQUEST, {"status": "rejected", "error": "payload must be object"}
    if payload.get("event_type") != "mobile_capture.v1":
        return False, HTTPStatus.OK, {"status": "ignored", "event": payload.get("event_type", "unknown")}
    schema_version = payload.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        return False, HTTPStatus.BAD_REQUEST, {"status": "rejected", "error": "unsupported schema_version"}
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        return False, HTTPStatus.BAD_REQUEST, {"status": "rejected", "error": "missing or invalid request_id"}
    if not header_request_id:
        return False, HTTPStatus.BAD_REQUEST, {"status": "rejected", "error": "missing X-Request-ID"}
    if header_request_id != request_id:
        return False, HTTPStatus.CONFLICT, {"status": "rejected", "error": "X-Request-ID mismatch"}
    for field in ("route", "capture", "context", "source", "entities"):
        if field in payload and not isinstance(payload[field], dict):
            return False, HTTPStatus.BAD_REQUEST, {
                "status": "rejected",
                "error": f"{field} must be object",
            }
    capture = payload.get("capture") or {}
    for field in ("text", "raw_text", "modality"):
        if field in capture and not isinstance(capture[field], str):
            return False, HTTPStatus.BAD_REQUEST, {
                "status": "rejected",
                "error": f"capture.{field} must be string",
            }
    if not (capture.get("text") or "").strip():
        return True, HTTPStatus.OK, plan_capture(payload)
    return True, HTTPStatus.OK, {}


class MobileCaptureHandler(BaseHTTPRequestHandler):
    server_version = "HermesMobileCaptureStaging/0.1"

    @property
    def cfg(self) -> Config:
        return self.server.cfg  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            enabled_domains = sorted(self.cfg.write_domains & SUPPORTED_WRITE_DOMAINS) if self.cfg.write_enabled else []
            json_response(
                self,
                HTTPStatus.OK,
                {
                    "status": "ok",
                    "service": "mobile-capture-v1-staging",
                    "mode": "allowlisted-write" if enabled_domains else "dry-run",
                    "write_domains": enabled_domains,
                },
            )
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/webhooks/mobile-capture-v1":
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "unknown route"})
            return
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0 or length > MAX_BODY_BYTES:
            json_response(self, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"status": "rejected", "error": "invalid body size"})
            return
        raw = self.rfile.read(length)
        ok, reason = verify_hmac_v2(self.headers, raw, self.cfg.secret)
        if not ok:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"status": "rejected", "error": reason})
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            json_response(self, HTTPStatus.BAD_REQUEST, {"status": "rejected", "error": "invalid json"})
            return
        valid, status, response = validate_payload(payload, self.headers.get("X-Request-ID", ""))
        if not valid:
            json_response(self, status, response)
            return

        request_id = str(payload.get("request_id"))
        payload_hash = hashlib.sha256(raw).hexdigest()
        domain, _, _ = canonical_domain(payload)
        grocery_resume = False
        try:
            # Reminder has a durable operation ledger and a per-request OS lock;
            # slow model/network calls never hold the grocery/expense global lock.
            # No SQLite transaction is held across any network or helper call.
            reminder_route = exact_write_route(payload) == ("aura", "reminder", "aura.reminder_capture")
            with nullcontext() if reminder_route else CAPTURE_EXECUTION_LOCK:
                init_db(self.cfg.ledger_path)
                with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    row = conn.execute(
                        "SELECT payload_hash, status, response_json FROM mobile_capture_ledger WHERE request_id = ?",
                        (request_id,),
                    ).fetchone()
                    replacing_claim = False
                    if row:
                        old_hash, old_status, old_response = row
                        if old_hash != payload_hash:
                            conn.rollback()
                            json_response(self, HTTPStatus.CONFLICT, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
                            return
                        grocery_resume = domain == "aura.grocery_capture" and old_status in {"processing", "grocery_pending"}
                        if (domain == "aura.grocery_capture" and old_status in {"processing", "grocery_pending"}
                                and not write_is_authorized(payload, self.cfg)):
                            conn.rollback()
                            # Disabling new writes must not turn a partially
                            # dispatched grocery batch into a dry-run receipt.
                            pending = grocery_capture.GroceryPending(request_id, code="grocery_server_gate_disabled").response
                            pending = grocery_capture.preserve_pending_progress(pending, old_response)
                            json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, pending)
                            return
                        if old_status not in {LEDGER_WAITING_FOR_SERVER_GATE, "processing", "grocery_pending"}:
                            cached = json.loads(old_response)
                            cached = correct_legacy_grocery_clarification(payload, cached, old_status)
                            cached = current_cached_expense_preview(conn, cached)
                            # Keep complete previews and terminal no-write reasons
                            # intact. Only proven writes/runs become duplicate.
                            if (
                                cached.get("status") != "needs_confirmation"
                                and cached.get("write_committed") is True
                            ):
                                cached["status"] = "duplicate"
                            cached["duplicate"] = True
                            cached["display_message"] = cached.get("display_message") or "Ya estaba enviado ✅"
                            conn.commit()
                            json_response(self, HTTPStatus.OK, cached)
                            return
                        replacing_claim = True

                    ts = now_iso()
                    if replacing_claim:
                        conn.execute(
                            "UPDATE mobile_capture_ledger SET domain = ?, status = 'processing', response_json = CASE WHEN ? THEN response_json ELSE '{}' END, updated_at = ? WHERE request_id = ?",
                            (domain, grocery_resume, ts, request_id),
                        )
                    else:
                        conn.execute(
                            "INSERT INTO mobile_capture_ledger(request_id, payload_hash, domain, status, response_json, created_at, updated_at) VALUES (?, ?, ?, 'processing', '{}', ?, ?)",
                            (request_id, payload_hash, domain, ts, ts),
                        )
                    conn.commit()

                response = response or execute_capture(payload, self.cfg, raw_payload_hash=payload_hash, require_existing_grocery_batch=grocery_resume)
                response.setdefault("request_id", request_id)
                response.setdefault("domain", domain)
                ts = now_iso()
                ledger_status = ledger_status_for_response(payload, response)
                response_json = json.dumps(response, ensure_ascii=False)
                with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    cursor = conn.execute(
                        "UPDATE mobile_capture_ledger SET domain = ?, status = ?, response_json = ?, updated_at = ? WHERE request_id = ? AND payload_hash = ?",
                        (domain, ledger_status, response_json, ts, request_id, payload_hash),
                    )
                    if cursor.rowcount != 1:
                        raise WriteExecutionError("request_claim_missing")
                    conn.commit()
        except grocery_capture.GroceryConflict:
            # The batch already owns a different body. Remove only this
            # conflicting, uncommitted transport claim; retain its batch/items.
            with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                conn.execute(
                    "DELETE FROM mobile_capture_ledger WHERE request_id=? AND payload_hash=? AND status='processing' AND domain='aura.grocery_capture'",
                    (request_id, payload_hash),
                )
                conn.commit()
            json_response(self, HTTPStatus.CONFLICT, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
            return
        except grocery_capture.GroceryPending as error:
            # The frozen batch and item journal are authoritative. Keep the
            # parent claim so a changed body cannot replace a partial operation.
            with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                previous = conn.execute("SELECT response_json FROM mobile_capture_ledger WHERE request_id=? AND payload_hash=?", (request_id, payload_hash)).fetchone()
                pending = grocery_capture.preserve_pending_progress(error.response, previous[0] if previous else "{}")
                conn.execute(
                    "UPDATE mobile_capture_ledger SET status='grocery_pending',response_json=?,updated_at=? WHERE request_id=? AND payload_hash=? AND status IN ('processing','grocery_pending')",
                    (json.dumps(pending, ensure_ascii=False), now_iso(), request_id, payload_hash),
                )
                conn.commit()
            json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, pending)
            return
        except WriteExecutionError as error:
            # A processing request may safely be retried: expense
            # operation ledgers are authoritative across uncertain
            # outcomes and prevent a second POST. Other adapters retain their
            # existing request-id idempotency contracts.
            with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                conn.execute(
                    "DELETE FROM mobile_capture_ledger WHERE request_id = ? AND payload_hash = ? AND status = 'processing' AND domain NOT IN ('aura.reminder_capture','aura.grocery_capture')",
                    (request_id, payload_hash),
                )
                conn.commit()
            json_response(
                self,
                HTTPStatus.BAD_GATEWAY,
                {
                    "status": "error",
                    "error": ("reminder_configuration_pending" if str(error) == "reminder_configuration_pending" else "reminder_pending_retry") if reminder_route else "write_adapter_failed",
                    "request_id": request_id,
                    "domain": domain,
                },
            )
            return
        json_response(self, HTTPStatus.OK, response)


def run_server(cfg: Config) -> None:
    init_db(cfg.ledger_path)
    server = ThreadingHTTPServer((cfg.host, cfg.port), MobileCaptureHandler)
    server.cfg = cfg  # type: ignore[attr-defined]
    print(f"mobile-capture-v1 staging server listening on http://{cfg.host}:{cfg.port}")
    server.serve_forever()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=os.getenv("MOBILE_CAPTURE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("MOBILE_CAPTURE_PORT", "8650")))
    parser.add_argument("--secret-file", type=Path, default=Path(os.getenv("MOBILE_CAPTURE_SECRET_FILE", str(DEFAULT_SECRET_FILE))))
    parser.add_argument("--ledger", type=Path, default=Path(os.getenv("MOBILE_CAPTURE_LEDGER", str(DEFAULT_LEDGER_PATH))))
    parser.add_argument("--write-enabled", action="store_true", default=env_flag("MOBILE_CAPTURE_WRITE_ENABLED"))
    parser.add_argument("--write-domains", default=os.getenv("MOBILE_CAPTURE_WRITE_DOMAINS", ""))
    parser.add_argument("--aura-helper", type=Path, default=Path(os.getenv("MOBILE_CAPTURE_AURA_HELPER", "/root/.hermes/scripts/apple_watch_capture.py")))
    parser.add_argument(
        "--expense-helper",
        type=Path,
        default=Path(
            os.getenv(
                "MOBILE_CAPTURE_EXPENSE_HELPER",
                str(Path(__file__).resolve().parent / "scripts" / "mobile_expense_capture.py"),
            )
        ),
    )
    parser.add_argument("--reminder-openai-key-file", type=Path, default=os.getenv("MOBILE_CAPTURE_REMINDER_OPENAI_KEY_FILE"))
    parser.add_argument("--reminder-todoist-token-file", type=Path, default=os.getenv("MOBILE_CAPTURE_REMINDER_TODOIST_TOKEN_FILE"))
    parser.add_argument("--reminder-todoist-credentials-file", type=Path, default=os.getenv("MOBILE_CAPTURE_REMINDER_TODOIST_CREDENTIALS_FILE"))
    parser.add_argument("--reminder-todoist-inbox-id", default=os.getenv("MOBILE_CAPTURE_REMINDER_TODOIST_INBOX_ID", ""))
    parser.add_argument("--reminder-budget-path", type=Path, default=os.getenv("MOBILE_CAPTURE_REMINDER_BUDGET_PATH"))
    parser.add_argument("--reminder-monthly-limit-usd", default=os.getenv("MOBILE_CAPTURE_REMINDER_MONTHLY_LIMIT_USD", "0"))
    parser.add_argument("--reminder-provider", default=os.getenv("MOBILE_CAPTURE_REMINDER_PROVIDER", "openai"))
    parser.add_argument("--reminder-model", default=os.getenv("MOBILE_CAPTURE_REMINDER_MODEL", "gpt-6-luna"))
    parser.add_argument("--reminder-ai-timeout", type=float, default=float(os.getenv("MOBILE_CAPTURE_REMINDER_AI_TIMEOUT", "8")))
    parser.add_argument("--reminder-todoist-timeout", type=float, default=float(os.getenv("MOBILE_CAPTURE_REMINDER_TODOIST_TIMEOUT", "5")))
    args = parser.parse_args()
    if not 0.1 <= args.reminder_ai_timeout <= 30 or not 0.1 <= args.reminder_todoist_timeout <= 15:
        parser.error("Reminder timeouts must be bounded: AI 0.1–30s; Todoist 0.1–15s")
    return args


if __name__ == "__main__":
    args = parse_args()
    domains = frozenset(value.strip() for value in args.write_domains.split(",") if value.strip())
    run_server(
        Config(
            secret=load_secret(args.secret_file),
            ledger_path=args.ledger,
            host=args.host,
            port=args.port,
            write_enabled=args.write_enabled,
            write_domains=domains,
            aura_helper=args.aura_helper,
            expense_helper=args.expense_helper,
            reminder_openai_key_file=args.reminder_openai_key_file,
            reminder_todoist_token_file=args.reminder_todoist_token_file,
            reminder_todoist_credentials_file=args.reminder_todoist_credentials_file,
            reminder_todoist_inbox_id=args.reminder_todoist_inbox_id,
            reminder_budget_path=args.reminder_budget_path,
            reminder_monthly_limit_usd=args.reminder_monthly_limit_usd,
            reminder_provider=args.reminder_provider,
            reminder_model=args.reminder_model,
            reminder_ai_timeout=args.reminder_ai_timeout,
            reminder_todoist_timeout=args.reminder_todoist_timeout,
        )
    )
