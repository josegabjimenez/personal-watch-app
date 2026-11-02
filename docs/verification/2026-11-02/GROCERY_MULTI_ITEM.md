# Mercado multi-item implementation — review handoff

Jose reported that dictating two grocery articles produced one combined entry.
The approved change separates clear additions, preserves quantities, and stores
durable per-item outcomes. This delivery prepares a focused PR and an offline
release package. It does not deploy, restart, replay a capture or change a real
shopping list. The earlier accepted grocery response/retry fix is preserved.

## Behavior

| Input or condition | Result |
| --- | --- |
| `agrega leche y huevos al mercado` | Two distinct unchecked entries |
| `2 litros de leche y 6 huevos` | Two entries with quantities retained |
| `un litro y medio de leche` | One entry; measurement conjunction retained |
| Known compound, such as `sal y pimienta` | One entry |
| Ambiguous compound/list, quantity conflict, duplicate product amounts | Clarification before any batch mutation |
| Existing unchecked canonical entry | Verified already-present receipt; no mutation |
| Existing checked canonical entry | Unchecked once for this new addition; receipt retains canonical text |
| Same parent/body after completion | Cached receipt; no helper call, mutation or re-unchecking |
| Partial result or unknown acknowledgement | HTTP503; original request identity and confirmed progress retained |
| Missing recovery journal/batch/item | Stop without recreating state or dispatching |

The parser is deterministic and bounded to 1,000 characters, ten articles,
120 characters per article and positive quantities up to 10,000. Its finite
conjunction vocabulary deliberately asks about uncertain wording. Unknown
single products and explicit comma lists remain supported within validation.
Removal, completion, cross-domain commands and instructions are rejected.

## Actual helper integration and recovery

A read-only authenticated console inspection acquired the exact deployed
helper source. Its SHA256 is
`24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0`.
The full source stays private because it includes unrelated functionality and
private destination identifiers. Checked-in tests use exact named AST nodes
with synthetic globals; the fixture validates their digest and can compare
them with the private snapshot. Production pins the complete helper hash.

The adapter uses grocery traversal, normalization, the shared capture flock
and Notion HTTP transport from that helper. It bypasses the generic capture
dispatcher, AI classification, event cache and notification paths. All batch
items are parsed and prepared before a mutation. Parent/body identity and the
ordered plan are frozen durably, with every child registered before dispatch.
SQLite records `dispatching` before network mutation and a committed receipt
only after exact-ID readback verifies the official list, text and unchecked
state. No SQLite transaction stays open across network calls.

An acknowledged candidate ID can reconcile by read-only lookup. If the remote
write may have happened but no ID was acknowledged, the operation remains
uncertain and is never blindly issued again. This favors avoiding duplicates;
manual reconciliation may be required. It is not an atomic remote batch:
earlier items may succeed before a later failure. Whole-batch success requires
verified receipts for every item, with accurate created/updated/already-present
counts. Completed receipts survive later user changes to an item.

The mobile ledger and the sibling `.grocery.sqlite3` journal are persistent
data outside the release directory. The latter contains both batches and item
operations. Durable preflight markers allow a temporary read outage to resume;
terminal clarifications and concurrent outcomes use compare-and-set updates.
Raw-body conflicts return409. A stale pending handler cannot downgrade a
terminal mobile receipt. Existing authentication and route/write gates apply.

## Verification

| Check | Result |
| --- | --- |
| Full backend suite | 229 passed, no failures/errors |
| Included parser / adapter / batch integration methods | 10 / 31 / 22 passed |
| Full shared Swift suite | 70 passed |
| Focused batch tests against installed-source baseline | 4 passed |
| Built historical four-file package suite | 16 passed |
| Independent recovery and final race review | No remaining findings |

Backend integration exercises the real adapter and extracted helper functions
against a synthetic Notion API, including pagination, quantity preservation,
target drift, full/partial outcomes, lost ACK, restart, concurrent requests,
terminal refusal races and missing state. The final transport race regression
forces a terminal winner before a stale pending handler and verifies the whole
ledger tuple remains unchanged and replay causes zero additional calls.

Commands:

```sh
PYTHONPATH=backend:backend/tests python3 -m unittest discover -s backend/tests -v
cd apple/PersonalWatchApp/Packages/PersonalWatchCore
xcrun swift test
```

The full backend suite requires permission to bind synthetic localhost test
listeners. All destination APIs use fakes; no real capture was submitted.
The package suite runs offline without listeners; commands and sealed build
verification are in `ops/hotfixes/grocery-multi-item/README.md`.

Five checked-in wire fixtures are reproduced byte-for-byte by the actual batch
engine/adapter tests. Swift verifies full, mixed and already-present receipts,
outbox persistence, exact-body retry after HTTP503 and rejection of unsafe or
uncorrelated claims. Production client sources are unchanged from installed
baseline `52867ae09e2caec66f28046f91b9bfd5ab030d04`; no reinstall is required
for this protocol. The current client shows a generic HTTP error for partial
results; detailed partial counts remain server-side. No new physical test or
live multi-item acceptance is claimed.

## Release and next step

Use only the sealed builder in `ops/hotfixes/grocery-multi-item/` for this
bounded release. It extends deployed BFF SHA256
`f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38`
with the grocery engine, adapter and parser. The historical overlay changes
only the grocery execution/HTTP handling, with static AST scope and import
closure checks. It excludes the combined MVP's held AI/expense runtime.
The release manifest pins every runtime artifact and its source inputs.

The PR is stacked on `feat/hermes-personal-mvp` for a focused diff; that base
and the combined MVP are not deployment candidates. Separate update automation
[PR #2](https://github.com/josegabjimenez/personal-watch-app/pull/2) remains
inactive and defaults to one BFF file. Applying its current map to this change
would omit three dependencies. A separately reviewed four-file mapping/main
candidate and activation authorization are required before using automation.

Review the focused package, paired database backups and activation plan before
any service change. Once new batch/item state exists, rolling code back to the
old single-item writer cannot safely handle an in-flight replay. Use a compatible
runtime or keep grocery writes disabled while reconciling; never delete the
journal or restore only one database. AI/Todoist remains on human hold.

Canonical task: [Mercado recovery](https://app.notion.com/p/3ddfcdfcb7b48142bce3fcdd9916c330).
