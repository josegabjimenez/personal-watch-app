# GPT-5.6-sol Max Thinking Review 005 — Final dry-run release readiness

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Date:** 2026-08-04 America/Bogota

**Reviewed candidate:** Hermes Capture 0.3.2 (5), based on the physically verified 0.3.1 (4) baseline plus the remediations in this review.

**Reviewer policy:** GPT-5.6-sol Max Thinking. Fable 5 was not used for this review.

## Review integrity

The independent review task `deleg_57b67765` inspected the repository, runtime service, ledger, tests, privacy manifest, signing configuration, Watch fallback, App Intents, and public-history safety. It timed out before producing a verdict. Its complete transcript was recovered and reconciled; no missing subagent conclusion is attributed or invented. The parent GPT-5.6-sol Max Thinking pass completed the review and verified the remediations below.

## Evidence classification

### Repository/runtime verified

- HMAC V2 signs `timestamp + "." + exact raw body` and the backend uses constant-time comparison.
- The backend enforces a five-minute replay window and exact `X-Request-ID`/body equality.
- The backend remains server-locked to `dry_run: true` and `would_write: false`; it has no external write implementation.
- The Watch fallback accepts only a persisted watchOS payload with `dry_run: true`, `allow_write: false`, and `allow_firefly_write != true`.
- Direct and fallback clients require `dry_run == true`, `would_write == false`, and matching response/request IDs.
- The route secret is stored with `AfterFirstUnlockThisDeviceOnly`; bootstrap uses interactive `WCSession.sendMessage`.
- Signing is automatic and no Team ID is present in `project.yml`.
- Runtime secret and ledger permissions are `0600`; the ledger directory is `0700`.
- Tailscale Serve remains tailnet-only and the BFF binds to loopback.
- Backend and HermesCore tests pass, Swift app sources parse, and public-repository safety scans are clean.

### User-reported physical evidence

- paired iPhone and Apple Watch installation and secure bootstrap;
- direct Watch HTTPS: `Enviado`, attempt 1, `direct_https`;
- forced direct failure: `Enviado`, attempt 1, `iphone_fallback`;
- full outage: same item `Falló`, attempt 1, then `Enviado`, attempt 2;
- manual iPhone Shortcut, iPhone Siri, and native Watch Siri;
- Xcode archive with no warnings;
- four built privacy manifests: iOS app, Watch app, and one Swift Crypto bundle per app;
- app manifests contain tracking disabled, no domains, and User Defaults reason `CA92.1`.

These physical results apply to 0.3.1 (4). Candidate 0.3.2 (5) still requires a focused rebuild and smoke test because this review changed client safety checks and file protection.

## Findings resolved during review

### R1 — Direct client did not bind a successful response to the request ID

**Original risk:** a dry-run-looking response for another request could mark the current outbox item sent.

**Resolution:** `OutboxDeliveryService` now requires `response.requestID == payload.requestID` in addition to the dry-run gates (`OutboxDeliveryService.swift:86-94`). A regression test verifies that a mismatch becomes sanitized `unsafe_response` and remains failed.

### R2 — Missing `would_write` was treated as safe

**Original risk:** optional chaining accepted a missing safety declaration because only explicit `true` was rejected.

**Resolution:** direct Watch/iPhone delivery and both fallback validation points now require `response.plan?.wouldWrite == false` (`OutboxDeliveryService.swift:86-94`, `WatchCaptureDeliveryCoordinator.swift:43-55`, `IPhoneWatchBootstrapCoordinator.swift:138-148`). A regression test verifies that a response with no plan is unsafe.

### R3 — Concurrent duplicate BFF requests could lose the HTTP response

**Original risk:** concurrent handlers could both observe a missing request ID; one then raised SQLite `UNIQUE` and closed the connection without a response. A forced eight-request concurrency reproduction produced six disconnected requests before remediation.

**Resolution:** the idempotency decision now executes under `BEGIN IMMEDIATE` with a bounded SQLite timeout (`mobile_capture_staging_server.py:391-400`). The new eight-worker test requires one accepted response and seven cached duplicates; all return HTTP 200.

### R4 — Existing runtime ledger permissions were broader than intended

**Original state:** ledger `0644`, data directory `0755`.

**Resolution:** runtime permissions were corrected to ledger `0600` and directory `0700`. `init_db` now enforces those modes (`mobile_capture_staging_server.py:72-90`), and the automated test checks them. The route secret remains `0600`.

### R5 — Outbox file protection was implicit

**Resolution:** iOS/watchOS writes now explicitly use atomic writing plus `completeFileProtectionUntilFirstUserAuthentication`, matching the Keychain availability model (`OutboxStore.swift:142-155`).

## Remaining conditions

### P1 — Blocking before any real external write

1. Build a separate server-side write controller. Do not reinterpret client `allow_write` as authority.
2. Allowlist domains and require a server-owned per-domain enable flag.
3. Implement a durable production ledger with processing/committed/partial-error states, external IDs, reconciliation, and crash recovery.
4. Prove duplicate and partial-failure behavior against each real downstream system; SQLite request caching alone is not exactly-once external delivery.
5. Implement confirmation correlation for ambiguous or sensitive operations.
6. Add secret rotation/revocation and rate limiting.
7. Run the BFF under a dedicated unprivileged service account instead of root while preserving access to only its secret and ledger.
8. Complete domain-specific parsers and regression matrices before enabling that domain. In particular, the dated Aura and multi-write Megan matrices remain incomplete.

### P1 — Blocking before distributing 0.3.2 (5)

1. Regenerate the local Xcode project from `project.yml`.
2. Build/install 0.3.2 (5) on paired physical devices.
3. Smoke-test direct Watch delivery, iPhone fallback, one failed/retried outbox item, iPhone Siri, and native Watch Siri.
4. Generate a new archive and repeat the privacy-manifest inspection.
5. Validate a fresh TestFlight installation and secure bootstrap.

### P2 — Privacy/product hardening

- Add a user-visible retention/deletion policy for sent and failed outbox items; current items are retained in the local JSON history (`OutboxStore.swift:112-146`).
- Reconcile App Store Connect privacy answers and a public privacy policy with the manifest and backend ledger behavior.
- Verify authentication behavior while iPhone and Watch are locked.
- Test expense, reminder, and grocery Siri phrases individually if every published phrase is release-critical.

## Verification performed

```text
Backend unit/integration tests: 10 passed
HermesCore tests in clean Swift 6.0 container: 15 passed
Concurrent duplicate regression: 1 accepted + 7 duplicate, all HTTP 200
Swift parse of app/core sources: passed
Runtime BFF after restart: active
Runtime health: mode dry-run
Ledger directory: 0700
Ledger: 0600
Route secret file: 0600
Source privacy plist: tracking false, no domains, CA92.1
Public-repository safety scan: no sensitive matches
```

## Verdict

# APPROVED TO DESIGN WRITE ENABLEMENT WITH CONDITIONS

This verdict approves continued dry-run use and the design/implementation of a separate server-side write-control phase. It does **not** approve enabling any external write, distributing 0.3.2 (5), or treating client context flags as write authorization. Every P1 condition remains a hard gate.
