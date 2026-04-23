# Browser Exploration Contract

This document defines the shared browser exploration foundation used by both published packages:

- `autocontext` on PyPI
- `autoctx` on npm

The goal is to support browser exploration in a way that is thin, inspectable, and enterprise-safe.

## Design Guide

The architecture is inspired by thin browser-control projects such as `browser-harness`, but adapted to fit AutoContext's package model:

- shared contract, separate Python and TypeScript projections
- no heavyweight browser framework bundled into core
- security-first defaults
- explicit evidence and audit records
- compatibility with future thin CDP backends or optional external adapters

## Current Scope

This foundation currently includes the shared contract, settings, validation, policy layer, and thin Python and TypeScript Chrome/CDP attachment backends. It does not yet ship CLI investigation wiring.

Included:

- canonical JSON Schemas for browser session config, actions, snapshots, and audit events
- TypeScript validators and generated contract types
- mirrored Python schemas and generated Pydantic models
- drift checks that keep the TypeScript and Python contract projections aligned
- security-focused policy helpers for allowlists and auth-sensitive actions
- mirrored `AUTOCONTEXT_BROWSER_*` settings in both packages
- backend-agnostic session/runtime protocol types for future adapters
- Python and TypeScript evidence stores for browser audit and snapshot artifacts
- Python and TypeScript Chrome/CDP session wrappers for `navigate`, `snapshot`, `click`, `fill`, `press`, and `screenshot`
- Python and TypeScript WebSocket CDP transport and debugger target discovery from `/json/list`
- Python and TypeScript settings-backed runtime factories for attaching to an existing debugger target

Not yet included:

- CLI investigation wiring such as `investigate --browser-url <url>`
- browser process launching or lifecycle management
- domain-skill persistence
- broader scenario or operator-loop execution wiring
- uploads/downloads as first-class browser actions

## Contract Documents

The canonical schemas live under:

- `ts/src/integrations/browser/contract/json-schemas/`

They are mirrored into Python under:

- `autocontext/src/autocontext/integrations/browser/contract/json_schemas/`

The shared document types are:

- `BrowserSessionConfig`
- `BrowserAction`
- `BrowserSnapshot`
- `BrowserAuditEvent`

## Security Defaults

The shared defaults intentionally favor enterprise adoption:

- browser exploration is off by default
- profile mode defaults to `ephemeral`
- auth, uploads, and downloads default to `false`
- headless defaults to `true`
- screenshot capture defaults to `true` for auditability
- navigation is expected to use an explicit domain allowlist

Policy helpers currently enforce:

- exact and wildcard domain allowlists for navigation
- rejection of invalid navigation URLs
- rejection of inline-credential navigation when auth is disabled
- rejection of password-field fills when auth is disabled
- rejection of download/upload-enabled session configs without an explicit root
- rejection of `user-profile` mode unless auth is explicitly enabled

## Package Surfaces

TypeScript exposes the shared browser module at:

- `autoctx/integrations/browser`

Python exposes the matching validation and policy helpers under:

- `autocontext.integrations.browser`

Both surfaces are intentionally small and backend-agnostic so additional runtime implementations can be introduced later without changing the contract.

The current CDP implementations are intentionally attach-oriented:

- use `AUTOCONTEXT_BROWSER_DEBUGGER_URL` / `browserDebuggerUrl` to point at an existing Chrome debugger endpoint
- use `AUTOCONTEXT_BROWSER_PREFERRED_TARGET_URL` / `browserPreferredTargetUrl` to prefer a specific page when multiple allowed targets are present
- keep browser launch/orchestration separate from the contract so enterprise deployments can choose their own browser-management model

## Regeneration

TypeScript generated types:

```bash
cd ts
node scripts/generate-browser-contract-types.mjs
```

Python mirrored schemas and models:

```bash
cd ts
node scripts/sync-python-browser-contract-schemas.mjs
```

Drift checks:

```bash
cd ts
npm run check:browser-contract-schemas
```
