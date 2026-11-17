# Security Policy

MacroPulse AI is a public research portfolio application. It does not have
end-user accounts or roles. The public HTTP endpoints, AI cost controls,
data-access boundaries, and evidence-validation rules are security-sensitive.

## Reporting a vulnerability

Please **do not** disclose a working exploit, secret, private connection
string, or personal information in a public issue or pull request.

Prefer GitHub's **Report a vulnerability** / private security advisory
interface when available in the repository's Security tab. If private
reporting is unavailable, contact the repository maintainer privately
through the GitHub profile before providing technical details.

A useful report contains the affected commit and endpoint/component,
reproduction preconditions, observed versus expected behavior, and impact.
Do not run destructive tests or stress production infrastructure.

## Security development

Pull requests are checked by CI, CodeQL and Gitleaks. Dependabot proposes
security patches for known vulnerable dependencies. Security failures must be triaged before merging;
there is no automatic production deployment.

A passing scan is not a guarantee of security. The offline CI jobs do not
test hosted Cloudflare, Render or Supabase configuration. See
[DevSecOps verification](docs/devsecops.md) for scope and
administrator-only GitHub configuration steps.

## Scope

We prioritize security issues in the public API, Worker, PostgreSQL
permissions, third-party integrations, model-grounding boundaries and
secrets handling. We do not collect account credentials because the
application does not offer accounts.

## Known risks

The Research security review identified these residual risks in the versioned design.
Fase 12.S1–S4 introduced one-time origin attestations, sensitive-input screening,
provider-egress constraints and intent admission. These safeguards were exercised
with local and synthetic tests; hosted origin reachability, active feature flags,
PostgreSQL grants/views, PostgREST exposure, and Gemini processing and retention
still require verification against the effective deployment.
[Origin authorization](docs/security/fase-12-s1-verification.md) ·
[Sensitive input and egress](docs/security/fase-12-s3-verification.md) ·
[Intent admission](docs/security/fase-12-s4-verification.md)

- **Origin admission and capacity.** Research POST now requires an HMAC-SHA256
  attestation over the request, bound to a single-use Durable Object reservation
  and consumed via an authenticated HTTPS callback before Research runs. The
  older shared Worker-to-Render secret remains valid **only for catalog/history
  reads**; if leaked, it could still bypass the edge quotas for those reads at
  a reachable origin. Key compromise, replay races, callback availability and
  hosted configuration remain residual concerns. Administrator and public
  requests have separate quotas but share origin capacity; a stolen admin
  credential can consume the administrator budget.
- **Provider data exposure.** The Fase 12.S3 checks reject recognizable
  sensitive-input patterns before request SQL or Gemini. Stage-specific
  allowlists constrain Gemini payloads, and sanitized telemetry excludes
  questions and credentials. Non-matching private text, ambiguous formats and
  novel obfuscation can still pass; permitted evidence statements may be sent
  for composition. Review provider processing and source reuse rights.
- **Research input and output integrity.** The Fase 12.S4 intent gate rejects
  recognized control-override, code/SQL execution and exfiltration requests,
  but its pattern coverage is incomplete and false positives remain possible.
  Validated plans can omit question qualifiers. The unverified fallback uses
  structural and text filters that accepted instruction-like text, written-out
  magnitudes, and unsupported causal claims in synthetic audit cases.
  Editorial checks limit eligible material and preserve attribution, but a
  valid citation can still contain a false or misleading source assertion.
- **Historical cutoffs and access boundaries.** Documentary publication and
  observation capture are separate cutoffs. Ambiguous requests and profiles
  with dynamic retrieval disabled can use later revisions. Database role and
  grant checks depend on deployed view definitions, owners, effective grants,
  and exposed schemas.
- **Contract drift.** The Worker, backend, and UI independently validate parts
  of the request and response contract. Their filtering and error handling can
  diverge, including on Unicode input.
