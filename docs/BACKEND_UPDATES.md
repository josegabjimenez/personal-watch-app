# Manual backend updates on the existing VPS

This delivers the update mechanism for review. It is **not activated**. Pushing or
merging this PR only runs synthetic checks. Nothing here installs credentials,
changes Tailscale/SSH policy, restarts the service, or modifies its settings/data.

## Release boundary

The existing service is `hermes-mobile-capture.service`. Its loopback listener,
Tailscale HTTPS endpoint, launch arguments, helper paths, private configuration,
SQLite ledger and adapter state stay in place. The receiver replaces only exact
Python files declared in a reviewed Git revision and bound to private fixed host
paths. No checkout, archive extraction, shell command, package installation,
environment change or database operation runs on the VPS.

At this PR's base (`970e14d`), `main` is older than the installed grocery hotfix.
The November 2 deployment evidence records installed BFF SHA256
`f6eae64baaec83a04439919b9ffe54f49f4330ae60cbe55d54412e3e04041c38` and unchanged
service/settings/health. That is prior verified evidence, **not a new live audit**.
PR #1 also contains paused AI/Todoist work. Neither this automation revision nor
PR #1 is designated a production candidate by this document.

**Before activation, merge a separately reviewed, focused production candidate
that retains the installed hotfix and the accepted Mercado behavior.** Its exact
`ops/deploy/release-files.json` must name the intended code. The initial map contains
only `bff`; the external Aura helper is preserved. A release needing helper changes
must first include its reviewed, credential-free source in Git, explicitly add
`aura_helper` to that map and the private receiver targets, and verify the pair
together. `expense_helper` is the only other supported slot. Changing the file map
requires a separate reviewed activation/configuration step; it cannot happen over
the release transport. Do not deploy a partial BFF/helper change.

The installed-code fingerprint covers the managed slots only. For a BFF-only
release it checks the helper's launch path, not its code bytes. Record and review
the unchanged helper's current hash/compatibility at each release handoff, and
serialize any other helper maintenance. Unchanged health cannot detect helper drift.

Every release must preserve the existing storage contract, including uncertain
write claims. Schema migrations, new dependencies/imported local modules,
configuration changes, domain activation and incompatible rollback are outside
this mechanism. Code review and synthetic compatibility checks must establish
that the proposed code can run with the existing interpreter/configuration/state
and that its predecessor can still run afterward. A code rollback never reverses
captures or restores an older database.

## What runs

1. `Backend checks` runs offline backend and deployment regressions on PRs/pushes.
2. After setup and explicit activation approval, `Manual backend update` runs only
   through **Run workflow**, from **main**. It requires a full 40-character commit
   already contained in that immutable workflow revision and a typed confirmation.
3. A credential-free job reruns the exact candidate's synthetic tests and builds
   deterministic JSON from regular committed Python blobs. Dirty/untracked files,
   ledgers, secrets and configuration are excluded. The code-only artifact expires
   after three days; its SHA256 is checked again before transport credentials appear.
4. The `watch-production` environment gates the SSH job. SSH validates the pinned
   host identity and uses a dedicated forced-command key. No agent forwarding,
   PTY, arbitrary remote command or trust-on-first-use is allowed.
5. A host `flock` rejects overlapping operations, including local invocations.
   GitHub concurrency also queues release runs without cancelling the active run
   (GitHub may replace an older pending run). A stale installed-code fingerprint
   fails before stopping the service; refresh status and review before retrying.
6. The receiver requires the exact active process arguments and exact loopback +
   HTTPS health profile. It saves the current code and ownership/modes durably,
   journals the operation, stops the service, atomically installs the code, and
   starts it. Success requires a different process identity, expected code hashes
   and unchanged health at both URLs. Health alone does not identify code or prove
   end-to-end grocery/expense/reminder behavior. It never sends a capture.
7. Failure or a catchable termination restores the previous code and verifies its
   restart/health. The run still fails so the operator sees that the update failed.
   A killed process/power loss leaves a journal: mutation is blocked until deliberate
   recovery. Each service operation is capped at 30 seconds; each health check has
   a bounded retry window. Network probes run in transient isolated child processes
   with parent deadlines, child lifetime limits and cleanup; they are not daemons.
   The SSH client never retries an uncertain update.

## Precise activation handoff — not performed by this PR

Jose must review the PR and authorize this concrete setup/live-release boundary.
No deployment key, CI access grant, environment, policy change or live route was
created during implementation. A missing credential does not prevent offline use
of the builder/tests.

1. On the existing VPS, narrowly read the service's `FragmentPath`, `ExecStart`,
   `WorkingDirectory`, `User`, `Group`, `MainPID`, `ActiveState`, `SubState` and
   `EnvironmentFiles` metadata. Verify only code hashes/ownership, explicit ledger
   and helper-state paths, and existing local/HTTPS `/health`. Do not dump the unit
   environment, journals, secrets, ledger rows or capture bodies. Confirm no shared
   helper consumer will be disrupted. Preserve current backups.
2. Confirm an existing reachable SSH route from the intended runner, through the
   current firewall/access policy. No such CI SSH route or credential was verified
   here; prior deployment used the provider console. Do not expose a tailnet route,
   open a port or create a runner/daemon to work around this gate. If the existing
   SSH route is unavailable, use the same reviewed sender from an already trusted
   machine with existing reachability, after the same activation approval.
