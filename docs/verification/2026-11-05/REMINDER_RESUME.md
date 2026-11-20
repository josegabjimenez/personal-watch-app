# AI Reminders resumed — November 5, 2026

This records the pre-authorization implementation checkpoint. The later approved
browser setup, observed consent error and OAuth endpoint correction are tracked in
[TODOIST_OAUTH_SETUP.md](TODOIST_OAUTH_SETUP.md).

Jose resumed the existing timed-personal-reminder delivery and explicitly chose reuse of the existing app OpenAI key. No new key was created, copied or exposed. The existing isolated branch/worktree and PR #1 are retained. Mercado's owner released service ownership with a path-only handoff; its last independent deployment check was November 4, and Jose accepted current Mercado use November 5. Grocery improvements remain deferred.

## Reviewable code

- Natural Spanish/English proposals retain original capture time and deterministic Bogotá validation. Selected contextual labels must exist in the current account; complementary labels are allowed, one duration maximum. Schedule offsets are not task duration. Deadlines, recurrence, multiple tasks, appointments, general capture, finance and HA remain outside this increment.
- Dedicated Todoist OAuth/PKCE provisioning and serialized rotating-refresh storage are prepared. No registration, consent, token request, credential transfer or server setup has run. Existing native connector authorization is not exported. The intended existing Inbox is pinned through private configuration; a different first account is rejected.
- Reminder usage has an independent persistent guard, default allowance zero, maximum two attempts per original request, conservative pre-call reservation and validated usage settlement. Grocery's USD2 guard is unchanged. Unknown cost outcomes are retained. A nonzero allowance requires a separate explicit decision.
- New command IDs are deterministic from the original request/hash and server signing identity. Previously persisted IDs remain intact. If budget history survives while a main operation record is missing, execution marks reconciliation required before further paid calls or writes. A restored ledger cannot silently produce a fresh task.
- Failed/uncertain replies stay retryable on the original outbox row. The client uses concise fixed local copy only for strictly correlated reminder error envelopes; it never marks these as saved.
- The twelve-file release wraps the recorded historical Mercado service instead of deploying the broad combined MVP server. Five grocery module bytes, original HTTP bodies, Config/CLI/grocery gates and journal state are preserved. [Package, backup, scoped rollout and rollback plan](../../../ops/hotfixes/reminder-ai/README.md).

## Verification

- Backend: 338 tests run, zero failures, one pre-existing optional private-helper snapshot test skipped. All provider transports are synthetic.
- Exact package: 37 tests passed, including sixteen signed HTTP/runtime cases against the generated historical service, replay/conflicts, budget gates, locks and unchanged grocery state.
- Shared Swift package: 72 tests passed, including pending-reminder correlation and retained outbox identity.
- Unsigned iOS Release build with embedded Watch passed in isolated temporary derived data. No signing, pairing, physical operation or app installation was performed.
- Independent review findings in budget retry behavior, callback timeout, 401 handling, SQLite reply locks, malformed routing and partial-ledger restore were resolved and rechecked. No unresolved blocking finding remained at this checkpoint.
- Sealed reminder package manifest SHA256: `548a7ac4f0bacfcf98e4846c2532fac833c7bbe2f17b91416bece45dd11344af`.
- Recorded six-file grocery manifest: `bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281`; source `f8738b9a1529c09a7a577550706d7fb876ea6f89`.

The guard is conservative admission accounting, not an account-wide billing limit or invoice prediction. Restoring both a budget database and its mutable witness to a mutually consistent old snapshot cannot be detected locally and is prohibited. OAuth's documented localhost-testing callback eligibility, actual model language accuracy/latency/usage, server save and actual device alerts remain unverified.

## Boundaries and next action

No live service, grocery data/counter, updater activation, credentials, paid call, Todoist record, account setting or network topology changed. The separate updater still needs combined integration review and remains disabled/unconfigured. Code merged, code installed and human acceptance are distinct states.

The immediate setup gate is Jose's consent to dedicated Todoist browser authorization and its private credential destination. The connected app alone does not configure the server. Secure server installation/transfer, a nonzero reminder allowance, exact loaded-unit diff, restart/domain activation, one precisely authorized live canary and observed phone/Watch alert remain later gates. Missing Todoist consent does not block merging an otherwise reviewed approved code deliverable.

Jose separately authorized squash-merging the four existing approved PRs on November 5. Repository integration is tracked through those PR receipts; it does not authorize production activation or treat the whole Watch MVP as accepted.
