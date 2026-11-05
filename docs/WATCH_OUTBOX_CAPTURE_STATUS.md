# Watch local capture + outbox phase

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Status:** implemented; Xcode/watchOS simulator validation required.

## User flow

```text
Watch action
  → system TextField/dictation UI
  → CaptureFactory
  → CapturePayloadV1 (dry-run, allow_write=false)
  → FileOutboxStore
  → success/failure haptic
```

Quick actions:

- Gasto → `megan.expense_capture`
- Recordatorio → `aura.reminder_capture`
- Mercado → `aura.grocery_capture`
- Captura → `argos.general_capture`

## Safety properties

- Capture is persisted before any future network attempt.
- Every payload receives a stable UUID `request_id`.
- Current context is always `dry_run: true` and `allow_write: false`.
- The pseudonymous Watch device ID is local app metadata, not a credential.
- No BFF endpoint or route secret is embedded in Watch source.
- Route secret provisioning remains blocked until Keychain + iPhone bootstrap is implemented.

## UI behavior

Tapping a quick action opens `WatchCaptureView`. Tapping the text field invokes the system watchOS text-entry surface, including dictation when available in the selected simulator/device configuration.

Tapping **Guardar** stores the item at the app's Application Support outbox and shows:

```text
Guardado en outbox · dry-run
```

## Verification

HermesCore XCTest coverage now includes:

- payload contract;
- HMAC V2 vector;
- required webhook headers;
- dry-run factory behavior;
- file outbox persistence;
- duplicate `request_id` deduplication;
- status transition from pending → sending → sent.

## Next phase

1. Add endpoint configuration to the iPhone companion.
2. Store route secret in iPhone Keychain.
3. Bootstrap endpoint and route-scoped secret to Watch Keychain using WatchConnectivity.
4. Add an outbox sender that signs exact bytes and POSTs to the Tailscale HTTPS BFF.
5. Keep `dry_run` and server-side no-write gates enabled.
