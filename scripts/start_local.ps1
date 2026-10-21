[CmdletBinding()]
param([switch]$WithModel,[switch]$SkipFrontend,[string]$PythonPath='', [ValidateRange(1024,65535)][int]$ApiPort=8000)
$ErrorActionPreference='Stop'
$applicationRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$parentRoot=[IO.Path]::GetFullPath((Join-Path $applicationRoot '..'))
$runtimeRoot=Join-Path $applicationRoot '.runtime'
New-Item -ItemType Directory -Force -Path $runtimeRoot | Out-Null
$pidsFile=Join-Path $runtimeRoot 'processes.json'
$oldEnvironment=@{}
$variableNames=@('DATABASE_URL','ARTIFACT_ROOT','HF_INFERENCE_URL','HF_MODEL','HF_HOME','HF_ADAPTER_PATH','WORKER_ID','WORKER_CPU','WORKER_MEMORY_MB','PYTHONPATH')
foreach($name in $variableNames){$oldEnvironment[$name]=[Environment]::GetEnvironmentVariable($name,'Process')}
$launched=@()
function Start-ApplicationProcess([string]$name,[string]$executable,[string[]]$arguments,[string]$workingDirectory){
    $outputPath=Join-Path $runtimeRoot ($name+'.out.log')
    $errorPath=Join-Path $runtimeRoot ($name+'.err.log')
    $process=Start-Process -FilePath $executable -ArgumentList $arguments -WorkingDirectory $workingDirectory -WindowStyle Hidden -RedirectStandardOutput $outputPath -RedirectStandardError $errorPath -PassThru
    return @{name=$name;pid=$process.Id;started=$process.StartTime.ToUniversalTime().ToString('o');started_ticks=$process.StartTime.ToUniversalTime().Ticks;executable=$executable}
}
try{
    if(Test-Path -LiteralPath $pidsFile){
        $retained=Get-Content -LiteralPath $pidsFile -Raw | ConvertFrom-Json
        foreach($entry in $retained){
            $live=Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
            if($live -and $live.StartTime.ToUniversalTime().Ticks -eq [long]$entry.started_ticks){throw 'Application processes already run. Use scripts/stop_local.ps1 first.'}
        }
    }
    $configCandidates=@((Join-Path $applicationRoot '.env'),(Join-Path $parentRoot '.local_services/runtime.env'))
    foreach($config in $configCandidates){
        if(Test-Path -LiteralPath $config){
            foreach($line in Get-Content -LiteralPath $config){
                if($line -match '^\s*(DATABASE_URL|HF_INFERENCE_URL|HF_MODEL|HF_HOME)\s*=(.*)$'){
                    $key=$matches[1];$value=$matches[2].Trim().Trim('"').Trim("'")
                    if(-not [Environment]::GetEnvironmentVariable($key,'Process')){[Environment]::SetEnvironmentVariable($key,$value,'Process')}
                }
            }
        }
    }
    if(-not $env:DATABASE_URL){throw 'Set DATABASE_URL to your PostgreSQL database, or add it to application .env. Do not use workflow_test for retained demo data.'}
    if(-not $PythonPath){
        $options=@((Join-Path $applicationRoot '.venv/Scripts/python.exe'),(Join-Path $parentRoot '.venv/Scripts/python.exe'))
        $PythonPath=$options | Where-Object {Test-Path -LiteralPath $_} | Select-Object -First 1
        if(-not $PythonPath){$PythonPath=(Get-Command python.exe -ErrorAction Stop).Source}
    }
    & $PythonPath -c 'import fastapi,sqlalchemy,psycopg,pandas,networkx' *> $null
    if($LASTEXITCODE -ne 0){throw 'Python dependencies missing. Install requirements.txt in the selected virtual environment.'}
    $env:PYTHONPATH=$applicationRoot
    $env:ARTIFACT_ROOT=Join-Path $applicationRoot 'data/artifacts'
    if(-not $env:HF_MODEL){$env:HF_MODEL='Qwen/Qwen2.5-0.5B-Instruct'}
    $trainedAdapter=Join-Path $applicationRoot 'training/artifacts/intent-lora'
    if(-not $env:HF_ADAPTER_PATH -and (Test-Path -LiteralPath (Join-Path $trainedAdapter 'evaluation.json'))){$env:HF_ADAPTER_PATH=$trainedAdapter}
    $env:HF_INFERENCE_URL='http://127.0.0.1:8001/v1/chat/completions'
    $cached=Join-Path $parentRoot '.local_services/hf_cache'
    if(-not $env:HF_HOME -and (Test-Path -LiteralPath $cached)){$env:HF_HOME=$cached}
    if($WithModel){
        $existing=Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue
        if($existing){Write-Host 'Model port8001 is already listening; reusing the existing local service.'}
        else{$launched+=Start-ApplicationProcess 'model' $PythonPath @('-m','app.llm_server') $applicationRoot}
    }
    if($ApiPort -ne 8000 -and -not $SkipFrontend){throw 'Alternative API ports require -SkipFrontend; the supplied Vite proxy targets8000.'}
    if(Get-NetTCPConnection -LocalPort $ApiPort -State Listen -ErrorAction SilentlyContinue){throw ('API port '+$ApiPort+' is already in use; stop the existing API first.')}
    $launched+=Start-ApplicationProcess 'api' $PythonPath @('-m','uvicorn','app.api:app','--host','127.0.0.1','--port',([string]$ApiPort)) $applicationRoot
    $ready=$false
    for($i=0;$i -lt 40;$i++){
        try{$response=Invoke-RestMethod -Uri ('http://127.0.0.1:'+$ApiPort+'/health') -TimeoutSec 2;if($response.status -eq 'ok'){$ready=$true;break}}catch{}
        Start-Sleep -Milliseconds 500
    }
    if(-not $ready){throw 'API did not start; inspect .runtime/api.err.log (no database credentials are printed by this launcher).'}
    foreach($worker in @(@{id='local-worker-a';cpu='2';memory='512'},@{id='local-worker-b';cpu='1';memory='256'})){
        $env:WORKER_ID=$worker.id;$env:WORKER_CPU=$worker.cpu;$env:WORKER_MEMORY_MB=$worker.memory
        $launched+=Start-ApplicationProcess $worker.id $PythonPath @('-m','app.worker') $applicationRoot
    }
    if(-not $SkipFrontend){
        $vite=Join-Path $applicationRoot 'frontend/node_modules/vite/bin/vite.js'
        if(-not (Test-Path -LiteralPath $vite)){throw 'Frontend dependencies missing. Run npm ci in frontend first.'}
        if(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue){Write-Host 'Frontend port5173 is already listening; reusing existing frontend.'}
        else{
            $node=(Get-Command node.exe -ErrorAction Stop).Source
            $launched+=Start-ApplicationProcess 'frontend' $node @(('"'+$vite+'"'),'--host','127.0.0.1','--port','5173','--strictPort') (Join-Path $applicationRoot 'frontend')
        }
    }
    $launched | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidsFile
    Write-Host ('API ready: http://localhost:'+$ApiPort+'/docs')
    if(-not $SkipFrontend){Write-Host 'Application: http://localhost:5173'}
    Write-Host ('Sample CSV: '+(Join-Path $applicationRoot 'samples/retail_transactions.csv'))
    Write-Host 'Logs and owned PIDs: application .runtime folder. Stop using scripts/stop_local.ps1.'
}catch{
    if($launched.Count -gt 0){
        $launched | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidsFile
        & (Join-Path $PSScriptRoot 'stop_local.ps1')
    }
    throw
}finally{
    foreach($name in $variableNames){[Environment]::SetEnvironmentVariable($name,$oldEnvironment[$name],'Process')}
}

