param([ValidateRange(1024,65535)][int]$Port=7867,[string]$DataRoot)
. (Join-Path $PSScriptRoot 'common.ps1')
if(!$DataRoot){$DataRoot=Join-Path $ProjectRoot 'data'}
$url="http://127.0.0.1:$Port";$health=Get-LocalHealth $url
if(!$health) {
    if(Test-LocalPort $Port){throw 'Port is occupied by an unrecognized service. Nothing was stopped.'}
    Write-Output '{"stopped":true,"already_stopped":true}';return
}
Assert-OwnedService $health $DataRoot
# Active/queued jobs are rejected atomically by the application. Never force-kill.
$reply=Invoke-RestMethod -Method Post ($url+'/api/system/stop') -TimeoutSec 10
for($i=0;$i -lt 90;$i++){if(!(Test-LocalPort $Port)){@{stopped=$true;pid=$reply.pid}|ConvertTo-Json -Compress;return};Start-Sleep -Milliseconds 500}
throw 'Graceful stop is still pending. Check server logs; no process was forcibly killed.'
