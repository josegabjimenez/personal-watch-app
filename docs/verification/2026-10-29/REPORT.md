# Reminder AI / Todoist increment — October 29, 2026

The Reminder action now uses the existing signed standalone BFF, a schema-only OpenAI interpreter and direct Todoist Inbox task writes. No framework migration or generic agent dispatch was introduced. Grocery and the separately confirmed simple COP expense implementation remain intact.

## Verified in isolation

- **161 offline backend tests passed**, including the retained grocery/expense regressions. All OpenAI and Todoist traffic was mocked; HTTP acceptance tests used synthetic loopback servers only.
- **59 shared Swift package tests passed**, including the new Todoist response decoding, safe-delivery contract and unverified-alert fields. No product Swift/UI source changed.
- Strict schema/refusal/incomplete handling, date/timestamp/ambiguity checks, original-capture relative schedules, exact Inbox/title/time/marker readback, paginated recovery, changed account rejection, and default-alert uncertainty have synthetic coverage.
- Durable plan/UUID/account/mapped-ID persistence, crashes before and after external save, lost acknowledgements, same-command retries, hash conflicts, expiry and concurrent replay are covered. Slow AI does not hold the grocery/expense global execution lock.
- A payment-reminder sentence creates only the Todoist task and cannot execute an expense. No legacy Reminder helper, Notion, Calendar or Slack fallback exists.
- Independent code review identified two actionable edges: first-attempt expiry during read-only Todoist preflight and oversized-capture retry loops. Both were fixed; the reviewer verified resolution with no remaining findings. Prior uncertain writes remain uncertain after expiry.
- Publishing account/repository identity was checked. No tracked deployment workflow or hosting configuration was found; GitHub workflow and deployment listings were empty before feature-branch publication. This is not a claim about unobserved third-party hooks.

## Reproduction

Backend:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=backend python3 -m unittest discover -s backend/tests -v
```

Swift, with isolated temporary build/cache paths:

```bash
DEVELOPER_DIR=/Applications/Xcode-beta.app/Contents/Developer \
CLANG_MODULE_CACHE_PATH=/tmp/personal-watch-reminder-clang-cache \
SWIFTPM_MODULECACHE_OVERRIDE=/tmp/personal-watch-reminder-swift-cache \
swift test --disable-sandbox --package-path apple/PersonalWatchApp/Packages/PersonalWatchCore \
  --scratch-path /tmp/personal-watch-reminder-swift --cache-path /tmp/personal-watch-reminder-package-cache
```

The first Swift invocation used mismatched Command Line Tools compiler/SDK and was blocked by the shared module-cache sandbox. Selecting the existing Xcode only for the command and isolating caches resolved it. No global toolchain change, shared derived-data use, simulator installation, signing or physical-device operation occurred. Full app builds/UI tests from earlier deliveries were not repeated for this backend-only product change; their historical results are not new evidence.

## Review and remaining gates

Implementation was prepared on an isolated managed worktree based on existing PR #1 head `a5fa613`. The primary device owner's checkout was left untouched. The subsequent device-diagnostics commit `91d07a6` is preserved during integration. The coordinator authorized a normal fast-forward update of the same PR after review; the isolated feature branch remains a durable artifact. No force push or second delivery PR is used.

No API key/token was read from any live service, desktop connector or agent environment. No paid model call, live user record write, production audit, deployment/restart/domain enablement, public exposure or physical-device action occurred. Model accuracy and actual latency in Spanish/English remain unmeasured. The model/provider selector is configurable; only the OpenAI adapter is registered in this increment.

The concrete [operator guide](../../REMINDER_AI_TODOIST.md) specifies private credential-file configuration and later authorized rollout/live checks. Real task creation, actual app/backend endpoint parity, Free-plan alert configuration, user-default alert offset and observed phone/Watch notification delivery remain unverified. Success claims stay limited to exact task readback; responses always retain `notification_verified=false`, `alert_configured=null` and `delivery_status=pending_verification`.
