# Gasto backend review candidate

The candidate starts from the [documented November 5 backend](../../../docs/verification/2026-11-05/REMINDER_REST_LIVE_TEST.md), revision `9b2884671e74afc7865663dc1e29d25eed3d26bf` and manifest SHA-256 `55d14d53043179256791d14242c4f44b17bb33534dd7e18102c61c6bbc17b038`. The offline builder checks the manifest and all twelve historical artifact hashes. It copies eleven modules byte-for-byte and adds dispatch to the existing server's two HTTP wrappers. Mercado and Recordatorios continue through their existing signed handlers with the original request bytes and shared request ledger.

The exact Megan expense route uses the existing signed request handler, frozen COP preview, expense operation ledger, and version 3 Firefly adapter. Both the global write flag and exact expense domain are required. Only an explicit confirmation of the frozen plan can claim a Firefly POST. The operation ledger limits it to one attempt. A lost acknowledgment or delayed Firefly readback can be reconciled by retrying the same signed confirmation. A prior legacy dry-run delivery remains bound to its original request ID and hash. A fresh draft has a separate request ID, and an unresolved operation for the same device and text still prevents a second withdrawal.

## Run the offline checks

Use a checkout with the historical objects `9b2884671e74afc7865663dc1e29d25eed3d26bf`, `f8738b9a1529c09a7a577550706d7fb876ea6f89` and `970e14dfd6d3788996135a933cc0909c3049f076`. The sealed grocery generator uses Python 3.9.

```sh
python3.9 ops/hotfixes/expense/build_release.py --output /private/tmp/personal-watch-gasto-candidate
python3.9 ops/hotfixes/expense/build_release.py --verify --output /private/tmp/personal-watch-gasto-candidate
PYTHONDONTWRITEBYTECODE=1 python3.9 -m unittest discover -s ops/hotfixes/expense -p 'test_*.py' -v
```

The generated manifest identifies the review candidate and its exact artifact hashes. Tests exercise its signed handler, shared SQLite state, and actual adapter using a fake Firefly gateway. The existing loopback backend regression separately exercises the helper subprocess and HTTP adapter against a local fake API.

If rollout is authorized separately, first verify the twelve installed hashes, the existing external grocery helper pin, and the effective service arguments. Preserve the parent and expense ledgers and the existing Mercado/Recordatorios state. Review the explicit addition of `megan.expense_capture` and the approved server-side Firefly configuration before any activation. The existing manual grocery updater is still pinned to its own historical package.
