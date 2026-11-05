# Reminder package over accepted Mercado

This package adds timed personal reminders to the recorded six-file historical Mercado release. It does **not** deploy the combined MVP server or use the obsolete four-file updater. The five grocery modules remain byte-identical. The historical server retains its Config, CLI arguments, HMAC functions, planners, grocery route gates and original HTTP method bodies; the builder adds only `import reminder_runtime`, private names for the original HTTP methods, and two thin wrappers. Non-reminder requests retain the original body and method path. Reminder execution has its own exact authority checks, short SQLite claims and per-request lock.

Recorded baseline: source `f8738b9a1529c09a7a577550706d7fb876ea6f89`, manifest SHA256 `bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281`, historical server SHA256 `309602498c2a9ccf3186e7c336ed9ff5cc2fe3b693866fc1fbfdb18c1b0d5100`. The owner last independently checked it on November 4; Jose accepted current Mercado use on November 5. This reminder work has not refreshed production state. A different installed baseline requires reconciliation rather than bypassing a hash check.

The package has exactly twelve runtime files: the server, the five frozen grocery modules, and `reminder_runtime.py`, `reminder_capture.py`, `reminder_ai.py`, `reminder_ai_budget.py`, `todoist_reminders.py`, `todoist_auth.py`. No finance adapter, generic dispatch, new public listener or app installation is included.

## Reproduce and verify

Obtain the exact previously reviewed grocery artifact as `GROCERY_RELEASE`; its six code files and manifest must match `baseline_manifest.json`. The public source commit above can reconstruct it with that revision's grocery AI builder. Do not rebuild it using later mutable source. This builder opens no credentials, services, ledgers or provider connections.

```sh
python3 ops/hotfixes/reminder-ai/build_release.py \
  --grocery-release "$GROCERY_RELEASE" --output /private/tmp/personal-watch-reminder-release
python3 ops/hotfixes/reminder-ai/build_release.py \
  --verify --output /private/tmp/personal-watch-reminder-release
```

