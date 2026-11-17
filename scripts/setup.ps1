param(
    [string]$Python = 'python',
    [switch]$Worker,
    [string]$Wheelhouse,
    [string]$Uv = 'uv'
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
function Checked([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "${Exe} failed: $LASTEXITCODE" }
}
Push-Location $project
try {
    New-Item -ItemType Directory -Force 'data/uv-cache','data/tmp' | Out-Null
    $env:UV_CACHE_DIR = Join-Path $project 'data/uv-cache'
    $env:TEMP = Join-Path $project 'data/tmp'
    $env:TMP = $env:TEMP
    if (!(Test-Path '.venv-api/Scripts/python.exe')) { Checked $Uv @('venv','.venv-api','--python',$Python) }
    Checked $Uv @('pip','install','--python','.venv-api/Scripts/python.exe','-r','configs/api.requirements.txt')
    Checked $Uv @('pip','install','--python','.venv-api/Scripts/python.exe','--no-deps','-e','.')
    Push-Location frontend
    try { Checked 'pnpm' @('install','--frozen-lockfile'); Checked 'pnpm' @('run','build') } finally { Pop-Location }
    if ($Worker) {
        if (!(Test-Path '.venv-worker/Scripts/python.exe')) { Checked $Uv @('venv','.venv-worker','--python',$Python) }
        $torchArgs = @('pip','install','--python','.venv-worker/Scripts/python.exe','torch==2.9.0+cu128','torchaudio==2.9.0+cu128')
        if ($Wheelhouse) { $torchArgs += @('--find-links',$Wheelhouse) }
        $torchArgs += @('--extra-index-url','https://download.pytorch.org/whl/cu128','--index-strategy','unsafe-best-match')
        Checked $Uv $torchArgs
        $requirements = if (Test-Path 'configs/worker.lock.txt') { 'configs/worker.lock.txt' } else { 'configs/worker.requirements.txt' }
        $workerArgs = @('pip','install','--python','.venv-worker/Scripts/python.exe','-r',$requirements)
        if ($Wheelhouse) { $workerArgs += @('--find-links',$Wheelhouse) }
        $workerArgs += @('--extra-index-url','https://download.pytorch.org/whl/cu128','--index-strategy','unsafe-best-match')
        Checked $Uv $workerArgs
    }
} finally { Pop-Location }
