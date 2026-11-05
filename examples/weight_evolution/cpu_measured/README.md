# Measured CPU outcomes and bounded acceptance

Milestone 15 adds a separately pinned CPU process observation profile and a small
acceptance ledger. The ledger compares the unchanged `legacy-v1` runner with
`measured-v2` on the same fixed token-sum checkpoint and one to four practice
requests. Each profile has exactly one predeclared batch. GPU execution remains
deferred.

See [M15 validation](MILESTONE15_VALIDATION.md) and its
[machine-readable results](MILESTONE15_RESULTS.json) for measured acceptance,
compatibility checks and remaining gaps.

Run these commands from the repository's `autocontext/` Python package directory:

```bash
uv run python ../examples/weight_evolution/cpu_measured/build_bundle.py \
  --output ../runs/cpu-measured-bundle --requests 4
uv run python ../examples/weight_evolution/cpu_measured/run.py \
  --bundle ../runs/cpu-measured-bundle --output ../runs/cpu-measured-acceptance
uv run python ../examples/weight_evolution/cpu_measured/report.py \
  --bundle ../runs/cpu-measured-bundle --output ../runs/cpu-measured-acceptance
```

Building writes the deterministic checkpoint, requests, both runtime identities,
failure contract and asset lock; it performs no model inference. The default
schedule reserves eight forward calls, sixteen input tokens and eight output
tokens across two batches. Fewer requests select a fixed prefix of the same
four-request schedule. There are no candidate proposals, checkpoint edits,
confirmation or audit requests, external providers or arbitrary worker commands.

The output owns immutable `manifest.json`, `claims/`, `outcomes/`, `results/` and
terminal `report.json` records. Every claimed batch is charged its full reserved
capacity without refunds. Verified usage is reported separately and remains
unknown for failed or uncertain batches. Both profiles are scheduled even if the
first fails; the second is a separate comparison condition, never a retry.

Running again verifies retained evidence before proceeding. A saved outcome can
complete its missing parent result. A claimed batch without a saved outcome is
sealed as uncertain, keeps its full reservation and is never dispatched again.
Only a previously unclaimed profile may then execute. Missing observations are
never reconstructed after the fact. Unknown output files, changed requests,
checkpoints, source code, contracts or runtime identities fail closed.

Reporting requires the pinned local runtime and verifies either complete evidence
or a pending prefix. It opens only existing files, performs no worker inference
and writes no results. Pending result publication is explicit. Semantic parity
is available only when both batches have valid completed receipts; acceptance
also requires both parent results to have been published. The comparison uses
the native receipt semantic digest, which excludes elapsed time and other
telemetry. All measured process observations remain in the `measured-v2` outcome.

The measured profile retains bounded process facts and versioned failure text.
Missing measurements remain unknown; a signal, elapsed interval or cleanup action
does not establish an OOM event or explain a historical failure. The legacy
profile retains only the known `CpuRunnerError` class when that runner fails.
Unexpected runner or publication exceptions can leave an explicitly uncertain
claim. A process launch that never returns an owned process handle remains
uncertain; it cannot establish that no child was started. Cleanup keeps its first
observed error separately from the primary failure and counts any additional
cleanup errors without inventing exception-chain links. The new failure text
contract handles short-prompt and replacement-marker
collisions through explicit omission when needed; it is not a general-purpose
secret detector.

This is integration acceptance for a tiny CPU classifier. It does not expand the
M11–M14 search campaigns, change their policies or joint confirmation gate,
establish an adaptive-search advantage, recover the original M13 failure cause
or reproduce Qwen. Existing milestone source pins and retained evidence remain
separate. Deterministic failure fixtures belong to the test suite; these example
commands expose only the fixed successful-path model schedule.
