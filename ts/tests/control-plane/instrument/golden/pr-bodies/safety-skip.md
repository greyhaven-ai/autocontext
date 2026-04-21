## Autocontext instrument — 1 files affected, 1 call sites wrapped

Command: `autoctx instrument --dry-run --exclude tests/** session=01HN0000000000000000000009`
Session: `01HN0000000000000000000009` · Generated at `2026-04-19T12:00:00.000Z` by `autocontext v0.0.0-golden`

### Summary by SDK
- **openai**: 1 call site wrapped

### Files affected
#### `src/clean.py` (+1 changes)
**Before:**
```python
OpenAI()
```
**After:**
```python
instrument_client(OpenAI())
```
*Rationale: Wraps the openai client construction with `instrument_client(...)` so every call through this client emits an Autocontext trace.*

### Files skipped
| Path | Reason |
| --- | --- |
| `src/opted_out.py` | all edits dropped by off directives |
| `src/secrets_file.py` | refusing to instrument src/secrets_file.py: matched Aws Access Key pattern at line 2. Review and relocate secrets before re-running. |

### Detected but unchanged
| Path | Plugin | Reason |
| --- | --- | --- |
| `src/opted_out.py` | `mock-openai-python` | all edits dropped by off directives |
| `src/secrets_file.py` | `mock-openai-python` | refusing to instrument src/secrets_file.py: matched Aws Access Key pattern at line 2. Review and relocate secrets before re-running. |

### How to apply
```bash
# Review the patches first:
ls .autocontext/instrument-patches/01HN0000000000000000000009/patches/

# Apply in-place (requires a clean working tree, or --force):
autoctx instrument --apply

# Or create a fresh branch + commit:
autoctx instrument --apply --branch autocontext-instrument --commit 'Instrument LLM clients'
```

### How to opt out
- Per-line: add `# autocontext: off` on the line **above** the client construction.
- Per-file: add `# autocontext: off-file` near the top of the file (re-enable with `# autocontext: on-file`).
- Per-path: use `--exclude <glob>` or `--exclude-from <file>`.

### Audit fingerprint
- Session: `01HN0000000000000000000009`
- Session-plan hash: `sha256:a1d4bb0740fe81fc0fd0b9b32faeeeb82b4596670d2c3cbbb876149e6b95c478` (of `plan.json`)
- Autoctx version: `0.0.0-golden`
- Registered plugins: `mock-openai-python@0.0.0`
- `.gitignore` rev: `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
