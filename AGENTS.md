# RocketLaboratory

Standalone Python toolkit plus original C++ BakkesMod SDK adapter. Start with README.md, docs/METHODOLOGY.md and THIRD_PARTY.md. Do not import NinjaBladeNoodle, Godot or Blender. Keep original asset/code provenance; no extracted Rocket League assets or copied proprietary code.

## Running and testing

- Python 3.12+: install with `python -m pip install .`; CLI `rocketlab --help`.
- Default backend demo emits synthetic transport fixtures, not Rocket League physics. Use it for UI/CLI/MCP development.
- Native opt-in: `--bridge-dir PATH`, armed expiring marker, loaded official compatible offline plugin. No arbitrary console, DLL injection, anti-cheat changes or game spawning endpoints.
- Portable C++: `cmake -S native -B build/native`, `cmake --build build/native`, `ctest --test-dir build/native --output-on-failure`.
- Python: `python -m unittest discover -s tests -v`. Browser acceptance should verify editing, validation, run, result and cancellation when those surfaces change.
- Audit before publishing: `python scripts/check-public-tree.py`; build a wheel and verify it contains no private files/dependencies.

## Evidence and safety

Record whether a result is synthetic, an internal contract test, a native plumbing check or a timing-qualified measurement. Do not claim exact parity or native timing/hidden-state restore from internal tests or scheduler Done. Preserve separate body/camera/input clocks and source fields. Missing observations remain missing; do not silently reconstruct/interpolate acceptance data.

One native run at a time, at most one reference game instance; honor capture/workspace budgets. Never prune user evidence automatically. Raw captures, manifests/settings, local paths and profiles are private and must not enter Git or releases. Failed/cancelled runs and stop errors must be explicit. Preserve neutralization/watchdog behavior.

## Dependencies and releases

Fetch pinned upstream sources into the external cache with checksum and safe extraction checks. Never vendor BakkesMod SDK/loader, RLBot closed-source Bridge.dll, game assets or runtime binaries. Keep original contributor notices when moving code. No prebuilt SDK-linked plugin releases until distribution terms are separately reviewed. The optional RLBot source fetch is not an implemented RLBot launcher/backend.

## Architecture

`experiment.py`: strict public case contract -> compatible numeric tape. `service.py`: shared jobs/execution. `bridge.py`: bounded nonce/ack file transport and native lease. `report.py`: bounded views/diagnostics. `server.py` + `web/`: loopback UI. `mcp.py`: stdout-clean stdio tools. `instrument/`: original formats and qualification diagnostics. `native/`: portable controller/writer + SDK access. Render/browser clocks must not be treated as physics clocks.
