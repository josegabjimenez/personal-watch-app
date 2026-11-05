# Pipo backend review remediation — mobile-capture-v1

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Date:** 2026-07-06 17:30 America/Bogota  
**Review source:** `docs/subagent-briefs/pipo-backend-staging-review.md`  
**Scope:** reconcile Pipo's backend staging review with the current Hermes generic webhook route and the synchronous BFF staging endpoint.

## Review verdict

Pipo correctly found that the **Hermes generic webhook subscription** is not enough for production writes or polished Watch UX:

- It has HMAC V2 and event filtering.
- It returns HTTP `202 accepted` and runs the agent in background.
- Its idempotency is a short-lived delivery cache, not a persistent ledger.
- It does not reject same `X-Request-ID` with changed body.
- It does not enforce `X-Request-ID == body.request_id` before agent dispatch.
- Dry-run/no-write was mostly prompt-level for the generic route.
- `{__raw__}` in the prompt exposed more payload than necessary to the agent session.

## Remediation applied

| Finding | Status | Remediation |
|---|---:|---|
| No persistent ledger in generic webhook | **Addressed for staging BFF only** | `backend/mobile_capture_staging_server.py` uses SQLite ledger at `backend/data/mobile_capture_ledger.sqlite`. Generic webhook remains async/log-only and must not write. |
| Same request ID + changed body returns duplicate | **Fixed in BFF** | BFF stores `sha256(raw_body)` and returns HTTP `409` `request_id_conflict` for same `request_id` with different body. |
| Header/body request ID mismatch accepted | **Fixed in BFF** | BFF now requires `X-Request-ID` and rejects mismatch with HTTP `409`. Missing header returns HTTP `400`. |
| Watch needs synchronous response | **Fixed in BFF** | BFF returns immediate JSON with `display_message`, `plan`, `dry_run`, `duplicate`, `question`, etc. |
| Prompt-level no-write only | **Partially addressed** | Generic webhook prompt now explicitly says client fields do not authorize writes. BFF is deterministic no-write. Production writes still require server-side flags and domain gates. |
| Docs/contract align to HMAC V2 | **Addressed** | Script and docs use `X-Webhook-Timestamp` + `X-Webhook-Signature-V2`. |
| Raw payload in prompt | **Reduced** | Generic webhook prompt no longer uses `{__raw__}`; it renders only selected fields needed for planning. Note: Hermes gateway may still keep raw payload metadata internally; production should minimize or redact at adapter/BFF layer. |
| Add app headers | **Addressed in fixture helper** | `sign_and_post_mobile_capture.py` now sends `X-Hermes-Payload-Version`, `X-Hermes-Client`, and `X-Hermes-Device-ID`. |

## Verification after remediation

### Unit/static checks

```text
python3 -m py_compile backend/mobile_capture_staging_server.py backend/scripts/sign_and_post_mobile_capture.py backend/tests/test_mobile_capture_staging_server.py
# exit 0

cd backend && PYTHONPATH=. python3 -m unittest discover -s tests -v
# Ran 9 tests in 0.001s — OK
```

### Live BFF checks

Against:

```text
http://127.0.0.1:8650/webhooks/mobile-capture-v1
```

| Case | HTTP | Result |
|---|---:|---|
| Valid Megan fixture | 200 | `accepted`, dry-run plan, no write |
| Duplicate same body/request ID | 200 | `duplicate`, cached response |
| Same request ID + changed body | 409 | `request_id_conflict` |
| Missing `X-Request-ID` | 400 | `missing X-Request-ID` |
| Header/body request ID mismatch | 409 | `X-Request-ID mismatch` |
| Invalid signature | 401 | `invalid signature` |
| Wrong event | 200 | `ignored` |

### Generic Hermes webhook checks

Route updated with sanitized prompt and re-tested:

```text
POST http://localhost:8644/webhooks/mobile-capture-v1
→ HTTP 202 accepted for fixture-prompt-update-0001
```

This confirms the route still accepts valid signed payloads after prompt tightening.

## Remaining blockers before real writes

These are still intentionally blocked:

1. Deploy the BFF/equivalent synchronous endpoint durably behind HTTPS/TLS.
2. Add server-side config flags for per-domain writes.
3. Add production-grade ledger backup/retention policy.
4. Keep generic Hermes webhook route log-only/no-write or make it downstream of the BFF ledger.
5. Add privacy/log redaction at the backend layer before production captures.
6. Pass physical Apple Watch QA.
7. Pass Fable 5 domain write gates for Megan/Aura.

## Decision

For the native Watch/iPhone MVP, target the **synchronous BFF contract**, not the generic Hermes webhook directly. The generic route remains useful as an async/log compatibility route, but it must not be the write-enabled production path unless Hermes itself gets an equivalent deterministic pre-handler/ledger.
