# traces/

Public trace schema, data-plane curation, dataset adapters/discovery,
distillation pipeline, redaction, and sharing/publishing workflows.

Maps to these Python homes:

- `production_traces/` (contract, taxonomy, emit, redaction/validate):
  `public-schema*.ts`, `redaction*.ts`, `trace-ingest-workflow.ts`
- `sharing/` (attestation, bundle, manifest, publishers, review):
  `publishers*.ts`, `publishing-workflow.ts`, `export-*-workflow.ts`
- `training/autoresearch/` (data selection, augmentation, distillation):
  `distillation-*.ts`, `dataset-*.ts`

Distinct from `ts/src/production-traces/`, which is the direct TS mirror
of `production_traces/` (contract, ingest, redaction, dataset, cli
layers). This directory predates that split and covers the broader
surface above; see `docs/knowledge-production-trace-boundary-map.md` for
the "leave behavior where it already works" rule that keeps both
directories shipping rather than merging them.
