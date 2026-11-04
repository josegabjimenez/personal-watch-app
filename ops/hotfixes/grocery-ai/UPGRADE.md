# Grocery Luna upgrade

`upgrade_release.py` is a separate upgrade from the sealed installed four-file
grocery release to the reviewed six-file Luna release. It does not use or change
the historical initial installer. The target manifest SHA256 is
`bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281`.

The operator must first provide an absolute path to an existing ordinary private
provider-key file on the server, owned by the root service account with mode
`0600`. The installer checks only its metadata and never reads, copies or prints
the key. It also checks only metadata of the existing HMAC secret. Key
provisioning is outside this installer; no desktop credentials are used.

Run as root with an already verified package and the existing public HTTPS
`/health` URL. Replace the example paths and hostname with inspected values:

```sh
python3 /reviewed/upgrade_release.py \
  --package /reviewed/grocery-luna-release \
  --backup-root /private/grocery-upgrade-backups \
  --key-file /private/grocery-openai.key \
  --health-url https://existing.example/health
```

That command performs read-only checks. To perform the already authorized
upgrade, use the same arguments plus both explicit activation flags:

```sh
  --apply --initialize-budget
```

The check requires the active root-owned `hermes-mobile-capture.service`, exact
four-file source hashes, the pinned external helper, loopback host/port, and only
the grocery write domain. It rejects unexpected AI files/settings, pending or
uncertain work, unknown journal schemas, unbudgeted prior v2 interpretation,
unloaded unit changes, and unknown grocery drop-ins. Existing unrelated arguments,
environment, unit files, helper paths and HMAC configuration remain intact.

During activation it reserves parent-ledger writes with bounded `BEGIN IMMEDIATE`
and requires zero in-flight parent claims. New capture claims block behind that
reservation while the exact unit is stopped. The server's internal
`CAPTURE_EXECUTION_LOCK` is a process-local thread lock; the installer does not
claim to hold it or an external helper/global filesystem lock. This operation
does not stop another unit or make unrelated external edits atomic.

While the service is stopped, it backs up all four code files, the parent SQLite
database, an existing grocery journal, an existing budget marker, and relevant
configuration metadata. SQLite copies receive integrity checks and logical
readback verification. Only then does the isolated child import the verified
staged modules to create additive journal tables and explicitly initialize or
validate the budget. The durable `<journal>.ai-budget-initialized` marker is
written before the SQL commit and is retained if initialization fails.

All six complete files are staged with root ownership and the existing entrypoint
mode. The entrypoint is replaced last. The single new drop-in contains only:

- `GROCERY_AI_ENABLED=1`
- `GROCERY_AI_MODEL=gpt-6-luna`
- `GROCERY_AI_OPENAI_KEY_FILE=<provided private absolute path>`
- `GROCERY_AI_TIMEOUT_SECONDS=8`

Budget, model rates and provider endpoint cannot be overridden by this installer.
After daemon reload and start, it checks the exact arguments, environment,
metadata, helper, data identities, all six hashes and strict grocery-only HTTPS
health. It sends no capture, provider request or grocery test item. Reports
contain fixed codes and aggregate counts, without private paths, dictations,
request IDs, key contents or raw exception text.

If a failure occurs before budget initialization, the installer resumes the
verified unchanged baseline when safe. Once initialization starts, a failure
leaves this unit stopped and reports `attention`; an incomplete marker, newer
budget state and any installed compatible code are retained. Recovery must keep
v2/budget-aware code and the existing data. Never restore either database or
restart the old four-file writer over newer state. The installer intentionally
does not automate that unsafe fallback.

After successful installed-hash/configuration/HTTPS readback, the authorized
synthetic provider check uses the actual installed runtime and the same budget
journal as the app. Supply the inspected paths, the same private key file and a
stable run ID; this sends only three fixed synthetic fixtures and stops at the
first failure:

```sh
python3 /reviewed/live_smoke.py \
  --runtime /existing/backend-directory \
  --manifest /reviewed/grocery-luna-release/manifest.json \
  --manifest-sha256 bad63273cc49f2f7ca7342adcd3bd7f6d6e00a9658a7682d40d53c96a6d3a281 \
  --journal /existing/mobile-ledger.sqlite3.grocery.sqlite3 \
  --key-file /private/grocery-openai.key \
  --run-id luna-20261104-first
```

The manifest stays in the reviewed package; no seventh runtime file is installed.
The harness verifies and executes the six installed source bytes against it,
bypassing cached Python modules. It calls no helper/Notion service and never
writes grocery products. Do not change the run ID to repeat an uncertain call;
retain its budget reservation. A successful small fixture test is not physical
Watch acceptance or a general accuracy guarantee.

Local fault tests, including real reviewed-module initialization against
temporary synthetic databases, run with:

```sh
python3 -m unittest discover -s ops/hotfixes/grocery-ai -p test_upgrade_release.py
```
