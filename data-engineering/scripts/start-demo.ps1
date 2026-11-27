$ErrorActionPreference = 'Stop'
$dataEngineeringRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $dataEngineeringRoot
try {
    $environmentPython = Join-Path $dataEngineeringRoot '.venv\Scripts\python.exe'
    if (Test-Path -LiteralPath $environmentPython) {
        & $environmentPython -m swarmlink_data serve --demo
    } else {
        python -m swarmlink_data serve --demo
    }
} finally {
    Pop-Location
}
