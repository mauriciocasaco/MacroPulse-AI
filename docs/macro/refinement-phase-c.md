# Phase C — LLM and RAG response engine

Baseline: `14d31b3e12a0401306eb6c5062e5fd2d65631779` on `codex/refinement-phase-c`, initially clean, in `C:/Users/Usuario/.codex/worktrees/refinement-phase-c/MarketPulse-AI`. Root, local refs, branch, HEAD and complete porcelain status were checked before edits. User authorized the custom multi-agent workflow and phase commits. No merge or push was performed by this agent. Effective model/effort metadata was unavailable; no separate model selection is claimed.

## Result and contract

- Optional `indicator_id` and `event_id` preserve old requests. Explicit events require exact dates: COVID 2020-02-01 to 2020-12-31; Ukraine 2022-02-01 to 2024-02-29; Generative AI 2023-09-01 to 2026-09-30. Conflicting parsed series, event, geography or authority abstain before providers. Original custom question remains local; the model receives canonical intent and bounded public evidence.
- Real observation periods are bounded by the request. Selected events exclude prior anchors; legacy requests retain verified, explicitly labeled anchors. Source dates, period boundaries, original decimal values, units and formulas remain in the deterministic evidence. Statistical breaks abstain from endpoint/swing comparison.
- Positive level series calculate `(last-first)/first*100`, rounded to six decimals for the explanation. A positive baseline falling to zero gives -100%. Rate/growth series use percentage points or basis points. Nonpositive and near-zero baselines abstain from relative percentages; threshold is max(1e-12, 1e-6 times the largest absolute observation). No percentage is clamped or enlarged.
- Largest swing means maximum absolute chronologically ordered observation-to-observation excursion, represented by the global extrema ordered by their actual dates. It is not claimed to be the largest percentage excursion. Its relative change is additionally calculated when meaningful.
- Actual LLM output is a narrative of cited prose statements. The model orders/combines complete approved statistic/document sentences and may add neutral transitions. Validation requires exact citation-local statement support, all required endpoint/swing statistics, and at least one institutional document. Invented values/dates/units/citations, omitted calculations and unsupported causal statements fail closed. There is no deterministic narrative fallback presented as LLM output. This is constrained extractive generation, not unrestricted paraphrasing or a general semantic verifier.
- Existing facts/calculations/interpretation fields remain. Additive fields are `narrative`, `model_id`, selected-context echoes, and evidence `rights_url`, `similarity_score`, `similarity_metric`. The model identifier comes from the actual configured Gemini invocation path only; injected mock generators return null. It identifies the configured API model, not a separately verified provider revision.
- Mandatory indicator/event/publication filters run before top-k. Eligible documents are ranked by descending genuine normalized TF-IDF cosine similarity; score and metric are returned without converting ranks or distances into similarity. Source titles, URLs, publication dates and rights URLs remain real. Reviewed corpus summaries and visible attribution are English.
- The client accepts optional legacy fields, rejects conflicting echoed context, unsupported/duplicate citations, invalid scores/metrics/rights URLs, invented model identifiers and increasing claimed relevance order. `askMacroAnalysis` adds an optional fifth selected-context argument.
- Loopback, local-enable, bounded request, public-demo exclusion, tracing-off, sanitized failures and read-only database contracts remain intact.

## Changed files

Tracked changes: `backend/macro_ai/corpus.json`, `gemini.py`, `models.py`, `retrieval.py`, `service.py`, `statistics.py`; `frontend/src/macro-api.ts`; `tests/test_macro_ai.py`.
New files: `backend/macro_ai/grounding.py`, `frontend/src/macro-api.test.ts`, `tests/test_macro_ai_refinement.py`, this document. No unrelated files, credentials or private datasets were read or changed.

## Verification

All commands run in the assigned worktree. Python executable was `C:/Users/Usuario/Documents/MarketPulse-AI/.venv/Scripts/python.exe`; the assigned frontend uses a local ignored node_modules junction to the existing install.

| Command | Working directory | Result |
| --- | --- | --- |
| Python `-m pytest tests/test_macro_ai.py tests/test_macro_ai_refinement.py -q` | worktree root | exit 0; 182 passed, no skips |
| `npm run typecheck` | frontend | exit 0 |
| `npm run lint` | frontend | exit 0; zero warnings |
| `npm test -- --reporter=dot` | frontend | exit 0; 115 passed across 7 files; no skips |
| `npm run build` | frontend | exit 0 |
| `git diff --check` | worktree root | exit 0 |
| Python `-m pytest -q -rs` before the last added -100% fixture | worktree root | exit 1; 513 passed, 81 skipped, 3 failed |

The three full-suite failures are unchanged remote-reader tests in `tests/test_macro_api.py`: the remote-settings fixture points to an ignored TLS certificate available in the original checkout but absent from this clean worktree. They fail configuration validation before any connection. The coordinator must rerun the complete suite in the integration checkout. The 81 skips require unset disposable PostgreSQL DSNs; they are not live integration validation.

New fixtures cover exact event windows, indicator/event/foreign-authority mismatches, sorted observations, endpoint and swing chronology, zero/negative/near-zero denominators, positive-to-zero change, rate units, event anchor exclusion, statistical breaks, event prefiltering, genuine cosine values/order, unsupported narrative numbers/dates/units/causality/citations, required coverage and mock model identity. Client tests cover additive/legacy contracts, echoes, citations, source ordering, score types/ranges and safe empty evidence.

## Limits and pending validation

No live Gemini, live PostgreSQL, account or production service was contacted. All automated LLM fixtures are mocks. The reviewed corpus has no Generative AI documents, and some fixed event/indicator windows have insufficient publication-safe context; those abstain instead of inventing sources. Current captured statistical vintages are not historical publication vintages and are labeled accordingly. Browser/visual integration, coordinator regression checks and independent review remain pending. This document is implementation evidence, not an audit verdict.
