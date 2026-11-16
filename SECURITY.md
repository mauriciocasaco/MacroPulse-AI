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

The Research security review identified these risks in the versioned design.
Hosted origin reachability, PostgreSQL permissions and view definitions,
PostgREST exposure, and Gemini processing and retention require verification
against the effective deployment.

- **Origin admission and capacity.** A leaked shared Worker-to-Render secret
  could allow direct requests to a reachable origin and bypass edge admission
  and quotas. The origin still enforces its own validation and resource limits.
  Administrator and public requests have separate edge budgets and share
  origin capacity. A stolen administrator credential can consume its budget.
- **Provider data exposure.** Credential patterns are screened. Sensitive text
  that escapes the checks can reach Gemini during interpretation or token
  counting. Composition can send reviewed evidence to the provider. Review
  provider processing and source reuse rights for this use.
- **Research output integrity.** Validated plans can omit question qualifiers.
  The unverified fallback uses structural and text filters that accepted
  instruction-like text, written-out magnitudes, and unsupported causal
  claims in synthetic audit cases. Editorial checks limit eligible material
  and preserve attribution. A valid citation can still carry a false or
  misleading assertion from its source.
- **Historical cutoffs and access boundaries.** Documentary publication and
  observation capture are separate cutoffs. Ambiguous requests and profiles
  with dynamic retrieval disabled can use later revisions. Database role and
  grant checks depend on deployed view definitions, owners, effective grants,
  and exposed schemas.
- **Contract drift.** The Worker, backend, and UI independently validate parts
  of the request and response contract. Their filtering and error handling can
  diverge, including on Unicode input.
