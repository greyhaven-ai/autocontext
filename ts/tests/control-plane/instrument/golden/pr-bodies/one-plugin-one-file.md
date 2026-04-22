## Autocontext instrument — 1 files affected, 1 call sites wrapped

Command: `autoctx instrument --dry-run session=01HN0000000000000000000009`
Session: `01HN0000000000000000000009` · Generated at `2026-04-19T12:00:00.000Z` by `autocontext v0.0.0-golden`

### Summary by SDK
- **openai**: 1 call site wrapped

### Files affected
#### `src/chat.py` (+1 changes)
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
_No files skipped._

### Detected but unchanged
_No detections were filtered by safety / directives / opt-outs._

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
- Session-plan hash: `sha256:59138e767e1568b1896e14be4e36d05199b69cc8ba981f46f019cc4279a8dfb8` (of `plan.json`)
- Autoctx version: `0.0.0-golden`
- Registered plugins: `mock-openai-python@0.0.0`
- `.gitignore` rev: `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`
