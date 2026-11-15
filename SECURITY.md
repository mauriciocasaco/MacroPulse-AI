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
[DevSecOps verification](docs/phase-10-1-devsecops.md) for scope and
administrator-only GitHub configuration steps.

## Scope

We prioritize security issues in the public API, Worker, PostgreSQL
permissions, third-party integrations, model-grounding boundaries and
secrets handling. We do not collect account credentials because the
application does not offer accounts.
