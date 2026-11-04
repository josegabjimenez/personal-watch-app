# Luna and USD 2 budget — 4 November 2026

Jose approved continuing existing PR #6 with GPT-6 Luna, a USD 2/month grocery
application guard, synthetic live inference checks and a narrowly scoped
installation/configuration/restart of the existing grocery backend. No merge is
assumed. Reminder/Todoist, expenses/Firefly, HA and general capture remain held.

## Current state

Code, offline tests, reviewed release and operator tools are prepared. **Not
installed or activated yet; no live Luna request has been made.** The coordinator
owns account eligibility/credit verification and secure key provisioning. It
reported that OpenAI passkey verification still needs the human; no server key
path/status has been supplied to this execution. ChatGPT Pro is user-confirmed,
but eligibility, claimed credit, balance and usable API access are not verified
here. No desktop or held reminder credential was discovered or reused.

A read-only HTTPS check of the existing personal endpoint returned healthy
`allowlisted-write` with only `aura.grocery_capture`. The previous live-service
owner was idle. This confirms baseline availability, not new-code installation,
file hashes or AI readiness. No server code, service, configuration or grocery
data was mutated in these checks.

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
| Upgrade fault/metadata/data-preservation contracts | 39 tests OK |
| Independent focus including ops | 161 tests OK |

Review caught and resolved current cache-write dimensions/30-minute pricing,
volatile `systemctl ExecStart` fingerprint fields, smoke execution binding to
verified bytes, the separate manifest/installed-files interface and month-crossing
cost reporting. None of the offline tests prove a live provider account,
semantic accuracy, service installation or physical Watch acceptance.

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

## Exact remaining handoff

The coordinator supplies only the absolute server API-key file path plus private
file/access status. It must be an ordinary non-symlink file, owned/readable by
the existing service user, mode `0600`, at most 4,096 bytes. Do not paste the key
or copy it to this checkout. No additional budget/restart approval is needed for
the already approved scope.

With that handoff and usable scoped server access, rerun exact preflight to
establish current service arguments, hashes, helper/config metadata and absence
of active/pending writers. Apply only the pinned six-file upgrade after its
verified backups. Then verify installed hashes, scope, arguments and HTTPS,
run the bounded synthetic smoke against the same journal, and let Jose submit
his intended Watch grocery request. No app reinstall is required. Do not claim
ready until these live checks actually pass; preserve both SQLite files and the
budget marker, and never restore old allowance/history to bypass failure.

See [current behavior/setup/rollout](../../GROCERY_AI_MATCHING.md) and the upgrade
instructions in `ops/hotfixes/grocery-ai/UPGRADE.md`. The original 3 November
report remains historical evidence of the earlier five-file proposal.

Canonical task: https://app.notion.com/p/40dfcdfcb7b481cdb1d8d02f51d654fe
