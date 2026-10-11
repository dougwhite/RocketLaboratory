# Installation and build requirements

## Toolkit only: Windows and Linux

- Python 3.12+ and pip, or uv. No Node.js, Godot, Blender, NumPy or OpenCV required.
- Git is needed for a source checkout; SSH setup is a GitHub preference, not a runtime dependency.
- `python -m pip install .` builds the small pure-Python package. Setuptools is the build dependency; pip may download it into its isolated build environment.
- `rocketlab serve` binds loopback only. The browser UI has no CDN/network assets.
- Default results: `$XDG_DATA_HOME/RocketLaboratory` or `~/.local/share/RocketLaboratory` on Linux; `%LOCALAPPDATA%/RocketLaboratory` on Windows. Results are private by default and excluded from repository releases. `--workspace` chooses another location.

## Native plugin compilation: Windows x64

Required: Visual Studio 2022/compatible MSVC C++ tools, an x64 Windows SDK, CMake 3.20+, and the BakkesMod SDK. The prior instrument was developed against API 95; the pinned SDK revision is recorded in the dependency lock. SDK/compiler compatibility must be rechecked when versions change.

Use an x64 Developer PowerShell:

```powershell
python -m pip install .
./scripts/build-plugin.ps1
# Or use an existing external SDK:
./scripts/build-plugin.ps1 -SdkPath C:/external/BakkesModSDK
```

The script automatically downloads the pinned SDK **outside the repository**, configures the x64 MSVC build, builds the plugin and runs portable C++ checks. The DLL stays local. It is not a prebuilt artifact supplied by this repository. An existing SDK is accepted through CMake `-DBAKKESMOD_SDK=...`; its provenance/version is the user's responsibility.

Manual commands:

```powershell
cmake -S native -B build/native -A x64 -DROCKETLAB_BUILD_PLUGIN=ON -DBAKKESMOD_SDK=C:/external/BakkesModSDK
cmake --build build/native --config Release
ctest --test-dir build/native -C Release --output-on-failure
```

MinGW is intentionally rejected for the plugin because the SDK library uses the MSVC x64 ABI. On Linux, CMake defaults to the portable instrument checks and does not download/use the SDK.

## Native use: Linux / Proton

Rocket League remains a Windows game. You provide a legitimate local installation, Steam/another supported launcher, a compatible Proton/Wine prefix, and an official offline-compatible BakkesMod installation. Proton installation belongs to Steam/the user; RocketLaboratory does not replace system Proton or patch the game. Microsoft build tools are needed only on the machine compiling the plugin, not on the Linux capture host.

The file bridge directory must refer to **the same prefix/data folder** the loaded plugin uses. Under a Steam prefix, `%APPDATA%` commonly maps to `drive_c/users/steamuser/AppData/Roaming`; verify the actual installation rather than assuming its location. The legacy `noodle-instrument` folder name is a wire-compatibility detail, not a NinjaBladeNoodle dependency.

BakkesMod Linux/Proton support and loader compatibility can change. This release provides a file-bridge attachment path, not a certified universal Proton installer. Follow official compatibility guidance, honor refusal prompts, and only use an offline local session. Do not modify anti-cheat to force an unsupported installation.

## RLBot

`rocketlab deps fetch rlbot-python-source` retrieves the pinned MIT Python interface source for future integration work. This does not install the RLBot server, launch Rocket League, implement the RLBot protocol, or bundle its closed-source Bridge.dll. For an RLBot runtime, use official installation instructions and retain upstream terms. v0.1 uses the SDK file bridge as its native execution backend.

## Storage and resources

- One execution worker per toolkit service; an OS lock serializes native access across services targeting the same bridge folder.
- At most 7200 candidate steps and 16 cars per public experiment, 115200 dense actor-steps overall.
- Native instrument: 128 MiB capture cap, 8 MiB writer queue, 120 s watchdog. Host: 110 s execution limit after tape arming; preparation adds a bounded prefix.
- Result workspace: 1 GiB admission budget, at least 256 MiB free disk headroom. Existing evidence is never pruned automatically.
- No video capture, game spawning, heavy renderer or Blender dependency. Failed native captures are retained privately for diagnosis, with the plugin's own per-capture cap. The bridge's SDK operation audit log is also private. New runs are refused above 768 MiB retained bridge captures or 32 MiB audit history; archive selected evidence before proceeding.
- Cancellation and UI/server shutdown attempt stop/neutralization. If acknowledgements fail, the instrument's independent watchdog is the final fallback; inspect stop-error metadata before another run.
