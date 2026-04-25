# Package Topology

This directory is the source of truth for the phase-one package topology of the
core/control split.

## Ubiquitous language

- **Umbrella package**: the user-facing compatibility distribution that keeps
  the current install and CLI entrypoints stable.
- **Core package**: the foundational runtime artifact that will remain Apache
  compatible.
- **Control package**: the higher-level operator and management artifact that
  will carry the separately licensed control-plane surfaces.
- **Compatibility shell**: the thin layer that delegates from the old package
  roots to the new artifacts while migration is in progress.

The canonical machine-readable map lives in `package-topology.json`.