Output must be new. A changed source, manifest, symlink, extra file or import outside the reviewed closure fails verification. Sealing is a separate maintainer action after runtime code/review is frozen; never reseal to bypass a mismatch.

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s ops/hotfixes/reminder-ai -p 'test_*.py' -v
```

All verification uses synthetic providers and temporary files; loopback tests need ordinary local socket permission. Test success is not live Todoist/phone acceptance.

## Secure setup before rollout approval

1. Reuse the approved existing app OpenAI key through a **path-only** setting. Its value is never copied into this package, chat or a command. Do not change or remove the grocery credential setting.
2. Complete the dedicated Todoist OAuth setup after Jose consents to its private destination and browser authorization. The setup tool registers a public PKCE client and asks only `task:add` plus `data:read`; the latter is account-wide read permission, although this backend reads only identity, labels, Inbox reconciliation and exact tasks. It does not export the Codex plugin's authorization. The one-time callback binds loopback only; Todoist documents localhost for testing, and actual callback eligibility remains a setup check. No public HTTPS endpoint is added.
3. Store the resulting private bundle only at the explicitly confirmed destination. Its refresh token rotates; writes are atomic with a durable pre-refresh reservation. An uncertain refresh requires reauthorization, not replaying an old token. A separate explicit secure transfer/installation is needed for the service destination. Keep local/server copies from being used concurrently: rotating refresh credentials cannot safely have independent writers.

   The setup authorization URL follows Todoist's live OAuth discovery metadata.
   See the [November 5 setup follow-up](../../../docs/verification/2026-11-05/TODOIST_OAUTH_SETUP.md)
   for the older route's observed consent error and the independently checked
   endpoint correction. A browser click alone does not establish that the bundle
   was saved successfully; verify the CLI's local status and file permissions.
4. Set the exact existing Inbox metadata pin, verified against the chosen app-server account. The first account must match it. Reuse existing contextual labels; never create labels or reorganize projects. An appointment, project note or generic capture is outside this package.
5. The separate reminder monthly allowance defaults to **zero**. A nonzero limit remains a required spending/rollout decision; the grocery USD2 counter neither funds nor limits reminders. Each attempted call reserves USD0.32 against a worst-context pricing envelope, then verified completed usage releases excess using conservative upper rates. Missing/refused/uncertain usage retains the reservation. At most two model attempts per original request are permitted. The reserve is not a prediction of invoice cost; actual model accuracy, latency and usage have not been measured here.

## Exact scoped rollout plan — not executed

Only after setup, independent review and explicit scoped rollout approval:

1. Acquire single service ownership. Inspect the **actual loaded** unit, argv, FragmentPath, DropInPaths and EnvironmentFile paths without printing environment values. Preserve the existing HTTPS/HMAC location, host/port, external helper and grocery-only drop-in. Confirm whether domains are supplied by argv or environment; do not assume an environment override changes explicit argv. The inactive updater pins an obsolete four-file package and must stay inactive.
2. Verify the installed six hashes against the recorded baseline and the external helper pin `24be164879b114487516b85b6811cce8239a4ad0744ec1e36bfc575a75a8e7e0`. Verify the twelve-file candidate against its committed seal. Abort on drift, symlinks, conflicting service ownership or a changed helper.
3. Quiesce only the existing mobile-capture service using its established stop procedure. Preserve pending/uncertain grocery and reminder operations; unresolved in-flight outcomes require reconciliation, not cancellation or identity reset. While stopped, create one private backup receipt and a coherent snapshot of the current runtime files, parent ledger, grocery journal, grocery initialized marker, any reminder budget DB/witness, OAuth bundle metadata and relevant unit/drop-in metadata. Include WAL/SHM or use SQLite backup from the stopped state; never mix snapshots from different points in time. Do not read or print credential values.
4. Preserve live data in place. Install the new server and six reminder modules into the same runtime directory while retaining the five grocery module bytes and file ownership/mode. Do not run the previous grocery upgrader. Initialize a **new** reminder budget once with the specifically approved allowance, using `backend/scripts/reminder_budget.py initialize --budget <approved-path> --monthly-limit-usd <approved-limit> --confirm-initialize`. A missing/corrupt budget is not permission to initialize a replacement. Preserve its witness outside automatic database restores.
5. Install only the reviewed reminder configuration, adding `aura.reminder_capture` alongside the existing grocery domain in the actual effective configuration. Reuse the existing OpenAI file path. Do not edit grocery settings/cap/state, global Todoist notification settings, finance/HA configuration or the network topology. Use the actual existing unit's reviewed configuration mechanism; any required argv edit must be part of the final reviewed diff, not guessed from this example.
6. Start only that service. Verify active state, all twelve hashes, unchanged five grocery hashes, unchanged endpoint and expected health domain list. A health response does not prove authentication, a paid model call or a task write. Inspect local safe error codes only; keep captured text, tokens and raw logs private.
7. Run no task/AI canary until its precise separate authority exists. The proposed bounded verification is one clearly identified synthetic short-future personal reminder, its exact Todoist Inbox/title/labels/Bogotá task-time readback, and retry of the **same original capture** showing the same stable task with no duplicate. Do not use new request IDs to test replay. Record actual model latency/usage separately from mocks. A payment-reminder sentence must create only a task.
8. Verify the installed client's actual endpoint and compatible response path without unnecessary reinstall or pairing changes. Read the default reminder offset and the task's actual reminders through supported APIs when authorized. Task time must remain unchanged even if the default alert offset differs. Do not claim configured/delivered alerts from task creation, change global preferences, purchase an upgrade, or add an unverified custom reminder. Observe the alert on Jose's intended phone/Watch under recorded conditions before accepting notification delivery.

The unit's complete effective configuration and the new spending allowance are deliberately **unapproved/unset**. These are concrete preflight/approval gates, not a generic permission to change services. No merge, restart, route activation, paid API call, real Todoist task, transfer or login was performed by this implementation.

## Rollback and restore

Prefer disabling **new reminder writes** through the reviewed configuration while preserving all records and recovery metadata. If code rollback is necessary, restore only the recorded six-file grocery runtime and original grocery-only configuration, with the service quiesced. Preserve the parent/grocery journals, both initialized witnesses, reminder operation rows and any unknown writes. Old grocery code ignores reminder rows; do not remove them or issue fresh request/command IDs. Keep compatible reminder recovery code available for reconciliation without re-enabling unapproved writes.

Never restore a pre-budget grocery backup over live spending state. Never restore the reminder DB and its witness to an earlier mutually consistent snapshot; local code cannot detect that joint rollback. Database-only loss/restore fails closed. Restoring a main operation ledger while reminder budget history survives marks the capture as requiring reconciliation, and stable HMAC-derived command IDs provide additional protection. Existing previously persisted command IDs are preserved. Do not auto-initialize, clear unknown reservations, refund unknown calls or rotate the signing identity to make a replay work.

The app can say a task is saved only after exact readback. Uncertain outcomes stay failed/retryable in the original outbox with concise local copy; saved task, alert configuration and observed device receipt remain separate results.
