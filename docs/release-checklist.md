# Release Checklist

Use this checklist when preparing a tagged release such as `v0.2.1`.

## 1. Decide Scope

- Review `CHANGELOG.md` and recent merged PRs.
- Decide whether the release affects the Python package, the TypeScript package, or both.
- Confirm whether any user-facing docs, examples, support text, or issue templates should change with the release.

## 2. Sync Version Metadata

Update every version surface that should ship together:

- `autocontext/pyproject.toml`
- `autocontext/src/autocontext/__init__.py`
- `ts/package.json`

If one package is intentionally not being released, note that clearly in the PR.

## 3. Update Public Docs

Review the docs that new users, contributors, and agents are most likely to land on:

- `README.md`
- `autocontext/README.md`
- `ts/README.md`
- `examples/README.md`
- `autocontext/docs/agent-integration.md`
- `CHANGELOG.md`
- `SUPPORT.md`

## 4. Validate Package Surfaces

Python:

```bash
cd autocontext
uv build
```

Optional but recommended when the Python package changed:

```bash
cd autocontext
UV_CACHE_DIR=/tmp/uv-cache uv run ruff check src tests
UV_CACHE_DIR=/tmp/uv-cache uv run mypy src
UV_CACHE_DIR=/tmp/uv-cache uv run pytest
```

TypeScript:

```bash
cd ts
npm run build
npm test
npm pack --dry-run
```

## 5. Sanity-Check Publishing Inputs

- Confirm `.github/workflows/publish-python.yml` and `.github/workflows/publish-ts.yml` still match the intended publish surfaces.
- Treat `.github/workflows/publish-python.yml` and `.github/workflows/publish-ts.yml` as the supported release workflows. Do not add a parallel publish path without updating the trusted publisher configuration first.
- Confirm release notes in `CHANGELOG.md` reflect the tagged version.
- Confirm any install commands in the READMEs still match the package names and binaries.

## 6. Publish

- Merge the release prep to the intended branch.
- Create and push package-specific tags in the format `py-vX.Y.Z` and `ts-vX.Y.Z`.
- Watch the tag-triggered GitHub Actions `publish-python` and `publish-ts` workflows for PyPI and npm.
- Approve the `release` environment when the trusted publish jobs pause for deployment review.

## 7. Post-Release

- Verify the published version on PyPI and npm.
- Spot-check the package README rendering on package indexes when relevant.
- Move any unfinished notes back under `Unreleased` and open follow-up issues if needed.
