# Historical grocery multi-item release and guarded initial activation

This package extends the exact deployed BFF whose SHA256 is
`f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38`.
It does not contain the current full MVP backend, financial confirmation flow,
reminder AI/Todoist integration, credentials, private payloads or service data.
The builder is offline. The separate standalone `install_release.py` defaults
to read-only preflight; only its explicit `--apply` mode can stop the intended
service and activate this exact sealed package after the existing authorization
and final installer review are satisfied.

The output contains four runtime files and one manifest:

- `mobile_capture_staging_server.py`: the deployed historical source with only
  `execute_capture` and `MobileCaptureHandler.do_POST` replaced by named source
  from `server_overlay.py`, plus `import grocery_capture`.
- `grocery_capture.py`: frozen parent batches and verified aggregate receipts.
- `grocery_helper_adapter.py`: add-only adapter with durable item claims and
  acknowledged-ID reconciliation. External helper SHA256 is pinned to
  `24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0`.
- `grocery_item_parser.py`: deterministic, bounded additions-only parsing.
- `manifest.json`: exact hashes of the four runtime files, source overlay and
  deployed baseline. No secrets or private request identifiers appear in it.

The builder reconstructs the deployed baseline using the previous reviewed
`grocery-live-clarification` builder's pinned historical commit and exact patch.
It then requires the deployed SHA256 above. AST scope checks prove all other
historical functions, class methods, imports, configuration defaults, planning
logic and server write gates remain unchanged. A static import-closure check
also rejects runtime imports outside the standard library and the four bundled
module names, so root-only reminder/AI/expense modules cannot hide a missing
release dependency. The existing pinned external helper is loaded separately.

## Offline review and reproducible build

Run from the repository root. The test suite receives synthetic requests in
memory and uses the AST-extracted helper contract with FakeNotion. It starts no
service, opens no network listener and makes no external HTTP requests.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s ops/hotfixes/grocery-multi-item -p test_release.py -v
```

The current offline package suite contains **16 test methods**. It checks the
actual built legacy server, exact runtime import closure, preflight outage and
process restart, partial progress, both lost-acknowledgement cases, missing
journal/item state, raw-body conflicts, disabled write gates, historical cached
clarification, unchanged ordinary caches, and deterministic builds and rejection of tampered packages.
It also verifies that neither database holds a transaction during Notion calls.

While runtime changes are still being reviewed, tests use an explicit test-local
candidate manifest. They do not seal repository pins. After the final runtime
freeze and independent review, deliberately seal the checked-in manifest once:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 ops/hotfixes/grocery-multi-item/build_release.py --seal-manifest
```

Then build a new local staging directory and verify it:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 ops/hotfixes/grocery-multi-item/build_release.py \
  --output /private/tmp/grocery-multi-item-review-release
PYTHONDONTWRITEBYTECODE=1 python3 ops/hotfixes/grocery-multi-item/build_release.py \
  --verify --output /private/tmp/grocery-multi-item-review-release
```

The build first checks frozen input hashes, stages into a temporary new
directory, verifies all files, and only then renames that directory to the
requested output. Existing outputs are never intentionally replaced. Any source
or overlay drift after sealing fails closed. A new reviewed manifest is required
for a changed release; never refresh pins merely to make a failing build pass.

## Guarded initial activation installer

`install_release.py` is self-contained and needs no Git checkout, builder or
application imports on the target host. It pins the exact sealed manifest and
four runtime hashes. This is an initial-activation tool: it requires the deployed
f6 baseline, the pinned external helper, no grocery journal, no sibling grocery
modules, no pending grocery transport state and, in normal initial mode, no
existing backup for this release. It preserves the current unit, command arguments, configuration and
secret locations. It does not enable AI, other routes or a paid Apple workflow.

With the real existing paths substituted, its default is preflight only:

```sh
python3 install_release.py --package /path/to/sealed-release \
  --backup-root /path/to/private-backups \
  --health-url https://existing-host.example/health
```

After the concrete installer/package have final review and activation is
already authorized, the same command with `--apply` performs the change:

```sh
python3 install_release.py --package /path/to/sealed-release \
  --backup-root /path/to/private-backups \
  --health-url https://existing-host.example/health --apply
```

### Resume one unchanged failed initial attempt

Default preflight and `--apply` still refuse an existing release backup. The
explicit `--resume-verified-backup` option validates and reuses that same original
backup; it creates no replacement, copy or second backup directory. Use exactly
the original backup root. Do not rename or delete the existing backup to bypass
the initial-activation guard.

First run read-only recovery preflight, then add `--apply` only after the corrected
executor's final independent review and the normal action review:

```sh
python3 install_release.py --package /path/to/sealed-release \
  --backup-root /path/to/original-private-backups \
  --health-url https://existing-host.example/health --resume-verified-backup
python3 install_release.py --package /path/to/sealed-release \
  --backup-root /path/to/original-private-backups \
  --health-url https://existing-host.example/health --resume-verified-backup --apply
