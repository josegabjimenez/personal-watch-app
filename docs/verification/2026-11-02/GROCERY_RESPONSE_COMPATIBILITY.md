# Grocery response compatibility

## Confirmed diagnosis

The installed 0.3.5 (8) client sent a valid live grocery request. A focused,
human-approved read-only VPS check found the matching stored response and
identified the deployed backend as the historical `970e14d` source:

`3746e4c73689cc8ae5ab80958db51802b6a51491238a5c68c0b6585125c9f7a4`

The response was the exact legacy grocery-classification clarification:
`needs_confirmation`, `dry_run=true`, `write_committed=false` and
`plan.would_write=false`. Its request/domain matched; its keys, planning
metadata, question and display copy matched the historical branch. That branch
returns after dry-run preflight and before any write-helper call. The Watch
correctly refused the live/dry-run mismatch. No capture was replayed during
diagnosis, and no service, configuration or ledger was changed.

## Prepared correction

The current backend gains a narrow cached-response presentation correction.
The separate historical release patch also corrects fresh grocery preflight
clarifications. Both return `needs_input`, `dry_run=false`, no committed write,
no planned write, no side effects and no missing gates, with a question asking
which grocery article to add.

Only the exact old no-write response qualifies. The correction requires the
stored terminal status, matching request/domain, exact legacy response/plan
keys and values, and an exact safe live grocery route/context. The existing
payload-hash conflict check runs first. Unknown fields, missing or mixed flags,
financial capabilities, other domains, ordinary gated dry-runs and committed
responses do not qualify. The cached correction calls no helper and leaves
the original ledger row, timestamps, response bytes and payload hash intact.

The Swift production safety check is unchanged. A synthetic regression proves
that the original malformed reply remains unsafe, while an explicit retry of
the same payload accepts the corrected terminal no-write response. Attempts
increase normally and no second outbox identity is created.

## Historical release and verified deployment

Validation passed:

| Check | Result |
| --- | --- |
| Current backend regression | 166 tests passed |
| Historical release suite | 11 test methods passed, including a wrapper running 12 unchanged historical tests |
| Shared Swift suite | 66 tests passed |
| Final exact-envelope Swift fixture | 1 focused test passed after copy alignment |
| Release build/verify | Exact pinned artifact hash; deterministic two-file output |
| Independent review | No consequential findings |

The historical suite replaces exactly one obsolete assertion of the malformed
envelope with the new fresh-clarification assertions; the original historical
test source is not rewritten. Local tests use synthetic data and mocked
adapters. No UI/binary rebuild was needed for this backend contract correction.

Use only `ops/hotfixes/grocery-live-clarification/build_release.py` to construct
the one-file historical release. It pins the original source and patch hashes,
applies exact hunks without fuzzy matching, checks the resulting hash and
syntax, and emits a backend file plus manifest. It has no deploy or restart
command. Do not deploy the current combined MVP backend for this fix.

Jose explicitly approved the one-file runtime update on 2 November 2026. The
installer verified the active baseline and explicit loopback bind/ledger
arguments, retained one exclusive code backup with the original hash, replaced
only the verified backend file atomically, and restarted only
`hermes-mobile-capture.service`. Its rollback path would restore the original
code and restart the same unit if startup or health failed.

The update succeeded: the active service, normal-certificate HTTPS `/health`,
unchanged command arguments/health profile and preserved ownership/mode were
verified. A subsequent read-only check confirmed the installed release hash
`f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38`
and the exact original backup. The installer opened no ledger or credential
file and submitted no capture. No ledger migration was performed.

AI/Todoist remains on human hold. This package changes no reminder/expense
implementation, server allowlist, authentication, credentials or app binary.
No live canary or physical capture acceptance is claimed. The user can now
retry the same saved grocery request; its known legacy clarification is
presented as no-write `needs_input`, without executing the helper again.
