# Physical-device readiness and Watch→iPhone capture fallback

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Version:** 0.3.1 (4)

**Status:** physical paired-device reliability QA passed in dry-run: secure bootstrap, direct Watch HTTPS, interactive iPhone fallback, full-outage preservation, and same-item retry.

## Verified physical-device QA — 2026-07-30 America/Bogota

Jose confirmed the following on a physical paired iPhone and Apple Watch running beta Apple OS software with the matching beta Xcode toolchain:

```text
iOS companion installed and launched
watchOS companion installed and launched
BFF health check: dry-run
route-scoped HMAC secret stored in iPhone Keychain
interactive configuration bootstrap acknowledged by Watch
physical Watch capture: Enviado
attempts: 1
delivery path: Directo / direct_https
```

Jose then launched the physical Watch app with direct transport failure simulated while leaving interactive phone fallback enabled:

```text
HERMES_SIMULATE_OFFLINE=1
HERMES_DISABLE_PHONE_FALLBACK unset
physical Watch capture: Enviado
attempts: 1
delivery path: vía iPhone / iphone_fallback
iPhone aggregate diagnostics: latest route vía iPhone
```

This confirms the physical Watch kept its persisted payload, relayed no secret, and accepted the iPhone response only after the existing dry-run/no-write/request-ID checks.

Finally, Jose enabled both physical Debug gates and confirmed durable outage recovery:

```text
HERMES_SIMULATE_OFFLINE=1
HERMES_DISABLE_PHONE_FALLBACK=1
same outbox item: Falló
attempts: 1
sanitized error: network_-1009

both gates disabled
Reintentar pendientes
same outbox item: Enviado
attempts: 2
```

The item was retried rather than recaptured, preserving its original payload and request ID for BFF idempotency.

The private MagicDNS hostname, route secret, Apple Team ID, provisioning material, and device identifiers were not added to Git or this document. Server-side writes remained disabled.

The physical reliability matrix for version 0.3.1 (4) is complete. Shortcut/Siri hardware discovery and archive privacy-report inspection remain separate release-readiness checks.

## Verified paired-simulator QA — 2026-07-22 America/Bogota

Jose confirmed both required scenarios:

```text
Direct Watch path simulated offline
→ iPhone fallback
→ Enviado
→ Intentos: 1
```

and:

```text
Direct Watch path simulated offline
+ iPhone fallback disabled
→ Falló
→ Intentos: 1
→ Debug switches disabled
→ Reintentar pendientes
→ same request_id
→ Enviado
→ Intentos: 2
```

This verifies that interactive fallback does not replace the durable Watch outbox and that full outage recovery preserves idempotency. No external write occurred.

## Purpose

The Apple Watch keeps direct `URLSession` HTTPS as the primary capture path. A physical Watch may be unable to reach a tailnet-only MagicDNS endpoint directly, depending on how the paired iPhone and VPN route Watch traffic.

For transient transport failures only, Hermes now uses an interactive iPhone fallback:

```text
Watch outbox (durable source)
  → direct HTTPS attempt
  → transient network / HTTP 502, 503, or 504 failure
  → WCSession.sendMessage(payload only)
  → iPhone validates the envelope and dry-run gates
  → iPhone Keychain supplies its own route secret
  → iPhone signs and POSTs to the same BFF
  → response returns to Watch
  → same Watch outbox item becomes sent
```

## Security properties

The fallback message contains the already-created capture payload but never contains:

- HMAC secret;
- bearer token;
- Keychain data;
- broad Hermes API credential;
- private endpoint credentials.

The fallback uses only interactive `WCSession.sendMessage`. It does not use deferred/persistent WatchConnectivity transports such as:

```text
updateApplicationContext
transferUserInfo
transferFile
```

The iPhone rejects fallback payloads unless all of these hold:

