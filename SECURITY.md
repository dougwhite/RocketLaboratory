# Local execution and private evidence

Run the web server on loopback only. It is a single-user local tool, not a service to expose through a reverse proxy. It uses Host/origin checks and a random mutation token; raw telemetry is still visible to authorized local users/processes. Do not run untrusted browser extensions or grant remote users access to the workspace.

Native control is opt-in, offline-gated, time-limited and allowlisted. Do not point a server at an untrusted instrument folder. Respect official game/mod compatibility checks. The repository cannot guarantee game compatibility or immunity from upstream enforcement.

Results, camera settings and native identifiers remain private by default. Do not attach raw telemetry to public issues without reviewing its contents. No credentials are needed by the toolkit. GitHub SSH credentials are solely for Git operations and must never be supplied through the web/MCP API.

Report implementation security problems privately through GitHub's repository security reporting when available. Provide a synthetic reproduction without game/account/private data. Do not publish exploit details or private files in an issue.
