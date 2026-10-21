[CmdletBinding()]
param([switch]$RestartWorkers)
$ErrorActionPreference='Stop'
$applicationRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$parentRoot=[IO.Path]::GetFullPath((Join-Path $applicationRoot '..'))
$runtimeRoot=Join-Path $applicationRoot '.runtime'
$pidsFile=Join-Path $runtimeRoot 'processes.json'
$parsedEntries=Get-Content -LiteralPath $pidsFile -Raw | ConvertFrom-Json
$entries=@($parsedEntries)
$entry=$entries | Where-Object name -eq 'api' | Select-Object -First 1
if(-not $entry){throw 'No launcher-owned API record found.'}
$oldEnvironment=@{}
foreach($name in @('DATABASE_URL','ARTIFACT_ROOT','HF_INFERENCE_URL','HF_MODEL','PYTHONPATH','WORKER_ID','WORKER_CPU','WORKER_MEMORY_MB')){$oldEnvironment[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
function Stop-OwnedApiTree([int]$processId,[long]$startTicks){
    $live=Get-Process -Id $processId -ErrorAction SilentlyContinue
    if(-not $live -or $live.StartTime.ToUniversalTime().Ticks -ne $startTicks){return}
    foreach($child in Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$processId)){
        $childProcess=Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if($childProcess -and $childProcess.StartTime.ToUniversalTime().Ticks -ge $startTicks){Stop-OwnedApiTree $childProcess.Id $childProcess.StartTime.ToUniversalTime().Ticks}
    }
    Stop-Process -Id $processId -ErrorAction SilentlyContinue
}
try{
    foreach($config in @((Join-Path $applicationRoot '.env'),(Join-Path $parentRoot '.local_services/runtime.env'))){
        if(Test-Path -LiteralPath $config){
            foreach($line in Get-Content -LiteralPath $config){
                if($line -match '^\s*(DATABASE_URL|HF_MODEL)\s*=(.*)$'){
                    if(-not [Environment]::GetEnvironmentVariable($matches[1],'Process')){[Environment]::SetEnvironmentVariable($matches[1],$matches[2].Trim().Trim('"').Trim("'"),'Process')}
                }
            }
        }
    }
    if(-not $env:DATABASE_URL){throw 'Missing local database configuration.'}
    $env:PYTHONPATH=$applicationRoot
    $env:ARTIFACT_ROOT=Join-Path $applicationRoot 'data/artifacts'
    $env:HF_INFERENCE_URL='http://127.0.0.1:8001/v1/chat/completions'
    Stop-OwnedApiTree ([int]$entry.pid) ([long]$entry.started_ticks)
    $process=Start-Process -FilePath $entry.executable -ArgumentList @('-m','uvicorn','app.api:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $applicationRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeRoot 'api.out.log') -RedirectStandardError (Join-Path $runtimeRoot 'api.err.log') -PassThru
    $replacement=@{name='api';pid=$process.Id;started_ticks=$process.StartTime.ToUniversalTime().Ticks;started=$process.StartTime.ToUniversalTime().ToString('o');executable=$entry.executable}
    $retainedEntries=@($entries | Where-Object name -ne 'api');$replacements=@($replacement)
    if($RestartWorkers){
        foreach($workerEntry in @($entries | Where-Object { $_.name -in @('local-worker-a','local-worker-b') })){
            Stop-OwnedApiTree ([int]$workerEntry.pid) ([long]$workerEntry.started_ticks)
            $env:WORKER_ID=$workerEntry.name;$env:WORKER_CPU=if($workerEntry.name -eq 'local-worker-a'){'2'}else{'1'};$env:WORKER_MEMORY_MB=if($workerEntry.name -eq 'local-worker-a'){'512'}else{'256'}
            $workerProcess=Start-Process -FilePath $workerEntry.executable -ArgumentList @('-m','app.worker') -WorkingDirectory $applicationRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $runtimeRoot ($workerEntry.name+'.out.log')) -RedirectStandardError (Join-Path $runtimeRoot ($workerEntry.name+'.err.log')) -PassThru
            $replacements+=@{name=$workerEntry.name;pid=$workerProcess.Id;started_ticks=$workerProcess.StartTime.ToUniversalTime().Ticks;started=$workerProcess.StartTime.ToUniversalTime().ToString('o');executable=$workerEntry.executable}
        }
        $retainedEntries=@($retainedEntries | Where-Object {$_.name -notin @('local-worker-a','local-worker-b')})
    }
    @($retainedEntries) + @($replacements) | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidsFile
    for($i=0;$i -lt 40;$i++){
        try{Invoke-RestMethod -Uri 'http://127.0.0.1:8000/datasets' -TimeoutSec 2 | Out-Null;Write-Host 'Local API restarted with persistent history endpoints.';return}catch{Start-Sleep -Milliseconds 500}
    }
    throw 'API readiness failed; inspect .runtime/api.err.log.'
}finally{
    foreach($name in $oldEnvironment.Keys){[Environment]::SetEnvironmentVariable($name,$oldEnvironment[$name],'Process')}
}
