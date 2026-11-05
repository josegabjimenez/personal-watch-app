# Watch history and iPhone diagnostics

> **Historical record.** This document retains the earlier app name and its recorded candidate or planning stage. The current app is **Personal Watch App**; use [the current three-action MVP](PERSONAL_WATCH_APP_MVP.md) and [device setup](XCODE_DEVICE_SETUP.md) for current scope and readiness.

**Status:** implemented; Xcode simulator validation required.

## Watch history

The Watch home screen now includes **Historial**. It reads the same file-backed outbox used by delivery and displays newest items first.

Each row shows only local capture data:

- friendly route name;
- capture text;
- `pending`, `sending`, `sent`, or `failed`;
- attempt count;
- sanitized `last_error` code when present.

The history view never reads or displays the HMAC secret, request signature, private endpoint, or server response body.

## iPhone diagnostics

The iPhone configuration screen now includes **Actualizar diagnóstico**. It uses an interactive `WCSession.sendMessage` request while both apps are reachable.

The Watch returns only:

```text
configured: Bool
outbox_readable: Bool
outbox_total: Int
outbox_pending: Int
outbox_sending: Int
outbox_sent: Int
outbox_failed: Int
```

The response is rejected by the iPhone if values are negative or if:

```text
pending + sending + sent + failed != total
```

No capture text, secret, signature, endpoint, device ID, or request ID crosses to the iPhone diagnostics view.

## Retry verification

HermesCore includes a controlled test that:

1. persists a capture;
2. simulates `URLError.notConnectedToInternet`;
3. verifies `failed`, attempt `1`, and the original request ID;
4. retries the same payload;
5. receives a safe dry-run response;
6. verifies `sent`, attempt `2`, no residual error, and no duplicate outbox item.

This preserves server idempotency because retry reuses the original `request_id`.

## Existing user validation

Jose verified all current Watch routes through the live staging BFF:

```text
Megan expense: Dry-run Megan $45.000 COP
Aura grocery: dry-run ✅
Aura reminder: dry-run ✅
Argos general capture: dry-run ✅
```

External writes remain disabled.

## Simulator checklist

1. Pull and regenerate the Xcode project.
2. Run the iOS and Watch targets on the existing paired simulators.
3. Open **Historial** on Watch and verify the four successful captures show `Enviado`.
4. Keep Hermes open on Watch.
5. On iPhone tap **Actualizar diagnóstico**.
6. Verify:
   - Watch configurado: `Sí`;
   - Outbox legible: `Sí`;
   - Total equals the sum of displayed status counts;
   - Enviados includes the verified captures.

## Deferred production work

- define bounded retention/compaction for an outbox that grows indefinitely;
- validate on physical Apple Watch;
- test accessibility and long localized strings;
- add corruption recovery without silently deleting captures;
- keep all writes disabled until separate server/domain authorization is approved.
