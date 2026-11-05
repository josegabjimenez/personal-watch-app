# App Intents and Siri dry-run capture

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Status:** general-capture App Intent verified through manual Shortcut and Siri on physical iPhone, plus native Siri execution on physical Apple Watch. The historical simulator Auto Shortcuts regression remains simulator-only.

## Verified iPhone Shortcut result — 2026-07-20 America/Bogota

Jose manually created a Shortcut using the Hermes expense App Intent and confirmed:

```text
Shortcut execution → live dry-run response
Hermes iOS local outbox → Enviado
```

This verifies:

```text
Shortcuts → App Intent → iPhone outbox → iPhone Keychain →
HMAC V2 → Tailscale HTTPS → synchronous BFF → display response
```

The manually created Shortcut works. Only simulator Auto Shortcut discovery remains affected by the Apple regression below. No external write occurred.

## Verified physical Siri and Shortcuts result — 2026-08-04 America/Bogota

Jose verified the general-capture intent on the paired physical devices:

```text
manual iPhone Shortcut: dry-run response and iPhone outbox sent count +1
iPhone Siri phrase "Capturar con Hermes": dry-run response and sent count +1
Apple Watch Siri phrase "Capturar con Hermes": dry-run response
native Watch evidence: new item in Watch-local history, Enviado, attempt 1
```

The Watch-local history item distinguishes native watchOS execution from an iPhone-delegated Shortcut. The expense, reminder, and grocery phrases remain individually unverified on physical Siri, although they share the same runner and tested payload/delivery core.

No capture content, endpoint, secret, Team ID, or device identifier is recorded in this evidence. No external write occurred.

## Intents

Hermes exposes four authenticated App Intents:

```text
Registrar gasto en Hermes       → megan.expense_capture
Crear recordatorio en Hermes    → aura.reminder_capture
Agregar al mercado en Hermes    → aura.grocery_capture
Capturar con Hermes             → argos.general_capture
```

Each intent asks the system for free-form text, creates the same `CapturePayloadV1` used by the Watch UI, persists it before network access, signs it with HMAC V2, and returns the synchronous BFF `display_message` to Siri/Shortcuts.

## Suggested Spanish phrases

```text
Registrar gasto con Hermes
Nuevo gasto en Hermes
Crear recordatorio con Hermes
Recuérdame algo con Hermes
Agregar al mercado con Hermes
Mercado en Hermes
Capturar con Hermes
Nueva captura en Hermes
```

The phrases contain no endpoint, credentials, capture content, or personal data.

## Authentication policy

Every intent declares:

```swift
.requiresLocalDeviceAuthentication
```

The system must authenticate/unlock the local device according to platform policy before execution. `openAppWhenRun` remains `false` so Siri can return the result directly.

## Local-first delivery

```text
Siri/App Shortcut
  → free-form text parameter
  → CapturePayloadV1 with modality app_intent
  → file outbox
  → route-scoped secret from local Keychain
  → direct HTTPS + HMAC V2
  → synchronous dry-run response
  → spoken/displayed result
```

The payload remains:

```text
dry_run: true
allow_write: false
```

## Execution surfaces

- On iPhone, the intent uses the endpoint and Keychain configured in the iPhone app and the iPhone-local outbox.
- On Watch, it uses the endpoint and Keychain provisioned through the secure bootstrap and the Watch-local outbox.
- WatchConnectivity is not required for the capture request after Watch provisioning.

The iPhone companion now shows aggregate counts for its own Siri/App Intent outbox and can retry deliverable iPhone items.

## Concurrent outbox hardening

The outbox now uses a sibling `outbox.json.lock` advisory file lock around every read-modify-write operation. This prevents separate UI/App Intent store instances from silently overwriting each other's captures inside the same app container.

The lock contains no payload or credentials and is released automatically when its file descriptor closes or the process exits.

## Controlled offline test

Debug builds support the environment variable:

```text
HERMES_SIMULATE_OFFLINE=1
```

When enabled, delivery fails after `markSending` and transitions the already-persisted item to `failed` with sanitized network state. It never contacts the BFF.

Manual flow:

1. Add the environment variable to the Watch scheme and run.
2. Capture text from the Watch UI.
3. Confirm the item appears `Falló` in history.
4. Remove the variable and relaunch.
5. Tap **Reintentar pendientes**.
6. Confirm the same item becomes `Enviado` with attempt count `2`.

This hook is compiled only in Debug builds.

## Automated verification

HermesCore coverage includes:

- App Intent modality while preserving dry-run/no-write context;
- 20 concurrent enqueues through separate store instances with zero lost items;
- network failure preserving the original request ID;
- successful retry to `sent` with attempt count `2`;
- existing HMAC, endpoint, headers, idempotency, and response safety tests.

## Known Xcode 26 Simulator regression

Apple Developer Forums document a simulator regression matching this project exactly:

```text
Attempted to fetch Auto Shortcuts, but couldn't find the AppShortcutsProvider
```

Affected combinations reported by Apple developers include:

```text
Xcode 26.5 through Xcode 27 beta 2
iOS Simulator 26.5 through iOS 27 beta 2
```

The same error occurs with Apple's official App Intents sample, so this log alone does not prove that the app's provider is missing. Jose reproduced it with Xcode 26.6 and an iOS 26.5 simulator.

Recommended validation order:

1. Confirm the Xcode build contains a successful `ExtractAppIntentsMetadata` step.
2. In Shortcuts, manually create a shortcut by adding one Hermes App Intent action instead of running an automatically suggested App Shortcut.
3. If available, test with an older compatible simulator runtime such as iOS 26.3.
4. Treat a physical iPhone/Watch run as the authoritative App Shortcuts validation.

References:

- <https://developer.apple.com/forums/thread/835888>
- <https://developer.apple.com/forums/thread/836585>

Do not rewrite a valid `AppShortcutsProvider` solely to work around this simulator regression.

## Remaining Apple QA

- invoke the expense, reminder, and grocery Spanish phrases individually on physical iPhone and Watch if release coverage requires every phrase;
- verify authentication behavior while each device is locked;
- validate a fresh TestFlight install and bootstrap;
- keep the archive privacy artifact evidence with release records.
