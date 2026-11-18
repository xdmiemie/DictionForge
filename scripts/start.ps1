param([ValidateRange(1024,65535)][int]$Port=7867,[string]$DataRoot,[switch]$NoBrowser)
. (Join-Path $PSScriptRoot 'common.ps1')
if (!$DataRoot) {$DataRoot=Join-Path $ProjectRoot 'data'}
$DataRoot=[IO.Path]::GetFullPath($DataRoot)
Set-DictionForgeEnvironment -DataRoot $DataRoot -Port $Port
$url="http://127.0.0.1:$Port"
$hash=[Security.Cryptography.SHA256]::Create()
try {$key=([BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes("$ProjectRoot|$DataRoot|$Port".ToLowerInvariant())))).Replace('-','')} finally {$hash.Dispose()}
$mutex=New-Object Threading.Mutex($false,"Local\DictionForge-start-$key")
$locked=$false
try {
    try {$locked=$mutex.WaitOne(30000)} catch [Threading.AbandonedMutexException] {$locked=$true}
    if (!$locked) {throw 'Another start is still in progress. Retry shortly.'}
    $health=Get-LocalHealth $url
    $reused=$null -ne $health
    if ($health) {Assert-OwnedService $health $DataRoot}
    else {
        if (Test-LocalPort $Port) {throw "Port $Port is already occupied. Nothing was changed."}
        $python=Join-Path $ProjectRoot '.venv-api\Scripts\python.exe'
        foreach($required in @($python,(Join-Path $ProjectRoot '.venv-worker\Scripts\python.exe'),(Join-Path $ProjectRoot 'frontend\dist\index.html'))) {
            if (!(Test-Path -LiteralPath $required)) {throw "Missing $required. Run scripts/setup.ps1 -Worker first."}
        }
        $logs=Join-Path $DataRoot 'logs';New-Item -ItemType Directory -Force $logs | Out-Null
        $stamp=[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')
        $receipt=& $python (Join-Path $PSScriptRoot 'launch_server.py') --stdout (Join-Path $logs "server-$stamp.out.log") --stderr (Join-Path $logs "server-$stamp.err.log")
        if($LASTEXITCODE -ne 0){throw 'Could not launch the service. Inspect server logs.'}
        $process=Get-Process -Id (($receipt | ConvertFrom-Json).pid)
        for($i=0;$i -lt 120;$i++) {
            Start-Sleep -Milliseconds 500
            $health=Get-LocalHealth $url
            if($health) {Assert-OwnedService $health $DataRoot;break}
            if($process.HasExited) {throw "Service exited. Inspect $logs/server-$stamp.err.log"}
        }
        if(!$health) {throw "Service did not become ready. Inspect $logs; no process was forcibly killed."}
    }
    if(!$NoBrowser) {Start-Process $url}
    @{url=$url;pid=$health.pid;reused=$reused;workspace=$ProjectRoot;data_root=$DataRoot;browser_opened=(!$NoBrowser)} | ConvertTo-Json -Compress
} finally {if($locked){$mutex.ReleaseMutex()};$mutex.Dispose()}
