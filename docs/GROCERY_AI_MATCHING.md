# Mercado: AI interpretation for review

Jose approved the grocery-only Luna rollout and USD 2/month application budget
on 4 November 2026. Account eligibility/credit and secure key provisioning are
being handled by the coordinator; this document does not claim them verified.
Installation readiness must be established by the current verification record.
Jose's
latest Mercado tests failed acceptance; the exact failed dictations have been
requested and remain pending. Synthetic provider fixtures validate our contract
and safety boundaries, not a model's actual Spanish accuracy or account access.

The optional AI route interprets one complete Spanish request against the
complete current grocery catalog. It proposes exact dictated product spans and
existing candidate IDs. Trusted code validates source coverage, identities,
quantities and distinguishing attributes; it then freezes the interpretation
and item plans before dispatch. Products absent from the catalog are added
unchecked under the existing unique `Otros` group. An existing unchecked
equivalent is reused without renaming, changing its quantity or creating a row.

Supported operations are additions, reuse of missing products, and marking an
existing bought product missing only under explicit “me falta…”/“nos faltan…”
dictation (optionally “otra vez”). This intent is derived from the original
request prefix and frozen by trusted code; the model cannot supply the flag.
Other requests preserve bought status and ask for a complete clarified request.
Completing, deleting,
clearing, merging rows, renaming, replacing/summing quantities and other domains
are unsupported. Materially different brands, diets, varieties and sizes remain
distinct. The post-proposal guard recognizes selected common synonyms, accents,
plural forms and one-edit product-head typos; unknown equivalences can require
clarification. AI is not a guarantee against interpretation errors.

Any ambiguous product, amount, boundary or match refuses the whole request before
dispatch. A concise Spanish question appears in `display_message`, with
`status:needs_input`, `dry_run:false`, `write_committed:false` and
`plan.would_write:false`. The installed Apple protocol accepts this terminal
no-write result. Jose then dictates the **complete clarified request** as a new
capture. There is no contextual “sí”/“el primero” selection flow. No client
source change or physical reinstallation is required for this behavior.

Each request has one durable inference claim. Its accepted interpretation and
catalog survive preparation outages and restarts; retries never ask the model
for another interpretation. An interruption after the claim but before saving
its result remains pending for review and cannot write. Known provider failures
produce a cached honest no-write result with no parser fallback. A partial or
uncertain dispatched batch retains its exact request and item identities; do
not replace it with a new request or erase its journals to force a retry.

The full catalog is rechecked after inference and before each new dispatch.
Only confirmed preceding creates and explicit missing-state updates from the
same registered batch are allowed as expected changes. Concurrent stale
requests stop rather than silently rematch.
Lost mutation acknowledgements use the existing read-only reconciliation by
acknowledged ID; a missing ID never authorizes another mutation.

## Approved Luna setup and remaining key handoff

The repository already contains an OpenAI Responses adapter for held reminders.
This grocery module uses the same public API pattern independently and imports
no reminder/Todoist code or credentials. Codex/ChatGPT sign-in is not treated as
a backend credential. The coordinator provisions the API key; this execution
accepts only its absolute server file path and verified private-file status.
It does not copy desktop or reminder secrets or run another account login flow.

