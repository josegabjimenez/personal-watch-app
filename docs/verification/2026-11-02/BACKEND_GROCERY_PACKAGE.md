# Sealed grocery package integration — PR #2

Date: November 2, 2026, America/Bogota. This continues the approved automation task
after PR #3's grocery-only squash merge. PR #2 remains a focused automation PR
against `main`; the held MVP/AI base was not merged into this checkout.

## Source agreement

- Package revision: `1fe67948051f1d25140e13900d51275c498e0770`.
- Reviewed PR #3 head: `1f98ec601eea5e8e9b66e6a18ce8886a3bd816b8`.
- Exact sealed manifest SHA256:
  `9d34903345cac05cf695bc2839640e9aa9c773dd7a7261e6d5de0d7159a21f0e`.
- Deterministic schema-2 transport SHA256:
  `043e220510282e132083d2064b6f56fb6b1f07f5b7c610ebdb9c02af0b057718`.
- All four generated artifact hashes match the original sealed manifest. The
  branch's combined raw BFF, external helper, private configuration and databases
  are not included. The eight source pins are in
  [the trusted profile](../../../ops/deploy/grocery-package.json).

The old raw single-file map was removed. The automation's reviewed `main` revision
and the package's fixed stacked-source revision are independent pins; another
revision or file map cannot be selected through workflow input.

## Actual offline verification

Python 3.9.6 on the existing host:

```text
python3 -m unittest discover -s ops/deploy/tests -p 'test_*.py'
53 deployment tests passed

python3 ops/deploy/build_bundle.py \
  --revision 1fe67948051f1d25140e13900d51275c498e0770 \
  --verify-tests --output <temporary code-only output>
16 tests against the actual built historical package passed
```

The actual four sealed files were also run through the legacy entrypoint symlink
with `/usr/bin/python3 -B ... --help` and a trap sibling module at the old location.
Import resolution and `--ledger` help succeeded without creating a database or
opening a listener. Python compilation, `actionlint` 1.7.12 and whitespace checks
passed. CI additionally runs the unchanged historical backend regression.

Synthetic deployment fixtures cover:

- Exact source/manifest bytes, hashes, paths, revision and four-file agreement;
  no full-branch, symlink, extra/missing artifact or private-environment packaging.
- Whole-package staging and atomic entry selection; normal Python caches are
  constrained and removed before receiver starts, without changing the app's unit.
- Real process locks and SIGTERM/SIGKILL, including inhibition before floor
  persistence and a killed health parent whose child must not retain the lock.
- Boot attempts cannot start the legacy entry after inhibition. Normal and recovery
  paths inhibit before persisting the monotonic batch compatibility floor.
- Compatible original four-file recovery and code rollback; failed first activation
  with a single-item predecessor remains stopped/inhibited and pending rather than
  replaying through an incompatible writer. Existing batch-journal metadata blocks
  adopting a legacy single-item baseline.
- Both synthetic SQLite files/claims and credentials stay byte-identical and outside
  code snapshots. No database is restored, reset or inspected by the receiver.
- Health/start failures, hard network deadlines, post-health helper drift and repeated
  stop failures. An unconfirmed stop is reported honestly; no claim that a still-active
  process stopped. Boot inhibition remains an attempted protective write until verified.
- Protected snapshots and code stages, truthful cleanup failure, recognized partial
  staging recovery, and preservation of tampered/unknown staging files and operator
  backups rather than recursive deletion.

Independent review found and resolved boot-floor ordering, cache/manifest handling,
recovery stop-failure reporting and staging-cleanup concerns. No consequential code
or workflow findings remain. The final additional helper-drift regression passed
after that independent review; application/helper sources were unchanged.

## Unperformed activation boundary

This task performed no new VPS diagnostic, SSH write, service restart, workflow
dispatch, live grocery capture, credential upload, access/policy change, backup
pruning, merge, device operation or AI/reminder resumption. Other worktrees and
their branch refs were preserved. The primary checkout's existing generated Apple
output remains outside the PR.

Follow [the concrete activation handoff](../../BACKEND_UPDATES.md), serialized with
the Mercado owner's exclusive live rollout. Normal automation adoption follows a
verified compatible four-file baseline. It still requires Jose's PR review, a trusted
existing SSH route, a dedicated restricted key, audited private receiver configuration,
consistent paired-data backup and a separately authorized first automation operation.
No new service, daemon, paid tool, App Store publication or Apple membership is needed.
