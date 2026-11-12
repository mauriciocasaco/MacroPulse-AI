#requires -Version 5.1
[CmdletBinding()]
param([ValidateSet('Start', 'Stop', 'Install', 'Firewall', 'ApplyFirewall', 'Serve')][string]$Action = 'Start')

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$frontend = Join-Path $root 'frontend'
$windowsPowerShell = Join-Path $PSHOME 'powershell.exe'
if (!(Test-Path -LiteralPath $windowsPowerShell)) {
    $windowsPowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
}
$hash = [Security.Cryptography.SHA256]::Create()
try {
    $id = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToUpperInvariant()))).Replace('-', '')
} finally { $hash.Dispose() }
$name = "Local\MacroPulse-$id"
$localDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) "MacroPulse AI\$id"
$statusPath = Join-Path $localDir 'status.txt'
$pcUrl = 'http://127.0.0.1:5173/'

function Get-PrivateNetwork {
    $profiles = @(Get-NetConnectionProfile | Where-Object NetworkCategory -eq 'Private')
    $routes = Get-NetRoute -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceIndex -in $profiles.InterfaceIndex } |
        Sort-Object { $_.RouteMetric + $_.InterfaceMetric }
    foreach ($route in $routes) {
        $address = Get-NetIPAddress -InterfaceIndex $route.InterfaceIndex -AddressFamily IPv4 |
            Where-Object { $_.AddressState -eq 'Preferred' -and !$_.SkipAsSource -and
                $_.IPAddress -match '^(10\.|172\.(1[6-9]|2[0-9]|3[01])\.|192\.168\.)' } |
            Select-Object -First 1
        if ($address) { return $address }
    }
    throw 'No active Private Windows network with a private IPv4 gateway was found. Connect to your trusted home network and select the Private network profile in Windows Settings.'
}

function Get-Node {
    $node = Get-Command node.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if (!$node) { throw 'Install a Node.js version listed in frontend/package.json, then run npm ci in frontend.' }
    return $node.Source
}

function Install-Shortcut {
    $programs = [Environment]::GetFolderPath('Programs')
    $icon = Join-Path $localDir 'MacroPulse AI.ico'
    $png = [IO.File]::ReadAllBytes((Join-Path $frontend 'public\icons\macropulse-192.png'))
    # ICO directory header for one embedded 192x192 PNG.
    [byte[]]$header = @(0, 0, 1, 0, 1, 0, 192, 192, 0, 0, 1, 0, 32, 0) +
        [BitConverter]::GetBytes([int]$png.Length) + [BitConverter]::GetBytes([int]22)
    [IO.File]::WriteAllBytes($icon, [byte[]]($header + $png))
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut((Join-Path $programs 'MacroPulse AI.lnk'))
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\cmd.exe'
    $shortcut.Arguments = '/d /c ""{0}""' -f (Join-Path $root 'MacroPulse AI.bat')
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = 'Start MacroPulse AI for Windows and iPhone Safari'
    $shortcut.Save()
    Write-Host 'Open Start, search for MacroPulse AI, and select Pin to taskbar if Windows offers it.'
}

function Enable-LanFirewall {
    $network = Get-PrivateNetwork
    $node = Get-Node
    $rule = "MacroPulse-LAN-$id-$($network.InterfaceIndex)"
    $scope = @{
        Name = $rule; Direction = 'Inbound'; Action = 'Allow'; Protocol = 'TCP'; LocalPort = '5173'
        Profile = 'Private'; InterfaceAlias = $network.InterfaceAlias; RemoteAddress = 'LocalSubnet'; Program = $node
        Enabled = 'True'; EdgeTraversalPolicy = 'Block'
    }
    if (Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue) {
        Set-NetFirewallRule @scope
    } else {
        New-NetFirewallRule @scope -DisplayName 'MacroPulse AI private LAN (TCP 5173)' | Out-Null
    }
    Write-Host "Private LAN firewall access configured on $($network.InterfaceAlias)."
}

function Enter-Mutex($mutex, [int]$timeout) {
    try { return $mutex.WaitOne($timeout) }
    catch [Threading.AbandonedMutexException] { return $true }
}

function Open-Event([string]$suffix) {
    try { return [Threading.EventWaitHandle]::OpenExisting("$name-$suffix") }
    catch [Threading.WaitHandleCannotBeOpenedException] { return $null }
}

function Start-Child([string]$exe, [string]$arguments, [string]$directory, [hashtable]$settings, [bool]$backend = $false) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $exe
    $info.Arguments = $arguments
    $info.WorkingDirectory = $directory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.EnvironmentVariables.Clear()
    $baseKeys = @('SystemRoot', 'WINDIR', 'SystemDrive', 'PATH', 'PATHEXT', 'ComSpec', 'TEMP', 'TMP',
        'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)', 'ProgramData')
    if ($backend) {
        $baseKeys += @('USER_RO', 'PASS_USER_RO', 'HOST_RO', 'PORT_RO', 'DB_RO', 'SSL_MODE_RO', 'SSL_ROOT_CERT_RO',
            'MARKETPULSE_DEV_PROJECT_REF', 'GEMINI_API_KEY', 'MARKETPULSE_GEMINI_MODEL')
    }
    foreach ($key in $baseKeys) {
        $value = [Environment]::GetEnvironmentVariable($key)
        if ($null -ne $value) { $info.EnvironmentVariables[$key] = $value }
    }
    foreach ($key in $settings.Keys) { $info.EnvironmentVariables[$key] = $settings[$key] }
    return [Diagnostics.Process]::Start($info)
}

