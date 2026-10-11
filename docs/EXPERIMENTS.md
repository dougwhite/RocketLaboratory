# Experiment contract

A case defines an input schedule plus an explicitly limited preparation recipe:

```json
{
  "schema": "rocketlaboratory.experiment.v1",
  "name": "Throttle step response",
  "duration_steps": 360,
  "preparation": "settled_floor",
  "motion_policy": "planar",
  "actors": [{
    "car_index": 0,
    "segments": [{
      "start_step": 60,
      "duration_steps": 240,
      "controls": {"throttle": 1.0}
    }]
  }]
}
```

Controls outside authored segments are neutral. Indices identify currently existing cars; each is resolved to a capture-local actor ID after capture starts. Bindings are saved with the input tape. A car's index is not a permanent identity. The native actor registry resets with each capture.

Axes: `throttle`, `steer`, `pitch`, `yaw`, `roll`, `dodge_forward`, `dodge_strafe`, finite values in [-1,1]. Buttons: `handbrake`, `jump`, `activate_boost`, `holding_boost`, true/false. `jumped` is engine-derived and cannot be authored true. The fixture flip-cancel schedule is an input-delivery experiment, not proof of a mechanically correct half flip.

`duration_steps` is 1..7200. At most 16 distinct cars and 115200 dense actor-steps are accepted. Unknown fields, invalid values, segment overlaps, out-of-bounds segments and duplicate indices are rejected. Missing actors are an error; the service never silently creates or substitutes one.

**Candidate steps are engine frame observations.** The plugin advances tapes using candidate frame tags observed through SDK hooks; exact solver-step alignment is not established. There is no JSON `physics_hz` setting that promises the experiment advances at a requested solver frequency. Native durations depend on observed frames; wall-clock watchdogs stop long/stalled cases.

## Preparation

`settled_floor` requires 120 consecutive candidate frames with all four world wheel contacts, low linear/angular speed and unused jump state, according to available SDK observations. It does not set arbitrary hidden state. Place the existing cars in a suitable starting condition yourself.

`fixed_pose_then_settle` invokes one guarded native recipe for a single local car at index 0 in the supported Stadium local-server context: position `(0,-3000,200)` in native units, known upright orientation and zero requested velocities, followed by the same floor settling gate. Setters being invoked is recorded as `applied_setters_unverified`, not a validated checkpoint restore. Capture readbacks must be checked separately. This is not a user-programmable pose setter.

Arbitrary suspension compression, contact manifolds, jump history, dodge clocks, camera settings, ball state, car spawning, airborne start states, resource mutation and manual camera-swivel controls are currently **unsupported** by the public runner. They must not be faked with undocumented JSON keys. Raw observations for some of these states are available even though writes/restores are not qualified.

`motion_policy`: `planar`, `vertical` or `neutral_baseline`. Used by input-delivery qualification diagnostics when explicitly assessed; it is not a physics acceptance target and does not change scheduler mechanics. The host's basic stream report does not automatically run this delivery assessment.

## Campaigns

A bounded list of independent cases, repeated serially:

```json
{
  "schema": "rocketlaboratory.campaign.v1",
  "cases": [{"experiment": {"schema": "..."}, "repeat": 3}]
}
```

Use a complete experiment object in each case; see `examples/repeatability-campaign.json`. Maximum 100 total runs, 20 repetitions per case. Every case is validated before any execution. Failure stops the campaign. Native session expiry is never silently extended; arm/reload manually between bounded campaigns. A fixed-pose repeat is a fresh setter invocation plus settling, not identical suspension/jump/contact state by assumption.

## Wire formats

NNI2 binary stream and NNIT1 dense input tapes remain compatible with the original MIT instrument. Internal `noodle.instrument.*` schema identifiers and SDK command names are retained for compatibility. Public experiment/job/report schemas use `rocketlaboratory.*`. No game project is imported or needed.

`rocketlab compile case.json tape.txt --bindings '{"0":7}'` requires explicit known actor bindings. Normally the runner discovers bindings and compiles automatically. Scientific exports preserve raw tuples without interpolation. The browser receives at most 1200 preview records with only chart fields and record clocks; full tuples remain in the raw capture. Preview decimation is not a resampling method for acceptance tests.
