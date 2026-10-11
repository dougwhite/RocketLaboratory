# Native instrument and file protocol

The original C++ adapter is built against public BakkesMod SDK interfaces. It uses public callbacks/getters/setters and records provenance, not extracted game geometry/code or memory dumps. The portable controller/writer logic is in `native/core.hpp`; SDK-specific access is in `native/plugin.cpp`.

The native plugin was previously developed inside NinjaBladeNoodle. The standalone extraction changes plugin branding and build target, retaining the legacy folder, command names and record formats for compatibility. Its portable tests verify host-independent logic; they do not certify current SDK hooks/game build semantics.

## Loading and arming

Copy your locally compiled `rocketlab_instrument.dll` to the official BakkesMod plugins folder using its normal installation procedure. Use BakkesMod's documented plugin load/reload controls. Arm the bridge **before** loading/reloading the plugin. The toolkit does not inject a DLL or operate a loader.

`automation-enable.txt`: `NNIA1 TOKEN EXPIRY_UNIX_MS`. The marker is valid for at most five minutes. An absent/invalid marker creates no recurring poll task. Changing, removing or expiring the marker disables the bridge and stops an owned capture. A new marker requires plugin reload.

`automation-request.txt`: `NNIC1 NONCE ISSUED_UNIX_MS OPERATION [FILENAME]`. Atomically published by the host. Requests are at most 1 KiB; stale/future requests and duplicate nonces are rejected. Operations: `actors`, `start`, `stop`, `tape`, `prepare_fixed`. No arbitrary console strings are accepted. Tapes use simple filenames confined to the instrument data folder.

`automation-result.json`: matching nonce, operation, success/error, game context, capture filename, capture-local actors, controller stage and limited preparation status. Success is acknowledgement of the command, not physics qualification. Only a matching nonce/operation is accepted by the host. Timeouts do not imply success.

The host obtains an OS lock in the bridge folder before a native run. This serializes all RocketLaboratory processes/workspaces that obey the protocol. Manually-issued console commands or other plugins remain external actors; avoid them during an experiment. Existing captures are detected before starting and are not interrupted.

## Observation clocks

Body/input samples originate from SDK controller/vehicle hooks. Camera samples originate from viewport callbacks. Separate RB/current/old-state tuples and render interpolation alpha are retained rather than silently merged. Engine frame tags, physics state timestamps, monotonic host timestamps, phase tags, callback sequences and related actor IDs are preserved.

Native `timing_qualified` remains false. Camera and body observations need not be simultaneous; a same-frame match is not a solver synchronization guarantee. Setting candidate tape inputs is followed by separate consumed-input readbacks; scheduler completion alone is insufficient.

## Output

The writer produces `.nni`, manifest JSON and terminal status JSON. The stream reader verifies record bounds, CRC, sequence continuity, field representation, finite values and required provenance where present. Generic unknown fields remain explicit names. Missing data is never replaced with default geometry, camera settings, timers or mechanics.

Successful native runs copy their newly-created capture/metadata into the private result job folder and remove only those copied bridge files. On failure, raw evidence remains private; watchdog and stop errors are recorded. The operation audit is also private. Nothing from the bridge folder is part of a source release.

CLI `native actors` inspects an already armed session. CLI `native stop` is an explicit stop request for a current offline experiment. The web/MCP interface only cancels runs owned by its service; it does not expose arbitrary native commands.