function Assert-Running($process, [string]$label) {
    if (!$process.HasExited) { return }
    if ($label -eq 'FastAPI') {
        switch ($process.ExitCode) {
            10 { throw 'Backend dependencies are missing. Install backend/requirements-ai.txt into .venv.' }
            11 { throw 'Existing Supabase DEV read-only configuration did not pass validation.' }
            12 { throw 'Gemini is missing from the existing backend environment and root .env.' }
        }
    }
    throw "$label exited (code $($process.ExitCode)). Check dependencies and ports TCP 8000 and 5173."
}

function Wait-Http([string]$url, $process, [string]$label, $stop) {
    $until = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Assert-Running $process $label
        if ($stop.WaitOne(0)) { return }
        $request = [Net.HttpWebRequest]::Create($url)
        $request.Proxy = $null
        $request.Timeout = 1000
        try {
            $response = $request.GetResponse()
            $response.Close()
            return
        } catch [Net.WebException] {
            if ($_.Exception.Response) { $_.Exception.Response.Close() }
        }
    } while (!$stop.WaitOne(250) -and [DateTime]::UtcNow -lt $until)
    if (!$stop.WaitOne(0)) { throw "$label did not respond at $url. Check the port and local firewall." }
}

function Set-Status([string]$message) {
    [IO.File]::WriteAllText($statusPath, $message)
}

function Invoke-Services {
    $lifetime = New-Object Threading.Mutex($false, "$name-services")
    if (!(Enter-Mutex $lifetime 0)) { $lifetime.Dispose(); return }
    $stop = New-Object Threading.EventWaitHandle($false, 'ManualReset', "$name-stop")
    $ready = New-Object Threading.EventWaitHandle($false, 'ManualReset', "$name-ready")
    try {
        Set-Status 'Starting MacroPulse AI.'
        $network = Get-PrivateNetwork
        $ip = $network.IPAddress
        $lanUrl = "http://${ip}:5173/"
        $python = Join-Path $root '.venv\Scripts\python.exe'
        $vite = 'node_modules\vite\bin\vite.js'
        if (!(Test-Path -LiteralPath $python)) { throw 'Create .venv with Python 3.12 and install backend/requirements-ai.txt.' }
        $node = Get-Node
        if (!(Test-Path -LiteralPath (Join-Path $frontend $vite))) { throw 'Frontend dependencies are missing. Run npm ci in frontend.' }
        $version = $null
        $nodeVersion = (& $node --version).TrimStart('v')
        if (![version]::TryParse($nodeVersion, [ref]$version) -or
            !(($version.Major -eq 22 -and $version -ge [version]'22.22.2') -or
              ($version.Major -eq 24 -and $version -ge [version]'24.15.0') -or $version.Major -ge 26)) {
            throw 'The installed Node.js version is outside the range in frontend/package.json.'
        }
        $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8000,5173 -ErrorAction SilentlyContinue)
        foreach ($listener in $listeners) {
            if ($listener.LocalAddress -in @('0.0.0.0', '::', '127.0.0.1') -or
                ($listener.LocalPort -eq 5173 -and $listener.LocalAddress -eq $ip)) {
                throw "Port $($listener.LocalPort) on $($listener.LocalAddress) is already in use. Close that service and retry."
            }
        }

        # Join before spawning. Closing the supervisor also closes its job handle
        # and terminates every descendant, including the Python venv redirector.
        Add-Type -Path (Join-Path $PSScriptRoot 'DevProcessJob.cs')
        [MacroPulse.DevProcessJob]::Attach()
        $bytes = New-Object byte[] 32
        $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
        try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
        $common = @{ MARKETPULSE_MACRO_AI_ENABLED = 'true'; MARKETPULSE_LAN_IP = $ip }
        $lan = @{ MARKETPULSE_LAN_DEV = 'true'; MARKETPULSE_LAN_TOKEN = [BitConverter]::ToString($bytes).Replace('-', '').ToLowerInvariant() }
        $backend = Start-Child $python 'scripts/dev-backend.py' $root ($common + $lan + @{
            MARKETPULSE_MODE = 'local'; MACRO_API_MODE = 'supabase-dev'
        }) $true
        $build = Start-Child $node "$vite build --mode windows-launcher" $frontend $common
        $buildDeadline = [DateTime]::UtcNow.AddSeconds(45)
        while (!$build.WaitForExit(250)) {
            if ($stop.WaitOne(0)) { return }
            Assert-Running $backend 'FastAPI'
            if ([DateTime]::UtcNow -ge $buildDeadline) { throw 'The frontend build timed out. Run npm run build in frontend for details.' }
        }
        if ($build.ExitCode -ne 0) { throw 'The frontend build failed. Run npm ci and npm run build in frontend for details.' }
        if ($stop.WaitOne(0)) { return }

        $pc = Start-Child $node "$vite preview --mode windows-launcher" $frontend $common
        $phone = Start-Child $node "$vite preview --mode windows-launcher" $frontend ($common + $lan)
        Wait-Http 'http://127.0.0.1:8000/health' $backend 'FastAPI' $stop
        Wait-Http $pcUrl $pc 'Windows frontend' $stop
        Wait-Http $lanUrl $phone 'LAN frontend' $stop
        if ($stop.WaitOne(0)) { return }
        Set-Status "Windows: $pcUrl`r`niPhone Safari: $lanUrl"
        $null = $ready.Set()

        $networkCheck = [DateTime]::UtcNow.AddSeconds(5)
        while (!$stop.WaitOne(500)) {
            Assert-Running $backend 'FastAPI'
            Assert-Running $pc 'Windows frontend'
            Assert-Running $phone 'LAN frontend'
            if ([DateTime]::UtcNow -ge $networkCheck) {
                $active = Get-PrivateNetwork
                if ($active.IPAddress -ne $ip -or $active.InterfaceIndex -ne $network.InterfaceIndex -or
                    $active.PrefixLength -ne $network.PrefixLength) {
                    throw 'The private network changed. Click MacroPulse AI to start it on the new address.'
                }
                $networkCheck = [DateTime]::UtcNow.AddSeconds(5)
            }
        }
        Set-Status 'MacroPulse AI stopped.'
    } catch {
        Set-Status $_.Exception.Message
        throw
    } finally {
        $ready.Dispose()
        $stop.Dispose()
        # Retain the lifetime mutex until process exit, when Windows closes the job.
    }
}

