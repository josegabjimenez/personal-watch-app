"""Frozen grocery batches; terminal success requires every durable item receipt."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Any

from grocery_helper_adapter import GroceryAdapter, GroceryAdapterError, child_operation_id
from grocery_item_parser import GroceryParseError, MAX_ITEMS, parse_grocery_items


DOMAIN = "aura.grocery_capture"
PLAN_VERSION = 1


class GroceryPending(RuntimeError):
    def __init__(self, request_id: str, completed: int | None = None, total: int | None = None, code: str = "grocery_pending_review"):
        super().__init__(code)
        self.response = {
            "status": "needs_reconciliation", "error": code,
            "request_id": request_id, "domain": DOMAIN,
            "display_message": "Mercado pendiente de revisión · conservé el envío",
        }
        if completed is not None and total is not None:
            self.response.update(completed_count=completed, total_count=total,
                display_message=f"Mercado: {completed} de {total} confirmados · envío pendiente de revisión")


class GroceryConflict(RuntimeError):
    pass


def preserve_pending_progress(response: dict, previous_json: str) -> dict:
    """Retain a correlated last-known count if durable item state is missing."""
    merged = dict(response)
    try:
        previous = json.loads(previous_json)
        completed, total = previous["completed_count"], previous["total_count"]
        if (previous.get("status") == "needs_reconciliation" and previous.get("request_id") == response["request_id"]
                and previous.get("domain") == DOMAIN and type(completed) is int and type(total) is int
                and 0 <= completed <= total <= MAX_ITEMS
                and ("completed_count" not in merged or
                     (merged.get("total_count") == total and completed > merged["completed_count"]))):
            merged.update(completed_count=completed, total_count=total,
                display_message=f"Mercado: {completed} de {total} confirmados · envío pendiente de revisión")
    except (ValueError, KeyError, TypeError):
        pass
    return merged


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def journal_path(ledger: Path) -> Path:
    return Path(str(ledger) + ".grocery.sqlite3")


@contextmanager
def connection(path: Path):
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA synchronous=FULL")
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Creation is private even when the caller has a permissive umask.
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    with connection(path) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS grocery_batches (
            request_id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL,
            plan_json TEXT NOT NULL, plan_hash TEXT NOT NULL,
            response_json TEXT, status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )""")


def safe_request(payload: dict) -> bool:
    route, context = payload.get("route"), payload.get("context")
    return (
        isinstance(route, dict) and isinstance(context, dict)
        and (route.get("agent"), route.get("intent"), route.get("domain")) == ("aura", "grocery", DOMAIN)
        and context.get("dry_run") is False and context.get("allow_write") is True
        and context.get("allow_firefly_write") is False
        and all(context.get(field, False) is False for field in ("user_confirmation", "user_cancellation", "requires_confirmation"))
        and context.get("confirmation_token") is None and context.get("operation_id") is None
        and isinstance(payload.get("request_id"), str) and bool(payload["request_id"].strip())
    )


def no_write(payload: dict, message: str, question: str | None = None) -> dict:
    return {
        "status": "needs_input" if question else "rejected", "dry_run": False,
        "write_committed": False, "request_id": payload.get("request_id"), "domain": DOMAIN,
        "display_message": message, "question": question,
        "plan": {"would_write": False, "side_effects": [], "notes": "No grocery item was dispatched."},
        "gates_missing": [],
    }


