# CLI contract

[`cli-contract.json`](cli-contract.json) is the shared command contract for the Python and npm CLIs. It covers every public command exposed by either runtime. Commands implemented by only one runtime remain in the contract with an explicit `intentional_gap` reason for the other runtime; they are not kept in test allowlists.

Each command has two input layers:

- `positionals` and `flags` define the canonical, cross-runtime surface. A spelling listed here must be accepted by every runtime whose support status is `yes`.
- `runtime_shapes.python` and `runtime_shapes.typescript` record the complete public input shape of each implementation, including runtime-specific options and defaults.

The remaining version-2 fields make process behavior testable: `output` distinguishes single JSON values, NDJSON streams, and text; records stdout/stderr routing; and links concrete schemas or fixtures where a stable wire shape exists. `exit_codes` defines success, usage, and execution failures. `examples` contains canonical invocations.

## Migrating from version 1

Version-1 consumers can keep using the Python `load_contract` or TypeScript `loadContract` helpers. Both loaders supply compatibility defaults for fields that did not exist in version 1. Consumers that read the JSON directly should treat absent version-2 fields as follows:

| Field | Version-1 fallback |
| --- | --- |
| `positionals`, `flags`, `examples` | empty list |
| `runtime_shapes` | empty object |
| `output.modes` | the command's `output_contract`, or `text` |
| `output.streaming` | `single` |
| success/error streams | `stdout` / `stderr` |
| exit codes | success `0`, usage `2`, execution `1` |

New or changed version-2 entries must specify these fields explicitly. Contract tests reject partial version-2 commands and verify the referenced schemas and fixtures exist.
