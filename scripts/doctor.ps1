param([string]$Python, [switch]$Assets)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
Set-DictionForgeEnvironment
$project = Split-Path -Parent $PSScriptRoot
if (!$Python) { $Python = Join-Path $project '.venv-worker/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $Python)) { throw "Missing worker Python: $Python. Run setup.ps1 -Worker first." }
$output = Join-Path $project 'data/diagnostics/environment.json'
& $Python (Join-Path $PSScriptRoot 'probe.py') --output $output
$code = $LASTEXITCODE
Get-Content -LiteralPath $output
if ($Assets) {
    & (Join-Path $PSScriptRoot 'assets.ps1') -Action check
    if ($LASTEXITCODE -ne 0) { $code = $LASTEXITCODE }
}
exit $code
