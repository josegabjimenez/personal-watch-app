"""Exact expense routing for the sealed Mercado/Recordatorios HTTP server."""
from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import expense_engine

DOMAIN = "megan.expense_capture"
ROUTE = ("megan", "expense", DOMAIN)


def route_get(handler, legacy, api):
    cfg = handler.cfg
    if handler.path != "/health" or not cfg.write_enabled or DOMAIN not in cfg.write_domains:
        return legacy()
    supported = api["SUPPORTED_WRITE_DOMAINS"] | {"aura.reminder_capture", DOMAIN}
    return api["json_response"](handler, 200, {
        "status": "ok",
        "service": "mobile-capture-v1-staging",
        "mode": "allowlisted-write",
        "write_domains": sorted(cfg.write_domains & supported),
    })


def route_post(handler, legacy, api):
    if handler.path != "/webhooks/mobile-capture-v1":
        return legacy()
    try:
        length = int(handler.headers.get("Content-Length") or "0")
    except (TypeError, ValueError):
        return legacy()
    if not 0 < length <= api["MAX_BODY_BYTES"]:
        return legacy()

    original = handler.rfile
    raw = original.read(length)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        payload = None
    if not isinstance(payload, dict) or expense_engine.exact_write_route(payload) != ROUTE:
        handler.rfile = io.BytesIO(raw)
        try:
            return legacy()
        finally:
            handler.rfile = original

    # Route selection has no authority. The existing expense HTTP handler
    # verifies the untouched signed body and owns both durable SQLite claims.
    cfg = handler.cfg
    expense_cfg = expense_engine.Config(
        secret=cfg.secret,
        ledger_path=cfg.ledger_path,
        host=cfg.host,
        port=cfg.port,
        write_enabled=cfg.write_enabled,
        write_domains=cfg.write_domains & {DOMAIN},
        expense_helper=Path(__file__).with_name("mobile_expense_capture.py"),
    )
    request = SimpleNamespace(
        path=handler.path,
        headers=handler.headers,
        rfile=io.BytesIO(raw),
        wfile=handler.wfile,
        cfg=expense_cfg,
        send_response=handler.send_response,
        send_header=handler.send_header,
        end_headers=handler.end_headers,
    )
    return expense_engine.MobileCaptureHandler.do_POST(request)
