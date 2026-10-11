# Investigation methodology and capability ledger

The goal is to distinguish observed rules from fitted guesses. A reliable result needs a declared build/context, reproducible preparation, verified input delivery, validated timing and independent holdouts. A toolkit UI or a passing internal test is not evidence that those conditions hold in Rocket League.

## Evidence layers

1. **Transport checks**: stream integrity, source field identity, bounded allocation, watchdogs and safe command handling. Synthetic tests establish these contracts only.
2. **Native plumbing checks**: offline context, expected map, actor bindings, authored/consumed input echoes, coverage, source timestamps and output movement. Scheduler Done is separate from these checks.
3. **Timing qualification**: independently establish which hook precedes/applies/follows a solver step; identify camera update and interpolation phases. Still unqualified.
4. **Initialization qualification**: verify complete hidden state or demonstrate reproducibility through a named settling recipe and measured distributions. Setter invocations alone do not qualify suspension/contact/jump history.
5. **Physics identification**: isolate inputs, fit the simplest defensible model, report residuals, test alternative explanations and reserve unseen start states/settings as holdouts. Never infer exact equations from a single matching trajectory.

The extracted `rocketlaboratory.instrument.qualify` module supplies stricter input-echo/body-coverage/map diagnostics over supplied capture, manifest, tape and match configuration. It deliberately leaves exact acceptance false. Use `python -m rocketlaboratory.instrument.qualify --help`. It is a separate analysis command; the simple host report only checks stream integrity.

## Granular experiment plan

- Neutral fall: gravity/damping with orientation and initial velocity varied. Requires qualified airborne initialization before this can become an automated native campaign.
- Ground response: neutral/coast/brake/throttle/boost; sweep starting speed and throttle; separately vary contact and suspension. v0.1 can script floor-settled input sequences, not arbitrary initial speed/checkpoints.
- Jump: pulse/hold/release and recovery; read native configuration and mechanic flags, then fit impulses/forces independently of renderer timing.
- Dodge/cancel: directional axes, button edge timing, opposing inputs, cooldown/history. Keep requested, hook readback and event/mechanic state separate.
- Contact: roof slide, ceiling detachment, ramps, walls, corners and goals. Validate geometry and contact semantics before force identification.
- Cars/ball/pads: relative-speed/orientation collision sweeps, demos, pickup/resets. Existing multi-car inputs and some observations are available; spawning/initialization/lifecycle qualification remain incomplete.
- Camera: acceleration, reversing, steering, air roll, ball-camera transitions and manual swivel across settings. Native POV/settings/targets are recorded, but authored swivel/ball-cam/settings interventions and aligned camera/body timing remain future work.

For each family: vary one factor first, repeat to estimate noise/hysteresis, then use constrained cross-factor cases. Label every preset/derived field with units, source and initialization/timing status. Release synthetic fixtures freely; users decide whether to publish their own raw native observations/settings.

## v0.1 capability ledger

| Capability | Status |
| --- | --- |
| Web editor, CLI, MCP, JSON validation | Implemented; internal and browser acceptance checks |
| Serial campaigns and native cross-process lease | Implemented; contract tests |
| Pinned external source retrieval/checksums | Implemented; external sources not bundled |
| Native binary format, input scheduler, SDK observations | Original adapter extracted; portable tests; standalone runtime qualification separate |
| Native attachment / nonce-acknowledged commands | Implemented; fake-plugin protocol tests do not certify a live game |
| Native input delivery assessment | Extracted strict diagnostics, synthetic regression checks |
| Arbitrary hidden-state initialization | Unsupported |
| Camera control/settings authoring | Unsupported; camera observation only |
| Complete checkpoint restore and atomic scene capture | Unqualified |
| Exact solver-phase timing / physics parity | Unqualified |
| RLBot launcher/backend integration | Future work; optional source fetch only |
| Proprietary binaries / extracted game assets | Excluded from distribution |

## Next qualification milestone

A maintainer should build the standalone DLL, attach through an official compatible offline mod setup, and capture a small neutral/throttle/jump pilot with manifests and input-delivery assessment. Report current game/mod/compiler versions and any transformed input readbacks. Only then qualify stage timing using controlled probes and define a fresh native acceptance suite. Do not promote synthetic contract tests to native evidence.
