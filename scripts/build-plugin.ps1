param([string]$SdkPath = "", [string]$BuildDirectory = "build/native")
$ErrorActionPreference = "Stop"
# Run in an x64 Native Tools command environment. The SDK remains external.
if (-not $SdkPath) {
    $result = python -m rocketlaboratory deps fetch bakkesmod-sdk
    if ($LASTEXITCODE -ne 0) { throw "SDK fetch failed" }
    $SdkPath = ($result | ConvertFrom-Json).path
}
cmake -S native -B $BuildDirectory -A x64 -DROCKETLAB_BUILD_PLUGIN=ON "-DBAKKESMOD_SDK=$SdkPath"
if ($LASTEXITCODE -ne 0) { throw "CMake configuration failed" }
cmake --build $BuildDirectory --config Release
if ($LASTEXITCODE -ne 0) { throw "Native build failed" }
ctest --test-dir $BuildDirectory -C Release --output-on-failure
if ($LASTEXITCODE -ne 0) { throw "Native portable tests failed" }
Write-Output "Local DLL: $BuildDirectory/Release/rocketlab_instrument.dll. SDK/loader redistribution terms remain separate."
