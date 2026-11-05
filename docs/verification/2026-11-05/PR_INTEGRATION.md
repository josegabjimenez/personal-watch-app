# Approved PR integration — November 5, 2026

Jose explicitly authorized squash-merging the existing approved Watch PRs, resolving conflicts and verifying the combined result. This is repository integration authority, not production rollout, updater activation, new funding or live Todoist authority. A single integration owner used the existing isolated reminder worktree; other owners' checkouts and dependency branches are preserved.

## Dependency receipts

- PR #5 was squash-merged into `feat/hermes-personal-mvp`: `91644d421da9e96eedbafde2b0a7de18c12d5778`. It retains the original installer's startup wait and verified-backup resume behavior.
- PR #6 was squash-merged into the same MVP branch: `c760f28909c89d2df521b46ac31ef19ef7a06140`. It retains the accepted six-file grocery runtime, grocery-only USD2 guard and distinct upgrade readiness/recovery behavior.
- Their changed paths are disjoint; both implementations are needed. The combined four-file historical tests required a source-fixture fix: current AI imports cannot be packaged as four files. Tests now read the three exact hash-pinned modules at `1fe6794`; production builders, installers and seals remain unchanged. The intentional six-file test reuse still uses its current five modules.

The reminder delta is independently reviewed and sealed at manifest `548a7ac4f0bacfcf98e4846c2532fac833c7bbe2f17b91416bece45dd11344af`. Missing Todoist browser consent does not block merging this guarded, inactive code. Its original checkpoint verification is in [REMINDER_RESUME.md](REMINDER_RESUME.md).

## Combined validation and activation boundary

At the combined MVP checkpoint, 458 backend tests ran with zero failures and one optional private-snapshot skip. Historical release/installer tests passed 16 + 43, including PR #5 readiness/resume; six-file AI release tests passed 31. Shared Swift verification remains 72 passing tests and an unsigned iOS/embedded Watch Release build. Further operator checks and final PR/main receipts are recorded in the PRs.

Before merging, workflow inspection found normal synthetic checks and a separate **manual-only** update workflow. The repository's deployment-variable and secret-name listings were empty; deployment history was empty. No setting, secret or workflow run was changed to enable delivery. Normal checks on code merge are authorized; a service update is not implied.

The approved updater PR #2 is deliberately pinned to the older four-file profile. It rejects the accepted six-file baseline and the proposed twelve-file reminder package before mutation. It remains unconfigured/disabled. Integration must preserve this failure boundary and document it; changing a JSON revision or relaxing hashes cannot make it a compatible updater. Adoption of a new runtime would require a separately reviewed profile, state/witness preservation and explicit activation.

Repository merged, server installed and human accepted remain distinct states. Current Mercado acceptance is Jose's statement, with existing documented semantic limits retained. No reminder save, alert configuration or device notification is claimed by these merges. No primary checkout, service, pairing, data journal, budget marker or credential was changed by this integration.
