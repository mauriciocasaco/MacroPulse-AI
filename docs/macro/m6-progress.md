# M6 local integration record

## Baseline and execution

- Integration branch is `codex/macropulse-migration`. Initial HEAD and the local remote-tracking ref both resolved to `abc0f1c5cc860c21e1dbc5d514efc182f072f1b4`. Initial status was clean.
- The protected baseline tag resolved to `4f2ceadaea0901a3fc75d63683566821bbe8027c`.
- AUTO-EFFICIENT ran sequentially in the existing checkout. The Desktop session had no explicit repository mode or launcher context. One primary completed both workstreams and the consolidated review. Effective model and reasoning effort were unverified. No independent auditor participated.
- Integration is a single local commit on the migration branch, `feat(macro): rebrand portfolio and harden AI evidence selection`. Resolve its ID with `git log -1 --format=%H -- docs/macro/m6-progress.md`. No worktree branches or merge were needed.

## M5 review and M6 changes

- The implemented LangGraph is an acyclic StateGraph with explicit state, a recursion limit, read-only statistics, document retrieval, an evidence gate, abstention and a generic error path. The date range, corpus size, number of documents and Gemini request are bounded. The server controls calculated facts and validates evidence IDs. The request and provider invocation disable LangSmith tracing.
- M5 recognized all five demo questions and one of five evaluated paraphrases. Broad episode tags could admit documents unrelated to the indicator. M6 adds focused bilingual aliases, requires the indicator tag and prioritizes topic overlap before lexical cosine similarity. The corpus remains eight attributed, dated summaries. Rights URLs and restrictions are recorded in the [reuse review](data-reuse-policy.md). Its publication cutoff is separate from the series revision and capture dates.
- M5 solicited narrative that the server discarded. M6 asks Gemini for evidence IDs only and validates the result against the retrieved sources. Provider errors, missing or malformed results, unexpected prose and unknown IDs fail closed. The chronology regression checks September versus October in synthetic M3 and HICP fixtures. A hostile document fixture verifies that a compromised provider selection cannot create an unknown citation. Trusted editorial review of corpus summaries remains necessary.
- MacroPulse AI is the default when macro data is enabled. The market archive remains available, and builds with macro disabled keep their prior fallback. The portfolio README describes the implemented stack and the separate public readiness gates. The manifest retains its identity, scope and start URL. The existing pulse icon is brand-neutral.

## Verification

| Check | Result |
| --- | --- |
| Backend pytest with disposable PostgreSQL 17, pgvector and local TLS | 427 passed, including 25 macro AI cases |
| Five originals and five paraphrases | 10/10 expected indicator sets and expected source present in retrieved evidence |
| Vitest and Worker | 57 and 6 passed |
| Frontend lint, TypeScript and PWA builds | Passed for local, default public and opt-in public |
| Built PWA smoke with synthetic responses | Verified root routing and research availability for all three variants in jsdom. Verified manifest identity and every generated shell integrity entry. Installed-browser update behavior was outside this check. |
| Bandit | No medium or high severity findings in the macro AI, macro data, API and public origin modules |
| Dependency audits | No known findings from npm audit or pip-audit for the local AI requirements and included public runtime |
| Secret scan | Gitleaks checked commits since the protected baseline with redaction and the staged M6 diff |

The local suites covered Worker-to-origin authentication, route and method allowlists, input and size limits, rate limits, API cache exclusion, public analysis prohibition and DEMO projection privileges with disposable data. Live DEMO isolation and deployed limits remain release checks. Gemini was mocked. The final citation-only contract has yet to be verified against the live provider.

## Public handoff

The consolidated local review found no outstanding critical or high-severity defect in the inspected change. The repository's independent audit gate and weighted score remain pending in a fresh ELABORADO session. Before release, authorize and load the isolated Supabase DEMO projection, verify grants and source attribution, configure the Render origin and Cloudflare Worker, and test their deployed boundaries. Keep public Gemini disabled until a separate design establishes abuse protection, global budget and concurrency controls, monitoring and a kill switch.
