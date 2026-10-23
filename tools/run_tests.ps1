param(
    [string]$CompilerSetup = 'C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat',
    [string]$PythonPath
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $PythonPath) { $PythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe' }
foreach ($requiredPath in @($CompilerSetup, $PythonPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) { throw "Missing dependency: $requiredPath" }
}
Push-Location $projectRoot
try {
    foreach ($profile in @('baseline','v2','v3','v3-outputs')) {
        $outputDirectory = 'build-host\' + $profile
        New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
        $definition = if ($profile -eq 'baseline') { '' } elseif ($profile -eq 'v3-outputs') { '/DMP_FIRMWARE_V3=1 /DMP_OUTPUT_STREAM=1' } else { '/DMP_FIRMWARE_' + $profile.ToUpper() + '=1' }
        foreach ($test in @('core_test','usb_reports_test','output_stream_test')) {
            $binaryPath = $outputDirectory + '\' + $test + '.exe'
            $compilerCommand = '"' + $CompilerSetup + '" >nul && cl /nologo /std:c11 /W4 /WX ' + $definition + ' /I firmware\core firmware\core\protocol.c firmware\core\auth.c firmware\core\usb_reports.c firmware\core\diagnostics.c firmware\core\output_stream.c firmware\tests\' + $test + '.c /Fe:' + $binaryPath + ' /Fo:' + $outputDirectory + '\ && ' + $binaryPath
            & cmd.exe /d /c $compilerCommand
            if ($LASTEXITCODE -ne 0) { throw "Native test failed: $profile $test" }
        }
    }
    & $PythonPath -m pytest -q host/tests tools/tests
    if ($LASTEXITCODE -ne 0) { throw 'Python tests failed' }
} finally {
    Pop-Location
}