```

Resume requires the exact private ordinary backup directory and its three
original ordinary, singly linked files: `backup.json`,
`previous-mobile_capture_staging_server.py` and `mobile-ledger.sqlite3`. The
canonical marker's exact fields, sealed release identity, old-code hash and
metadata, database backup hash and quick check must all match. The recovered
live database must still match the marker's pre-backup bytes exactly and pass a
quick check. WAL/non-DELETE storage or WAL sidecars are refused because a main
file hash cannot establish equality of committed WAL contents. The baseline,
helper, absent journal/siblings and no-pending-work guards continue to apply.
These checks repeat under the drain reservation and again with the unit off.
No backup or database is overwritten, restored, renamed, reset or deleted.

After either new-code or rollback startup, the executor waits up to five seconds
for an active process with the exact original arguments and working directory.
A transient launcher is not accepted as ready. Once the exact signature appears,
the existing strict source/helper/hash/metadata and HTTPS checks run. A persistent
mismatch remains a failure; neither health alone nor a relaxed argument match is
sufficient.

The deployment lock and parent SQLite drain are bounded to five seconds. The
installer rechecks source/helper hashes and the same process before stopping
only `hermes-mobile-capture.service` while holding the drain reservation. After
the unit is confirmed off, it releases that reservation and backs up through a
separate read-only SQLite source connection. The old BFF backup, database backup,
quick checks and hashes are verified before any code replacement. One exclusive
backup directory is retained per sealed release; no cleanup/retention job is
created and no old backup is overwritten.

All four files are staged and installed while stopped, with the BFF replaced
last. Startup verification checks hashes, owner/group/mode, unchanged arguments
and the exact existing health profile over normal HTTPS. Redirects and proxy
settings are disabled; no credential or capture endpoint is requested.

A failure before code changes restarts the unchanged runtime when safe. A
failure after activation stops the unit and permits old-code rollback only if
the journal remains absent or has verified empty known schema, and no pending
parent remains. New batch/item data, unknown schema or missing state needed by a
pending parent produce `attention`/`unsafe_rollback`: the compatible code remains
and the service stays stopped for review. Databases are never restored, deleted,
reset or replayed by this installer. A recovered activation reports
`rolled_back`; successful activation reports `installed`. Reports contain error
codes and verification metadata, never request IDs, captured text or secrets.

Run its synthetic controller/filesystem/SQLite fault tests locally:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s ops/hotfixes/grocery-multi-item -p test_install_release.py -v
```

## Runtime and data boundaries

The parent mobile ledger retains its existing path. The grocery journal is
`Path(str(cfg.ledger_path) + '.grocery.sqlite3')`; it contains both frozen batches
and per-item operations. Keep both databases outside replaceable release code.
Keep the existing route-secret file, helper path, environment and service unit.
Do not bundle or copy the external helper into this release.

The grocery journal records a durable preflight phase before read-only Notion
preparation. A transient preflight outage can retry that same phase after a
restart. Every child operation is registered before its prepared batch is frozen;
only then may mutations begin. Completed and terminal no-write results are
retained durably, so racing preflight outcomes cannot mask dispatched progress.

Before helper/Notion work, the handler commits an exact-raw-body parent claim
and closes its SQLite connection. Partial or uncertain results return HTTP 503,
retain that parent and their item progress, and can reconcile only through the
same request/body. Pending parents require their existing batch; missing batch
or item state stops without recreating or replaying it. Completed responses are
cached. A reused ID with changed bytes returns 409. If a partially restored mobile
ledger accepts a wrong-body transport claim, only that conflicting `processing`
claim is removed; its authoritative batch/items remain untouched.

The existing global/server route gates still apply. Turning writes off while a
batch is pending returns 503 and preserves its state instead of creating a
terminal dry-run receipt. No finance, reminder, general-capture, completion or
removal route is enabled.

## Activation review checklist — no activation is authorized by this file

1. Verify the current deployed BFF still matches the pinned starting SHA and
   the external helper still matches its pinned SHA. Inspect only the intended
   service metadata; do not expose credentials or unrelated services.
2. Review the sealed manifest and offline results. Stage all four runtime files
   together in a separate release directory; do not overwrite the running file
   piecemeal. Preserve ownership, environment, helper, route secret and database
   locations. Check that all three new sibling modules are readable by the
   configured Python process.
3. Back up the paired mobile ledger and grocery journal consistently before
   activation. Check existing pending batches; never delete, reset or edit their
   identities. Retain backups with fixed policy rather than accumulating manual
   copies indefinitely.
4. Obtain explicit activation authorization for the concrete four-file release,
   process replacement/restart and agreed verification. Confirm the pending
   journal compatibility and rollback plan before changing the service.
5. After authorized activation, verify the source/module hashes and ordinary
   health without performing an unapproved real grocery capture. Any write
   canary, real item creation or cleanup requires its own authorized scope.

## Rollback after grocery state exists

A code-only copy of the old single-item BFF is a valid rollback only before the
new runtime has created batch/item state. Once an in-flight batch exists, the old
single-item writer cannot read the new journal and must never receive a replay
of that request. Use a runtime that understands the same frozen plans and item
journal, or keep grocery writes disabled while reconciling. Do not delete the
journal, restore only one database, clone request IDs, re-dictate the full batch
as a workaround, or run the legacy generic helper to replay uncertain work.
