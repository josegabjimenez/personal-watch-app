# Luna and USD 2 budget — 4 November 2026

Jose approved continuing existing PR #6 with GPT-6 Luna, a USD 2/month grocery
application guard, synthetic live inference checks and a narrowly scoped
installation/configuration/restart of the existing grocery backend. No merge is
assumed. Reminder/Todoist, expenses/Firefly, HA and general capture remain held.

## Current state

**Installed and active; semantic verification is partial.** The exact six-file
runtime from commit `f8738b9a1529c09a7a577550706d7fb876ea6f89` was built and
verified on the VPS, installed with the same seal below, and verified again in
the running service. HTTPS returned healthy `allowlisted-write` with only
`aura.grocery_capture`. The existing service owner was inactive before mutation.

Jose explicitly approved encrypted transfer through the authenticated Hostinger
Web Console when the Mini had no usable SSH identity. Only the newly provisioned
key from its approved local destination was used. A server-only temporary RSA
envelope protected the transfer; the raw key never appeared in commands, chat,
Git or logs. The target ordinary private file passed root ownership/mode checks;
the temporary private encryption material was removed. No SSH access or policy
was added, and held integration credentials were not discovered or reused.

Three small synthetic provider calls were made without helper/Notion/grocery
writes. The explicit-missing case passed. The original compound smoke failed;
a distinct comma diagnostic returned `needs_input`. These results do not establish
compound interpretation accuracy or physical Watch acceptance.

## Live installation and recovery

Read-only preflight passed before installation. The upgrader verified backups of
the four previous code files and both SQLite files, then initialized the budget
while stopped and installed all six target files. Its immediate post-start check
reported `service_arguments` and deliberately stopped the unit, retaining the
new code, budget marker and data. No old writer or database restore was used.

A separately reviewed compatible recovery checked all six target hashes, the
exact grocery drop-in, source/paired-database backup hashes and integrity, legacy
table contents, data identities, private-file metadata and unchanged base
configuration. It bound those checks to the actual service entrypoint and ledger,
held the operator lock, started only `hermes-mobile-capture.service`, waited for a
valid target snapshot and verified strict grocery-only HTTPS. The service is
active. No data history or allowance was reset.

The precise transient process snapshot was not retained. `Type=simple` and the
immediate snapshot path exposed a readiness gap; the operator now waits for the
three bounded transient startup observations before running every original strict
comparison. That correction changes operator code only; the installed six-file
runtime and its seal are unchanged.

| Synthetic live check | Observed result | Latency |
| --- | --- | --- |
| Original compound/synonym fixture | Validated interpretation differed from expected; stopped original harness | 2,756 ms |
| Distinct comma-separated diagnostic | `needs_input`, zero proposed items; stopped diagnostic | 1,747 ms |
| Originally planned explicit-missing fixture | Correct source span and candidate; `apply` | 1,634 ms |
| Ambiguous-variant fixture | Not dispatched | — |

The first settled failure and every request identity remain preserved. The lost
first proposal cannot establish its exact semantic cause; a changed diagnostic
does not make the original smoke pass. No calls were retried by resetting IDs.
Final guard accounting was 892,900 nanodollars charged, zero unsettled reservation
and 1,999,107,100 nanodollars remaining in the Bogotá November allowance. This is
the guard's conservative accounting, not a provider invoice.

## Implemented

- Exact `gpt-6-luna`, low reasoning, Standard/default tier, 30-minute implicit
  caching, strict structured output, no tools, no model/tier/rate/cap override.
- Existing one-interpretation claim and v1/v2 grocery identity/retry/partial
  recovery preserved. Budget is admitted durably immediately before dispatch.
- Fixed USD 2 application cap in Bogotá calendar months. Maximum reservation
  USD 0.318 covers the entire documented context and all 3,000 permitted output
  tokens. Valid full usage settles at conservative input/output envelopes;
  output already includes reasoning. Unknown, timed-out and lost outcomes keep
  their full reservation across month changes and restarts.
