# Analytics And Adoption

Use this guide to answer a few common maintainer questions:

- How much interest is the repo getting?
- Which package ecosystems are seeing usage?
- Can we see which projects depend on the repo or packages?
- Can we identify who accessed the repo?

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

- Package page: <https://pypi.org/project/autoctx/>
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
