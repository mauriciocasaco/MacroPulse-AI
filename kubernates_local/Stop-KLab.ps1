#requires -Version 5.1
# Stops only port-forward processes recorded by Start-KLab.ps1, then stops the kind node.
$ErrorActionPreference = 'Stop'
$node = 'macropulse-lab-control-plane'
$statePath = Join-Path (Join-Path $env:LOCALAPPDATA 'MacroPulse-AI\k-lab') 'port-forwards.json'
function Test-ManagedTunnel($entry) {
    if (-not $entry) { return $false }
    $proc = Get-CimInstance Win32_Process -Filter "ProcessId = $([int]$entry.ProcessId)" -ErrorAction SilentlyContinue
    if (-not $proc -or -not $proc.CommandLine) { return $false }
    $cmdline = [string]$proc.CommandLine
    return ($cmdline -match 'kubectl(?:\.exe)?' -and
        $cmdline.Contains('port-forward') -and
        $cmdline.Contains("service/$($entry.Service)") -and
        $cmdline.Contains("$($entry.LocalPort):$($entry.RemotePort)"))
}
Write-Host '=== MacroPulse AI | Stop local Kubernetes lab ===' -ForegroundColor Cyan
if (Test-Path $statePath) {
    try { $entries = @(Get-Content $statePath -Raw | ConvertFrom-Json) }
    catch { $entries = @(); Write-Warning 'Port-forward state is not readable.' }
    foreach ($entry in $entries) {
        if (Test-ManagedTunnel $entry) {
            Stop-Process -Id ([int]$entry.ProcessId) -Force
            Write-Host "[OK] Closed managed tunnel $($entry.Name)." -ForegroundColor Green
        } else { Write-Host "[INFO] No matching managed process for $($entry.Name)." }
    }
    Remove-Item $statePath -Force
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker CLI not found.' }
& docker container inspect $node 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { Write-Host '[INFO] kind node not found.'; exit 0 }
$running = (& docker inspect -f '{{.State.Running}}' $node).Trim()
if ($running -eq 'true') {
    & docker stop $node | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Could not stop kind node.' }
    Write-Host '[OK] Existing kind node stopped.' -ForegroundColor Green
} else { Write-Host '[INFO] kind node is already stopped.' }
Write-Host 'Docker Desktop and unrelated containers were not stopped.'
