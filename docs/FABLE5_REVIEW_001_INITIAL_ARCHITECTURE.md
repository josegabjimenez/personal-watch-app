# Fable 5 Review 001 — Initial Watch-first architecture

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Date:** 2026-07-06 15:47 America/Bogota  
**Scope:** decisions after Pipo/Atenea/Horacio first-pass briefs.  
**Status:** provisional until Megan/Aura domain audit and physical Xcode/watchOS validation.

## Verdict table

| Decision | Verdict | Conditions |
|---|---|---|
| One iOS + watchOS app, Watch-first | APPROVED | iPhone remains companion/config/debug; Watch core capture must work independently. |
| Canonical `/webhooks/mobile-capture-v1` route | APPROVED | Keep legacy Megan/Aura webhooks as fallback during migration. |
| Narrow webhook instead of Hermes API Server for MVP | APPROVED | API Server only for future Ask Hermes/chat with separate security review. |
| HMAC route secret for native app | APPROVED WITH CONDITIONS | Secret in Keychain; exact-body signing; no logs; rotation plan. |
| API Server bearer token on Watch | REJECTED FOR MVP | Reconsider only with BFF/scoped mobile token. |
| Watch `URLSession` direct primary | APPROVED WITH CONDITIONS | Must have local outbox first and test Wi‑Fi/cellular/iPhone proxy/no iPhone. |
| WatchConnectivity fallback/config sync | APPROVED | Must not be required for normal capture. |
| File-backed outbox actor | APPROVED | Stable request_id per capture; persist before network; retry/backoff. |
| SwiftData for MVP outbox | REJECTED FOR MVP | Too much migration/Watch complexity for simple queue. |
| Action-first UX with 3–6 favorites | APPROVED | Validate on 41mm/45mm screens and reduce if crowded. |
| App Intents/Siri phase after core Watch/iPhone | APPROVED | Prototype early, but do not block MVP core on Siri edge cases. |
| Custom STT in watchOS MVP | REJECTED | Use system dictation/text input; Speech framework support must be revalidated before any custom STT. |

## Lens 1 — Product and capture speed

**Assessment:** Strong. Horacio's “3 seconds, 1 intention, 1 haptic” gives the correct product constraint.

Conditions:

- First release should expose only the highest-value actions: Gasto, Recordatorio, Mercado, Captura.
- Avoid making all agents equally prominent on the Watch.
- Success screen should auto-close in ~1–1.5s.

Verification:

- Physical Watch timing test: open → dictate → confirmation.
- Target: ≤2 taps or 1 Siri phrase for frequent actions.

## Lens 2 — Apple platform native fit

**Assessment:** Good with cautions. Atenea confirmed App Intents/watchOS support at class level and warned against relying on iPhone/WatchConnectivity for critical capture.

Conditions:

- Validate deployment target in Xcode before using newer App Intents APIs.
- Use system input/dictation.
- Treat background execution as best-effort, never guaranteed.

Verification:

- Device matrix: paired iPhone nearby, Watch Wi‑Fi/cellular, iPhone away, poor network, wrist lowered mid-send.

## Lens 3 — Security and privacy

**Assessment:** Acceptable only with HMAC + narrow webhook. Storing a broad Hermes API key on Watch is not acceptable.

Conditions:

- Keychain only.
- Redacted logs.
- HMAC over exact body bytes.
- HTTPS/TLS; ATS exceptions only in Debug.
- Prompt must treat dictation as untrusted data.

Verification:

- Unit tests for HMAC vectors.
- Inspect release logs for tokens/transcripts.
- Confirm no secrets in repo.

## Lens 4 — Reliability and idempotency

**Assessment:** This is the biggest risk. Watch networking can die; finance/notion writes cannot duplicate.

Conditions:

- Persist outbox item before network.
- Same `request_id` on every retry.
- Server-side dedupe before side effects.
- Response statuses distinguish `accepted` vs `ok`.

Verification:

- Replay same request_id twice and verify one Firefly/Notion side effect.
- Drop network after dictation and verify queued resend.

## Lens 5 — Maintainability and evolution

**Assessment:** Strong if `HermesCore` remains contract-first and server owns domain logic.

Conditions:

- Shared payload/response models.
- Snapshot tests for JSON schema.
- Do not duplicate Megan/Aura rules in Swift.
- Add new agents as config/action records, not new networking stacks.

Verification:

- Adding a new action should not require a new HTTP client.
- Contract changes require schema version bump or backwards-compatible parser.

## Provisional final verdict

**APPROVED WITH CONDITIONS.**

The plan is technically sound and product-aligned. The non-negotiables before real finance/reminder writes are:

1. HMAC auth implemented and tested.
2. Outbox/idempotency implemented on client and server.
3. Physical Watch networking tests completed.
4. Megan/Aura domain gates approved.
5. No broad Hermes API Server key on Watch.
