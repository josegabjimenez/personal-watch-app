# GPT-5.6-sol Max Thinking review 001 — Watch history and diagnostics

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Reviewer:** GPT-5.6-sol, Max Thinking

**Review protocol:** direct architecture, correctness, security, reliability, UX, and maintainability review.

**Fable 5:** not used.

**Verdict:** **APPROVED FOR PAIRED-SIMULATOR QA WITH CONDITIONS**

## Scope

Reviewed:

- local Watch history over the file-backed outbox;
- Watch→iPhone diagnostics through interactive WatchConnectivity;
- controlled network-failure/retry coverage;
- existing dry-run and no-write guarantees.

## Findings

### 1. Security and privacy — pass

Diagnostics return aggregate counts and two booleans only. They do not return:

- HMAC secret or signature;
- endpoint;
- request/device IDs;
- capture text;
- server body.

Raw capture text is visible only in the local Watch history, which is the requested user-facing history surface.

### 2. Contract integrity — pass after remediation

The iPhone parser now rejects malformed diagnostics when counts are negative or do not add up to `total`.

The Watch reports `outbox_readable` separately. A decode failure is therefore not presented as a trustworthy empty outbox.

### 3. Retry and idempotency — pass

The controlled test proves a network failure transitions the item to `failed`, preserves the original request ID, and retries that same item to `sent` with attempt count `2`.

This is compatible with the BFF ledger rule:

```text
same request_id + same payload → duplicate/same logical request
same request_id + changed payload → conflict
```

### 4. UX — pass for simulator QA

Watch history uses friendly route names, short statuses, attempt counts, and sanitized error codes. iPhone diagnostics expose the operational facts needed without revealing payload content.

Physical-device checks are still required for readability, scrolling, Dynamic Type, haptics, and reachability behavior.

### 5. Reliability and maintainability — conditions

The current JSON outbox has no retention or compaction policy. This is acceptable for the current dry-run MVP but not for indefinite production use.

Diagnostics read the atomically written outbox file synchronously on the WatchConnectivity callback. This is acceptable for a small MVP outbox; move decoding off the callback path or maintain a summary index before the outbox becomes large.

There is no automatic corruption recovery. The app intentionally reports `outbox_readable: false` rather than deleting user captures.

## Required conditions before production

1. Validate history and diagnostics on a physical Apple Watch.
2. Add bounded retention/compaction while preserving failed and unsent items.
3. Add an explicit corruption backup/recovery path.
4. Exercise real offline→online retry from the Watch.
5. Keep `dry_run: true`, `allow_write: false`, and server-side write gates unchanged.
6. Perform a new GPT-5.6-sol Max Thinking review before enabling any external write.

## Conclusion

The phase is suitable for paired-simulator QA. It does not broaden credentials, expose capture content through diagnostics, or enable external writes. The remaining conditions are operational/production hardening items rather than blockers for the current simulator milestone.
