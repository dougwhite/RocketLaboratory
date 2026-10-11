# CLI, browser and MCP interfaces

All three frontends use `rocketlaboratory.service.Service`. It validates cases, allocates private jobs, bounds storage and serializes one execution worker. Native control additionally holds a shared OS lease on the external instrument folder.

## CLI

`rocketlab --help` lists commands. `--workspace` and `--bridge-dir` are global options before the command. `run` is synchronous for shell automation. `status`, `jobs` and `result` read saved jobs from another invocation. Ctrl-C cancels the owned run during service shutdown.

Browser/MCP cancellation belongs to the service that submitted the run. There is no cross-process generic kill endpoint. If a lost native session needs an explicit stop, use the allowlisted CLI `native stop` after inspecting ownership/state.

`compile` writes a new tape with explicit actor bindings. `export` writes a new JSONL preserving observations, without interpolation. `compare` calculates common raw actor/frame/phase position RMS and reports unmatched bodies. Users must independently establish equivalent actor bindings/initialization; this diagnostic does not retime streams or claim parity.

## HTTP

Loopback HTTP, default 8890. GET `/api/session` provides a random session token, capability report and presets. POSTs require `X-RocketLab-Token` and same-origin checks. Unexpected Host headers are rejected to reduce DNS-rebinding exposure. The token protects local browser mutations; this is not a multi-user or authenticated remote server.

- `GET /api/jobs`: recent jobs.
- `GET /api/jobs/{id}`: status.
- `GET /api/jobs/{id}/report`: bounded decimated preview/report.
- `GET /api/jobs/{id}/capture`: raw private capture download.
- `POST /api/validate`: experiment object.
- `POST /api/run`: `{ "experiment": {...}, "backend": "demo" | "native" }`.
- `POST /api/cancel`: `{ "job_id": "..." }`.

No CORS permission or remote bind option is exposed. The endpoint cannot upload files, inject arbitrary console commands or launch a game. It can submit native input tapes only if the operator explicitly configured an armed bridge directory.

## MCP

Stdio JSON-RPC, one UTF-8 JSON message per line, stdout used only for protocol responses. Negotiates supported 2024-11-05, 2025-03-26 or 2025-06-18 protocol versions; offers tools, without resources/prompts/subscriptions. Max incoming message 1 MiB. `initialize`, `notifications/initialized`, `ping`, `tools/list`, `tools/call` are supported. Tool failures return `isError`; malformed/unknown protocol requests return JSON-RPC errors.

Tools: `rocketlab_doctor`, `rocketlab_presets`, `rocketlab_validate`, `rocketlab_run`, `rocketlab_jobs`, `rocketlab_status`, `rocketlab_result`, `rocketlab_cancel`.

Run submits asynchronously. Poll status, then read results. The default source is synthetic demo. Set native backend explicitly only after doctor confirms a configured session and the operator has armed/reloaded the plugin. Native timing/restore remain unqualified even when a job completes successfully.

All external native control must be offline. AI agents should start with schema validation and a short pilot, preserve raw observations/settings privately, and never represent a synthetic fixture as a game measurement.
