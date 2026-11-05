#!/usr/bin/env python3
"""Sign and POST Personal Watch App mobile_capture.v1 fixtures to the local gateway.

Uses generic webhook HMAC V2:
  X-Webhook-Timestamp: <unix seconds>
  X-Webhook-Signature-V2: HMAC_SHA256(secret, f"{timestamp}.{raw_body}")

Never prints the secret.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fixture", help="Path to JSON fixture")
    parser.add_argument("--url", default="http://localhost:8644/webhooks/mobile-capture-v1")
    default_secret = Path(os.getenv("HERMES_HOME", str(Path.home() / ".hermes"))) / "mobile-capture-v1-secret.txt"
    parser.add_argument("--secret-file", default=str(default_secret))
    parser.add_argument("--invalid-signature", action="store_true", help="Deliberately send an invalid signature")
    parser.add_argument("--canonical", action="store_true", help="Canonicalize JSON before sending")
    parser.add_argument("--header-request-id", help="Override X-Request-ID for mismatch/conflict tests")
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    secret_path = Path(args.secret_file)
    if not fixture_path.exists():
        print(f"fixture not found: {fixture_path}", file=sys.stderr)
        return 2
    if not secret_path.exists():
        print(f"secret file not found: {secret_path}", file=sys.stderr)
        return 2

    raw = fixture_path.read_bytes()
    if args.canonical:
        payload = json.loads(raw.decode("utf-8"))
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    else:
        payload = json.loads(raw.decode("utf-8"))

    secret = secret_path.read_text(encoding="utf-8").strip().encode("utf-8")
    ts = str(int(time.time()))
    signed = ts.encode("utf-8") + b"." + raw
    signature = hmac.new(secret, signed, hashlib.sha256).hexdigest()
    if args.invalid_signature:
        signature = "0" * 64

    request_id = args.header_request_id if args.header_request_id is not None else payload.get("request_id", "")
    source = payload.get("source") or {}
    schema_version = payload.get("schema_version", "")
    platform = source.get("platform", "unknown")
    app_version = source.get("app_version", "dev")
    device_id = source.get("device_id", "unknown-device")
    client = f"HermesCapture/{platform}/{app_version}"
    cmd = [
        "curl",
        "-sS",
        "-w",
        "\nHTTP_STATUS:%{http_code}\n",
        "-X",
        "POST",
        args.url,
        "-H",
        "Content-Type: application/json",
        "-H",
        f"X-Webhook-Timestamp: {ts}",
        "-H",
        f"X-Webhook-Signature-V2: {signature}",
        "-H",
        f"X-Request-ID: {request_id}",
        "-H",
        f"X-Hermes-Payload-Version: {schema_version}",
        "-H",
        f"X-Hermes-Client: {client}",
        "-H",
        f"X-Hermes-Device-ID: {device_id}",
        "--data-binary",
        "@-",
    ]
    proc = subprocess.run(cmd, input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.stderr:
        sys.stderr.write(proc.stderr.decode("utf-8", errors="replace"))
    sys.stdout.write(proc.stdout.decode("utf-8", errors="replace"))
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
