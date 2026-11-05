# Reminder activation and first live test — November 5, 2026

Jose explicitly requested enabling reminders and one live test, then approved
a separate **USD2/month reminder allowance**. Mercado's allowance is unchanged.
This is the app's admission budget, not a prepaid charge or an account-wide
OpenAI billing limit.

## Activation verified

At `2026-11-05T21:21:00.845217+00:00`, the reviewed activation operator returned
`verified_enabled`, phase `complete`, with exactly these effective domains:

```json
["aura.grocery_capture", "aura.reminder_capture"]
```

An independent GET through the existing private HTTPS endpoint returned the same
domain list and health `ok`. Only the private reminder environment changed: the
allowance became `2` and the domain override included reminders. The original
unit/environment/drop-ins, argv, sandbox, network, twelve runtime hashes and five
grocery file inodes remained pinned. Existing reminder spending and witnesses
were validated using coherent private copies; nothing was initialized, reset or
restored. The updater remains inactive.

The exact deployed source is still `f8294bebeda53960f222e77cce3cc75bace74529`;
the twelve-file seal remains
`f3e0996c29cc3f88d3132db5885420345f8e07a8c9d4b00dff6cf177243ba92a`.
The private activation operator SHA256 is
`f8fdfa34f3c7a38adca11ca6224df548ccba27fe54a6b3c63d48eda45fa5e22b`;
all 22 synthetic activation tests passed independently.

## One live capture; save remains unverified

One durable, synthetic server capture was sent through the actual signed HTTP
endpoint. Its title was `Prueba de recordatorio Personal Watch`, captured at
16:23 Bogotá and intended for **16:33 Bogotá**. The original body, capture time
and request ID were saved before the signed HTTP dispatch. The server persisted
the deterministic command/temp identities before any possible Todoist command
dispatch.
This was a server canary, not evidence of input from a physical Watch.

The AI step completed and its usage settled:

| Measurement | Observed |
| --- | --- |
| Model attempts | 1 |
| Input tokens | 1003 |
| Output tokens | 226 |
| Conservative app-budget charge | USD0.0005269 |
| First signed HTTP duration | 5670.549 ms |

The charge above uses the reviewed guard's upper rates. It is not an observed
provider invoice. The original USD0.32 reservation was settled with verified
usage; no manual refund/reset or second model call was made.

The request did **not** produce an authoritative saved-task receipt. Its own
operation remains `uncertain`, with no known task ID. A read-only reconciliation
of the original marker/plan/identity completed without finding an active exact
match. That result does not prove that no previous task write occurred.

A reviewed same-body recovery was prepared, but its guard refused dispatch
because the original due time had elapsed. It submitted no recovery POST. No
replacement request ID, altered plan/date, second capture, manual ledger patch,
task cleanup or global notification setting change was used. The original
capture and spending record remain available for reconciliation.

The frozen canary runner passed 19 independent synthetic tests; the read-only
reconciliation helper passed four and the bounded recovery helper passed five.
Exact-body cached replay, saved-task readback, actual alert configuration and
phone/Watch delivery remain **unverified**.

## Read-only diagnosis and next boundary

Command-free Sync probes returned HTTP200 without submitting task commands.
Scoped reads verified that push, desktop and email notification settings are
enabled, the current plan supports at-due alerts, and Inbox capacity is 10 active
tasks against a limit of 300. These observations do not establish the original
provider failure's cause. No scope expansion or Todoist upgrade is justified
by the evidence. The original gateway intentionally exposes fixed safe errors;
it did not retain detailed provider failure diagnostics.

Only one live test was authorized and dispatched. A diagnostic follow-up with
safe HTTP/command error-code capture is being prepared for review. Executing a
new future capture could create an additional task and requires Jose's approval
for that additional test. The original uncertain identity will remain intact.

Activation is verified; functional reminder save and device notification
acceptance are not. The Watch MVP remains in progress.
