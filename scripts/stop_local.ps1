[CmdletBinding()]
param()
$ErrorActionPreference='Stop'
$applicationRoot=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pidsFile=Join-Path $applicationRoot '.runtime/processes.json'
if(-not (Test-Path -LiteralPath $pidsFile)){Write-Host 'No launcher-owned process record exists.';exit 0}
function Stop-OwnedTree([int]$processId,[long]$startTicks){
    $process=Get-Process -Id $processId -ErrorAction SilentlyContinue
    if(-not $process -or $process.StartTime.ToUniversalTime().Ticks -ne $startTicks){return}
    # Python virtual-environment launchers can spawn a child interpreter on Windows.
    # Inspect only children of the identity-verified process, and stop descendants first.
    $children=Get-CimInstance Win32_Process -Filter ('ParentProcessId='+$processId) -ErrorAction SilentlyContinue
    foreach($child in $children){
        $childProcess=Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
        if($childProcess -and $childProcess.StartTime.ToUniversalTime().Ticks -ge $startTicks){Stop-OwnedTree $child.ProcessId $childProcess.StartTime.ToUniversalTime().Ticks}
    }
    $process=Get-Process -Id $processId -ErrorAction SilentlyContinue
    if($process -and $process.StartTime.ToUniversalTime().Ticks -eq $startTicks){Stop-Process -Id $processId -ErrorAction SilentlyContinue}
}
$entries=Get-Content -LiteralPath $pidsFile -Raw | ConvertFrom-Json
foreach($entry in $entries){
    $process=Get-Process -Id $entry.pid -ErrorAction SilentlyContinue
    if($process -and $process.StartTime.ToUniversalTime().Ticks -eq [long]$entry.started_ticks){Stop-OwnedTree $entry.pid ([long]$entry.started_ticks);Write-Host ('Stopped '+$entry.name)}
}
Remove-Item -LiteralPath $pidsFile
Write-Host 'PostgreSQL and any reused external model/frontend service were left running.'
