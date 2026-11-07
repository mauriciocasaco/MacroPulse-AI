# Offline launcher test. A temporary launcher copy can only resolve the local Node fixture.
#Requires -Version 7.4
$ErrorActionPreference = 'Stop'
$PSNativeCommandUseErrorActionPreference = $false
$root = Split-Path -Parent $PSScriptRoot
$node = (Get-Command node -CommandType Application | Select-Object -First 1).Source
$pwsh = (Get-Process -Id $PID).Path
$fixture = Join-Path $root ("telemetry-fixture-" + [guid]::NewGuid())
try {
    $scripts = Join-Path $fixture 'scripts'
    [void][IO.Directory]::CreateDirectory($scripts)
    Copy-Item -LiteralPath (Join-Path $root 'scripts/CodexTelemetry.ps1') -Destination $scripts
    $mock = Join-Path $fixture 'offline mock.cjs'
    @"
const fs = require('node:fs');
const args = process.argv.slice(2);
if (args.includes('--version')) { console.log('codex-cli 0.166.0'); process.exit(0); }
const input = fs.readFileSync(0, 'utf8');
const usage = {input_tokens: 80, cached_input_tokens: 30, output_tokens: 20, reasoning_output_tokens: 10};
const emit = (event) => console.log(JSON.stringify(event));
if (args.includes('--output-schema')) {
  const text = JSON.stringify({Scope:'Bounded', Architecture:'None', Uncertainty:'Low', SecurityRisk:'Low'});
  emit({type:'item.completed', item:{type:'agent_message', text}});
  emit({type:'turn.completed', usage:{...usage, input_tokens: 8, cached_input_tokens: 3, output_tokens: 2, reasoning_output_tokens: 1}});
  process.exit(0);
}
emit({type:'item.completed', item:{type:'agent_message', text:'PRIVATE_SENTINEL reply'}});
if (input.includes('offline failure')) { emit({type:'turn.failed'}); process.exit(17); }
if (input.includes('offline interrupted')) process.exit(130);
if (input.includes('offline missing')) process.exit(0);
if (input.includes('offline malformed')) usage.cached_input_tokens = 1000;
emit({type:'turn.completed', usage, child_usage:{input_tokens:999}, payload:'PRIVATE_SENTINEL'});
"@ | Set-Content -LiteralPath $mock

    $launcher = Join-Path $scripts 'Invoke-MarketPulseCodex.ps1'
    $tokens = $null
    $errors = $null
    $source = Join-Path $root 'scripts/Invoke-MarketPulseCodex.ps1'
    $ast = [Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$errors)
    if ($errors) { throw ($errors | Out-String) }
    $resolver = $ast.Find({
        param($n)
        $n -is [Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Resolve-CodexInvocation'
    }, $true)
    $stub = "function Resolve-CodexInvocation { @{ Executable = '$($node.Replace("'", "''"))'; PrefixArguments = @('$($mock.Replace("'", "''"))') } }"
    $patched = $ast.Extent.Text.Replace($resolver.Extent.Text, $stub)
    Set-Content -LiteralPath $launcher -Value $patched
    $task = [guid]::NewGuid()
    $stderr = Join-Path $fixture 'stderr.txt'
    $records = Join-Path $fixture '.local/codex-telemetry/runs'
    foreach ($case in @(
        @{ mode = 'SIMPLE'; selected = 'SIMPLE'; marker = 'ok'; outcome = 'completed'; code = 0; status = 'observed' }
        @{ mode = 'MEDIO'; selected = 'MEDIO'; marker = 'ok'; outcome = 'completed'; code = 0; status = 'observed' }
        @{ mode = 'ELABORADO'; selected = 'ELABORADO'; marker = 'ok'; outcome = 'completed'; code = 0; status = 'observed' }
        @{ mode = 'AUTO'; selected = 'MEDIO'; marker = 'ok'; outcome = 'completed'; code = 0; status = 'observed' }
        @{ mode = 'SIMPLE'; selected = 'SIMPLE'; marker = 'offline failure'; outcome = 'failed'; code = 17; status = 'incomplete' }
        @{ mode = 'SIMPLE'; selected = 'SIMPLE'; marker = 'offline interrupted'; outcome = 'interrupted'; code = 130; status = 'incomplete' }
        @{ mode = 'SIMPLE'; selected = 'SIMPLE'; marker = 'offline missing'; outcome = 'unknown'; code = 0; status = 'incomplete' }
        @{ mode = 'SIMPLE'; selected = 'SIMPLE'; marker = 'offline malformed'; outcome = 'completed'; code = 0; status = 'incomplete' }
    )) {
        $lines = @(& $pwsh -NoProfile -File $launcher -Mode $case.mode -TaskId $task -Task "PRIVATE_SENTINEL $($case.marker)" -ReadOnly 2>$stderr)
        if ($LASTEXITCODE -ne $case.code -or $lines[0] -cne '{"type":"item.completed","item":{"type":"agent_message","text":"PRIVATE_SENTINEL reply"}}') {
            throw "Unexpected subprocess output or exit for $($case.marker)."
        }
        $diagnostic = Get-Content -Raw -LiteralPath $stderr
        if ($diagnostic -notmatch 'run_id=([a-f0-9-]{36})') { throw 'Missing run ID diagnostic.' }
        $raw = Get-Content -Raw -LiteralPath (Join-Path $records "$($Matches[1]).json")
        $record = $raw | ConvertFrom-Json
        if ($raw.Contains('PRIVATE_SENTINEL') -or $record.task_id -ne $task.ToString() -or
            $record.requested_mode -ne $case.mode -or $record.selected_mode -ne $case.selected -or
            $record.outcome -ne $case.outcome -or $record.execution_usage.status -ne $case.status -or
            $record.cli_version -ne '0.166.0') { throw "Unexpected saved record for $($case.marker)." }
        if ($case.status -eq 'observed' -and $record.execution_usage.input_tokens -ne 80) {
            throw 'Usage was double-counted or lost.'
        }
        if ($case.mode -eq 'AUTO' -and ($record.classifier_usage.input_tokens -ne 8 -or
            $record.classifier_usage.status -ne 'observed')) { throw 'Classifier usage was lost.' }
    }
    if (@(Get-ChildItem -LiteralPath $records -Filter '*.json').Count -ne 8) { throw 'Run records collided.' }
    'PASS 8 offline launcher cases, JSONL preservation, privacy, token boundaries, outcomes and exit codes'
} finally {
    $resolved = (Resolve-Path -LiteralPath $fixture -ErrorAction SilentlyContinue).Path
    if ($resolved -and $resolved -eq $fixture -and (Split-Path -Parent $resolved) -eq $root) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
