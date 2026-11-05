# Mercado AI candidate verification — 3 November 2026

Implementation and review are complete for this bounded candidate. Nothing has
been installed or activated. Jose's real failing dictations have not yet been
supplied; Mercado acceptance remains pending.

## Verified behavior

- Strict model proposal over the complete bounded actual candidate catalog;
  exact source spans/coverage and trusted candidate, quantity, attribute and
  operation validation before any batch dispatch.
- Spanish multi-item, compound conjunction, quantities, accents, head typos,
  selected product synonyms and lactose-free equivalence, using synthetic
  provider Responses fixtures and the exact extracted helper AST.
- Bought status preserved unless the original request explicitly begins
  “me/nos falta(n)…”. This trusted intent is frozen independently of model
  fields; retry never re-unchecks a later human purchase.
- Whole-request clarification for duplicate/near-match ambiguity, quantity or
  material-attribute conflict; invented IDs, missing qualifiers, quantity-only
  spans and decimal/measurement boundary corruption refuse without writes.
- Untrusted catalog text cannot grant tools or destinations. Malformed,
  refused, incomplete, unavailable and timed-out inference produces no claimed
  write or silent parser fallback.
- Durable single inference claim, saved interpretation before prepare, frozen
  parent/item identities, concurrent duplicates, stale snapshots, v1 upgrade,
  partial writes, lost acknowledgements, restart and read-only reconciliation.
- New five-file historical package excludes held Reminder/Todoist and expense
  modules. Prior four-file seal, installer, updater and all Apple sources are
  unchanged. The old package is reproduced from immutable base sources.

## Checks

| Check | Result |
| --- | --- |
| Full backend unittest discovery | 307 tests OK; 1 optional local/private snapshot check skipped |
| Actual generated five-file historical runtime | 29 tests OK |
| Shared `PersonalWatchCore` Swift suite | 70 tests passed using installed Xcode 27.2 |
| Independent focused provider/engine/adapter review | 109 tests OK; same optional snapshot skip |
| Independent generated-package rerun | 29 tests OK |
| New manifest seal, build and verify | Exact five artifact hashes verified |
| Whitespace check | Clean |

The independent reviewer reproduced and found three defects during development:
duplicate qualified candidates could select an arbitrary equivalent row, generic
plural stripping could conflate brand suffixes, and a malformed proposal could
split a decimal quantity. All three were corrected with regression cases and
rechecked before sealing. The final independent review reported no remaining
actionable runtime findings. Its wording correction for expected sibling updates
is included in the operator guide.

Reproduce backend/package checks from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend:backend/tests python3 -m unittest discover -s backend/tests -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ops/hotfixes/grocery-ai -p test_release.py -q
PYTHONDONTWRITEBYTECODE=1 python3 ops/hotfixes/grocery-ai/build_release.py --output /private/tmp/grocery-ai-review-new
PYTHONDONTWRITEBYTECODE=1 python3 ops/hotfixes/grocery-ai/build_release.py --verify --output /private/tmp/grocery-ai-review-new
```

The pinned synthetic helper AST uses Python 3.9's representation. Tests opening
synthetic loopback listeners need local test permission. Swift tests used a
temporary scratch directory and process-scoped Xcode selection; no device,
signing, global toolchain or installed app operation occurred.

## Scope and remaining gates

Read-only reconciliation confirmed PR #1, #2 and #5 open, #3/#4 merged into the
MVP base, with the updater owner idle and Reminder/Todoist still paused. This
branch is isolated from all owner checkouts and targets
`feat/hermes-personal-mvp` at `77885a5cc4d018b7e5b49266fd909e591078cf48`.
The repository/account identity was verified before publication. The inspected
workflow runs synthetic checks only; no hosting configuration was present in
this branch and the repository deployment list was empty before publication.
No deployment workflow or configuration is added or changed.

The active permission context supplies `auto_review` and `workspace-write` with
restricted networking. Native read-only thread metadata did not expose the
effective root model/effort or approval-policy field, so these are not claimed
verified. Heavy implementation helpers and the independent reviewer were
created through supported controls explicitly requesting `gpt-6.1-sol` and
`ultra`, without the coordinator's history. Global defaults and native
permissions were preserved.

No provider/account secret was inspected, copied or installed. There were no
paid API calls, live grocery transmissions, service/device/list mutations,
Firefly reads/writes, Todoist/HA activation, schedules, merge or deployment.

Actual model accuracy, latency, account access and Jose's failed examples remain
unverified. API credentials, billable-use budget, data transmission, rollout and
an intended real canary require separate authorization. The model deadline
bounds inference, not the inherited Notion traversal/transport. External edits
after the final list read retain Notion's existing non-atomic transport limits;
unconfirmed/partial batches remain pending rather than being reported successful.

The installed Apple protocol already supports this no-write clarification plus
complete re-dictation flow; no reinstall is needed. It does not support a linked
“sí”/“el primero” selection conversation. A late partial/unknown result must keep
the original capture instead of being replaced with a new one.

See [operator setup and reviewed rollout/rollback path](../../GROCERY_AI_MATCHING.md)
and the new manifest in `ops/hotfixes/grocery-ai/release_manifest.json`. The old
installer and inactive updater cannot install this five-file candidate. After
version-2 state exists, rollback means keeping a version-2-aware runtime (AI may
be disabled) or stopping writes for review; never send pending state back to the
old writer or restore stale databases.

Canonical task: https://app.notion.com/p/40dfcdfcb7b481cdb1d8d02f51d654fe
