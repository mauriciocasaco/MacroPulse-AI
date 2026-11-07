# Codex orchestration for MarketPulse AI

Phase 0.5 installs the project configuration. Phase 0.5B adds automatic
classification. Live verification belongs in a separate session. Use PowerShell
7.4+ (`pwsh`) and a signed-in Codex CLI that supports project roles, the named
models and structured `exec` output. On Windows the launcher prefers a native
`codex.exe` on PATH and also supports a global npm installation of
`@openai/codex`. For npm, it uses `codex.cmd` on PATH to locate the package and
starts its JavaScript entry point with `node.exe`. Node can be beside the shim or
on PATH. This keeps CLI arguments and redirected input out of the batch shell.
Use a trusted checkout
so the CLI can load `.codex/config.toml` and `AGENTS.md`. Stop if host policy
prevents loading them. The launcher starts a fresh `codex exec` run with
per-run model, effort, sandbox and agent overrides. It does not change global
configuration. An existing chat cannot apply these launcher overrides to itself.
[Codex commands](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
and [project subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)
describe the relevant CLI behavior.

| Mode | Primary | Participants |
| --- | --- | --- |
| SIMPLE | GPT-6.1 Sol Low | Primary only |
| MEDIO | GPT-6.1 Sol Medium | Coordinator and `ejecutor_ligero` (GPT-6.1 Sol Low) |
| ELABORADO | GPT-6.1 Sol Medium | Coordinator, `ejecutor` (GPT-6 Luna High) and `auditor` (GPT-6.1 Sol Low) |

Run from the project root. Supply the task via `-Task` with no private inputs,
credentials or confidential snippets. Treat the execution event stream as private.
Use `-ReadOnly` for inspection and smoke tests. The normal launch uses workspace
write with no approval escalation. If a forced mode is unsafe, the primary
reports why and requests authorization for a fresh run at the recommended
level. A subsequent launch is the operator's decision.

## AUTO preflight

Pass a natural-language task with `-Mode AUTO` or omit the mode. AUTO makes one
additional GPT-6.1 Sol Low inference before starting the execution session. That
adds latency and usage and does not count toward the execution participant total.
The preflight takes up to 8,000 characters and uses a temporary working directory
outside the checkout, read-only sandbox, no approval escalation, and disabled
agents, shell, apps, hooks, memories, image viewing and web search. It skips
personal configuration and project instructions, and uses ephemeral mode.
The prompt supplies the task text without repository files or environment data.
The CLI still uses its normal authentication, and managed configuration can
remain in effect. If your host injects tools or private context into the
preflight, use a forced mode or the manual ratings instead.

The classifier returns four enumerated ratings. The launcher validates its
JSON and successful completion, rejects unexpected tool events, and allows
60 seconds for the process after sending the prompt. Failed, unsupported,
timed-out or invalid classification stops the launch. There are no retries
or automatic fallback. Classifier transcripts stay off the launcher output.
For a manual override, supply all four ratings together. Partial overrides fail.

| Input | Lowest | Middle | Highest |
| --- | --- | --- | --- |
| Scope | Local, one bounded edit or inspection | Bounded, several related changes | CrossCutting, multiple subsystems or shared contracts |
| Architecture | None | Local, within existing boundaries | Structural, changes architecture or shared contracts |
| Uncertainty | Low, known path and checks | Moderate, investigation needed | High, unknown behavior, invariants or acceptance |
| SecurityRisk | Low, synthetic/local and no security boundary | Elevated, authentication, authorization, sensitive data, migrations or production impact | High, substantial exposure or operational risk |

Any highest rating or Elevated security risk selects ELABORADO. Otherwise any
middle rating selects MEDIO. All lowest ratings select SIMPLE. Explicit modes
bypass the classifier and go through the same in-session safety check. The
route on stderr records the selection, rating source or explicit override,
requested model and effort, and expected total. The execution event stream
goes to stdout.

## Codex Desktop

Open the trusted MarketPulse AI folder as a local project and start a fresh chat
after configuration changes. Desktop shares the Codex configuration layers.
The composer model and effort controls, including `/model` and `/reasoning`
where available, can set those values for a chat. A prompt such as
`MODE: ELABORADO` is ordinary text. No documented Desktop composer command binds
these project-specific modes to the launcher, runs the AUTO preflight, or applies
the launcher's agent limits. The project defaults keep delegation disabled.
For exact mode selection, run the PowerShell commands below from the Desktop
integrated terminal when available, or from an external PowerShell terminal.
The resulting run is a CLI session. UI availability and role activation on the
installed Desktop build remain unverified.
[Desktop settings](https://learn.chatgpt.com/docs/developer-settings),
[Desktop slash commands](https://learn.chatgpt.com/docs/app/slash-commands),
and [project trust](https://learn.chatgpt.com/docs/config-file/config-basic)
describe the available controls.

## Auditor permissions

The auditor's role instructions require read-only behavior. Codex subagents
inherit the parent's sandbox and approvals. With a workspace-write parent,
the role cannot guarantee host-enforced read-only isolation. With `-ReadOnly`
the parent and its subagents use the read-only filesystem sandbox, subject to
effective host policy. Connector permissions remain separate.
[Codex subagent permissions](https://learn.chatgpt.com/docs/agent-configuration/subagents)

When independent host-enforced review is required after a writable run, freeze
the implementation and open a separate CLI session with `-Mode SIMPLE -ReadOnly`.
Give that session the original requirements, a sanitized handoff, and permission
to inspect only the scoped diff and applicable rubric in
`.codex/agents/auditor.toml`. Require Sol Low, no external tools or live services,
and a verdict using the same audit criteria. Verify the new session's effective
permissions from host metadata. Transfer the verdict back to the Coordinator
and count it toward the same three-round review limit. This is an external
independent review and does not establish sandbox isolation for the built-in
auditor of a writable ELABORADO session.

## Future smoke tests

Use fresh sessions with synthetic, read-only tasks. Ask for the substantive
answer and role participation evidence. These are sample commands for a future
session, after the bootstrap has ended.

```powershell
./scripts/Invoke-MarketPulseCodex.ps1 -Mode SIMPLE -ReadOnly -Task 'Read the README and report the project title.'
./scripts/Invoke-MarketPulseCodex.ps1 -Mode MEDIO -ReadOnly -Task 'For a smoke test, have the required lightweight executor compare the README stack and overview, then report any omissions or ambiguities. Make no edits.'
./scripts/Invoke-MarketPulseCodex.ps1 -Mode ELABORADO -ReadOnly -Task 'For a smoke test, delegate a short architecture summary of the README to the executor and independently audit its accuracy and provenance with the auditor. Make no edits.'
./scripts/Invoke-MarketPulseCodex.ps1 -Mode AUTO -ReadOnly -Task 'Read the README and report the project title.'
./scripts/Invoke-MarketPulseCodex.ps1 -ReadOnly -Task 'Review the README overview and planned stack within the existing documentation structure. Identify inconsistencies and summarize them without editing.'
./scripts/Invoke-MarketPulseCodex.ps1 -Mode AUTO -ReadOnly -Task 'Design a new historical news ingestion pipeline that spans retrieval, storage and the research API. Produce a proposal only. Make no edits or service calls.'
```

The three AUTO cases are intended to exercise SIMPLE, MEDIO and ELABORADO,
respectively. Record the four inferred ratings and assess them against the
tasks. To verify the deterministic mapping independently of inference, repeat
with all four manual ratings using the table above. Force SIMPLE on a synthetic
request to design a cross-cutting authorization change. Expect an escalation
request with no implementation, spawns or mode change.

For each run record the CLI version, route line, session ID, outcome and
allowlisted host evidence. Inspect the CLI events and host session metadata for
the primary's effective model and reasoning effort and for every distinct
spawned session's role, effective model and effort. Count actual distinct
participating sessions including the primary. For ELABORADO, inspect the
executor/auditor handoffs, independent verdict, round count and frozen Git
baseline comparison. Check effective auditor permissions using host metadata
without attempting writes. Use a separate read-only review to check the
alternative workflow independently of a writable parent.

Treat missing effective model, effort, role, permission or agent count evidence
as unverified and state what the host exposed. Configuration, launcher output
and agent self-reports are declarations. Concurrent slot limits do not enforce
lifetime participation counts or named-role selection. The session contract
governs those and the three-round limit. Filesystem isolation alone does not
constrain connectors. The auditor must maintain read-only behavior regardless.
Record unavailable or unsupported capabilities separately from failed checks.
Keep raw traces private and share only sanitized evidence.

See [local telemetry](codex-telemetry.md) for task IDs, quality evidence and cost reports.
