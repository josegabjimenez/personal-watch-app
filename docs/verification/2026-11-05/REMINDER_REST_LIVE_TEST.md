# Reminder REST recovery and live test — November 5, 2026

Reminders are enabled with the approved **USD2/month** app allowance, separate
from Mercado. The additional approved live test now has an exact saved-task
readback, a cached replay of the original body and one verified Todoist alert.
Phone/Watch delivery is not verified. This remains a server test, not physical
Watch-input acceptance or acceptance of the entire MVP.

## Diagnostic and correction

Jose explicitly approved one additional test with a 60-minute window under the
existing allowance. Its original capture was frozen at 17:11:46 Bogotá, with a
fixed intended time of **18:11 Bogotá on November 5**. It was dispatched once
through the sealed signed handler in an isolated child. The observer recorded
successful user/label/task reads and an HTTP200 Sync response whose own command
failed with `AUTH_INSUFFICIENT_TOKEN_SCOPE` /403. The original first test's
uncertain outcome remains unresolved; this second command's rejection does not
prove that the first write failed.

The correction uses JSON `POST /api/v1/tasks` under the existing `task:add` and
`data:read` grants. The [official SDK](https://doist.github.io/todoist-api-python/api/)
supports the fixed `due_datetime`, `auto_reminder` and `auto_parse_labels` fields.
New captures persist REST selection; existing operations retain Sync. A durable
single-dispatch claim and the legacy-recognized `reconciliation_required` status
commit before REST POST. After a consumed claim, retries are read-only even if
no active task is found. The stable request header is additional correlation;
no undocumented REST retry-retention guarantee is assumed. Failure and elapsed
status transitions serialize with late worker claims.

No automatic Sync-to-REST fallback exists. A separately reviewed, one-use operator
verified the second capture's own definitive rejection, original body binding,
frozen plan/account/UUIDs, future time and settled usage before changing only its
transport. The first unknown capture remained unchanged. No third capture,
replacement date/identity, additional model call, broader scope, global setting
change or Todoist upgrade was used.

## Exact deployment

Runtime source: `9b2884671e74afc7865663dc1e29d25eed3d26bf`.
Twelve-file manifest SHA256:
`55d14d53043179256791d14242c4f44b17bb33534dd7e18102c61c6bbc17b038`.

Exactly `reminder_capture.py` and `todoist_reminders.py` differ from the prior
OAuth-corrected seal. The other ten artifacts, accepted grocery baseline,
grocery file inodes, existing configuration, allowance, sandbox and private
network topology remain pinned. The obsolete updater remains inactive.

At `2026-11-05T22:39:14.933401+00:00`, the reviewed two-module upgrade returned
`verified_enabled`. Coherent stopped-service backups and read-only validation
preserved all ledgers, spending records, witnesses and rotating OAuth state.
No application state was restored. Rollback closes reminder writes before
restarting legacy code. The historical installation/activation operators keep
their old pins; a separate frozen profile verifies this upgrade.

Post-test verification at `2026-11-05T22:41:15.531504+00:00` confirmed the exact
new source/seal, USD2 allowance, both domains and loopback health. The existing
private HTTPS endpoint independently returned health `ok` and both domains.

## Observed live result

The exact second body was sent through the actual signed HTTP endpoint after
the proof-bound migration. Todoist's exact GET verified title, description
marker, existing Inbox, account binding, empty labels, default priority,
nonrecurring Bogotá due time and absence of a deadline. The task ID persisted
before readback. Recovery reused the original plan and command identities.

| Measurement | Observed |
| --- | --- |
| Task and original-body replay | Verified; same task |
| Intended task time | 2026-11-05 18:11 America/Bogota |
| Actual recovery HTTP duration | 1166.964 ms |
| Cached replay HTTP duration | 92.489 ms |
| Extra model calls during recovery/replay | 0 |
| Budget and operation changes from replay | None |
| Todoist reminder inspection | One relative alert, offset 0 minutes |
| Alert instant / timezone | 2026-11-05T23:11:00Z / America/Bogota |
| Alert recipient | Expected account verified privately |
| Phone/Watch receipt | Unverified |

The second interpretation used 1003 input and 126 output tokens, with
USD0.0004269 settled conservative app-budget accounting. Together with the first
capture's USD0.0005269, the two authorized model attempts account for
**USD0.0009538**. These guard values are not provider-invoice measurements.
No reservation or counter was reset or refunded manually. Recovery added no
model usage. The initial diagnostic's 4627.472 ms measured child startup and full
handler execution; it was not an actual HTTP latency measurement.

At 22:40:26 UTC, the connected Todoist tool independently returned the task's
single nonrecurring, zero-offset reminder with the expected Bogotá instant and
recipient. This establishes configured alert state, not device delivery.
The app's normal response continues to distinguish task save from unverified
alert delivery.

## Verification and remaining review

The backend suite completed 477 tests with one optional skip; all 37
generated-package/runtime tests passed, and independent review approved the final two runtime hashes.
The upgrade's 16 tests, rejection proof's 8 tests and 6 independent real-SQLite
recovery flow tests passed. The latter cover one create plus cached replay,
unchanged original capture/parent/budget, missing preservation evidence,
compare-and-set conflicts, crash after migration and lost acknowledgements.
Both [push CI](https://github.com/josegabjimenez/personal-watch-app/actions/runs/42122873449)
and [PR CI](https://github.com/josegabjimenez/personal-watch-app/actions/runs/42122882489),
plus GitGuardian, passed on the exact runtime source above.

[PR #7](https://github.com/josegabjimenez/personal-watch-app/pull/7) remains open.
The original first capture remains uncertain and preserved; do not resend it,
change its expired time, infer absence, or migrate it using the second test's
proof. Device alert observation and the remaining physical MVP acceptance are
still pending. [Historical first activation/test receipt](REMINDER_ACTIVATION_TEST.md).
