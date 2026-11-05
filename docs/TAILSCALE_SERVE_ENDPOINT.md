# Tailscale Serve endpoint — Hermes Mobile Capture BFF

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Configured:** 2026-07-06 23:16 America/Bogota  
**Mode:** tailnet-only HTTPS via Tailscale Serve, not public Funnel.

## What BFF means

**BFF = Backend for Frontend.**

In this project it means a small backend endpoint designed specifically for the Watch/iPhone frontend. It sits between the native app and Hermes/agents.

Responsibilities:

- receive Watch/iPhone requests synchronously;
- verify HMAC V2;
- require matching `X-Request-ID` and `body.request_id`;
- maintain an idempotency ledger;
- return short Watch-friendly JSON immediately;
- keep Firefly/Notion/Calendar/Home Assistant writes disabled until server-side domain gates pass.

It is different from the generic Hermes webhook adapter, which is async and returns `202 accepted` while the agent runs in background.

## Local BFF

```text
http://127.0.0.1:8650
```

Health:

```http
GET http://127.0.0.1:8650/health
```

Capture:

```http
POST http://127.0.0.1:8650/webhooks/mobile-capture-v1
```

## Tailscale HTTPS endpoint

Configured with:

```bash
tailscale serve --bg --yes --https=8650 http://127.0.0.1:8650
```

Reachable inside Jose's tailnet at:

```text
https://<TAILSCALE_DNS_NAME>:8650
```

Use the MagicDNS hostname, **not** the `100.x.y.z` Tailscale IP. Tailscale Serve's TLS certificate is issued for the DNS name; connecting by IP causes hostname validation to fail in `URLSession`.

Health:

```http
GET https://<TAILSCALE_DNS_NAME>:8650/health
```

Capture endpoint for iOS/watchOS dev builds:

```http
POST https://<TAILSCALE_DNS_NAME>:8650/webhooks/mobile-capture-v1
```

## Verification performed

```bash
curl -fsS https://<TAILSCALE_DNS_NAME>:8650/health
```

Result:

```json
{"status":"ok","service":"mobile-capture-v1-staging","mode":"dry-run"}
```

Signed HMAC V2 fixture POST to the HTTPS endpoint returned:

```json
{
  "status": "accepted",
  "dry_run": true,
  "domain": "megan.expense_capture",
  "display_message": "Dry-run Megan $45.000 COP",
  "plan": {
    "side_effects": ["firefly.plan_only"],
    "would_write": false,
    "amount_cop": 45000,
    "transaction_kind": "cash_expense"
  }
}
```

HTTP status:

```text
200
```

## Current Serve status

```text
https://<TAILSCALE_DNS_NAME>:8650/
└── proxy http://127.0.0.1:8650
```

The local BFF is supervised by an enabled host service with automatic restart on failure. A Serve `502` means the local `127.0.0.1:8650` upstream is unavailable and should be checked before debugging the Apple client.

Existing services preserved:

```text
:443  → http://127.0.0.1:8644
:9119 → http://<TAILSCALE_DNS_NAME>:9120
```

## Disable command

If needed:

```bash
tailscale serve --https=8650 off
```

## Apple device note

This endpoint is **tailnet-only**. The iPhone/Mac used for development must be connected to Jose's Tailscale tailnet. Physical Apple Watch reachability must be tested because watchOS network behavior can differ depending on whether it is relaying through the paired iPhone, on Wi-Fi, or away from the phone.

For MVP development, use this **base URL** in the iPhone configuration screen:

```text
https://<TAILSCALE_DNS_NAME>:8650
```

The sender will append `/webhooks/mobile-capture-v1`; the health test appends `/health`.
