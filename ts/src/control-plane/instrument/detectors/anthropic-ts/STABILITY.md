# Stability — `autoctx/detectors/anthropic-ts`

**Stability level: stable** (API frozen until the next major version).

## Public surface

Symbols re-exported from `index.ts`:

| Symbol | Kind | Stability |
|--------|------|-----------|
| `plugin` | `DetectorPlugin` | stable |

`plugin` is a singleton that implements the `DetectorPlugin` contract from
`@autoctx/instrument-contract`. Its `id` is `@autoctx/detector-anthropic-ts`,
its `supports.language` is `"typescript"`, and its `supports.sdkName` is `"anthropic"`.

## SDK version range (target codebase)

This detector instruments TypeScript/JavaScript source files that use:

```
@anthropic-ai/sdk >=0.18,<2.0
```

Detection is based on static AST analysis (tree-sitter). The detector does not
import or execute the `@anthropic-ai/sdk` package.

## Semantic caveats

1. **Import variants supported**: The detector handles three import styles:
   - Named: `import { Anthropic } from "@anthropic-ai/sdk"`
   - Namespace: `import * as anthropic from "@anthropic-ai/sdk"; new anthropic.Anthropic()`
   - Aliased named: `import { Anthropic as AC } from "@anthropic-ai/sdk"`
   All three produce equivalent `WrapExpressionEdit` output.

2. **AnthropicBedrock and AnthropicVertex refused with reason**: Constructor calls
   to `AnthropicBedrock` or `AnthropicVertex` are detected but refused via a
   `PluginAdvisory` with `kind: "deferred-sdk-variant"`. These SDK variants are
   deferred to separate sub-specs. The customer's file is left unchanged.

3. **Factory-function refusal**: If `new Anthropic(...)` appears as the sole return
   expression of a function (e.g., `function make() { return new Anthropic(...) }`),
   the call is refused via a `PluginAdvisory` with `reason: "factory-function"`.
   The customer must manually refactor the factory before instrumentation can
   proceed.

4. **Idempotency via lexical check**: If a constructor call site is already
   wrapped — i.e., the surrounding source contains `instrumentClient(` within
   the match context — the site is skipped and a `PluginAdvisory` with
   `reason: "already-wrapped"` is emitted instead. This prevents double-wrapping
   on repeated `autoctx instrument --apply` runs.

## Breaking-change policy

This module follows **SemVer**. Any change to the `DetectorPlugin` contract
surface (e.g., a change to `produce()` return shape, `id`, `supports`, or
`treeSitterQueries`) that breaks registered plugin consumers requires a
**major version bump** of the `autoctx` npm package. Additions (new advisory
reason codes, new optional edit fields) are minor bumps. Internal refactors
are patch bumps.
