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

## Bounded interpretation failover

The public Research question interpreter ordinarily uses the configured Gemini
model (currently `gemini-3.6-flash`). When that model returns 429 or 5xx, a
network error or an HTTP timeout, the server may make **one** additional
interpretation attempt using the already allowlisted
`gemini-3.5-flash-lite`. This is not a retry of the original HTTP request:
the same Worker reservation, Turnstile validation, 65 s backend deadline,
input checks and evidence validation remain in force.

Safeguards:

- Never retry invalid input, schema validation, missing permissions, 400/401/403,
  or any other non-transient HTTP status.
- Only activate the alternate model when the primary model is not already Lite.
- The alternate model is limited to 10 s and must leave **30 s** before the
  backend deadline for evidence retrieval, composition, validation and output.
- At most **three** provider generations per request, and only after the
  special one-time interpretation failover. Normal limit remains two.
- The fallback model does not generate macroeconomic facts: it may only return
  structured scope data, which passes existing strict semantic validation.
- A second failure remains an observable 503/504 with no invented result.
- Logs include `gemini.interpret_failover` without request contents or
  credential values.

This increases the worst-case number of metered provider calls in the
transient-error path. Monitor model usage, request budgets and fallback rates.
There is **no availability guarantee** if both Gemini models are unavailable.

When a provider outage repeatedly exhausts this strategy, investigate
Gemini API operational status and rate limits rather than extending the
synchronous Cloudflare/Render deadlines. Google recommends bounded retries
on transient 429 and 5xx failures.
