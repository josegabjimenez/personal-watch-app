# WatchConnectivity secure bootstrap phase

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Status:** implemented; paired iPhone + Watch simulator validation required.

## Flow

```text
User taps “Enviar configuración al Watch”
  → iPhone loads route secret from Keychain
  → validates HTTPS MagicDNS base URL
  → WCSession.sendMessage (interactive/reachable only)
  → Watch validates payload
  → Watch stores secret immediately in Watch Keychain
  → Watch stores non-secret endpoint in local UserDefaults
  → Watch replies success
```

## Security choices

- Uses `sendMessage` only while the Watch app is reachable.
- Does **not** use `updateApplicationContext`, `transferUserInfo`, files, logs, or background queues for secret delivery.
- No secret is persisted in plaintext by app code.
- The Watch trims and writes the secret directly to `kSecClassGenericPassword` with `AfterFirstUnlockThisDeviceOnly`.
- The endpoint must be HTTPS and use a DNS hostname, not a raw Tailscale IP.
- The user explicitly initiates every bootstrap.
- This is credential provisioning, not authorization for real writes; BFF server-side dry-run gates remain authoritative.

## Simulator validation

1. Pull and regenerate the Xcode project.
2. Launch `HermesCaptureiOS` on the paired iPhone simulator.
3. Launch `HermesCaptureWatch` on the paired Watch simulator and leave it foregrounded.
4. Confirm the iPhone displays **Apple Watch conectado**.
5. Tap **Enviar configuración al Watch**.
6. Confirm the iPhone displays **Configuración segura enviada al Watch**.
7. Confirm the Watch displays **Configuración segura lista**.
8. Restart the Watch app and confirm the ready indicator persists.

## Failure behavior

- If Watch is not reachable, the iPhone asks the user to open Hermes on Watch.
- Invalid HTTPS endpoint or empty secret is rejected before sending.
- Watch rejects malformed/unknown commands.
- Delivery errors do not delete or mutate the iPhone Keychain value.
- The secret and endpoint are never included in user-facing error text.

## Next phase

Implement the Watch outbox sender:

1. load Watch endpoint + Keychain secret;
2. mark outbox item `sending`;
3. sign exact encoded bytes with HMAC V2;
4. POST to `/webhooks/mobile-capture-v1`;
5. mark `sent` or `failed`;
6. show the BFF `display_message` and haptic feedback;
7. keep all requests in dry-run/no-write mode.
