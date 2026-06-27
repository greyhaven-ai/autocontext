# Package split status

The only active package surfaces are the shipping packages:

- Python: `autocontext`
- TypeScript: `autoctx`
- Pi: `pi-autocontext`

Core/control split packages are deferred until a real release or downstream
consumer needs them. Do not add `packages/python/*`, `packages/ts/*`, topology
manifests, or facade barrels as placeholders; add a buildable package in the
same PR that publishes or consumes it.

See [`docs/core-control-package-split.md`](../docs/core-control-package-split.md).
