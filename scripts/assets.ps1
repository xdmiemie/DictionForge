param(
    [ValidateSet('check','download','prepare','import','map')][string]$Action = 'check',
    [string]$Id,
    [string]$Source,
    [string]$Group,
    [string]$Report
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv-api\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
$arguments = @((Join-Path $PSScriptRoot 'assets.py'), $Action)
if ($Id) { $arguments += @('--id', $Id) }
if ($Source) { $arguments += @('--source', $Source) }
if ($Group) { $arguments += @('--group', $Group) }
if ($Report) { $arguments += @('--report', $Report) }
& $python @arguments
exit $LASTEXITCODE

