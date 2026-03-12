# Recommended Repository Rules

These are the branch protection and PR guardrails recommended for `main` before broader external contributions.

## Branch Rules

- Require pull requests for all changes to `main`
- Block direct pushes to `main`
- Require at least 1 approving review
- Require all review conversations to be resolved
- Require branches to be up to date before merge
- Block force-pushes and deletions on `main`

## Required Status Checks

- `lint`
- `test`
- `smoke`

`primeintellect-live` should stay informational unless the repository is guaranteed to have the needed secrets on every fork and PR context.

## PR Scope Guidance

- Prefer one issue or one cleanup theme per PR
- Split repo-wide renames from behavior changes
- Split scaffolding/setup from runtime wiring when possible

