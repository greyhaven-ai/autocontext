# Stability — `autoctx/detectors/openai-ts`

**Stability level: stable** (API frozen until the next major version).

## Public surface

Symbols re-exported from `index.ts`:

| Symbol | Kind | Stability |
|--------|------|-----------|
| `plugin` | `DetectorPlugin` | stable |

`plugin` is a singleton that implements the `DetectorPlugin` contract from
`@autoctx/instrument-contract`. Its `id` is `@autoctx/detector-openai-ts`,
its `supports.language` is `"typescript"`, and its `supports.sdkName` is
`"openai"`.

## SDK version range (target codebase)

This detector instruments TypeScript/JavaScript source files that use:

```
openai >=4,<5
```

Detection is based on static AST analysis (tree-sitter). The detector does not
import or execute the `openai` npm package.

## Semantic caveats

1. **Import variants supported**: The detector handles three import styles:
   - Canonical: `import { OpenAI } from "openai"`
   - Namespace: `import * as openai from "openai"; new openai.OpenAI(...)`
   - Aliased: `import { OpenAI as OAI } from "openai"`
   All three produce equivalent `WrapExpressionEdit` output.

2. **AzureOpenAI refused with reason**: Constructor expressions `new AzureOpenAI(...)`
   are detected but refused via a `PluginAdvisory` with `reason: "azure-deferred"`.
   Azure support is deferred to a future sub-spec. The customer's file is left
   unchanged.

3. **Factory-function refusal**: If `new OpenAI(...)` appears as the sole
   return expression of a function body (arrow function or named function), the
   expression is refused via a `PluginAdvisory` with `reason: "factory-function"`.
   The customer must manually refactor the factory before instrumentation can
   proceed.

4. **Idempotency via lexical check**: If a constructor expression is already
   wrapped — i.e., the surrounding source contains `instrumentClient(` within
   the match context — the site is skipped and a `PluginAdvisory` with
   `reason: "already-wrapped"` is emitted instead. This prevents double-wrapping
   on repeated `autoctx instrument --apply` runs. Note the camelCase marker
   (`instrumentClient(`), which differs from the Python detector's
   `instrument_client(`.

## Breaking-change policy

This module follows **SemVer**. Any change to the `DetectorPlugin` contract
surface (e.g., a change to `produce()` return shape, `id`, `supports`, or
`treeSitterQueries`) that breaks registered plugin consumers requires a
**major version bump** of the `autoctx` npm package. Additions (new advisory
reason codes, new optional edit fields) are minor bumps. Internal refactors
are patch bumps.
