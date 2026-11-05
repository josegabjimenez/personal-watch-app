# Implementation status — 2026-07-06 17:30 America/Bogota

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

## Completed in this session

### Hermes generic webhook subscription

Created/updated dynamic subscription:

```text
mobile-capture-v1
URL: http://localhost:8644/webhooks/mobile-capture-v1
Events: mobile_capture.v1
Deliver: log
Mode: staging / dry-run / no-write prompt
```

Verified:

```text
hermes webhook list → 4 subscriptions, including mobile-capture-v1
GET http://localhost:8644/health → {"status":"ok","platform":"webhook"}
```

Signed POST tests against Hermes generic webhook:

| Case | Result |
|---|---|
| Valid Megan fixture | HTTP 202 accepted |
| Duplicate same `X-Request-ID` | HTTP 200 duplicate |
| Valid Aura fixture | HTTP 202 accepted |
| Invalid signature | HTTP 401 invalid signature |
| Wrong event | HTTP 200 ignored |
| Valid fixture after sanitized prompt update | HTTP 202 accepted |

Important limitation discovered and confirmed by Pipo:

- Hermes generic webhook adapter returns `202 accepted` immediately and runs the agent in background.
- Its duplicate handling is a short-lived delivery cache, not a persistent request ledger.
- It does not enforce the full mobile-capture contract before the agent sees the payload.
- This is useful for async ingestion/logging, but not enough for Watch UX that expects immediate JSON such as `display_message`, `needs_confirmation`, or `duplicate`.
- Therefore: **generic webhook stays log-only/no-write for this project.**

Prompt hardening applied after Pipo review:

- Removed `{__raw__}` from the prompt; it now renders only selected payload fields.
- Changed wording so client `context.allow_write`/`context.allow_firefly_write` never authorizes writes.
- Kept route in staging/no-write mode.

### Synchronous staging BFF

Created:

```text
backend/mobile_capture_staging_server.py
backend/scripts/sign_and_post_mobile_capture.py
backend/tests/test_mobile_capture_staging_server.py
backend/fixtures/megan-expense-simple.json
backend/fixtures/aura-reminder-simple.json
backend/fixtures/invalid-event.json
```

Server behavior:

- `GET /health`
- `POST /webhooks/mobile-capture-v1`
- HMAC V2: `X-Webhook-Timestamp` + `X-Webhook-Signature-V2`
- requires `X-Request-ID`
- rejects `X-Request-ID != body.request_id`
- validates schema/event/request ID
- SQLite idempotency ledger
- rejects same `request_id` with different `sha256(raw_body)`
- immediate synchronous JSON response
- server-owned write allowlist; only Aura Mercado is currently enabled

Server test URL:

```text
http://127.0.0.1:8650/webhooks/mobile-capture-v1
```

Verified results:

| Case | HTTP | Result |
|---|---:|---|
| Health | 200 | `mode: allowlisted-write`, write domains: only `aura.grocery_capture` |
| Valid Megan fixture | 200 | `accepted`, dry-run plan, `$45.000 COP`, no write |
| Duplicate Megan fixture | 200 | `duplicate`, same cached response, no write |
| Same request ID + changed body | 409 | `request_id_conflict` |
| Missing `X-Request-ID` | 400 | `missing X-Request-ID` |
| Header/body request ID mismatch | 409 | `X-Request-ID mismatch` |
| Valid Aura fixture | 200 | `accepted`, interpreted due `2026-07-07T14:00:00-05:00` |
| Invalid signature | 401 | `rejected` |
| Wrong event | 200 | `ignored` |

Code verification:

```text
python3 -m py_compile backend/mobile_capture_staging_server.py backend/scripts/sign_and_post_mobile_capture.py backend/tests/test_mobile_capture_staging_server.py
# exit 0

cd backend && PYTHONPATH=. python3 -m unittest discover -s tests -v
# Ran 13 tests — OK
```

## Security notes

- Secret is stored locally outside the repo at `<HERMES_HOME>/mobile-capture-v1-secret.txt` with mode `0600`.
- Secret was not printed in final output.
- The BFF uses HMAC V2 only for native-app style requests.
- Only the exact `aura` + `grocery` + `aura.grocery_capture` route is write-enabled server-side.
- Client `allow_write` is an intent request, never authority. Megan, reminders, general capture, Home Assistant, Calendar, and unknown domains remain dry-run.

## Pipo review integration

Integrated Pipo's backend review into:

```text
docs/subagent-briefs/pipo-backend-staging-review.md
docs/PIPO_BACKEND_REVIEW_REMEDIATION.md
docs/FABLE5_REVIEW_003_BACKEND_STAGING_REMEDIATION.md
```

