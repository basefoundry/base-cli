# Security policy

`base-cli` is a library for embedding a lifecycle into Python command-line
applications. We take reports about the framework, its release artifacts, and
the security controls documented in the [runtime threat model](docs/security-threat-model.md)
seriously.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
[Private Vulnerability Reporting](https://github.com/basefoundry/base-cli/security/advisories/new)
workflow. Do not open a public issue, pull request, or discussion containing
secrets, an exploitable proof of concept, or details that would enable an
unfixed attack.

Include, when safe to share:

- the affected base-cli version or commit and the Python/OS environment;
- a concise description of the impact and the attack preconditions;
- reproduction steps or a minimal proof of concept with secrets removed; and
- any proposed mitigation or an indication of whether exploitation is active.

If private reporting is unavailable for a repository or account, contact the
repository maintainers through the private contact route shown on the
[repository profile](https://github.com/basefoundry/base-cli) and reference
“base-cli security report”; do not publish the details first.

## Supported versions

Security fixes are targeted at the latest released minor line and the current
development branch. At the time this policy was published, that means the
`0.3.x` release line and `main`. Older pre-1.0 lines are best effort only;
upgrade to the latest release before requesting a backport. A release that
changes the supported window will update this table and the changelog.

| Version | Security support |
| --- | --- |
| `0.3.x` | Supported |
| `main` | Supported for fixes merged before the next release |
| `<0.3` | Upgrade strongly recommended; best effort only |

The [API stability policy](docs/api-stability.md) explains the pre-1.0
compatibility boundary. A security fix may require an emergency breaking
change when leaving a vulnerable behavior in place would expose users.

## Response and disclosure expectations

These are service targets rather than a guarantee:

- acknowledge a report within **3 business days**;
- provide an initial severity and affected-version assessment within **10
  business days**; and
- provide a status update at least every **7 days** while a report is active.

We will coordinate a fix, release notes, and (when appropriate) a GitHub
Security Advisory/CVE. We normally request a **90-day coordinated disclosure
window** from the first maintainer response, adjusted with the reporter when
the fix or downstream coordination needs more or less time. We may publish an
advisory earlier if exploitation is public or users need an urgent mitigation.

Reporter credit is given unless anonymity is requested. Please do not include
personal data or production credentials in a report.

## Scope and security boundaries

The framework protects the lifecycle data it owns: argv redaction, private
runtime files, fail-closed temporary cleanup, bounded JSON contracts, and
opt-in telemetry with a safe attribute set. It does not sandbox consumer
callbacks, third-party plugins, Python dependencies, shell commands, or the
operating system. Consumers must review the [threat model](docs/security-threat-model.md)
and complete the [security review checklist](docs/security-review.md) for their
own profile, plugins, paths, history writer, and telemetry exporter.
