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

## Topology

| Mode | Total participants | Roles |
| --- | ---: | --- |
| SIMPLE | 1 | Primary only |
| MEDIO | 2 | Coordinator and one `ejecutor_ligero` |
| ELABORADO | 3 | Coordinator, one `ejecutor` and one independent `auditor` |

## CLI model and reasoning settings

| Mode | Primary | Executor | Independent auditor |
| --- | --- | --- | --- |
| SIMPLE | GPT-6.1 Sol Low | — | — |
| MEDIO | GPT-6.1 Sol Medium | GPT-6.1 Sol Low | — |
| ELABORADO | GPT-6.1 Sol Medium | GPT-6 Luna High | GPT-6.1 Sol Low |

The launcher sets the primary with `--model` and `-c model_reasoning_effort`.
For delegated modes it sets `agents.default_subagent_model` and
`agents.default_subagent_reasoning_effort` for the invocation. CLI overrides
take precedence over project and personal defaults, subject to host policy.
Use the registered role without model or effort arguments when spawning. The
executors use the invocation defaults and the auditor's explicit role settings
take precedence over them. See [configuration precedence](https://learn.chatgpt.com/docs/config-file/config-basic)
and [subagent settings](https://learn.chatgpt.com/docs/agent-configuration/subagents).

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

1. In Codex Desktop for Windows, open the existing MarketPulse AI folder as a
   trusted local project. Start a new local Codex chat at its root after the
   configuration change. Keep the effective host permissions appropriate to
   the task. Use read-only permissions for smoke tests where available.
2. Select any model and effort supported by the host in the composer. The
   executors inherit the primary's selection unless the user explicitly
   configures a supported host override. The auditor role specifies GPT-6.1
   Sol Low.
3. Start the first message with one of these lines, followed by the task.

   ```text
   MODE: SIMPLE
   MODE: MEDIO
   MODE: ELABORADO
   ```

The first-message marker selects the topology in AGENTS.md. Open a fresh
session when changing topology. The project enables
multi-agent tools with two concurrently open subordinate threads, excluding
the primary. SIMPLE must spawn none. MEDIO uses one `ejecutor_ligero`.
ELABORADO uses one `ejecutor` and an independent `auditor`, reusing those
threads for corrections and review. The same three-round limit and full
auditor gate apply, including a weighted score of at least 8.50. Desktop has
no project AUTO preflight. The launcher still applies its per-run overrides.

Inspect Desktop subagent activity and host metadata for distinct participants,
roles, permissions and effective settings. Record observed model and effort,
including any deviation from the user's selection. A prompt, role file, slot
limit or agent self-report establishes intended settings only. When model or
effort metadata is unavailable, record `modelo/esfuerzo efectivos: no verificables`
for the unavailable values and continue in the same session. For example,
`MODE: MEDIO` with any supported composer selection activates the Coordinator
and one `ejecutor_ligero` even when effective settings are unavailable.

Report any operational blocker, such as a missing required role, unavailable
delegation, insufficient permissions, unresolved security risks or unexpected
repository conflicts. See the permissions section below for the auditor's
inherited sandbox.
[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[Desktop settings](https://learn.chatgpt.com/docs/developer-settings), and
[project trust](https://learn.chatgpt.com/docs/config-file/config-basic).

### Desktop smoke test

Run each in a separate fresh chat with a supported composer selection. Use
High for the MEDIO check and Medium for ELABORADO to exercise inheritance. Append
the following task after the corresponding mode marker. Require synthetic data
only, no edits or service calls, and report host evidence and gaps.

- SIMPLE asks the primary to sort the numbers 3, 1, 2.
- MEDIO asks the lightweight executor to sort the same numbers and the
  Coordinator to verify the answer and inspect the one subordinate thread.
- ELABORADO asks the executor to sort those numbers and the independent auditor
  to verify ordering and multiset preservation under the full audit gate.
  Verify the two named threads, frozen workspace evidence and audit round count.

### Desktop telemetry

The current local collector consumes the launcher's structured CLI event stream.
A Desktop chat does not go through that collector. Until an adapter is verified,
record only sanitized observations from the UI, such as role activity and the
audit verdict, separately from CLI run records.

Project hooks are a candidate for local lifecycle evidence. The documented
`SubagentStop` payload exposes agent identity and role, and `Stop` exposes a
turn identifier. These events can recur within a task. Their payloads do not
establish exact token usage, effective models or complete run boundaries. Before
adding a hook, verify event delivery and trust controls in a fresh Windows
Desktop chat, correlate IDs across continuations, and allowlist metadata without
retaining messages, transcripts or transcript paths. Keep unavailable metrics
unknown. No hooks or collector changes are needed for mode selection.
[Codex hooks](https://learn.chatgpt.com/docs/hooks).

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
