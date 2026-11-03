# Grocery installation and startup readiness

Jose explicitly approved merging PR #4 and applying the reviewed four-file
Mercado update on 3 November 2026, including verified code/data backups and a
brief restart of only `hermes-mobile-capture.service`. The direct human reply
was independently read in the existing coordinator chat. PR #4 was squash-merged
into its reviewed base as `77885a5cc4d018b7e5b49266fd909e591078cf48`.
The earlier missing-authorization rejection is resolved. This grants no AI,
expense, updater, App Store or real-capture test action.

The approved runtime remains the same sealed historical package. Manifest:
`9d34903345cac05cf695bc2839640e9aa9c773dd7a7261e6d5de0d7159a21f0e`.
It contains the historical BFF and three grocery modules, excluding the full
combined MVP backend. No runtime source or manifest changes are part of the
installer correction.

## First attempt and verified recovery

Fresh live preflight confirmed the exact baseline/helper/package, ordinary HTTPS
health, no batch journal, no new sibling modules, and no unfinished grocery work.
The parent ledger contained five accepted, five committed and two legacy
needs-confirmation grocery responses. Only aggregate statuses were inspected.

The approved installer created the exclusive verified backup and attempted the
four-file update. Its startup check returned `service_arguments`; rollback
restored the original code but its own startup verifier failed and left the
service stopped with `attention` / `recovery_failed`.

Read-only inspection confirmed original BFF hash
`f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38`,
three new modules absent, journal absent and unchanged transport counts. A
guarded start of only the original service restored availability. Bounded
observation verified exact original arguments and the expected HTTPS health.

Further verification established:

- Original source and code backup match the pinned baseline; metadata matches.
- The data backup hash matches its immutable marker and both SQLite integrity
  checks pass.
- Live database bytes match the recorded pre-backup source hash exactly;
  aggregate statuses match the backup.
- The backup records an absent grocery journal and the sealed manifest.
- Backup directory/data permissions remain private.

No database was restored, reset or deleted. No capture was sent or replayed.
The backup and original installer remain preserved. Recovery was independently
reviewed with no missing material evidence.

## Bounded installer correction

The original verifier reads process arguments immediately after `systemctl
start`, before waiting for health. An offline synthetic reproduction makes
both starts briefly expose launcher arguments and reproduces the reported
`service_arguments` / `recovery_failed` outcome. This demonstrates the missing
readiness boundary; the failed live attempt did not retain its transient argv,
so the precise live timing remains an inference.

The correction waits within a bounded deadline for the exact original
arguments and working directory, then retains the strict source/helper hashes,
file metadata and HTTPS checks. Persistent mismatches still fail.

An explicit resume mode validates the existing backup without modifying it.
It requires the same baseline, private ordinary backup members, exact sealed
marker/code/data hashes, successful integrity checks, original metadata, absent
journal/modules, no pending work and live database bytes still equal to the
pre-backup hash. These checks run again under the drain lock and while stopped.
The ordinary initial mode continues to reject an existing backup.

Saved and live WAL storage/sidecars are rejected. Backup validation uses an
immutable read-only SQLite connection after checking its rollback-journal
header, so validation cannot create backup sidecars. Resume requires the live
database to remain in DELETE journal mode; main-file equality alone is not used
as evidence for WAL-backed data.

No backup renaming, alternate backup root, ledger reset or deleted state may be
used to bypass the initial-activation guard. The same approved runtime and
one-service action require normal review after the corrected executor's
independent validation.

The corrected installer SHA256 is
`1136a2d05bf70d9a301944a079c17940894ca77b45f05aa39ab6a75e8eaa4071`.
All 43 focused tests passed, including the original 24 faults and 19 readiness
and immutable-resume cases. An independent run repeated all 43 successfully on
the same frozen source and found no unresolved recovery or scope issue.

Current outcome: original backend restored and healthy; corrected executor
prepared for the same approved installation after fresh resume preflight and
normal action review. Jose's intended two-item Watch result remains pending
and is not an agent end-to-end acceptance claim.