function Invoke-Controller {
    $control = New-Object Threading.Mutex($false, "$name-controller")
    $services = New-Object Threading.Mutex($false, "$name-services")
    $locked = Enter-Mutex $control 200000
    try {
        if (!$locked) { throw 'Another MacroPulse AI command is still running.' }
        $running = !(Enter-Mutex $services 0)
        if (!$running) { $services.ReleaseMutex() }
        if ($Action -eq 'Stop') {
            if ($running) {
                $stop = Open-Event 'stop'
                if ($stop) { try { $null = $stop.Set() } finally { $stop.Dispose() } }
                if (!(Enter-Mutex $services 20000)) { throw 'MacroPulse AI did not stop within 20 seconds.' }
                $services.ReleaseMutex()
            }
            Write-Host 'MacroPulse AI stopped.'
            return
        }
        if (!$running) {
            $launchArgs = '-NoProfile -ExecutionPolicy RemoteSigned -File "{0}" -Action Serve' -f $PSCommandPath
            $supervisor = Start-Process $windowsPowerShell -ArgumentList $launchArgs -WorkingDirectory $root -WindowStyle Hidden -PassThru
        }
        $until = [DateTime]::UtcNow.AddSeconds(160)
        do {
            $ready = Open-Event 'ready'
            if ($ready) {
                try { if ($ready.WaitOne(200)) { $started = $true; break } }
                finally { $ready.Dispose() }
            } else { Start-Sleep -Milliseconds 200 }
            if (($supervisor -and $supervisor.HasExited) -or (!$supervisor -and (Enter-Mutex $services 0))) {
                if (!$supervisor) { $services.ReleaseMutex() }
                if (Test-Path -LiteralPath $statusPath) { throw [IO.File]::ReadAllText($statusPath) }
                throw 'The MacroPulse AI supervisor exited before the services started.'
            }
        } while ([DateTime]::UtcNow -lt $until)
        if (!$started) {
            $stop = Open-Event 'stop'
            if ($stop) { try { $null = $stop.Set() } finally { $stop.Dispose() } }
            throw 'MacroPulse AI startup timed out. The stop signal was sent.'
        }
        Write-Host ([IO.File]::ReadAllText($statusPath))
        Write-Host "Status file: $statusPath"
        Start-Process $pcUrl
    } finally {
        if ($locked) { $control.ReleaseMutex() }
        $control.Dispose()
        $services.Dispose()
    }
}

try {
    $null = [IO.Directory]::CreateDirectory($localDir)
    switch ($Action) {
        'Install' { Install-Shortcut }
        'Firewall' {
            $launchArgs = '-NoProfile -ExecutionPolicy RemoteSigned -File "{0}" -Action ApplyFirewall' -f $PSCommandPath
            $elevated = Start-Process $windowsPowerShell -Verb RunAs -ArgumentList $launchArgs -Wait -PassThru
            if ($elevated.ExitCode -ne 0) { throw 'The firewall rule could not be installed. Verify the active network is Private and retry.' }
        }
        'ApplyFirewall' { Enable-LanFirewall }
        'Serve' { Invoke-Services }
        default { Invoke-Controller }
    }
} catch {
    Write-Host "MacroPulse AI: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
