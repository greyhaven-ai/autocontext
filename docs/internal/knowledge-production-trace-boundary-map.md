# Knowledge and Production Trace Boundary Notes

Status: **deferred**. This file is no longer a row-by-row extraction plan for
placeholder core/control packages. The current product ships through the
umbrella packages, and AC-838 intentionally removed package-split scaffolding.

## Current Rule

Keep knowledge and production-trace behavior where it already works until a real
package or downstream consumer needs a narrower surface.

- Runtime knowledge helpers keep their existing `autocontext.*` and `autoctx`
  compatibility paths.
- Production trace schemas and emit helpers keep their current umbrella exports.
- CLI, server, MCP, import/export, dataset, retention, and publishing workflows
  stay in the shipping packages until a concrete extraction PR moves one slice.

## Future Extraction Test

Only extract a slice when the PR can answer all of these:

1. Which consumer or release artifact needs the split now?
2. What is the smallest public surface to build and test?
3. Which umbrella import path stays as the compatibility shim?
4. Which workflows are explicitly not moving?

If the answer is just “future package hygiene,” leave the code where it is.
