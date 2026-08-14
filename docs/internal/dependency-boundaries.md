# Source Dependency Boundaries

`scripts/check_dependency_boundaries.py` scans Python and TypeScript source
imports and enforces the directional rules in
`scripts/dependency-boundaries.json`.

## Dependency directions

- TypeScript execution may orchestrate judge; shared parsers live under
  `ts/src/domain`, so judge does not import execution.
- TypeScript agents may consume providers; provider implementations remain
  under `ts/src/providers`, so providers do not import agents.
- TypeScript control-plane may adapt production-traces; production-traces
  exposes ports such as `RubricLookup` and does not import control-plane.
- Python knowledge may compose storage; storage must not import knowledge
  implementations. The remaining `context_selection_store.py` import is an
  explicit legacy allowance to retire separately.
- Python agents may consume config; generated routing contracts are config-owned,
  so config does not import agents.

## Ratchet policy

Every allowed legacy edge names both its source file and imported module. New
reverse edges fail with the exact source file, line, and import path. When a
legacy edge is removed, the check also fails until its allowance is deleted;
this prevents the baseline from growing or retaining obsolete exceptions.

Run the complete check from the repository root:

```bash
python3 scripts/check_dependency_boundaries.py
```

Use `--runtime python` or `--runtime typescript` for a focused scan.
