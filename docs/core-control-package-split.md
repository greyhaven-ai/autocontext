# Core/Control Package Split

Status: **deferred**. AC-838 removed the placeholder core/control packages and
machine-readable split manifests because nothing publishes or consumes those
artifacts yet.

## Current Package Surfaces

Keep the existing install paths as the source of truth:

- Python: `autocontext`, including the `autoctx` CLI entrypoint.
- TypeScript: `autoctx`, owning runtime, control-plane, and CLI exports for now.
- Pi: `pi-autocontext`, depending on the shipping `autoctx` package.

## Guardrails

- Existing public repo code stays under its current open-source licensing.
- Do not add dual-license metadata or non-Apache split-package license files for
  historical code.
- Do not recreate `packages/python/*`, `packages/ts/*`, or split manifests as
  planning scaffolding.
- Add a split package only when the same change builds it, documents migration,
  and either publishes it or wires a real consumer to it.

## Agent App Build Targets

Generated agent apps remain control-plane packaging around the current
`autoctx/agent-runtime` surface. The Node target may keep shipping through the
umbrella CLI. Edge-style targets stay spikes until the Node path proves which
runtime contracts are actually reusable.

Reusable runtime contracts stay umbrella-owned in `autoctx` until an extraction
PR has a concrete consumer. Hosted fleet orchestration remains separate product
work, not package-split scaffolding in this repo.

## Deferred Split Checklist

When a real split is needed, keep it small:

1. Start from one downstream import or release artifact.
2. Add the minimum package files needed to build it.
3. Preserve `autocontext`, `autoctx`, and `pi-autocontext` compatibility.
4. Add tests for that package's exact public surface.
5. Leave future packages uncreated.

See also
[`knowledge-production-trace-boundary-map.md`](./knowledge-production-trace-boundary-map.md)
for the similarly deferred knowledge/trace extraction notes.
