# Helpers are dot-sourced by the launcher. Never persist the input JSONL.
function New-CodexUsage {
    @{
        status = 'not_started'
        turns = 0
        invalid = $false
        input_tokens = 0L
        cached_input_tokens = 0L
        output_tokens = 0L
        reasoning_output_tokens = 0L
        failed_turn = $false
        spawn_events = 0
        observed_spawn_roles = @()
    }
}

function Add-CodexEvent {
    param([hashtable] $Usage, [hashtable] $Event)

    if ($Event.type -eq 'turn.failed') { $Usage.failed_turn = $true }
    if ($Event.type -eq 'item.completed' -and $Event.item.type -eq 'collab_tool_call' -and
        $Event.item.tool -eq 'spawn_agent' -and $Event.item.status -eq 'completed') {
        $Usage.spawn_events++
        if ($Event.item.agent_type -cin 'ejecutor_ligero', 'ejecutor', 'auditor') {
            $Usage.observed_spawn_roles += $Event.item.agent_type
        }
    }
    if ($Event.type -ne 'turn.completed') { return }
    $Usage.turns++
    if ($Usage.turns -gt 1) { return }
    $counts = $Event.usage
    $keys = @('input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens')
    if ($counts -isnot [System.Collections.IDictionary]) { $Usage.invalid = $true; return }
    foreach ($key in $keys) {
        $value = $counts[$key]
        if (($value -isnot [int] -and $value -isnot [long]) -or $value -lt 0) {
            $Usage.invalid = $true
            return
        }
    }
    if ($counts.cached_input_tokens -gt $counts.input_tokens -or
        $counts.reasoning_output_tokens -gt $counts.output_tokens) {
        $Usage.invalid = $true
        return
    }
    foreach ($key in $keys) { $Usage[$key] += $counts[$key] }
}

function Complete-CodexUsage {
    param([hashtable] $Usage, [bool] $Successful)

    if ($Usage.status -eq 'not_started') { return }
    # The launcher submits one turn. A second summary has ambiguous boundaries.
    $Usage.status = if ($Successful -and $Usage.turns -eq 1 -and
        !$Usage.invalid -and !$Usage.failed_turn) { 'observed' } else { 'incomplete' }
}

function Watch-CodexLine {
    param([hashtable] $Usage, [string] $Line)
    try {
        if (![string]::IsNullOrWhiteSpace($Line)) {
            Add-CodexEvent $Usage (ConvertFrom-Json -InputObject $Line -AsHashtable)
        }
    } catch {
        $Usage.invalid = $true
    }
}

function Save-CodexRun {
    param([string] $Directory, [hashtable] $Record)
    $temporary = Join-Path $Directory "$($Record.run_id).tmp"
    try {
        [void][IO.Directory]::CreateDirectory($Directory)
        $path = Join-Path $Directory "$($Record.run_id).json"
        [IO.File]::WriteAllText($temporary, ($Record | ConvertTo-Json -Depth 8 -Compress))
        [IO.File]::Move($temporary, $path, $true)
    } catch {
        [Console]::Error.WriteLine('MarketPulse telemetry could not write a run checkpoint.')
    } finally {
        try { [IO.File]::Delete($temporary) } catch { }
    }
}
