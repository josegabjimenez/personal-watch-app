# GPT-5.6-sol Max Thinking review 002 — App Intents and Siri

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Reviewer:** GPT-5.6-sol, Max Thinking

**Fable 5:** not used; historical documents remain records only.

**Verdict:** **APPROVED FOR XCODE/SIMULATOR QA WITH CONDITIONS**

## Scope

Reviewed:

- four iOS/watchOS App Intents and App Shortcuts;
- local authentication policy;
- local-first HMAC delivery;
- iPhone and Watch outbox behavior;
- concurrent file access;
- controlled offline testing;
- public-repository security.

## Findings

### Security and privacy — pass

- All intents require local device authentication.
- Suggested phrases contain no PII, payload content, endpoint, secret, or identifiers.
- Secrets stay in the local target's Keychain.
- Siri receives only the short BFF display message or a sanitized local error.
- The debug offline hook is compile-gated and does not print secrets or payloads.

### Safety — pass

`CaptureFactory` still creates:

```text
dry_run: true
allow_write: false
```

`OutboxDeliveryService` still rejects responses that are not explicitly dry-run or that propose a write. App Intents cannot enable external writes.

### Reliability — pass after hardening

UI and App Intents may create different `FileOutboxStore` actors for the same file. A per-file advisory lock now serializes read-modify-write operations across those instances. The 20-way concurrent enqueue test passes without lost captures.

Every capture is persisted before Keychain lookup or network activity. Network errors remain retryable with the same request ID.

### Product and UX — pass for QA

The four initial intents match the validated Watch actions and use short Spanish prompts. Siri/Shortcuts can respond without opening the app. iPhone-local failed intents now have aggregate diagnostics and a retry action.

### Apple platform conditions

Linux can verify HermesCore and parse Swift syntax, but cannot run App Intents metadata extraction. Xcode must confirm:

- `AppShortcutsProvider` discovery;
- phrase validation/localization;
- `IntentDialog` metadata;
- authentication behavior;
- watchOS execution target;
- Siri availability on simulator/device.

A known Apple simulator regression affects Xcode 26.5–27 beta 2 with iOS Simulator 26.5–27 beta 2: Auto Shortcuts may report that no `AppShortcutsProvider` exists even for Apple's sample project. This must be separated from provider correctness by checking metadata extraction, manually invoking the underlying App Intent, and using an older runtime or physical device for authoritative validation.

## Required conditions before physical-device milestone

1. Build iOS and Watch schemes with full Xcode.
2. Confirm all four shortcuts appear in the Shortcuts app.
3. Invoke at least expense and reminder through Siri.
4. Confirm the returned message comes from the live dry-run BFF.
5. Run the Debug offline→retry story and verify attempt count `2`.
6. Test locked/unlocked behavior on physical iPhone and Apple Watch.
7. Keep writes disabled.

## Deferred production work

- bounded retention and compaction;
- corruption backup/recovery;
- privacy manifest and App Privacy labels;
- Spanish localization resources instead of inline strings;
- TestFlight/Release provisioning and energy/connectivity testing;
- explicit policy for which future home/financial intents may execute while locked.

## Conclusion

The implementation is appropriate for Xcode and paired-simulator QA. No external-write authority was added. The primary unknowns are Apple metadata/discovery behavior and physical-device execution, not the capture contract, HMAC path, or outbox reliability.
