$ErrorActionPreference = 'Stop'
$dataEngineeringRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $dataEngineeringRoot
try {
    if (-not (Test-Path -LiteralPath '.runtime/robot-keys.json')) {
        throw 'Generate .runtime/robot-keys.json with the keys command first.'
    }
    $environmentPython = Join-Path $dataEngineeringRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $environmentPython) {
        & $environmentPython -m swarmlink_data serve --host 0.0.0.0 --keys .runtime/robot-keys.json --robots 1 --data .runtime/live
    } else {
        python -m swarmlink_data serve --host 0.0.0.0 --keys .runtime/robot-keys.json --robots 1 --data .runtime/live
    }
} finally {
    Pop-Location
}
