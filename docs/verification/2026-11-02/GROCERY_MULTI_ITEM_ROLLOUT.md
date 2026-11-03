# Grocery-only rollout record

PR #3 was squash-merged into its reviewed base `feat/hermes-personal-mvp` as
`1fe67948051f1d25140e13900d51275c498e0770`, from reviewed head
`1f98ec601eea5e8e9b66e6a18ce8886a3bd816b8`. Its implementation/review milestone
is complete. This does not merge PR #1/#2, accept the whole MVP, or resume
AI/Todoist integration.

Jose asked to merge and continue toward his personal MVP. The bounded next
outcome is installation of the sealed historical grocery package, consistent
backups and non-effect health verification. The combined MVP backend is excluded.
No shopping-list test write, capture replay, finance or Todoist action is part
of this verification; Jose performs his intended two-item request afterward.

## Reconciled state before activation

A read-only check of the intended active service confirmed:

- Existing BFF SHA256:
  `f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38`.
- Exact external helper SHA256:
  `24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0`.
- Explicit loopback bind and existing ledger argument; no configuration changes.
- Grocery transport rows: five `accepted`, five `committed`, two
  `needs_confirmation`; zero `processing` or `grocery_pending` rows.
- No grocery batch journal and none of the three new runtime sibling modules.
- Normal-certificate HTTPS health returned 200, status `ok`, service
  `mobile-capture-v1-staging`, mode `allowlisted-write` and only
  `aura.grocery_capture` enabled.

Only aggregate statuses were read. No request identifiers, capture text,
credential files or unrelated production records were exposed.

The sealed four-file package and manifest were staged in a private temporary
directory on the same VPS. Every artifact hash and Python syntax was verified
there without importing/executing the runtime or changing the active service.
The manifest SHA256 is
`9d34903345cac05cf695bc2839640e9aa9c773dd7a7261e6d5de0d7159a21f0e`.

## Activation boundary

The installer must recheck the exact source/helper, unit arguments and pending
state at action time. A bounded parent SQLite write reservation drains the
historical runtime's helper transaction before stopping only the intended
service. While stopped, consistent database snapshots and a verified original
code backup precede all four file replacements. The service retains its unit,
arguments, credentials, helper, network address and data locations.

Rollback never restores data. Returning to the old single-item code is allowed
only while no new batch/item state exists. If progress exists or cannot be
verified, stop the service and retain the compatible runtime for reconciliation.
Never erase a journal, reset an identity or replay through the legacy helper.

## Installer verification and approval gate

The standalone installer SHA256 is
`0b4f5636dfd64b0696a4d08558bbc8ff7ecd0ddb2a1bbe416c661c3c6b5bc8df`.
All 24 focused offline tests passed and were independently repeated. Fault
coverage includes a busy drain, pending requests, source/helper/package drift,
backup and partial-copy failure, startup/health failure, metadata drift,
dangling symlinks and unfamiliar SQLite schema. Review findings concerning
buffer flushing, code-backup readback, file metadata and unknown journal state
were corrected; final independent review found no remaining actionable issue.
The sealed runtime files and their manifest were not changed.

The same installer bytes were staged and verified on the VPS. Its default
read-only preflight returned `preflight_ok`. The explicit `--apply` action was
then rejected by automatic approval review before execution: the instruction
to merge and continue did not explicitly authorize live file replacement,
backup creation and service stop/restart. The rejected action was not retried
through another route.

A subsequent read-only check confirmed the old backend hash unchanged, service
active, ordinary HTTPS health successful and no release backup created.
Only private temporary code staging exists. No activation, restart, real
capture submission/replay or database restoration occurred.

The exact remaining gate is Jose's explicit authorization to install this
sealed four-file package, create the verified code/data backup and stop/start
only `hermes-mobile-capture.service`, followed by non-effect health/hash checks.
On approval, run fresh guards again; do not infer that this old preflight is
still current. Successful installation still needs Jose's intended two-item
result before Mercado is marked accepted.

## Personal installation preference

Jose wants personal use only, with no App Store publication or paid Apple
membership. Continue the existing Xcode Personal Team route. Apple states that
free device provisioning expires seven days from issuance and requires rebuild
and reinstall afterward; backend-only changes do not require a new app install.
[Apple Developer account guidance](https://developer.apple.com/help/account/basics/about-your-developer-account/)
was checked on 2 November 2026. No enrollment, purchase, TestFlight submission,
App Store submission or renewal reminder was created.

Here, “production” means Jose's personal backend: the server used by his
installed app. It does not describe App Store distribution.

Canonical task: [Mercado recovery](https://app.notion.com/p/3ddfcdfcb7b48142bce3fcdd9916c330).