- body size is at most 64 KiB;
- event and schema are `mobile_capture.v1` v1;
- request ID is present;
- source platform is `watchOS`;
- source surface is `watch_app` or `app_intent_watch`;
- capture text is nonempty and at most 16 KiB;
- `dry_run == true`;
- `allow_write == false`;
- `allow_firefly_write != true`.

The iPhone accepts the BFF response only when:

```text
dry_run == true
plan.would_write != true
response.request_id == payload.request_id
```

Fallback is intentionally denied for authentication failures, client errors, invalid responses, unsafe responses, and unknown failures. It is eligible only for:

```text
network errors
HTTP 502
HTTP 503
HTTP 504
```

If direct delivery succeeds but its response is lost, fallback/retry reuses the same request ID. The BFF ledger therefore returns a duplicate safely instead of creating a second effect.

## Delivery-path observability

Version 0.3.1 (4) persists a bounded delivery-path enum on successful outbox items:

```text
direct_https
iphone_fallback
```

The Watch history presents these as `Directo` and `vía iPhone`. Aggregate Watch diagnostics expose only the most recent successful path to the iPhone companion as `Directo HTTPS` or `vía iPhone`; no capture text is included. Existing outbox JSON without this optional field remains decodable.

This lets physical-device QA determine which network route actually succeeded instead of inferring it from timing or simulator switches.

## Durability model

The iPhone does not create a second outbox copy for a fallback request. The Watch outbox remains the durable source of truth throughout the interactive attempt.

If the iPhone is not reachable, its app is not active, or the fallback fails, the Watch item remains failed/retryable. No capture is silently discarded.

## Controlled simulator validation

Keep the paired iPhone Hermes app open and reachable. In the Watch scheme, set:

```text
HERMES_SIMULATE_OFFLINE = 1
```

Do not set `HERMES_DISABLE_PHONE_FALLBACK`. Submit a Watch capture.

Expected result:

```text
direct Watch path: simulated offline
Watch→iPhone fallback: used
BFF response: dry-run
Watch history: Enviado
attempt count: 1
```

To test full outage with no fallback, set both:

```text
HERMES_SIMULATE_OFFLINE = 1
HERMES_DISABLE_PHONE_FALLBACK = 1
```

Expected result:

```text
Watch history: Falló
attempt count: 1
```

Disable both variables, relaunch, and press `Reintentar pendientes`. Expected:

```text
same request_id
Watch history: Enviado
attempt count: 2
```

These environment variables are Debug-only.

## Privacy manifest

`Apps/Shared/PrivacyInfo.xcprivacy` is included in both app targets through the shared source path. It declares:

- no tracking;
- no tracking domains;
- other user content for app functionality;
- purchase history for expense capture functionality;
- pseudonymous device ID for app functionality;
- UserDefaults required-reason API `CA92.1`.

Hermes does not record or transmit raw audio. Dictation uses system UI and the app receives text.

The manifest is intentionally conservative. App Store Connect privacy answers and an Xcode archive privacy report must still be reconciled before TestFlight/App Store distribution.

## Signing

Both targets use:

```text
CODE_SIGN_STYLE: Automatic
```

No `DEVELOPMENT_TEAM`, certificate, provisioning profile, Apple account ID, or device identifier is committed. Jose selects his personal Apple Development Team locally in Xcode.

## Physical-device QA gates

1. Build iPhone and Watch targets with full Xcode.
2. Install Hermes 0.3.0 (3) on a paired physical iPhone/Watch.
3. Confirm iPhone Tailscale connectivity and BFF health.
4. Provision configuration and secret to Watch via ephemeral bootstrap.
5. Test direct Watch capture with iPhone nearby.
6. Test transient direct failure with iPhone fallback.
7. Test full outage and later retry with the same request ID.
8. Test a manually created Shortcut on iPhone.
9. Test App Shortcut/Siri discovery on physical devices.
10. Inspect the Xcode privacy report and archive validation.

Real writes remain blocked.
