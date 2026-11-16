# Research reliability: provider failures and 503/504 triage

## Why this change exists

The public Research path can already have verified server-side statements,
official statistics, and citations before the Gemini **composition** call.
Previously, an HTTP or time-budget failure in this final optional wording
step escaped the graph and returned a generic 503/504. We now use the existing
server-rendered deterministic narrative **only when the evidence is present
and verified**, preserving source IDs, units and causal disclaimers.

The change does **not** invent economic answers, ignore invalid citations,
relax request authorization, retry non-idempotent Research, raise rate limits,
or extend the Render/Worker deadlines.

## Diagnosing actual public failures

Each public request emits `public_research_stage` with `research_id`,
`component`, `stage`, `duration_ms`, `outcome`, and optional error type,
HTTP status and Gemini provider HTTP status. Inspect logs in **Render**
and Cloudflare Workers Live Logs, correlated by research ID; do not
copy secrets, full prompts, or user data.

Classify at least 20 observed failures before tuning infrastructure:

- `worker.turnstile` / `worker.quota_admit`: Cloudflare challenge, quota or
  Durable Object availability.
- `worker.render_fetch`: network/startup/Render latency or upstream error.
- `fastapi.admission` and `fastapi.response`: configuration, server
  concurrency, deadline, serialization and internal exceptions.
- `supabase.connect`, `supabase.history`, `supabase.editorial`: DB
  connection/query timeout and connection pool health.
- `gemini.count_tokens`, `gemini.generate_content`, `gemini.validate_schema`:
  Gemini latency, 429 quota, 5xx or malformed provider response.
- `langgraph.compose_draft`: recoverable provider failures can now result in
  a valid deterministic narrative with `narrative_kind=deterministic`.
- `langgraph.interpret_with_llm`: free-form questions still depend on model
  interpretation; this fix does not mask a failed interpretation as a
  factually supported answer.
- `worker.research_total`: end-to-end status for the admission + response.

Do not equate a 503 with a provider outage without stage evidence.
`Public Research is busy` means capacity/admission, not an LLM outage.

## Remaining reliability work

1. Verify Render settings and actual deployed commit, active instance
   health, memory/CPU and errors in the last hour.
2. Inspect Gemini 429, 5xx and latency distributions separately from
   Supabase query times and Worker admission.
3. Measure end-to-end availability, successful evidence-backed answer rate,
   p50/p95 latency, provider timeout/429 rates and fallback frequency.
4. Consider queue/polling or async jobs if the 65-second synchronous budget
   remains dominant; do not blindly increase it.
5. Test known-safe questions after Cloudflare and Render changes, with
   low request volumes and a controlled API budget.

**This PR improves graceful degradation but is not, by itself, evidence
that every public timeout or 503 has been eliminated.** Do not declare
reliability resolved before production logs and post-deploy measurements.
