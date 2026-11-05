# Todoist local authorization follow-up — November 5, 2026

Jose approved the dedicated Todoist browser flow and the previously documented
private local credential destination. Sign-in completed, but the first consent
route ended at `app.todoist.com/oauth/permission_submit` with Todoist's 404 page
after **Allow access**. No callback arrived and no credential bundle was saved.
The local callback attempts subsequently timed out. A successful browser click
is therefore not evidence that backend authorization completed.

## Provider discovery and correction

Credential-free GET requests to the official metadata endpoints returned:

- [Protected resource metadata](https://api.todoist.com/.well-known/oauth-protected-resource):
  resource and authorization server `https://api.todoist.com`.
- [Authorization server metadata](https://api.todoist.com/.well-known/oauth-authorization-server):
  authorization endpoint `https://api.todoist.com/oauth/authorize`, registration
  endpoint `https://api.todoist.com/oauth/register`, token endpoint
  `https://api.todoist.com/oauth/access_token`, PKCE `S256`, and public-client
  authentication method `none`.

The prose [OAuth guide](https://developer.todoist.com/api/v1/#tag/Authorization/OAuth)
still names the `app.todoist.com` authorization route. The setup helper now uses
the endpoint advertised by the live authorization-server metadata. The observed
404 motivates this correction; discovery alone does not prove that it resolves
the complete browser flow.

Only the authorization endpoint changes. The existing `task:add` and `data:read`
scopes, redirect/state checks, PKCE, fixed token transport, private-file rules and
rotating refresh storage remain unchanged. No arbitrary endpoint fallback or
additional resource parameter is introduced.

## Candidate verification

The focused authorization suite passed all 35 synthetic tests. Independent review
verified the discovery documents and found no blocker in the endpoint correction.
The reviewed twelve-file candidate replaces only `todoist_auth.py`; all other
runtime hashes and baseline metadata match the previous seal. Its new manifest
SHA256 is `f3e0996c29cc3f88d3132db5885420345f8e07a8c9d4b00dff6cf177243ba92a`.
The earlier `548a7ac4…11344af` artifact remains the pre-correction checkpoint.
All 37 generated-package/runtime tests passed, and the new package was built and
independently verified against that seal. No other runtime hash changed.

This local setup does not install credentials on the server, enable reminders,
alter the grocery runtime, grant a reminder allowance or create Todoist tasks.
The local credential file remains ignored by Git. Provider authorization,
server installation and observed phone/Watch delivery require separate evidence.
