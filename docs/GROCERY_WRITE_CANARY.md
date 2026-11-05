# Aura grocery write canary

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Date:** 2026-08-08

**Candidate:** Hermes Capture 0.3.3 (6)

**Write scope:** `aura` + `grocery` + `aura.grocery_capture`

## Authorization

The user explicitly authorized creating one uniquely identifiable temporary item in the official Notion grocery list, reading it back, verifying idempotency, archiving it, and enabling writes only for Mercado if the complete canary passed.

## Server-side controls

The BFF requires all of the following before invoking the existing Aura helper:

- valid HMAC V2 over the exact raw body;
- matching `X-Request-ID` and body `request_id`;
- client request context `dry_run == false` and `allow_write == true`;
- server global write switch enabled;
- exact server allowlist match for agent `aura`, intent `grocery`, and domain `aura.grocery_capture`;
- deterministic helper preflight classified as `grocery` or `grocery_done`;
- SQLite request-id idempotency decision plus the helper's event-id idempotency.

Client flags express intent but cannot authorize a write. Server configuration remains the authority.

## Sanitized canary evidence

A temporary item with a random canary suffix was sent through the signed BFF endpoint. Private endpoint, request ID, Notion block ID, credentials, and item suffix are intentionally omitted.

```text
Initial HTTP response:          200
BFF status:                     committed
Response dry_run:               false
Response write_committed:       true
Request correlation:            passed
Domain correlation:             passed
Notion read-after-write:         passed
Same-request replay cached:      passed
Exactly one matching block:      passed
Archive operation:               passed
Notion read-after-archive:       archived == true
Active temporary items remain:   no
```

The canary therefore completed the required lifecycle:

```text
create → read/verify → duplicate replay → exactly-once check → archive → read/verify cleanup
```

## Negative controls

Signed live-style requests were also submitted with client `allow_write == true` for non-allowlisted domains. All remained dry-run:

| Domain | `dry_run` | `plan.would_write` | `write_committed` |
|---|---:|---:|---:|
| `megan.expense_capture` | `true` | `false` | `false` |
| `aura.reminder_capture` | `true` | `false` | `false` |
| `argos.general_capture` | `true` | `false` | `false` |

## Client delivery semantics

For a live Mercado request, the client marks an outbox item sent only when all bindings match and the response explicitly contains:

```text
response.request_id == payload.request_id
response.domain == payload.route.domain
response.dry_run == false
response.plan.would_write == true
response.write_committed == true
```

Dry-run responses retain their stricter existing contract. Live writes do not use Watch-to-iPhone fallback; a transient direct-network failure leaves the durable Watch outbox item failed and available for direct retry with the same request ID.

## Result

**PASSED — server-side writes enabled only for Mercado.**

Megan expenses, Aura reminders, general capture, Home Assistant, Calendar, and every unknown domain remain non-writing. Physical paired-device smoke testing of 0.3.3 (6) is still required before calling the new client build user-verified.
