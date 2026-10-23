param(
    [ValidateSet('baseline', 'v2', 'v3')][string]$Profile = 'v3',
    [switch]$OutputStream,
    [string]$ToolchainPath,
    [string]$CMakePath,
    [string]$NinjaPath,
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ToolchainPath) { $ToolchainPath = Join-Path $projectRoot '.tools\arm\arm-gnu-toolchain-13.2.Rel1-mingw-w64-i686-arm-none-eabi' }
$vsCmakeDirectory = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\Common7\IDE\CommonExtensions\Microsoft\CMake'
if (-not $CMakePath) { $CMakePath = Join-Path $vsCmakeDirectory 'CMake\bin\cmake.exe' }
if (-not $NinjaPath) { $NinjaPath = Join-Path $vsCmakeDirectory 'Ninja\ninja.exe' }
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$sdkPath = Join-Path $projectRoot 'third_party\pico-sdk'
foreach ($requiredPath in @($CMakePath, $NinjaPath, $PythonPath, (Join-Path $ToolchainPath 'bin\arm-none-eabi-gcc.exe'), (Join-Path $sdkPath 'lib\tinyusb\src\tusb.c'))) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Missing dependency: $requiredPath. See docs/LOCAL_DEVELOPMENT.md." }
}
if ($OutputStream -and $Profile -ne 'v3') { throw 'OutputStream requires the v3 profile' }
$buildSuffix = if ($OutputStream) { '-outputs' } else { '' }
$buildPath = Join-Path $projectRoot ('build-pico-' + $Profile + $buildSuffix)
$outputOption = if ($OutputStream) { 'ON' } else { 'OFF' }
$v2Option = if ($Profile -eq 'v2') { 'ON' } else { 'OFF' }
$v3Option = if ($Profile -eq 'v3') { 'ON' } else { 'OFF' }
$previousSdkPath = $env:PICO_SDK_PATH
try {
    $env:PICO_SDK_PATH = $sdkPath
    & $CMakePath -S (Join-Path $projectRoot 'firmware') -B $buildPath -G Ninja "-DCMAKE_MAKE_PROGRAM=$NinjaPath" "-DPICO_TOOLCHAIN_PATH=$ToolchainPath" -DPICO_NO_PICOTOOL=1 "-DMP_FIRMWARE_V2=$v2Option" "-DMP_FIRMWARE_V3=$v3Option" "-DMP_OUTPUT_STREAM=$outputOption"
    if ($LASTEXITCODE -ne 0) { throw 'CMake configuration failed' }
    & $CMakePath --build $buildPath
    if ($LASTEXITCODE -ne 0) { throw 'Firmware compilation failed' }
    $binaryPath = Join-Path $buildPath 'moza_ps5_diagnostic.bin'
    $uf2Path = Join-Path $buildPath ('moza_ps5_diagnostic_' + $Profile + $buildSuffix + '.uf2')
    & $PythonPath (Join-Path $projectRoot 'tools\bin_to_uf2.py') $binaryPath $uf2Path
    if ($LASTEXITCODE -ne 0) { throw 'UF2 conversion failed' }
    if ($Profile -eq 'v3') {
        $versionText = if ($OutputStream) { '0.3.1-outputs-experimental' } else { '0.3.0-experimental' }
        & $PythonPath (Join-Path $projectRoot 'tools\verify_v3_firmware.py') --bin $binaryPath --uf2 $uf2Path --version $versionText
        if ($LASTEXITCODE -ne 0) { throw 'V3 artifact verification failed' }
    }
    Write-Output "Built $Profile firmware: $uf2Path"
} finally {
    $env:PICO_SDK_PATH = $previousSdkPath
}
