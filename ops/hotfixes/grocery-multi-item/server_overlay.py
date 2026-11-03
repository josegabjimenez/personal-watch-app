"""Named source overlay for the exact deployed historical server.

Only these two function bodies and `import grocery_capture` enter the release.
This file is review material; it does not start a server or invoke a helper.
"""
from __future__ import annotations

import grocery_capture


def execute_capture(payload: dict[str, Any], cfg: Config, *, raw_payload_hash: str | None = None, require_existing_grocery_batch: bool = False) -> dict[str, Any]:
    planned = plan_capture(payload)
    if not write_is_authorized(payload, cfg):
        return planned
    domain, _, _ = canonical_domain(payload)
    if domain != "aura.grocery_capture":
        return planned
    return grocery_capture.execute(payload, cfg, raw_payload_hash=raw_payload_hash, require_existing_batch=require_existing_grocery_batch)


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
    require_existing_grocery_batch = False
    try:
        # The parent binds exact request bytes before any helper/Notion call.
        # The frozen grocery batch and per-item journal arbitrate every retry.
        with CAPTURE_EXECUTION_LOCK:
            init_db(self.cfg.ledger_path)
            with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT payload_hash,status,response_json FROM mobile_capture_ledger WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if row:
                    old_hash, old_status, old_response = row
                    if old_hash != payload_hash:
                        conn.rollback()
                        json_response(self, HTTPStatus.CONFLICT, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
                        return
                    if old_status not in {"processing", "grocery_pending"}:
                        # Preserve the deployed historical cache behavior,
                        # including its exact no-write clarification correction.
                        cached = json.loads(old_response)
                        clarified = correct_legacy_grocery_clarification(payload, cached, old_status)
                        if clarified is cached:
                            cached["status"] = "duplicate"
                        else:
                            cached = clarified
                        cached["duplicate"] = True
                        cached["display_message"] = cached.get("display_message") or "Ya estaba enviado ✅"
                        conn.rollback()
                        json_response(self, HTTPStatus.OK, cached)
                        return
                    require_existing_grocery_batch = domain == "aura.grocery_capture"
                    if require_existing_grocery_batch and not write_is_authorized(payload, self.cfg):
                        conn.rollback()
                        pending = grocery_capture.GroceryPending(request_id, code="grocery_server_gate_disabled").response
                        pending = grocery_capture.preserve_pending_progress(pending, old_response)
                        json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, pending)
                        return
                else:
                    ts = now_iso()
                    conn.execute(
                        "INSERT INTO mobile_capture_ledger(request_id,payload_hash,domain,status,response_json,created_at,updated_at) VALUES (?,?,?,'processing','{}',?,?)",
                        (request_id, payload_hash, domain, ts, ts),
                    )
                conn.commit()
            # sqlite3's context manager does not close its connection itself.
            # Explicit close guarantees no SQLite connection/transaction remains
            # across a helper or Notion call.
            conn.close()

            response = response or execute_capture(payload, self.cfg, raw_payload_hash=payload_hash,
                                                   require_existing_grocery_batch=require_existing_grocery_batch)
            response.setdefault("request_id", request_id)
            response.setdefault("domain", domain)
            with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
                cursor = conn.execute(
                    "UPDATE mobile_capture_ledger SET domain=?,status=?,response_json=?,updated_at=? WHERE request_id=? AND payload_hash=? AND status IN ('processing','grocery_pending')",
                    (domain, str(response.get("status", "accepted")), json.dumps(response, ensure_ascii=False), now_iso(), request_id, payload_hash),
                )
                if cursor.rowcount != 1:
                    raise WriteExecutionError("request_claim_missing")
                conn.commit()
            conn.close()
    except grocery_capture.GroceryConflict:
        # A restored/missing mobile ledger must not let a new wrong-body claim
        # mask the original batch identity. Delete only this conflicting claim;
        # the authoritative batch and item journal are never changed here.
        with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
            conn.execute(
                "DELETE FROM mobile_capture_ledger WHERE request_id=? AND payload_hash=? AND status='processing' AND domain='aura.grocery_capture'",
                (request_id, payload_hash),
            )
            conn.commit()
        conn.close()
        json_response(self, HTTPStatus.CONFLICT, {"status": "rejected", "error": "request_id_conflict", "request_id": request_id})
        return
    except grocery_capture.GroceryPending as error:
        # Persist progress without deleting/replacing a partially dispatched
        # parent. An already-terminal concurrent result must not be downgraded.
        with sqlite3.connect(self.cfg.ledger_path, timeout=65.0) as conn:
            previous = conn.execute(
                "SELECT response_json FROM mobile_capture_ledger WHERE request_id=? AND payload_hash=?",
                (request_id, payload_hash),
            ).fetchone()
            pending = grocery_capture.preserve_pending_progress(error.response, previous[0] if previous else "{}")
            conn.execute(
                "UPDATE mobile_capture_ledger SET status='grocery_pending',response_json=?,updated_at=? WHERE request_id=? AND payload_hash=? AND status IN ('processing','grocery_pending')",
                (json.dumps(pending, ensure_ascii=False), now_iso(), request_id, payload_hash),
            )
            conn.commit()
        conn.close()
        json_response(self, HTTPStatus.SERVICE_UNAVAILABLE, pending)
        return
    except WriteExecutionError:
        # An uncertain claim remains reserved. This overlay never deletes a
        # grocery parent merely because a transport or persistence step failed.
        json_response(self, HTTPStatus.BAD_GATEWAY, {
            "status": "error", "error": "write_adapter_failed", "request_id": request_id, "domain": domain,
        })
        return
    json_response(self, HTTPStatus.OK, response)