- Serialized SQLite admissions, immutable policy, checksummed rows/aggregate
  verification, explicit initialization marker, fail-closed missing/corrupt state,
  backwards-month refusal and no request-time initialization/reset.
- New six-file historical grocery package; original historical server/overlay,
  helper pin, old four-file installer/updater and Apple source unchanged.
- New explicit upgrade tool for the exact installed four-file predecessor,
  paired verified code/data backups, stopped-unit initialization, grocery-only
  scoped drop-in and strict readback. Post-initialization failures keep compatible
  state/code and stop the one unit; there is no database restoration/downlevel
  writer fallback.
- A fixed three-case live-smoke harness verifies actual installed bytes against
  a separately pinned manifest, compiles only verified sources and calls Luna
  with synthetic data against the same initialized budget journal. It imports no
  helper, makes no grocery writes, stops after failure and never repeats a run's
  dispatched IDs. Accounting change is labeled guard-wide and omitted when the
  month changes; it is not claimed as an exact provider bill.

The guard is an application admission control at the reviewed pricing
assumptions, not an account-wide invoice cap or an advisory dashboard budget.
Other clients, taxes and future pricing are outside this code's control.
Promotional credits do not enlarge its allowance. Unknown obligations may keep
the guard exhausted after a calendar rollover; no reset/repair API frees them.

## Evidence

| Check | Result |
| --- | --- |
| Full offline backend suite | 349 tests OK, one optional private-snapshot check skipped |
| Actual generated historical six-file runtime | 31 tests OK |
| Budget/provider/AI-engine focus | 88 tests OK |
| Synthetic smoke harness | 6 tests OK |
| Shared Apple core/outbox protocol | 70 tests passed, no app source change/install |
| Upgrade fault/metadata/data-preservation contracts | 44 tests OK, including five startup-readiness/failure tests |
| Complete operator/package/smoke suite after readiness correction | 81 tests OK |
| Local encrypted transport contracts | 14 provisioning + 12 envelope tests OK, synthetic only |
| Earlier independent focus including ops | 161 tests OK at frozen runtime delivery |
| Final operator and compatible recovery review | No blocking findings after targeted corrections |

Review caught and resolved current cache-write dimensions/30-minute pricing,
volatile `systemctl ExecStart` fingerprint fields, smoke execution binding to
verified bytes, the separate manifest/installed-files interface and month-crossing
cost reporting. Offline checks remain distinct from the live results above;
physical Watch acceptance and compound semantic accuracy remain unestablished.

Six-file seal:
`bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281`.
The exact source hashes live in `ops/hotfixes/grocery-ai/release_manifest.json`.
Build/verify rejects source drift and does not import a helper or deploy.

The authoritative model, reasoning-token and prompt-caching docs were searched
and opened on 4 November:

- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna): Responses,
  low reasoning, structured output, Standard USD 0.10/M input and USD 0.50/M output,
  documented context/multipliers.
- [Reasoning cost controls](https://developers.openai.com/api/docs/guides/reasoning):
  total output limits include reasoning and non-visible output.
- [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching):
  explicit 30-minute retention, known cache-write usage dimensions and rate.

## Remaining acceptance

Jose can make a narrow intended Watch request for one product he actually needs,
beginning with `me falta [producto]`, and verify its pending state in his list.
No app reinstall is required. If clarification is returned, send a complete new
dictation; contextual `sí`/`el primero` remains unsupported. Record the exact
dictation and displayed result before interpreting physical acceptance.

The compound cases still require investigation; do not report the original smoke
suite or general AI matching as accepted. Preserve both SQLite files and the
budget marker. Never restore older allowance/history or reset an identity to
repeat an uncertain call. No merge or held-domain activation occurred.

See [current behavior/setup/rollout](../../GROCERY_AI_MATCHING.md) and the upgrade
instructions in `ops/hotfixes/grocery-ai/UPGRADE.md`. The original 3 November
report remains historical evidence of the earlier five-file proposal.

Canonical task: https://app.notion.com/p/40dfcdfcb7b481cdb1d8d02f51d654fe
