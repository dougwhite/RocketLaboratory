# Licensing and provenance boundaries

The repository's MIT license covers our original toolkit, C++ adapter and synthetic fixtures. Original NinjaBladeNoodle contributor notices are retained in `LICENSE`; extraction source hashes are recorded in `docs/EXTRACTION.json`. No NinjaBladeNoodle game simulation, assets, measured vehicle presets or private telemetry are incorporated.

## External components

| Component | Relationship | Distribution policy |
| --- | --- | --- |
| BakkesMod SDK | Optional external MSVC build dependency; pinned public upstream archive | No SDK headers/libraries vendored or released here. The upstream repository has no standalone LICENSE identified at review; its README documents cloning/building plugins. This is not a blanket redistribution grant. |
| Official BakkesMod loader | User-provided runtime | Not bundled, modified or reimplemented. Follow upstream compatibility and terms. |
| RLBot Python interface | Optional pinned MIT source retrieval for future integration | Kept in external cache with upstream notices; not needed by the current native backend. |
| RLBot server / Bridge.dll | Possible user-provided runtime for future integration | Not bundled. Core's MIT license does not make its closed-source Bridge.dll open source or grant this project redistribution rights. |
| Rocket League | User-provided proprietary reference application | No executable, assets, private SDK internals, extracted geometry or proprietary source included. Use remains governed by applicable upstream terms. |
| Proton/Wine | Optional user-provided compatibility environment | Not bundled or modified by this project. |
| Python/setuptools/CMake/MSVC | Runtime/build tools | User/build-environment dependencies, not included artifacts. |

Reviewed primary sources:

- [BakkesMod SDK development README](https://github.com/bakkesmodorg/BakkesModSDK/blob/master/README.md)
- [RLBot Python interface MIT license](https://github.com/RLBot/python-interface/blob/master/LICENSE)
- [RLBot Core's explicit closed-source bridge description](https://github.com/RLBot/core#bridge)
- [Official RLBot installation](https://rlbot.org/)
- [Official Proton repository](https://github.com/ValveSoftware/Proton)

Pinned archives and hashes live in `src/rocketlaboratory/dependency-lock.json`. Fetching preserves upstream notices in the external tree. Dependency management does **not** erase license obligations; caches and linked outputs still have to be reviewed under their own terms.

## Native DLL and releases

The source includes only our original SDK adapter, with SDK includes supplied externally at build time. The MSVC plugin links `pluginsdk.lib`; whether/how that linked artifact can be redistributed needs separate review. This initial public repository ships **source only** and does not publish prebuilt SDK-linked DLLs. CI runs portable tests and pure-Python packaging rather than uploading native DLLs or dependencies.

The provisional RocketLaboratory name is not trademark clearance. This is an independent offline research toolkit, not an official product. No claim is made that reverse engineering, dependency downloads or MIT licensing resolve game EULA or other upstream restrictions.

## Release allowlist

Allowed public content: original source, documentation, schema, dependency URLs/hashes, generic example cases and synthetic tests. Excluded: native captures/manifests/settings, account identifiers, personal controller profiles, experiment videos, original game assets, SDK/loader binaries, dependency caches and host configuration. Run `python scripts/check-public-tree.py` on the staged tree before publishing. Default raw results remain private.
