# Contributing

Use synthetic fixtures for protocol/schema/security tests. Native experiments belong in a separately declared evidence layer with version/context/clock/initialization limits. Changes to the SDK adapter need portable regressions plus a separately documented native pilot before making compatibility claims.

The Python core has no runtime dependencies. Prefer the shared service contract over implementing frontend-specific runners. Keep public experiment schemas versioned, neutral gaps deterministic, failures explicit and storage bounded.

Run Python tests, portable C++ checks, browser acceptance for UI changes, and the public-tree audit. Verify the installed wheel works outside the source checkout. CI tests on Windows/Linux and builds a pure-Python wheel; dependency caches and SDK-linked DLLs are excluded from artifacts.

Include provenance and licenses for new third-party material. Fetching an external dependency is not license clearance. Do not contribute proprietary/extracted assets, private captures, measured player profiles, SDK bundles or binaries. For maintainers, publish only allowlisted source/docs/synthetic fixtures; review license changes separately.
