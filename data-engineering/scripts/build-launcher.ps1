$ErrorActionPreference = 'Stop'
$dataEngineeringRoot = Split-Path -Parent $PSScriptRoot
$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) {
    $compilerPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe'
}
if (-not (Test-Path -LiteralPath $compilerPath)) {
    throw 'The Windows .NET Framework C# compiler is unavailable.'
}
$launcherSource = Join-Path $PSScriptRoot 'DashboardLauncher.cs'
$launcherOutput = Join-Path $dataEngineeringRoot 'Start Dashboard.exe'
& $compilerPath /nologo /target:winexe /optimize+ /reference:System.Windows.Forms.dll "/out:$launcherOutput" $launcherSource
if ($LASTEXITCODE -ne 0) { throw 'Building the launcher failed.' }
Write-Host "Built $launcherOutput"
