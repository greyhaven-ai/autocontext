# Fixture loader (AC-767)

`autocontext` can pre-fetch external reference data ("authoritative ground truth")
before generation 1 starts, so downstream agents see the right canonical values
up front instead of inferring or hallucinating them.

This is different from:

- `bootstrap/` which captures the **local** environment snapshot.
- `analytics/regression_fixtures.py` which synthesizes fixtures **from friction**.

The fixture loader fetches from an external URL or local path, checksum-verifies,
caches, and threads a rendered summary into the agent prompts.

## Quick start

1. Enable the feature flag:

   ```bash
   export AUTOCONTEXT_FIXTURE_LOADER_ENABLED=true
   ```

2. Create a manifest at `autocontext/knowledge/<scenario>/fixtures.json`:

   ```json
   {
     "entries": [
       {
         "key": "challenge_data",
         "source": "https://example.com/challenge.txt",
         "expected_sha256": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
       },
       {
         "key": "local_vectors",
         "source": "/absolute/path/to/test_vectors.json"
       }
     ]
   }
   ```

3. Run autocontext as usual. At gen 1 the loader will fetch each entry, store
   it under `autocontext/knowledge/.fixture-cache/<scenario>/<key>.bin` with a
   `<key>.provenance.json` sidecar, and inject a `## Available fixtures` block
   into agent prompts.

## Manifest format

| Field             | Required | Notes                                                                                                                                                                    |
| ----------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `key`             | yes      | Safe identifier: `^[A-Za-z0-9_][A-Za-z0-9_.\-]*$`. Path traversal is rejected.                                                                                           |
| `source`          | yes      | `http(s)://` URL, `file://` URL, or absolute local path.                                                                                                                 |
| `expected_sha256` | no       | 64-char hex digest. If present, every fetch and every cache read is verified against it; mismatch raises `FixtureChecksumError` (fetch) or invalidates the cache (read). |

Missing manifest is a graceful no-op — no error, no event. Same for an empty
`entries` list.

## Cache semantics

- **Cache hit + checksum match** → no network call, no fetcher invocation.
- **Cache hit + cached `.bin` corrupted (sha mismatch vs provenance)** →
  cache treated as missing, full refetch.
- **Cache hit + manifest source changed** → refetch (the source URL is part
  of the freshness check even when no `expected_sha256` is provided).
- **Cache hit + manifest `expected_sha256` changed and old payload matches
  the new value** → still treated as fresh (your manifest pinned the new
  hash to the old bytes, which is presumably what you meant).
- **Fetched body fails `expected_sha256`** → raises `FixtureChecksumError`;
  cache is not updated.
- **Fetcher cannot retrieve the source** → raises `FixtureFetchError`.

## How agents see it

`stage_preflight.py` calls `render_fixtures(fixtures)` and assigns the result to
`ctx.fixtures_section`. The prompt-budget pipeline
(`prompts/context_budget.py`) gives the `fixtures` component an 800-token cap
and trims it after `environment_snapshot` if the budget tightens.
`prompts/templates.py` injects the rendered block between the environment
snapshot and the playbook, so it appears in the competitor / analyst / coach /
architect prompts.

For programmatic access, agents can read `ctx.fixtures[key].bytes_` directly.

## Programmatic API

```python
from autocontext.loop.fixture_loader import (
    FixtureManifest,
    FixtureCache,
    UrlFetcher,
    load_fixtures,
    load_scenario_fixtures,
    render_fixtures,
)
```

- `FixtureManifest.from_json(path)` — parse a manifest file; missing path → empty.
- `load_fixtures(manifest, *, fetcher, cache, scenario)` — low-level orchestration.
- `load_scenario_fixtures(scenario, *, knowledge_root, cache_root, fetcher=None)`
  — convenience that reads `<knowledge_root>/<scenario>/fixtures.json` and uses
  `UrlFetcher` by default.
- `render_fixtures(fixtures)` — emit the `## Available fixtures` prompt block.

## Settings

| Setting                  | Default | Description                                   |
| ------------------------ | ------- | --------------------------------------------- |
| `fixture_loader_enabled` | `False` | Master switch; the loader is silent when off. |

The cache directory is fixed at `.fixture-cache` under the knowledge root.
