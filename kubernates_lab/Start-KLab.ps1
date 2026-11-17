#requires -Version 5.1
# Starts the existing MacroPulse kind lab and locally scoped port forwards on Windows.
# No new cluster or production infrastructure is created.
$ErrorActionPreference = 'Stop'
$context = 'kind-macropulse-lab'
$node = 'macropulse-lab-control-plane'
$stateDir = Join-Path $env:LOCALAPPDATA 'MacroPulse-AI\k-lab'
$statePath = Join-Path $stateDir 'port-forwards.json'
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

function Require-Command([string]$name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if (-not $cmd) { throw "Missing '$name' in PATH." }
    return $cmd.Source
}
function Test-DockerReady {
    try {
        & docker info --format '{{.ServerVersion}}' 1>$null 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}
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
function Test-Listening([int]$port) {
    return [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1)
}

Write-Host '=== MacroPulse AI | Local Kubernetes lab ===' -ForegroundColor Cyan
$null = Require-Command 'docker'
$kubectl = Require-Command 'kubectl'
if (-not (Test-DockerReady)) {
    $desktopExe = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (-not (Test-Path $desktopExe)) { throw 'Docker Engine unavailable. Start Docker Desktop first.' }
    Start-Process -FilePath $desktopExe | Out-Null
    $deadline = (Get-Date).AddSeconds(120)
    while (-not (Test-DockerReady) -and (Get-Date) -lt $deadline) { Start-Sleep -Seconds 3 }
    if (-not (Test-DockerReady)) { throw 'Docker Engine did not become ready.' }
}
& docker container inspect $node 1>$null 2>$null
if ($LASTEXITCODE -ne 0) { throw "Existing kind node '$node' not found. This helper does not bootstrap a fresh cluster." }
$running = (& docker inspect -f '{{.State.Running}}' $node).Trim()
if ($running -ne 'true') {
    & docker start $node | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Failed to start existing kind node.' }
}
$deadline = (Get-Date).AddSeconds(90)
$ready = $false
while ((Get-Date) -lt $deadline) {
    & $kubectl --context $context get nodes --request-timeout=5s 1>$null 2>$null
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ready) { throw "Kubernetes context '$context' not responding." }
foreach ($target in @(
    @{ Namespace='k-lab'; Deployment='macropulse-api' },
    @{ Namespace='monitoring'; Deployment='grafana' },
    @{ Namespace='monitoring'; Deployment='prometheus-server' }
)) {
    & $kubectl --context $context -n $target.Namespace rollout status "deployment/$($target.Deployment)" --timeout=90s
    if ($LASTEXITCODE -ne 0) { Write-Warning "$($target.Namespace)/$($target.Deployment) is not ready." }
}
$tunnels = @(
    @{ Name='grafana'; Namespace='monitoring'; Service='grafana'; LocalPort=3000; RemotePort=80 },
    @{ Name='api'; Namespace='k-lab'; Service='macropulse-api'; LocalPort=8000; RemotePort=8000 },
    @{ Name='prometheus'; Namespace='monitoring'; Service='prometheus-server'; LocalPort=9090; RemotePort=80 }
)
$previous = @()
if (Test-Path $statePath) {
    try { $previous = @(Get-Content $statePath -Raw | ConvertFrom-Json) }
    catch { Write-Warning 'Could not read previous port-forward state.' }
}
$managed = @()
foreach ($item in $tunnels) {
    $old = $previous | Where-Object { $_.Name -eq $item.Name } | Select-Object -First 1
    if ($old -and (Test-ManagedTunnel $old)) {
        Write-Host "[OK] $($item.Name) already forwarded (PID $($old.ProcessId))." -ForegroundColor Green
        $managed += $old
        continue
    }
    if (Test-Listening $item.LocalPort) {
        Write-Warning "localhost:$($item.LocalPort) is occupied. Leaving the existing process untouched."
        continue
    }
    $argsList = @('--context', $context, '-n', $item.Namespace, 'port-forward',
        '--address', '127.0.0.1', "service/$($item.Service)", "$($item.LocalPort):$($item.RemotePort)")
    $out = Join-Path $stateDir "$($item.Name).out.log"
    $err = Join-Path $stateDir "$($item.Name).err.log"
    $process = Start-Process -FilePath $kubectl -ArgumentList $argsList -WindowStyle Hidden -PassThru -RedirectStandardOutput $out -RedirectStandardError $err
    Start-Sleep -Milliseconds 1300
    if ($process.HasExited -or -not (Test-Listening $item.LocalPort)) {
        Write-Warning "Port forward for $($item.Name) did not become available. Check $err"
        if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
        continue
    }
    $managed += [pscustomobject]@{
        Name=$item.Name; Namespace=$item.Namespace; Service=$item.Service
        LocalPort=$item.LocalPort; RemotePort=$item.RemotePort; ProcessId=$process.Id
    }
    Write-Host "[OK] $($item.Name): http://localhost:$($item.LocalPort)" -ForegroundColor Green
}
ConvertTo-Json -InputObject @($managed) -Depth 3 | Set-Content -Path $statePath -Encoding UTF8
Write-Host 'Grafana: http://localhost:3000 | API: http://localhost:8000/docs | Prometheus: http://localhost:9090' -ForegroundColor Cyan
Write-Host 'These managed forwards persist after this terminal closes. Stop with Stop-KLab.cmd.'
