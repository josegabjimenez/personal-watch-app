# Historical grocery clarification compatibility artifact

This is a **local, one-file release artifact**, not a deployment command. It is
for the confirmed historical backend at `970e14d`; it does not package the
current MVP, expense adapter, reminder integration or AI dependencies.

The patch changes only:

- The authorized grocery preflight-classification clarification: return a live
  `needs_input` response with explicit no-write flags and correct correlation.
- Presentation of the exact known historical cached clarification: require its
  ledger status, nine top-level keys, three exact plan fields, old copy/gates,
  matching request/domain, exact grocery route and safe live context. Return a
  corrected copy, without updating the saved response, payload hash or ledger.

Unknown/mixed envelopes are not repaired. Numeric `0`/`1` do not count as boolean
flags. Ordinary caches, server gates, HMAC checks, helper invocation and all
other historical functions remain unchanged. The client safety guard stays on.

## Build and verify locally

From the repository root, choose a **new** output directory:

```sh
python3 ops/hotfixes/grocery-live-clarification/build_release.py \
  --output .private/releases/grocery-live-clarification
python3 ops/hotfixes/grocery-live-clarification/build_release.py \
  --verify --output .private/releases/grocery-live-clarification
```

The builder reads the pinned commit using local `git show`, applies exact
unified hunks without fuzz/offset, checks all hashes, and compiles for syntax
without importing/running the backend. It writes exactly
`mobile_capture_staging_server.py` and a deterministic `manifest.json`. Existing
output directories are never overwritten. `--source PATH` can instead supply
an exact local baseline copy; any different baseline fails closed. No fetch,
service access, credentials or runtime data are used.

| Material | SHA256 |
| --- | --- |
| Historical backend | `3746e4c73689cc8ae5ab80958db51802b6a51491238a5c68c0b6585125c9f7a4` |
| Unified patch | `8eac63e556d84a6706b5b4f6c26670a328bec16c1aea10566a4b01a18dc7dc46` |
| Patched backend | `f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38` |

## Offline verification

```sh
python3 -m unittest discover \
  -s ops/hotfixes/grocery-live-clarification -p test_release.py -v
```

The tests build the artifact in a temporary directory, exercise fresh and cached
clarification through synthetic helpers/temporary SQLite, and check byte-for-byte
ledger preservation, conflicts, exact refusal shapes, boolean types, gates,
unchanged ordinary caches, deterministic packaging and tamper rejection. AST
comparison checks all unrelated historical functions/imports are unchanged.

The original historical test source is loaded unchanged. Exactly one named test,
`test_grocery_route_mismatch_does_not_execute_write`, is explicitly replaced
because it asserts the malformed `needs_confirmation` / `dry_run=true` envelope;
the corrected no-write expectations are covered directly. The other twelve
historical tests run unchanged, including HMAC, gates and concurrent idempotency.
That original concurrency test binds only a temporary `127.0.0.1` server with
synthetic inputs; restricted sandboxes may require ordinary loopback permission.
No production helper, service, ledger or record is used.

Runtime replacement/restart, rollback and any user-request reconciliation remain
separate authorization steps. See the repository's
[compatibility diagnosis and proposed handoff](../../../docs/verification/2026-11-02/GROCERY_RESPONSE_COMPATIBILITY.md).
