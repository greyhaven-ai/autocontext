# Analytics And Adoption

Use this guide to answer a few common maintainer questions:

- How much interest is the repo getting?
- Which package ecosystems are seeing usage?
- Can we see which projects depend on the repo or packages?
- Can we identify who accessed the repo?

## Run Artifact Analytics

For completed autocontext runs with persisted context-selection artifacts, summarize candidate versus selected context, selected token estimates, duplicate-content rate, useful-artifact recall, and freshness by generation. The report also emits diagnostics for duplicate selected content, low useful-artifact recall, and selected-token bloat.

```bash
cd autocontext
uv run autoctx analytics context-selection --run-id <run_id>
uv run autoctx analytics context-selection --run-id <run_id> --json
```

For completed runs with persisted `RunTrace` artifacts, emit trace-grounded
findings from the same reporter used by run-end writeups. Trace ids are the
filenames under `knowledge/analytics/traces/` without the `.json` suffix
(for example `trace-run-123` from `knowledge/analytics/traces/trace-run-123.json`).
If a run only has an events stream, rebuild traces first:

```bash
cd autocontext
uv run autoctx analytics rebuild-traces --run-id <run_id> --json
uv run autoctx analytics trace-findings --trace-id <trace_id>
uv run autoctx analytics trace-findings --trace-id <trace_id> --kind weakness
uv run autoctx analytics trace-findings --trace-id <trace_id> --json
```

Use `--kind writeup` (the default) for a full trace-grounded summary with
`findings`, `failure_motifs`, `recovery_paths`, and `summary`. Use
`--kind weakness` for a recommendation-focused report with `weaknesses`,
`failure_motifs`, `recovery_analysis`, and `recommendations`. Under `--json`,
missing traces return a parseable payload such as
`{"status":"failed","error":"...","trace_id":"..."}` and exit non-zero.

## Repository Traffic

For GitHub-hosted repo traffic, use the repository Traffic view:

- GitHub UI: `Insights` -> `Traffic`
- Metrics available: views, unique visitors, clones, unique cloners, top referrers, and popular content
- Retention: GitHub only keeps the most recent 14 days in the UI

CLI/API equivalents:

```bash
gh api repos/greyhaven-ai/autocontext/traffic/views
gh api repos/greyhaven-ai/autocontext/traffic/clones
gh api repos/greyhaven-ai/autocontext/traffic/popular/referrers
gh api repos/greyhaven-ai/autocontext/traffic/popular/paths
```

Use weekly snapshots if you want longer-running trendlines.

## Package Adoption

### npm

The npm package page is the easiest package-level signal:

- Package page: <https://www.npmjs.com/package/autoctx>
- Watch the recent download count and any dependent package links npm exposes

### PyPI

PyPI does not provide a simple project-specific downloads dashboard in its main UI.

Practical options:

- Package page: <https://pypi.org/project/autocontext/>
- For official download analysis, use PyPI's BigQuery dataset

PyPI's `/stats/` API is global PyPI-wide data, not per-project package downloads.

## Dependents And "Used By"

GitHub dependency graph is the best built-in signal for public dependents.

What it can show:

- public repos that declare this repo or package as a dependency
- package ecosystem relationships when manifests are recognized

Important limitations:

- the "Used by" sidebar only appears in some cases
- it depends on dependency graph support and recognized manifests
- it is not a complete picture of all real-world usage

## Can We See Who Accessed The Repo?

Usually, no.

For a public GitHub repository:

- you can see aggregate repo traffic
- you generally cannot see exactly who viewed or cloned the repo

For organizations:

- org owners can review the organization audit log for actor and repository events
- that is useful for member/admin activity, not for identifying anonymous public viewers

## Practical Recommendations

- Check GitHub Traffic weekly and record the numbers somewhere durable if you care about trends.
- Watch npm for public package uptake.
- Use PyPI BigQuery if Python download counts become important enough to track regularly.
- Check GitHub dependency graph and dependents for public adopters.
- Do not expect individual-level viewer identity for public repository traffic.

## Useful References

- GitHub traffic docs: <https://docs.github.com/en/repositories/viewing-activity-and-data-for-your-repository/viewing-traffic-to-a-repository>
- GitHub traffic API docs: <https://docs.github.com/rest/metrics/traffic>
- GitHub dependency graph docs: <https://docs.github.com/en/code-security/supply-chain-security/understanding-your-software-supply-chain/about-the-dependency-graph?apiVersion=2022-11-28>
- GitHub org audit log docs: <https://docs.github.com/en/organizations/keeping-your-organization-secure/managing-security-settings-for-your-organization/reviewing-the-audit-log-for-your-organization>
- npm package page: <https://www.npmjs.com/package/autoctx>
- PyPI BigQuery docs: <https://docs.pypi.org/api/bigquery/>
- PyPI stats API docs: <https://docs.pypi.org/api/stats/>
