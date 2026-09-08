# Security Policy

## Reporting a Vulnerability

Do not open public GitHub issues for security vulnerabilities.

Use GitHub private vulnerability reporting for this repository when it is available. Include:

- a description of the vulnerability
- the affected version or commit
- reproduction steps or proof of concept
- impact assessment
- any suggested mitigation

If private vulnerability reporting is not available yet, do not publish the details in a public issue. Open a minimal issue asking for a private contact path and omit the vulnerability details.

## Response Expectations

We will aim to:

- acknowledge receipt promptly
- confirm whether the issue is in scope
- communicate remediation status as fixes progress
- coordinate disclosure timing when a fix is ready

## Supported Versions

This project is pre-1.0 and moving quickly. Security fixes, when available, are expected to land on the latest mainline version rather than through long-lived backport branches.

## CI and Release Trust Boundaries

External contributors require workflow approval even after an earlier PR has
been merged. Review the exact head before approving a run, and review new
commits again. Main requires fresh review and the Python, TypeScript, smoke,
and dependency-security checks from GitHub Actions.

Pull-request CI runs with read-only repository permissions on GitHub-hosted
runners. Keep privileged PR triggers, service secrets, publishing credentials,
and untrusted artifact promotion out of those jobs. Pin action commits and
tool versions, disable unnecessary caches, and avoid persisting checkout
credentials.

Release builds validate their source against protected main and successful CI
before producing artifacts. Publication uses a separate job, the artifact from
that same workflow run, and a protected environment with independent approval.
Release tag creation is restricted, and existing release tags are immutable.
The corresponding PyPI/npm trusted publisher must require the exact repository,
workflow filename, and protected environment. These GitHub and registry settings
must be maintained together; workflow files alone cannot enforce registry trust.

Live service credentials belong only in the protected `live-integration`
environment. The main-only manual workflow scopes them to live command steps.
Reviewers must still trust all code and dependencies in that job: separate
steps are not a process-isolation boundary.