The fixed model is [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
using Responses, low reasoning and strict structured output. Official docs opened
on 4 November list Standard rates of USD 0.10 input / USD 0.50 output per million
tokens. The [reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)
confirms that the output limit includes reasoning and other non-visible tokens.
Model output still undergoes local validation.

Luna, the USD 2 budget, scoped installation/configuration and one-service restart
are approved. Activation requires actual usable API access plus the reviewed
release and verified data/code backups. Eligibility for a Pro API-credit benefit
and actual claimed credit/account access remain separate checks. No merge,
Reminder/Todoist activation, expense or household-control change is authorized.

Only these grocery-specific process environment settings are read:

| Setting | Meaning |
| --- | --- |
| `GROCERY_AI_ENABLED=1` | Explicit opt-in for **new** requests; absence retains prior parser behavior. |
| `GROCERY_AI_OPENAI_KEY_FILE` | Absolute path to a private, owner-readable ordinary server key file; symlinks and permissive modes are refused. No key in CLI arguments, source or desktop credential reuse. |
| `GROCERY_AI_MODEL` | Must be `gpt-6-luna` or absent; other models are refused. |
| `GROCERY_AI_TIMEOUT_SECONDS` | Defaults to 8; positive and at most 30. |

One inference call per request, no model retry; at most four transport workers.
Strict JSON output is capped at 3,000 tokens, response bytes at 64 KiB, serialized
request data at 60,000 bytes, capture at 1,000 characters, ten products, and 200
catalog rows. Oversize/partial catalogs fail closed instead of being truncated.
Requests use Standard `service_tier:default`, fixed implicit 30-minute prompt
caching, `store:false`, no tools, no unrestricted Notion access and no raw
provider output/error logging. Dictation and catalog text are untrusted data.
Notion transport/locking retains its existing timeouts; the eight-second model
deadline is not an end-to-end webhook latency guarantee.

## Persistent USD 2/month application guard

The guard shares the existing `.grocery.sqlite3` journal and uses calendar months
in `America/Bogota`. It is a transactional application admission cap, not a
dashboard budget or a guarantee about the entire provider account's invoice.
Other clients, taxes and future provider pricing are outside this code's control.
Promotional credits do not increase the application's allowance.

Before dispatch, a fresh request reserves USD **0.318**: the entire documented
1,050,000-token model context as possible input plus all 3,000 permitted output
tokens. This deliberately avoids assuming a byte/token relationship. Accounting
uses conservative USD 0.30/M input and USD 1.00/M output rates. These cover the
reviewed Standard long-context, 30-minute cache-write and regional multipliers:
0.10 × 2 × 1.25 × 1.10 = 0.275/M input, and
0.50 × 1.50 × 1.10 = 0.825/M output. [Prompt-caching docs](https://developers.openai.com/api/docs/guides/prompt-caching)
confirm 30-minute writes cost 1.25×; the request fixes that retention explicitly.
12-hour cache settings, Fast tiers and model/rate/budget overrides are excluded.

A valid expected-model/tier response with consistent usage replaces the maximum
reservation with conservative accounting for **all** input and `output_tokens`;
reasoning tokens are already included and are not counted twice. Refusal or
malformed interpretation can still have a known charge. Missing, inconsistent,
unknown billing dimensions, timeout or lost response keep the full reservation.
These unknown obligations carry into every later month; six unknown calls leave
only USD 0.092 and block another maximum reservation even after month rollover.
Known settlement across a boundary counts in both admission and settlement month
for safety. A backwards month clock fails closed.

SQLite transactions serialize concurrent admissions. Duplicate request IDs never
receive a fresh dispatch, and interpretation recovery does not reserve/call again.
Budget exhaustion returns a concise no-write message; there is no automatic
retry or bypass to the legacy parser. The fixed policy is persisted and checked;
changing its model, rates or cap against existing state fails closed.

Only explicit installation initialization creates the budget tables. It requires
an existing grocery journal and no unaccounted earlier AI history. A private
`<journal>.ai-budget-initialized` marker binds the database to that policy and is
written before the SQL commit. Missing/corrupt/deleted state or interrupted setup
requires review, never automatic repair or a fresh allowance. Preserve this
marker with both SQLite files. Code rollback must not restore an older spending
database or erase unknown reservations.

## Reviewed release and recovery path

Use the new [six-file upgrade instructions](../ops/hotfixes/grocery-ai/UPGRADE.md)
for exact preflight/apply flags and safe failure behavior. This upgrade path is
separate from the unchanged initial four-file installer.

1. Review the focused PR and the new six-file manifest under
   `ops/hotfixes/grocery-ai/`. Build and verify it offline using that folder's
   builder. Its server is reconstructed from the pinned historical grocery
   server plus the previously reviewed overlay; full MVP/reminder/expense
   modules are outside the package import closure. Keep the existing four-file
   release, its manifest, installer and inactive updater unchanged.
2. Before the authorized installation, the service owner
   verifies current installed hashes, helper pin, service arguments and pending
   work; drains writes, stops the service, and makes/verifies paired code and
   SQLite backups, preserving the budget marker if present. Do not reset or
   rewrite existing records. This
   candidate creates version-2 plans and adds a local batch membership table
   through its ordinary initialization. Preserve the parent ledger, grocery
   journal and all IDs.
3. Install the complete reviewed **six** sibling files while stopped, preserving
   file owner/modes, helper, secrets, endpoint and ledger paths. The old four-file
   installer and PR #2's pinned updater cannot install this changed package.
   Neither is enabled or repinned by this PR. Do not deploy the combined MVP.
4. After verifying the private key, initialize the budget once while stopped,
   configure only the grocery-specific environment and start the same service.
   Verify hashes, arguments, scoped configuration and HTTPS health with **only**
   `aura.grocery_capture` enabled. The `live_smoke.py` harness makes at most three
   small synthetic inference calls against this same budget, stopping after any
   failure and making no Notion/helper calls. Then Jose performs his intended
   Watch request. Confirm his actual examples and latency before acceptance.
5. If provider interpretation misbehaves, disable `GROCERY_AI_ENABLED` and keep
   the budget-aware six-file runtime. Existing frozen version-2 requests still
   resume their own plans without new inference; new requests use the prior
   parser. If executable recovery is necessary after version-2 state exists,
   retain a reviewed version-2-compatible runtime or stop/disable writes for
   review. Never return pending version-2 journals to the old four-file writer,
   restore stale databases, reset identities, or automatically replay uncertain
   captures. A failed receipt/health check is not success.

Current actual installation/smoke state belongs in the dated verification record;
preparing code or a passing offline fixture is not a live-readiness claim.
Device, API-account, global-permission, schedule, updater and held integrations
remain outside this execution's changes.

Canonical task: https://app.notion.com/p/40dfcdfcb7b481cdb1d8d02f51d654fe
