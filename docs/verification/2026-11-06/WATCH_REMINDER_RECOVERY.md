# Stuck reminder recovery and connection diagnosis — November 6, 2026

Physical Watch acceptance has **not passed**. Jose reported an existing
Recordatorio that locked the composer and showed “Sin conexión” on retry.
The matching History row reports `network_-1003` and four attempts. That code
establishes the most recent hostname-resolution failure, not the outcomes of
earlier attempts or absence of an external task. The original capture remains
protected; its exact identity and remote receipt have not yet been reconciled.
No new user Watch test or assistant-generated live capture was sent here.

## Client correction

Client source: `b7d5f41eb59a30667b9d5c5e89c1b138b15e4ba3`.
Candidate: **0.3.6 (9)** for the existing iPhone and Watch applications.

Previously, restoring a pending Reminder disabled editing and reused that
capture for every send. Only Mercado offered an escape to a fresh editor.
The new **Nuevo recordatorio → Conservar y abrir nuevo** action atomically
retains the original in History before opening a blank draft. It adds optional
local retry-suppression metadata outside the payload. Original text, UUID,
creation time, encoded request body, attempts, last error, receipt and delivery
status remain intact at preservation. It does not cancel a server task or claim
that the earlier request was never saved.

Suppression survives reopening the app and excludes that row from composer
restore, bulk retry and a new delivery claim. All transitions use the existing
cross-instance file lock. A request already in flight may still finish; a late
acknowledgement updates its actual delivery result without clearing suppression.
Storage errors keep the old editor attached. A race that already produced a
terminal receipt releases the editor only after exact safe correlation.
The next explicit Enviar creates a fresh capture for a **different intention**.

History preserves the row and shows its delivery state separately from local
retention. Read-only details expose the selected reminder's complete text,
request ID, original capture time and error for precise reconciliation.
Expired reminders are not retimed locally. A verified terminal no-write response
clears the composer while retaining the original response; unknown outcomes
remain unknown.

## Connection evidence and diagnostics

The old generic message collapsed DNS, connection refusal, missing internet,
timeout, lost connection and TLS failures. Fixed local messages now distinguish
those categories, cancellation and authorization rejection. Numeric error codes
remain stored; raw error descriptions, private URLs and provider bodies are not
shown. A timeout/lost reply explicitly allows an uncertain saved outcome.

**Comprobar conexión** performs only an unsigned GET of the configured HTTPS
`/health` endpoint. It sends no capture, request identity, HMAC, secret or stored
authorization, and does not change the outbox. Its ephemeral session disables
cookies, credential storage, caching and redirects while retaining system TLS
validation. Request/resource timeouts are 8/10 seconds and only expected health
responses of at most 4096 bytes are accepted. An accessible health endpoint does
not verify HMAC or prove a reminder write succeeded.

Live Reminder still uses direct URLSession delivery; the app's explicit
WatchConnectivity relay remains dry-run-only. Apple's system URLSession route
may use the phone or Watch networking; pairing or Mac reachability does not
establish the actual Watch route. [Apple networking routes](https://developer.apple.com/documentation/watchos-apps/keeping-your-watchos-app-s-content-up-to-date),
[TN3135](https://developer.apple.com/documentation/technotes/tn3135-low-level-networking-on-watchos).

Current observations remain separate:

- The existing private backend HTTPS health check succeeds from the Mac and
  advertises grocery and reminder write domains. This is not Watch reachability.
- Jose's latest selected Watch error is DNS `-1003`, attempts 4. The configured
  hostname and previous-attempt outcomes have not been read back today.
- A CoreDevice inventory advertised a connected Watch management tunnel; the
  selected app metadata read and a selected-file backup nevertheless timed out.
  The iPhone management route was unavailable at that observation. No app data
  was copied or changed, and no installation was attempted.
- The latest confirmed historical device installation is 0.3.5 (8) from November 1.
  Current source compatibility had been checked, but that did not establish
  present connectivity. Today's UI correction requires a client update.

No Tailscale setting, DNS policy, public exposure, certificate trust, HMAC check,
iOS/Todoist notification setting, server service, credential or spending cap was
changed. The backend runtime and approved USD2/month reminder allowance are not
modified by this client fix. No old server test was replayed.

## Candidate and verification

Both signed applications built with Xcode 27.2. Signature verification, existing
team/application identifiers and valid cached device profiles were checked.
Bundle identities, module names, outbox location and Keychain code remain
unchanged. Signed bundles, profiles and device identifiers stay in the private
project archive. The production-source manifest covers 29 files:
`c5cf7b676cd3d447c3328a6e7e152cf568c34f279fb217d5c5b620a205ee6c53`.
No device was updated or plain-launched as part of this preparation.

- Shared Swift suite: 87 tests passed.
- Independent focused review: 15 tests passed, no consequential findings.
- Focused backend recovery checks: 7 tests passed with fake providers; no backend
  code changed and the full 477-test suite was not rerun locally.
- Six new simulator UI cases passed: DNS preservation/new draft/relaunch,
  cancellation, retained unknown sending, correlated pending response,
  expired no-write receipt and read-only connection check without another retry.
- Full 16-case iOS simulator regression: passed with zero failures/skips in
  356.476 seconds. Simulator results are not physical Watch acceptance.

The first build found an unavailable Watch DisclosureGroup; it was replaced by
read-only NavigationLink details. UI-only selectors were refined for the native
confirmation/popover hierarchy without weakening the state assertions. The
final signed application code is unchanged by those test-only corrections.

## Targeted update and recovery plan

1. Re-establish read-only access to the two existing paired devices. Obtain fresh
   selected-file backups of each app's outbox and preferences. Do not export
   Keychain secrets. Verify the selected app identities and candidate signatures
   before an install; management discovery alone is insufficient.
2. When the original request is identified, inspect only its exact backend record
   and corresponding external receipt/alert. A missing active match does not
   establish task absence. Preserve any old or uncertain result and apply the
   existing elapsed/read-only semantics.
3. Update the existing iPhone application in place and its exact embedded Watch
   bundle with the same identities. Never uninstall, unpair or reset to clear UI.
4. Read back versions and the saved files before accepting the update. Compare
   original outbox bytes and endpoint privately. Preserve both snapshots if
   anything differs; do not restore an older outbox automatically over newer
   state or re-enter/change credentials speculatively.
5. Plain launch without synthetic flags. Check the Watch's connection with the
   new read-only control. The user can explicitly retain the selected old
   reminder locally and open a new draft; this is not remote cancellation.
6. Only once the path and retained state are understood should Jose send the
   separately approved new ten-minute Watch test. Correlate its task, alert,
   outbox/UI state and human-observed notification as separate evidence.

Older client binaries ignore the optional suppression field. Do not downgrade
while retained reminders remain unresolved; preserve the retention choices in
any separately reviewed rollback. Backend data, budgets and rotating OAuth state
must never be reset/restored to work around an uncertain result.

[PR #7](https://github.com/josegabjimenez/personal-watch-app/pull/7) remains open;
this work does not authorize its merge or claim completed physical acceptance.
[Earlier server task/replay and configured-alert evidence](../2026-11-05/REMINDER_REST_LIVE_TEST.md).
