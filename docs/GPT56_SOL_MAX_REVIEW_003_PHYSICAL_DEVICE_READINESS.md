# GPT-5.6-sol Max Thinking review 003 — Physical-device readiness

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Reviewer:** GPT-5.6-sol, Max Thinking

**Fable 5:** not used; historical documents remain records only.

**Verdict:** **APPROVED FOR PAIRED-SIMULATOR AND PHYSICAL-DEVICE QA WITH CONDITIONS**

## Scope

- transient Watch→iPhone capture fallback;
- reuse of the Watch outbox and request ID;
- iPhone-side payload validation and signing;
- integration in Watch UI, retry, and Watch App Intents;
- privacy manifest;
- automatic signing without repository-specific Team data.

## Product and platform review

Direct Watch HTTPS remains primary. The fallback does not make iPhone availability a prerequisite for local capture because the Watch always persists first. It only improves immediate delivery when the iPhone is currently reachable.

`WCSession.sendMessage` is appropriate for this fallback because it is interactive and exposes immediate reachability. It is not treated as a durable queue. If it cannot complete, the Watch outbox remains retryable.

A physical Watch is still required to determine whether tailnet-only MagicDNS routes directly through the paired iPhone/VPN path. Simulator success cannot establish that platform behavior.

## Security and privacy review

Approved controls:

- no secret crosses the fallback message;
- iPhone loads its own secret from Keychain;
- payload size, schema, source, content, and dry-run gates are revalidated;
- response request ID must match;
- unsafe/write-capable responses are rejected;
- fallback is not allowed for HTTP 400/401, invalid response, unsafe response, or unknown errors;
- no deferred WatchConnectivity transport carries capture content;
- errors returned to Watch are bounded codes;
- privacy manifest declares no tracking and conservatively declares functional user content, purchase history, and device ID collection;
- Team IDs and provisioning data remain local.

Residual privacy consideration: the fallback message contains the user's capture text because the iPhone must submit the request. It uses the paired-device WatchConnectivity channel and is not queued through deferred APIs. This is necessary app functionality and must remain covered by the public privacy declaration.

## Reliability and idempotency review

Approved controls:

- Watch outbox exists before either network path;
- one request ID survives direct failure, fallback, and later retry;
- iPhone does not create a conflicting second outbox;
- response loss remains safe because the server ledger deduplicates;
- fallback eligibility is limited to network and gateway-availability failures;
- fallback success transitions the original Watch item to `sent`;
- fallback failure leaves the original item failed/retryable.

Residual issue: `sendMessage` requires the iPhone app to be reachable. This is accepted because fallback is opportunistic and the Watch outbox remains authoritative.

## Maintainability review

One coordinator now owns Watch delivery policy for:

```text
WatchCaptureView
WatchContentView retries
Watch App Intents
```

This avoids diverging direct/fallback logic among surfaces. Eligibility policy lives in a tested HermesCore property.

## Automated evidence

Required before publication:

- HermesCore XCTest suite passes, including transient-failure eligibility;
- shared Swift contract typechecks;
- Apple source parses;
- PrivacyInfo plist parses and expected keys are verified;
- project YAML parses;
- backend tests pass;
- repository secret scan is clean;
- BFF remains active and dry-run.

## Conditions before claiming physical completion

1. Full Xcode build of both targets.
2. Paired simulator fallback verification.
3. Physical iPhone/Watch installation and bootstrap.
4. Direct path and iPhone fallback observations on physical hardware.
5. Full outage and retry observation.
6. Xcode archive privacy report reconciliation.
7. App Shortcut/Siri verification on physical hardware.
8. Writes remain disabled.

## Final conclusion

The implementation is appropriate for QA and materially improves physical-device resilience without broadening client privilege. It is not yet approved for real external writes or App Store distribution.
