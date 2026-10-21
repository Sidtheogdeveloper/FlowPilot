param([ValidateSet('Auto','Trained','Stronger')][string]$Variant = 'Auto')
$ErrorActionPreference = 'Stop'
$applicationRoot = Split-Path $PSScriptRoot -Parent
$workspaceRoot = Split-Path $applicationRoot -Parent
$adapterFolder = Join-Path $applicationRoot 'training/artifacts/table-lora'
$comparisonFile = Join-Path $applicationRoot 'training/artifacts/model-comparison/comparison-4b.json'
$comparison = if (Test-Path -LiteralPath $comparisonFile) { Get-Content -LiteralPath $comparisonFile -Raw | ConvertFrom-Json } else { $null }
if ($Variant -eq 'Auto') { $Variant = 'Stronger' }
if ($Variant -eq 'Stronger') {
    if (-not $comparison -or -not $comparison.recommend_stronger) { throw 'Verify the 4B runtime comparison first, or explicitly choose -Variant Trained.' }
    $manifest = Get-Content -LiteralPath (Join-Path $applicationRoot 'evidence/stronger_4b_download.json') -Raw | ConvertFrom-Json
    $gguf = Join-Path $workspaceRoot '.local_services/gguf/Qwen3-4B-Instruct-2507-Q4_K_M.gguf'
    if (-not (Test-Path -LiteralPath $gguf)) { throw 'The verified 4B GGUF download is missing.' }
    $composeArgs = @('compose','--env-file','.runtime/docker.verify.env','-p','workflow-phase2-verify','-f','compose.yaml','-f','compose.verify.yaml','-f','compose.tables.yaml','-f','compose.stronger.yaml')
    Push-Location $applicationRoot
    try {
        & docker @composeArgs up -d table_ollama
        if ($LASTEXITCODE -ne 0) { throw 'Could not start the local 4B service. Check that Docker is running.' }
        & docker @composeArgs exec -T table_ollama ollama show flowpilot-tables *> $null
        if ($LASTEXITCODE -ne 0) {
            & docker @composeArgs exec -T table_ollama ollama create flowpilot-tables -f /models/Modelfile
            if ($LASTEXITCODE -ne 0) { throw 'Could not import the downloaded 4B model into Ollama.' }
        }
    } finally { Pop-Location }
    Write-Output 'Verified Qwen3-4B-Instruct-2507 service available at http://127.0.0.1:8003. This model is not newly fine-tuned; the trained 0.5B adapter remains a separate option.'
    return
}
if ($Variant -eq 'Trained') {
    $evaluationFile = Join-Path $adapterFolder 'evaluation.json'
    if (-not (Test-Path -LiteralPath $evaluationFile)) { throw 'Train and evaluate the table adapter before starting this service.' }
    $evaluation = Get-Content -LiteralPath $evaluationFile -Raw | ConvertFrom-Json
    if (-not $evaluation.activation_recommended) { throw 'The table adapter has not passed its semantic correctness activation gate.' }
    $env:HF_ADAPTER_PATH = $adapterFolder
    $env:HF_MODEL = 'Qwen/Qwen2.5-0.5B-Instruct'
    $env:HF_MODEL_REVISION = '7ae557604adf67be50417f59c2c2f167def9a775'
    $env:LLM_INT8 = '0'
}
if (Get-NetTCPConnection -LocalPort 8002 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8002 is already in use. Inspect that service before starting another model.' }
$pythonPath = Join-Path $workspaceRoot '.venv/Scripts/python.exe'
$env:MODEL_CACHE_ROOT = Join-Path $workspaceRoot '.local_services'
$env:HF_HOME = Join-Path $workspaceRoot '.local_services/hf_cache'
$env:HF_HUB_OFFLINE = '1'
$env:LLM_HOST = '127.0.0.1'
$env:LLM_PORT = '8002'
$env:LLM_THREADS = '8'
$logFolder = Join-Path $applicationRoot '.runtime'
New-Item -ItemType Directory -Path $logFolder -Force | Out-Null
$modelProcess = Start-Process -FilePath $pythonPath -ArgumentList @('-m','app.llm_server') -WorkingDirectory $applicationRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logFolder 'table-model.out.log') -RedirectStandardError (Join-Path $logFolder 'table-model.err.log') -PassThru
$record = @{pid=$modelProcess.Id; model=$env:HF_MODEL; variant=$Variant; started=$modelProcess.StartTime.ToUniversalTime().ToString('o')}
$record | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $logFolder 'table-model-process.json')
Write-Output ('Table model starting on http://127.0.0.1:8002; model ' + $env:HF_MODEL + '; PID ' + $modelProcess.Id)