Summary: Pipo's blockers were valid for the generic Hermes webhook path. The synchronous BFF addresses idempotency, header binding, and response semantics. After Review 005 authorized designing a constrained write phase, Aura Mercado passed a separate reversible canary; no other write domain inherited that approval.

## Tailscale Serve HTTPS endpoint

Configured Tailscale Serve for the synchronous BFF:

```bash
tailscale serve --bg --yes --https=8650 http://127.0.0.1:8650
```

Tailnet-only HTTPS endpoint:

```text
https://<TAILSCALE_DNS_NAME>:8650/webhooks/mobile-capture-v1
```

Verified:

```text
GET /health → {"status":"ok","service":"mobile-capture-v1-staging","mode":"dry-run"}
Signed HMAC V2 Megan fixture → HTTP 200 accepted / dry_run true / no write
```

See `TAILSCALE_SERVE_ENDPOINT.md`.

## Apple simulator milestone

Verified on Jose's paired iPhone + Apple Watch simulators:

```text
Xcode project generation and app installation
HermesCore XCTest suite: 8 tests, 0 failures
System Watch text input
File-backed outbox before network
HTTPS/HMAC endpoint configuration in iPhone Keychain
User-initiated WatchConnectivity bootstrap into Watch Keychain
Signed Watch POST through Tailscale Serve
Synchronous BFF display_message rendered on Watch
```

Observed end-to-end result:

```text
Input: 45 mil en Uber
Watch: Dry-run Megan $45.000 COP
```

## History, diagnostics, and App Intents milestone

Verified or implemented:

```text
Watch local history: user-verified
Aggregate Watch→iPhone diagnostics: user-verified
Megan expense route: user-verified dry-run
Aura grocery route: user-verified dry-run
Aura reminder route: user-verified dry-run
Argos general route: user-verified dry-run
HermesCore XCTest suite: 18 tests, 0 failures
Concurrent UI/App Intent outbox locking: automated test passed
Network failure → same-request-ID retry: automated test passed
Transient-fallback eligibility policy: automated test passed
Legacy outbox migration without delivery path: automated test passed
Four authenticated iOS/watchOS App Intents: implemented
Manually created iPhone Shortcut: user-verified dry-run and local status Enviado
Physical iPhone manual Shortcut and Siri general capture: user-verified dry-run
Physical Apple Watch native Siri general capture: user-verified in Watch-local history
Auto Shortcuts on iOS 26.5 Simulator: blocked by known Apple regression
Spanish general-capture App Shortcut metadata: physical iPhone and Watch verified
Expense/reminder/grocery Siri phrases: physical per-phrase QA pending
Watch→iPhone capture fallback: paired-simulator QA verified
Full outage → same-request-ID retry: paired-simulator QA verified
Delivery-path persistence and sanitized last-route diagnostics: physical QA verified
Privacy manifests: archive passed with no warnings; app and Watch CA92.1 verified in built artifacts
Physical-device build 0.3.1 (4): installed on paired iPhone and Apple Watch
Physical BFF health, Keychain provisioning, and Watch bootstrap: user-verified
Physical Watch direct HTTPS dry-run: user-verified Enviado, attempt 1, route direct_https
Physical Watch→iPhone fallback: user-verified Enviado, attempt 1, route iphone_fallback
Physical full-outage preservation and same-item retry: user-verified Falló attempt 1 → Enviado attempt 2
Final GPT-5.6-sol Max Thinking review: approved to design write enablement with conditions
Candidate 0.3.2 (5): response/request binding, strict would_write gate, concurrent idempotency, and storage-permission hardening implemented
Candidate 0.3.3 (6): explicit committed-write response contract and direct-only Mercado write requests implemented
Backend suite: 13 tests, 0 failures
Aura Mercado reversible canary: create/read/replay/exactly-once/archive/read passed
Runtime write allowlist: only aura + grocery + aura.grocery_capture
```

One authorized temporary Mercado canary write occurred and was read back, verified exactly once, archived, and read back as archived. No active canary item remains.

## Recommended next step

Keep every non-Mercado write disabled. Next validation lane:

1. Regenerate Xcode and smoke-test candidate 0.3.3 (6) on the paired physical Watch/iPhone.
2. Dictate one real needed Mercado item and confirm the Watch reports the committed response.
3. Retry only if needed; verify the same request ID does not duplicate the item.
4. Repeat archive/privacy inspection before distribution.
5. Verify locked-device authentication behavior and a fresh TestFlight bootstrap.
6. Require a separate gate and reversible canary before enabling reminders, expenses, or any other domain.
