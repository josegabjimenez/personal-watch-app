# Fable 5 Review 003 — Backend staging remediation

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Date:** 2026-07-06 17:30 America/Bogota  
**Scope:** remediation after Pipo backend review of `/webhooks/mobile-capture-v1`.  
**Verdict:** APPROVED FOR WATCH MVP DEVELOPMENT; NOT APPROVED FOR PRODUCTION WRITES.

## 1. Product / Watch UX

**Verdict:** Pass for MVP development.

- The generic Hermes webhook is correctly classified as async/log-only.
- The synchronous BFF returns immediate JSON suitable for Watch UI:
  - `display_message`
  - `duplicate`
  - `needs_confirmation`
  - short dry-run plan
- This supports raised-wrist capture better than waiting for a background Hermes agent run.

**Condition:** Native app dev builds should point to the BFF/equivalent synchronous endpoint, not the generic webhook directly.

## 2. Apple platform fit

**Verdict:** Pass for local/dev MVP.

- Direct HTTPS-style request pattern maps to `URLSession` on watchOS.
- Headers are explicit and easy to reproduce in Swift/CryptoKit:
  - `X-Webhook-Timestamp`
  - `X-Webhook-Signature-V2`
  - `X-Request-ID`
  - app/device metadata headers
- Synchronous response allows simple Watch state transitions.

**Condition:** Before Apple Watch physical QA, expose via HTTPS reachable from device. No plain local HTTP outside simulator/dev LAN debugging.

## 3. Security / privacy

**Verdict:** Improved, still gated.

Improvements:

- HMAC V2 + timestamp.
- `X-Request-ID` required.
- Header/body request ID mismatch rejected.
- Same request ID with changed body rejected.
- Generic webhook prompt no longer includes `{__raw__}`.
- Client `allow_write` flags are explicitly non-authoritative.

Remaining risk:

- Generic Hermes gateway may still retain raw payload metadata internally.
- BFF secret distribution to Apple devices still needs Keychain/bootstrap design.
- Production logs need explicit redaction/minimization policy.

**Condition:** no production writes until HTTPS + Keychain + logging policy are implemented.

## 4. Reliability / idempotency

**Verdict:** Pass for staging, not production.

Verified live:

- duplicate same body → `200 duplicate`
- same `request_id` changed body → `409 request_id_conflict`
- missing `X-Request-ID` → `400`
- mismatch → `409`
- invalid signature → `401`

**Condition:** production ledger must be durable, backed up, and retained long enough for finance idempotency. SQLite is acceptable for local staging; deployment must decide backup/retention.

## 5. Maintainability / evolution

**Verdict:** Pass.

- BFF is intentionally separate from Hermes generic webhook because they solve different UX problems.
- Docs now explain the two-surface model.
- Tests cover HMAC, parser basics, dry-run planning, wrong event, missing/mismatched request IDs.
- Implementation tickets now mark staging completion and production gap.

**Condition:** before adding writes, replace simple planner stubs with domain services or server-side adapters that use existing Megan/Aura workflows safely.

## Final gate decision

```text
APPROVED FOR:
- Xcode scaffold
- Watch/iPhone client development against BFF
- dry-run integration testing
- physical Watch network/UI QA

BLOCKED FOR:
- Firefly writes
- Notion/Calendar writes
- Home Assistant actions
- production Apple Watch usage without HTTPS + Keychain + durable deployment
```
