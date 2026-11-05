# Backend update automation — review evidence

Date: November 2, 2026, America/Bogota.

This records the initial `6f5fc65` delivery. Its single-file design was superseded
by the [sealed grocery package integration](BACKEND_GROCERY_PACKAGE.md), which is
the current PR #2 acceptance evidence and activation boundary.

## Scope and provenance

- Independent branch: `codex/backend-update-automation`.
- Audited base: `main` / `970e14dfd6d3788996135a933cc0909c3049f076`.
- PR #1 remains separate: its MVP/AI/Todoist runtime is not included here.
- No backend application source, Apple client, grocery parser or helper changed.
- Primary checkout reused; original `codex/mercado-recovery-device` branch retained.
  Other managed worktrees were not modified. Generated local Apple output remains
  outside this PR.
- Current chat runtime evidence verifies `gpt-6.1-sol`, `ultra`, `on-request`,
  `auto_review`, `workspace-write`. No global/per-project permission setting changed.

## Verification performed

Local Python 3.9.6, synthetic data only:

```text
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -p 'test_*.py'
13 tests passed

python3 -m unittest discover -s ops/deploy/tests -p 'test_*.py'
49 tests passed
```

The historical backend regression needs a temporary loopback listener; it passed
under the existing approval review without any production connection. Deployment
tests cover exact Git provenance despite dirty/untracked files, fixed code slots,
strict input/hash/profile rejection, bounded requests, transport host identity and
credential cleanup, stale/drift guards, metadata preservation, multi-file commit
ordering, failed startup/health, actual process flock and SIGTERM/SIGKILL, interrupted
mixed-file recovery, hard network probe deadlines/cleanup, and protected retention.
Synthetic SQLite/secret sentinels remain unchanged and absent from bundles/results.

Both workflows passed `actionlint` 1.7.12 and YAML parsing. The official actionlint
archive checksum was verified before use. Python modules passed compilation;
`git diff --check` passed. Action versions were resolved against official tag refs
and pinned to full commit SHAs. The first push can run synthetic checks only:
the release workflow has exclusively `workflow_dispatch`, is main-only, and is
disabled unless `WATCH_UPDATES_ENABLED=true`. No such variable or credential was set.

Independent review of implementation, tests, workflows and activation documentation
finished with no consequential unresolved findings. Findings fixed during review:

- Preserve fixed receiver error codes across transport and suppress arbitrary diagnostics.
- Prune failed candidate snapshots only after verified recovery while preserving protected copies.
- Bound HTTP/DNS/body-read lifetime with transient isolated probes, parent deadlines,
  child lifetime limits, cleanup and no inherited release lock.

## Remaining activation boundary

Implementation/PR readiness is separate from production activation and acceptance.
Existing November 2 deployment evidence was inspected narrowly; the VPS was not
accessed anew. No SSH route/CI credential was provisioned or verified, receiver
installed, secret uploaded, policy changed, service restarted, data migrated,
backup pruned, capture submitted, device operated or paused AI work resumed.

Follow [the activation handoff](../../BACKEND_UPDATES.md) after Jose's review and
authorization. It requires actual private runtime paths/argv/health, the existing
reachable SSH route and trusted host identity, an explicitly authorized restricted
key/environment, and a separately reviewed compatible production candidate retaining
the installed hotfix. This automation branch's raw historical BFF is **not** that
candidate. The current map preserves the external helper; its source hash and
compatibility need review at release handoff. No auto-merge or live deployment is
authorized by this evidence.
