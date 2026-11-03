# Mercado: AI interpretation for review

This increment is a grocery-only candidate, not an installed release. Jose's
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

## Provider setup is a separate decision

The repository already contains an OpenAI Responses adapter for held reminders.
This grocery module uses the same public API pattern independently and imports
no reminder/Todoist code or credentials. No secret, configured inference access,
paid call or live grocery transmission was inspected or assumed in this task.
Codex subscription access is not treated as backend API access.

The proposed small model is `gpt-5-mini`, which the [official model page](https://developers.openai.com/api/docs/models/gpt-5-mini)
lists with Responses and structured-output support. The [structured-output guide](https://developers.openai.com/api/docs/guides/structured-outputs)
documents strict JSON schema and separate refusal handling. Sources were opened
on 3 November 2026. Model output still undergoes local validation.

Default is disabled. Later activation needs an authorized server-side OpenAI API
key/account and a decision approving billable grocery inference and transmission
of the dictated request plus bounded grocery candidates. No price, credits,
available account permissions or spending allowance is asserted here. Jose must
choose the API budget and authorize the separate rollout/canary before live use.

Only these grocery-specific process environment settings are read:

| Setting | Meaning |
| --- | --- |
| `GROCERY_AI_ENABLED=1` | Explicit opt-in for **new** requests; absence retains prior parser behavior. |
| `GROCERY_AI_OPENAI_KEY_FILE` | Absolute path to a private, owner-readable ordinary server key file; symlinks and permissive modes are refused. No key in CLI arguments, source or desktop credential reuse. |
| `GROCERY_AI_MODEL` | Defaults to `gpt-5-mini`; account availability remains unverified. |
| `GROCERY_AI_TIMEOUT_SECONDS` | Defaults to 8; positive and at most 30. |

One inference call per request, no model retry; at most four transport workers.
Strict JSON output is capped at 3,000 tokens, response bytes at 64 KiB, serialized
request data at 60,000 bytes, capture at 1,000 characters, ten products, and 200
catalog rows. Oversize/partial catalogs fail closed instead of being truncated.
Requests use `store:false`, no tools, no unrestricted Notion access and no raw
provider output/error logging. Dictation and catalog text are untrusted data.
Notion transport/locking retains its existing timeouts; the eight-second model
deadline is not an end-to-end webhook latency guarantee.

## Reviewed release and recovery path

1. Review the focused PR and the new five-file manifest under
   `ops/hotfixes/grocery-ai/`. Build and verify it offline using that folder's
   builder. Its server is reconstructed from the pinned historical grocery
   server plus the previously reviewed overlay; full MVP/reminder/expense
   modules are outside the package import closure. Keep the existing four-file
   release, its manifest, installer and inactive updater unchanged.
2. Before any separately authorized installation, the existing service owner
   verifies current installed hashes, helper pin, service arguments and pending
   work; drains writes, stops the service, and makes/verifies paired code and
   SQLite backups. Do not copy, reset or rewrite existing records. This
   candidate creates version-2 plans and adds a local batch membership table
   through its ordinary initialization. Preserve the parent ledger, grocery
   journal and all IDs.
3. Install the complete reviewed **five** sibling files while stopped, preserving
   file owner/modes, helper, secrets, endpoint and ledger paths. The old four-file
   installer and PR #2's pinned updater cannot install this changed package.
   Neither is enabled or repinned by this PR. Do not deploy the combined MVP.
4. Start with AI disabled; verify hashes and HTTPS health with **only**
   `aura.grocery_capture` enabled. After explicit provider/budget/activation
   approval, set the grocery-specific environment and restart under the same
   service owner. Check a separately authorized, intended capture with readback
   and replay. Confirm Jose's actual examples and latency before acceptance.
5. If provider interpretation misbehaves, disable `GROCERY_AI_ENABLED` and keep
   the AI-aware five-file runtime. Existing frozen version-2 requests still
   resume their own plans without new inference; new requests use the prior
   parser. If executable recovery is necessary after version-2 state exists,
   retain a reviewed version-2-compatible runtime or stop/disable writes for
   review. Never return pending version-2 journals to the old four-file writer,
   restore stale databases, reset identities, or automatically replay uncertain
   captures. A failed receipt/health check is not success.

No server, device, list, API account, credentials, global permission setting,
schedule, updater or held reminder/expense integration is changed by preparing
this candidate.

Canonical task: https://app.notion.com/p/40dfcdfcb7b481cdb1d8d02f51d654fe
