# Historical grocery multi-item release — preparation only

This package extends the exact deployed BFF whose SHA256 is
`f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38`.
It does not contain the current full MVP backend, financial confirmation flow,
reminder AI/Todoist integration, credentials, private payloads or service data.
Nothing here activates or deploys a service.

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
