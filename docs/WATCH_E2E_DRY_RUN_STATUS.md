# Watch end-to-end dry-run delivery

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Status:** verified end-to-end on Jose's paired iPhone + Apple Watch simulators.

## Verified result — 2026-07-17 America/Bogota

Jose completed the Watch expense flow with:

```text
45 mil en Uber
```

The Watch displayed the synchronous BFF response:

```text
Dry-run Megan $45.000 COP
```

This verifies the real simulator path:

```text
Watch UI → local outbox → Watch Keychain secret → HMAC V2 →
Tailscale Serve HTTPS → synchronous BFF → display_message
```

No Firefly write occurred; the BFF response remained `dry_run: true` and `would_write: false`.

## User flow

```text
Watch quick action
  → system text input/dictation
  → create CapturePayloadV1
  → persist to file outbox
  → mark sending + increment attempts
  → load endpoint from Watch preferences
  → load route secret from Watch Keychain
  → encode exact JSON bytes
  → HMAC V2(timestamp + "." + raw_body)
  → HTTPS POST /webhooks/mobile-capture-v1
  → validate synchronous BFF response
  → mark sent or failed
  → show display_message + haptic
```

The outbox write happens before configuration lookup or network activity.

## Success behavior

A successful BFF response must satisfy:

```text
dry_run == true
plan.would_write != true
```

The Watch then marks the item `sent` and displays the server-provided `display_message`, such as:

```text
Dry-run Megan $45.000 COP
Recordatorio dry-run ✅
Mercado dry-run ✅
Captura validada · dry-run ✅
```

## Failure behavior

The item remains durable and becomes `failed`. The outbox stores only sanitized codes:

```text
http_401
http_<status>
network_<code>
invalid_response
unsafe_response
send_failed
```

It never stores response bodies, secrets, signatures, or private URLs in `last_error`.

User-facing messages remain short. HTTP 401 specifically asks the user to provision again from iPhone.

## Retry behavior

The Watch home screen includes **Reintentar pendientes**. It retries up to ten deliverable items per tap, excluding:

- items already marked `sent`;
- items that reached five attempts.

The same original payload and `request_id` are reused. Server idempotency therefore turns uncertain repeated delivery into `duplicate`, not a second domain action.

## Verification

HermesCore tests cover:

- successful signed delivery → `sent`;
- required HMAC/request headers;
- HTTP 401 → `failed`;
- sanitized `http_401` persistence with no response-body leakage;
- failed item remains retryable;
- existing contract, endpoint, HMAC-vector, and outbox tests.

## Simulator checklist

1. Pull and regenerate the Xcode project.
2. Confirm iPhone and Watch remain paired.
3. Confirm Watch says **Configuración segura lista**.
4. Open **Gasto** and enter `45 mil en Uber`.
5. Tap **Enviar**.
6. Expect `Dry-run Megan $45.000 COP`.
7. Repeat with Mercado or Captura.
8. If network is intentionally unavailable, expect a saved-for-retry message; reconnect and tap **Reintentar pendientes**.

## Write safety

This phase does not enable external writes. Payload flags remain `dry_run: true` and `allow_write: false`, and the server remains authoritative. Firefly, Notion, Calendar, Home Assistant, and other external systems are unchanged.
