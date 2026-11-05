# Subagent synthesis — Hermes Siri AI Watch-first

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Consolidated:** 2026-07-06 15:47 America/Bogota  
**Inputs:** Pipo technical architecture, Atenea Apple constraints audit, Horacio UX/art direction.

## Executive synthesis

The three first-pass agents converge on the same product/technical direction:

> Build one iOS + watchOS app, but design and ship it **Watch-first**. The Watch must capture independently through a narrow Hermes webhook; iPhone is the configurator, fallback relay, history/debug surface, and later Siri/App Intents companion.

## Decisions accepted for the next implementation lane

### 1. Canonical mobile endpoint

Use a unified webhook route:

```http
POST /webhooks/mobile-capture-v1
```

Rationale:

- It is not Apple-Watch-only; it also covers iPhone app, App Intents, Siri, and Shortcuts migration.
- It keeps the mobile app away from Hermes API Server's broader powers.
- It supports narrow prompt/tool scope and safer audit.

Legacy routes remain alive:

- `/webhooks/apple-watch-expense` for Megan/Firefly.
- Existing Aura capture webhook for Notion/Calendar.

The unified route can internally route or adapt to those flows during migration.

### 2. HMAC over route secret as MVP auth

Use HMAC-SHA256 over exact JSON body bytes:

```http
X-Webhook-Timestamp: <unix seconds>
X-Webhook-Signature-V2: <hex_hmac_sha256("<timestamp>.<raw_body>", route_secret)>
X-Request-ID: <uuid>
X-Hermes-Payload-Version: 1
X-Hermes-Client: HermesCapture/watchOS/<version>
```

Compatibility fallback: `X-Gitlab-Token` may remain for current Shortcuts, but the native app should target HMAC.

### 3. Watch direct networking + iPhone fallback

Primary:

- Watch sends directly to Hermes via `URLSession`.

Secondary:

- `WatchConnectivity` handles configuration bootstrap, outbox sync, and relay fallback.

Rejected for MVP:

- Relay-only through iPhone.
- Hermes API Server bearer token on Watch.

### 4. Outbox before network

Every capture should be persisted locally with a stable `request_id` before attempting network delivery.

This prevents losing captures when:

- Jose lowers his wrist,
- Watch app is suspended,
- network changes,
- Hermes is temporarily unavailable.

### 5. Action-first UX

Horacio's UX recommendation is accepted:

- The Watch is not a dashboard.
- Home should have one dominant `Capturar` action plus 3–6 favorites.
- 3 seconds, 1 intention, 1 haptic of closure.
- iPhone owns complex configuration/history.

## Important corrections to the original draft docs

1. Replace `/webhooks/apple-watch` as canonical route with `/webhooks/mobile-capture-v1`.
2. Prefer HMAC headers over simple bearer token for native app MVP.
3. Treat App Intents/Siri as phase after Watch/iPhone core, not phase zero.
4. Make file-backed outbox a hard requirement, not a future nice-to-have.
5. Treat direct Watch networking as primary, WatchConnectivity as fallback.
6. Use system dictation/input; do not build custom STT in watchOS MVP.

## Fable 5 immediate concerns

### Product/capture

- PASS if frequent captures are ≤2 taps or 1 Siri phrase.
- FAIL if Watch starts with a dense agent dashboard.

### Apple platform

- PASS if Watch works without iPhone for core capture.
- FAIL if WatchConnectivity is required for normal capture.

### Security/privacy

- PASS if native app uses HMAC route secret in Keychain and no secrets in logs.
- FAIL if API Server bearer token is stored on Watch.

### Reliability/idempotency

- PASS if every capture has persisted outbox + stable request_id + server dedupe.
- FAIL if a retry can duplicate Firefly/Notion records.

### Mantenibility/evolution

- PASS if agents/actions are data-driven by route.intent and shared `HermesCore`.
- FAIL if each button hardcodes different HTTP clients/contracts.

## Next subagent batch already launched

- Megan: finance-domain contract for `expense_capture`.
- Aura: reminder/grocery/home capture contract.
- Fable 5: formal audit table of current decisions.

## Source briefs copied into project

```text
docs/subagent-briefs/pipo-technical-architecture.md
docs/subagent-briefs/atenea-apple-platform-constraints.md
docs/subagent-briefs/horacio-ux-art-direction.md
```
