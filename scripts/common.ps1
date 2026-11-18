# Shared process-local environment; never changes system PATH or another project.
$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
function Set-DictionForgeEnvironment {
    param([string]$DataRoot = (Join-Path $ProjectRoot 'data'), [int]$Port = 7867)
    $env:PYTHONUTF8 = '1'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:PYTHONPATH = Join-Path $ProjectRoot 'backend'
    $env:DICTIONFORGE_DATA = [IO.Path]::GetFullPath($DataRoot)
    $env:DICTIONFORGE_PORT = "$Port"
    $env:DICTIONFORGE_WORKER_PYTHON = Join-Path $ProjectRoot '.venv-worker\Scripts\python.exe'
    $env:DICTIONFORGE_UPSTREAM = Join-Path $ProjectRoot 'vendor\gpt-sovits'
    $paths = @('vendor\tools\ffmpeg\bin','vendor\tools\git\cmd','vendor\tools\node','vendor\tools\uv') | ForEach-Object { Join-Path $ProjectRoot $_ }
    $env:PATH = ($paths -join ';') + ';' + $env:PATH
    $env:UV_CACHE_DIR = Join-Path $ProjectRoot 'data\uv-cache'
    $env:TEMP = Join-Path $ProjectRoot 'data\tmp'
    $env:TMP = $env:TEMP
    New-Item -ItemType Directory -Force $env:TEMP,$env:DICTIONFORGE_DATA | Out-Null
}
function Assert-OwnedService($Health, [string]$DataRoot) {
    if ($Health.name -ne 'DictionForge' -or $Health.service -ne 'ready' -or
        $Health.workspace -ine $ProjectRoot -or $Health.data_root -ine ([IO.Path]::GetFullPath($DataRoot))) {
        throw 'Port belongs to another service or workspace. Nothing was stopped. Check the port and data root.'
    }
}
function Get-LocalHealth([string]$Url) {
    try { return Invoke-RestMethod ($Url + '/api/health') -TimeoutSec 3 } catch { return $null }
}
function Test-LocalPort([int]$Port) {
    $client = New-Object Net.Sockets.TcpClient
    try { $task=$client.ConnectAsync('127.0.0.1',$Port); if ($task.Wait(500)) {return $client.Connected}; return $false } catch {return $false} finally {$client.Dispose()}
}
function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "${Exe} failed: $LASTEXITCODE" }
}