def load_batch(path: Path, request_id: str, payload_hash: str) -> tuple[dict, dict | None] | None:
    with connection(path) as conn:
        row = conn.execute(
            "SELECT payload_hash,plan_json,plan_hash,response_json,status FROM grocery_batches WHERE request_id=?",
            (request_id,),
        ).fetchone()
    if row is None:
        return None
    if row[0] != payload_hash:
        raise GroceryConflict("request_id_conflict")
    try:
        plan = json.loads(row[1])
        if sha(canonical(plan)) != row[2] or type(plan["version"]) is not int or plan["version"] != PLAN_VERSION:
            raise ValueError("invalid frozen plan")
        if row[4] in {"preflight", "no_write"}:
            if plan != {"version": PLAN_VERSION, "phase": "preflight"}:
                raise ValueError("invalid preflight marker")
            if row[4] == "preflight":
                if row[3] is not None:
                    raise ValueError("preflight has an unexpected receipt")
                return plan, None
            response = json.loads(row[3])
            if (response.get("request_id") != request_id or response.get("domain") != DOMAIN
                    or response.get("status") not in {"needs_input", "rejected"}
                    or response.get("dry_run") is not False or response.get("write_committed") is not False
                    or response.get("plan", {}).get("would_write") is not False):
                raise ValueError("invalid terminal no-write receipt")
            return plan, response
        items = plan["prepared"]["items"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS:
            raise ValueError("invalid frozen items")
        operations = [child_operation_id(request_id, item) for item in items]
        if len(set(operations)) != len(operations):
            raise ValueError("duplicate child identity")
        if row[4] == "committed":
            response = json.loads(row[3])
            if response["request_id"] != request_id or response["domain"] != DOMAIN:
                raise ValueError("invalid durable response")
            if response["status"] not in {"committed", "duplicate"} or response["dry_run"] is not False:
                raise ValueError("invalid durable status")
            if type(response["write_committed"]) is not bool or response["write_committed"] is not response["plan"]["would_write"]:
                raise ValueError("invalid durable write flags")
            return plan, response
        if row[4] != "pending":
            raise ValueError("invalid batch state")
        return plan, None
    except (KeyError, TypeError, ValueError, GroceryAdapterError) as error:
        raise GroceryPending(request_id, code="grocery_batch_state_invalid") from error


def replay_response(plan: dict, response: dict) -> dict:
    if "prepared" not in plan:
        return dict(response, duplicate=True)
    return dict(response, status="duplicate", duplicate=True,
                display_message=f"Mercado: {len(plan['prepared']['items'])} artículos confirmados · sin repetir envío")


def store_no_write(path: Path, request_id: str, payload_hash: str, response: dict):
    # A refusal may win only before another worker freezes/dispatched the plan.
    with connection(path) as conn:
        conn.execute("UPDATE grocery_batches SET status='no_write',response_json=?,updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND payload_hash=? AND status='preflight'",
                     (canonical(response), request_id, payload_hash))
    return load_batch(path, request_id, payload_hash)


def execute(payload: dict, cfg: Any, *, raw_payload_hash: str | None = None, require_existing_batch: bool = False) -> dict:
    if not safe_request(payload):
        return no_write(payload, "Solicitud de Mercado no válida · no cambié nada")
    request_id = payload["request_id"]
    body_hash = raw_payload_hash or sha(canonical(payload))
    path = journal_path(cfg.ledger_path)
    try:
        if require_existing_batch:
            if not path.is_file():
                raise GroceryPending(request_id, code="grocery_journal_missing")
            existing = load_batch(path, request_id, body_hash)
            if existing is None:
                raise GroceryPending(request_id, code="grocery_batch_missing")
        else:
            init_db(path)
            existing = load_batch(path, request_id, body_hash)
            if existing is None:
                marker = canonical({"version": PLAN_VERSION, "phase": "preflight"})
                with connection(path) as conn:
                    conn.execute("INSERT OR IGNORE INTO grocery_batches(request_id,payload_hash,plan_json,plan_hash,status) VALUES (?,?,?,?, 'preflight')",
                                 (request_id, body_hash, marker, sha(marker)))
                existing = load_batch(path, request_id, body_hash)
        if existing is None:
            raise GroceryPending(request_id, code="grocery_batch_missing")
        if existing[1] is not None:
            return replay_response(*existing)
        if existing[0].get("phase") == "preflight":
            try:
                items = parse_grocery_items(str((payload.get("capture") or {}).get("text") or ""))
            except GroceryParseError as error:
                response = no_write(payload, error.message, None if error.code in {"unsupported_action", "unsafe_instruction"} else error.question)
                existing = store_no_write(path, request_id, body_hash, response)
                if existing is not None and existing[1] is not None:
                    return replay_response(*existing)
        if getattr(cfg, "aura_helper", None) is None:
            raise GroceryPending(request_id, code="grocery_adapter_unavailable")
        adapter = GroceryAdapter(cfg.aura_helper, path)
        if existing is not None and existing[0].get("phase") == "preflight":
            try:
                prepared = adapter.prepare(items)
            except GroceryAdapterError as error:
                if getattr(error, "needs_input", False):
                    existing = store_no_write(path, request_id, body_hash, no_write(payload, error.message, error.message))
                else:
                    existing = load_batch(path, request_id, body_hash)
                if existing is not None and existing[1] is not None:
                    return replay_response(*existing)
                if existing is not None and existing[0].get("phase") == "preflight":
                    raise GroceryPending(request_id, 0, len(items), "grocery_preflight_unavailable") from error
            else:
                plan = {"version": PLAN_VERSION, "prepared": prepared}
                plan_json = canonical(plan)
                # Registration commits before the frozen-plan CAS. Only its
                # winner can enter dispatch; orphan prepared rows never write.
                adapter.register_operations([(child_operation_id(request_id, item), item) for item in prepared["items"]])
                with connection(path) as conn:
                    conn.execute("UPDATE grocery_batches SET plan_json=?,plan_hash=?,status='pending',updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND payload_hash=? AND status='preflight'",
                                 (plan_json, sha(plan_json), request_id, body_hash))
                existing = load_batch(path, request_id, body_hash)
        if existing is None:
            raise GroceryPending(request_id, code="grocery_batch_missing")
        plan, completed_response = existing
        if completed_response is not None:
            return replay_response(plan, completed_response)
        if plan.get("phase") == "preflight":
            raise GroceryPending(request_id, code="grocery_preflight_unavailable")
        receipts = []
        items = plan["prepared"]["items"]
        for item in items:
            operation_id = child_operation_id(request_id, item)
            try:
                result = adapter.status(operation_id, item)
                if isinstance(result, dict) and result.get("code") == "not_started":
                    raise GroceryPending(request_id, len(receipts), len(items), "grocery_item_state_missing")
                if isinstance(result, dict) and result.get("status") == "prepared":
                    result = adapter.execute(operation_id, item)
            except (GroceryAdapterError, OSError, ValueError) as error:
                raise GroceryPending(request_id, len(receipts), len(items)) from error
            if not isinstance(result, dict) or result.get("status") != "committed":
                raise GroceryPending(request_id, len(receipts), len(items))
            receipt = result.get("receipt")
            if not isinstance(receipt, dict) or receipt.get("checked") is not False:
                raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_invalid")
            if (receipt.get("item") != item["expected_text"] or receipt.get("requested_item") != item["item"]
                    or receipt.get("destination") != plan["prepared"]["destination"]
                    or receipt.get("operation_id") != operation_id or receipt.get("plan_hash") != item["plan_hash"]
                    or receipt.get("action") != item["action"] or receipt.get("parent_id") != item["parent_id"]):
                raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_invalid")
            if not isinstance(receipt.get("block_id"), str) or not receipt["block_id"].strip():
                raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_invalid")
            if result.get("write_committed") is not True and result.get("write_committed") is not False:
                raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_invalid")
            if (receipt.get("action") in {"created", "updated"}) is not result["write_committed"]:
                raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_invalid")
            receipts.append(dict(receipt, write_committed=result["write_committed"]))
        ids = [receipt["block_id"] for receipt in receipts]
        if len(set(ids)) != len(ids):
            raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_identity_collision")
        counts = {action: sum(receipt.get("action") == action for receipt in receipts)
                  for action in ("created", "updated", "already_present")}
        if sum(counts.values()) != len(items):
            raise GroceryPending(request_id, len(receipts), len(items), "grocery_receipt_action_invalid")
        wrote = any(receipt["write_committed"] for receipt in receipts)
        response = {
            "status": "committed" if wrote else "duplicate", "dry_run": False, "write_committed": wrote,
            "request_id": request_id, "domain": DOMAIN, "duplicate": not wrote, "question": None,
            "display_message": f"Mercado: {len(items)} confirmados · {counts['created']} agregados · {counts['updated']} actualizados · {counts['already_present']} ya estaban",
            "plan": {"would_write": wrote, "side_effects": ["notion.official_grocery_list.write"] if wrote else [],
                     "notes": "Every frozen grocery item has a durable verified receipt."},
            "external_ids": {"notion_block_ids": ids, "grocery_items": receipts, "item_count": len(items), **counts},
            "gates_missing": [],
        }
        with connection(path) as conn:
            cursor = conn.execute("UPDATE grocery_batches SET response_json=?,status='committed',updated_at=CURRENT_TIMESTAMP WHERE request_id=? AND payload_hash=? AND plan_hash=?",
                                  (canonical(response), request_id, body_hash, sha(canonical(plan))))
            if cursor.rowcount != 1:
                raise GroceryPending(request_id, len(receipts), len(items), "grocery_batch_state_changed")
        return response
    except (sqlite3.Error, OSError, ValueError, GroceryAdapterError) as error:
        raise GroceryPending(request_id, code="grocery_state_unavailable") from error
