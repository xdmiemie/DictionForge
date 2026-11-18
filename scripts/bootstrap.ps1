param([switch]$Offline)
. (Join-Path $PSScriptRoot 'common.ps1')
$lock=Get-Content -LiteralPath (Join-Path $ProjectRoot 'configs/tools.lock.json') -Raw | ConvertFrom-Json
$cache=Join-Path $ProjectRoot 'data\downloads';$base=Join-Path $ProjectRoot 'vendor\tools'
New-Item -ItemType Directory -Force $cache,$base | Out-Null
foreach($tool in $lock.tools) {
    $target=Join-Path $base $tool.id
    $marker=Join-Path $target 'dictionforge-tool.json'
    if(Test-Path -LiteralPath $marker) {
        $installed=Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        if($installed.archive_sha256 -ne $tool.sha256){throw "Tool $($tool.id) differs from lock; existing files preserved."}
        foreach($file in $installed.files) {
            if(!(Test-Path -LiteralPath (Join-Path $target $file.path)) -or (Get-FileHash -LiteralPath (Join-Path $target $file.path) -Algorithm SHA256).Hash -ine $file.sha256){throw "Tool file changed: $($tool.id)/$($file.path)"}
        }
        Write-Output "Verified $($tool.id) $($tool.version)";continue
    }
    if(Test-Path -LiteralPath $target){throw "Unrecognized tool directory preserved: $target"}
    $archive=Join-Path $cache $tool.filename
    if(!(Test-Path -LiteralPath $archive) -or (Get-Item -LiteralPath $archive).Length -ne $tool.size -or (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ine $tool.sha256) {
        if($Offline){throw "Missing valid offline archive: $archive"}
        $pending="$archive.part"
        Invoke-Checked (Join-Path $env:SystemRoot 'System32\curl.exe') @('--fail','--location','--retry','8','--retry-all-errors','--retry-delay','2','--connect-timeout','30','--max-time','300','--continue-at','-','--output',$pending,$tool.url)
        if((Get-Item -LiteralPath $pending).Length -ne $tool.size -or (Get-FileHash -LiteralPath $pending -Algorithm SHA256).Hash -ine $tool.sha256){throw "Download verification failed: $pending. Existing installation unchanged."}
        Move-Item -LiteralPath $pending -Destination $archive -Force
    }
    $staging=Join-Path $base ('.extract-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory $staging | Out-Null
    Invoke-Checked (Join-Path $env:SystemRoot 'System32\tar.exe') @('-xf',$archive,'-C',$staging)
    $source=if($tool.prefix){Join-Path $staging $tool.prefix}else{$staging}
    $resolved=[IO.Path]::GetFullPath($source);$destination=[IO.Path]::GetFullPath($target)
    if(!$resolved.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase) -or !$destination.StartsWith($base+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Tool path escaped workspace'}
    Move-Item -LiteralPath $resolved -Destination $destination
    $files=@(Get-ChildItem -LiteralPath $destination -File -Recurse | ForEach-Object {@{path=$_.FullName.Substring($destination.Length+1);sha256=(Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()}})
    @{id=$tool.id;version=$tool.version;archive_sha256=$tool.sha256;files=$files} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $marker -Encoding UTF8
    Write-Output "Installed $($tool.id) $($tool.version)"
}