3. Review and install `ops/deploy/receiver.py` separately as a root-owned regular
   file at `/usr/local/libexec/personal-watch-receiver.py` (mode `0755`). Run it with
   `/usr/bin/python3 -I`. Install a private, root-owned mode `0600`
   `/etc/personal-watch-deploy.json`, adapting the example to **audited actual**
   paths/argv/health. Record the installed receiver's commit and SHA256 with the
   activation evidence. Updating Git source does not update this host receiver;
   receiver upgrades need a separate reviewed installation. Its directories and
   targets must have root ownership and no
   group/other write permission. This preserves existing ownership; if the actual
   layout differs, resolve that in the reviewed activation handoff, without silently
   changing the service. The receiver/config live outside the managed code paths.
4. Use `/var/lib/personal-watch-deploy` (root-only `0700`) for new code-only release
   state. Keep it separate from application state. Preserve the existing explicit
   `--ledger` path even if it is inside `backend/data`; never initialize a replacement
   ledger or copy data into releases. Retain all private helper state/config paths.
5. After explicit credential/access approval, create a dedicated deployment key,
   not a personal/admin key. The existing root account can restrict just this key
   with this `authorized_keys` entry (substitute the public key only):

   ```text
   restrict,command="/usr/bin/python3 -I /usr/local/libexec/personal-watch-receiver.py" ssh-ed25519 <DEDICATED_PUBLIC_KEY>
   ```

   The receiver also requires `SSH_ORIGINAL_COMMAND=personal-watch-release-v1`.
   This key grants code-deployment authority under the existing service's
   privileges. Reviewed application code must therefore be trusted with those
   privileges; a forced command is not a sandbox for malicious application code.
   No general-purpose SSH key belongs in CI. Verify denied shell/forwarding requests
   and a read-only `status` using this exact restricted key before enabling releases.
6. Verify the SSH host public key/fingerprint through the trusted provider console
   or previously trusted host record, then save the exact `known_hosts` line for the
   actual hostname/port. An unverified `ssh-keyscan` result is insufficient. Never
   use `StrictHostKeyChecking=no`, `accept-new` or disabled TLS validation.
7. In GitHub, create `watch-production`, restrict deployment branches to `main`,
   and require Jose's review where supported. Protect `main` with PR review and
   `Backend checks / synthetic`. Confirm environment-review support for the actual
   repository/account before relying on it. Add only environment secrets
   `WATCH_SSH_HOST`, `WATCH_SSH_PORT` (or leave empty for 22), `WATCH_SSH_USER`,
   `WATCH_SSH_PRIVATE_KEY`, `WATCH_SSH_KNOWN_HOSTS`. No app/Notion/Firefly credentials
   enter CI. After that setup is accepted, set repository variable
   `WATCH_UPDATES_ENABLED=true`. It is absent/disabled by default.
8. Run **status** first. Inspect its hashes/active/previous/recovery state privately.
   Confirm the proposed focused production revision retains the installed fix and
   is compatible with current helper/storage. Obtain authorization for that exact
   first live release. This PR does not grant it or mark functional acceptance.

## Normal use after activation

In Actions → **Manual backend update** → **Run workflow**, select **main**.

| Operation | Required inputs | Confirmation |
| --- | --- | --- |
| Inspect | `action=status` | `status` |
| Update | `action=deploy`, full `revision`, fresh `expected_current` | `deploy <revision>` |
| Roll back | `action=rollback`, retained `release_id`, fresh `expected_current` | `rollback <release_id>` |
| Recover interrupted operation | `action=recover` after inspecting status | `recover` |

`current` is SHA256 of the canonical sorted JSON map `{slot: installed_sha256}`.
`release_id` is SHA256 of canonical bundle JSON. Keep successful receipts with the
reviewed commit/PR; outputs contain only fixed status/error codes and hashes.
Rollback supports the previous healthy release or the preserved adoption baseline.
The baseline uses revision `000…000`, meaning captured host code, not a Git commit.
It is never confused with reviewed-source provenance.

When SSH disconnects or a job times out, the outcome is unknown: inspect **status**
before any new operation. If `recovery_required=true`, choose **recover** to restore
the journal's pre-release code and verify health. If recovery itself fails, stop
updates and use the existing provider console to diagnose only the fixed error
boundary. Preserve the journal, all code snapshots, data and credentials. Do not
replay captures, delete uncertain rows, or force a new baseline to clear drift.

Release retention keeps the newest configured number (default five) of managed
code snapshots plus active, previous and adoption baseline; those protections can
make the total slightly larger. Verified failure recovery also prunes old failed
candidates. Pending recovery is never pruned. Unknown filenames, old operator
backups and everything outside the receiver's own `releases` directory are untouched.

For a trusted local sender, supply the same five `WATCH_SSH_*` values through
private process environment (never commit a shell command containing values):

```bash
python3 ops/deploy/build_bundle.py --revision <FULL_REVIEWED_SHA> --output /tmp/watch-release.json
python3 ops/deploy/send_release.py --action status
python3 ops/deploy/send_release.py --action deploy --bundle /tmp/watch-release.json --expected-current <FRESH_FINGERPRINT>
```

Local transport does not verify GitHub merge/CI/review state for you. Establish
those gates for the exact SHA first. Do not enable shell tracing or verbose SSH.

## Offline verification

```bash
PYTHONPATH=backend python3 -m unittest discover -s backend/tests -p 'test_*.py'
python3 -m unittest discover -s ops/deploy/tests -p 'test_*.py'
```

Tests use temporary Git repositories, synthetic code/data, fake service/health,
real isolated process locks and termination signals. They make no production
connection. The historical backend regression opens only a temporary loopback
HTTP server. See the PR verification record for the actual run and review results.

Implementation follows the primary [GitHub workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax),
[OpenSSH key restrictions](https://man.openbsd.org/sshd.8) and
[host identity verification](https://man.openbsd.org/ssh_config) documentation.
