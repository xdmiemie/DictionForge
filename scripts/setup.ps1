param([switch]$Worker,[switch]$Recreate,[switch]$Offline,[switch]$Assets,[string]$Wheelhouse)
. (Join-Path $PSScriptRoot 'common.ps1')
Set-DictionForgeEnvironment
& (Join-Path $PSScriptRoot 'bootstrap.ps1') -Offline:$Offline
$python=Join-Path $ProjectRoot 'vendor\tools\python\python.exe'
$uv=Join-Path $ProjectRoot 'vendor\tools\uv\uv.exe'
$node=Join-Path $ProjectRoot 'vendor\tools\node\node.exe'
$pnpm=Join-Path $ProjectRoot 'vendor\tools\pnpm\bin\pnpm.cjs'
if($Recreate -and (Test-LocalPort 7867)){throw 'Stop the service before rebuilding environments. Existing environments preserved.'}
Push-Location $ProjectRoot
try {
    foreach($name in $(if($Worker){@('api','worker')}else{@('api')})) {
        $envRoot=Join-Path $ProjectRoot ".venv-$name"
        if($Recreate -and (Test-Path -LiteralPath $envRoot)) {
            $backup=$envRoot+'-backup-'+[DateTime]::UtcNow.ToString('yyyyMMddHHmmss')
            if(!([IO.Path]::GetFullPath($envRoot)).StartsWith($ProjectRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or !([IO.Path]::GetFullPath($backup)).StartsWith($ProjectRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Environment move escaped workspace'}
            Move-Item -LiteralPath $envRoot -Destination $backup
        }
        $interpreter=Join-Path $envRoot 'Scripts\python.exe'
        if(!(Test-Path -LiteralPath $interpreter)){Invoke-Checked $uv @('venv',$envRoot,'--python',$python)}
        $requirements=if($name -eq 'api'){'configs/api.requirements.txt'}else{'configs/worker.lock.txt'}
        $install=@('pip','sync','--python',$interpreter,$requirements,'--link-mode','hardlink')
        if($Offline){$install+='--offline'}
        if($Wheelhouse){$install+=@('--find-links',$Wheelhouse)}
        if($name -eq 'worker'){$install+=@('--torch-backend','cu128','-c','configs/worker.sources.txt')}
        Invoke-Checked $uv $install
        Invoke-Checked $uv @('pip','check','--python',$interpreter)
    }
    Push-Location (Join-Path $ProjectRoot 'frontend')
    try {
        $modules=Join-Path $ProjectRoot 'frontend\node_modules'
        if($Recreate -and (Test-Path -LiteralPath $modules)) {
            $archive=Join-Path $ProjectRoot ('data\environment-backups\node_modules-'+[DateTime]::UtcNow.ToString('yyyyMMddHHmmss'))
            New-Item -ItemType Directory -Force (Split-Path -Parent $archive) | Out-Null
            if(!([IO.Path]::GetFullPath($modules)).StartsWith($ProjectRoot+'\',[StringComparison]::OrdinalIgnoreCase) -or !([IO.Path]::GetFullPath($archive)).StartsWith($ProjectRoot+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Module backup escaped workspace'}
            Move-Item -LiteralPath $modules -Destination $archive
        }
        $install=@($pnpm,'install','--frozen-lockfile','--store-dir',(Join-Path $ProjectRoot 'data\pnpm-store'))
        if($Offline){$install+='--offline'}
        Invoke-Checked $node $install
        Invoke-Checked $node @($pnpm,'run','build')
    } finally {Pop-Location}
    $upstream=Join-Path $ProjectRoot 'vendor\gpt-sovits'
    $sourceLock=Get-Content configs/upstream.lock.json -Raw | ConvertFrom-Json
    if(!(Test-Path -LiteralPath $upstream)) {
        if($Offline){throw 'Upstream clone missing in offline mode.'}
        Invoke-Checked 'git' @('clone','--depth','1','--branch',$sourceLock.tag,$sourceLock.repository,$upstream)
    }
    Invoke-Checked $python @('scripts/apply_patches.py')
    if($Assets){& (Join-Path $PSScriptRoot 'assets.ps1') -Action prepare;if($LASTEXITCODE -ne 0){throw 'Asset preparation failed'}}
    Write-Output 'DictionForge setup completed. Run scripts/doctor.ps1 -Assets, then scripts/start.ps1.'
} finally {Pop-Location}
