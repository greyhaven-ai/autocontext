# Recovering Artifacts Written To Doubled Paths

Python releases 0.4.7 through 0.18.0 wrote some artifacts to a doubled path
when the artifact roots are relative, which is the default configuration
(AC-1033). For example, a generation's metrics landed at

```
runs/<run>/generations/gen_1/runs/<run>/generations/gen_1/metrics.json
```

instead of `runs/<run>/generations/gen_1/metrics.json`. Newer releases write
to the intended path but do not move files written earlier, so replay, RLM
context loading and trace-based reports cannot see them for older runs.
Configurations with absolute roots were not affected.

## What Was Affected

- Per-generation run artifacts: replays, `metrics.json`, `narrative.md` and,
  when enabled, `consultation.md`, `exploration_collapse_guard.json` and Pi
  session traces (0.4.7 through 0.18.0).
- Analytics traces under `knowledge/analytics/traces/` (0.4.8 through 0.18.0).
- The same files under `sandboxes/<id>/` for MCP sandbox runs.
- 0.4.7 through 0.5.0 only: most `knowledge/<scenario>/` files, such as hints,
  analysis, coach history and progress.

## Recover

Stop any autocontext process (runs, servers, MCP) that uses the directory.
Then, from the directory `autoctx` ran in (the one that contains `runs/` and
`knowledge/`), save the script below as `relocate_doubled_artifacts.py` and run
it with `python relocate_doubled_artifacts.py`. It only prints what it would
do. Run it again with `--apply` to move the files. It never overwrites an
existing file and is safe to run more than once. If you set custom relative
roots (for example `AUTOCONTEXT_RUNS_ROOT`), add their top-level directory names
to `SCAN`, and add the runs root and the knowledge root's `analytics/` directory
to `AUTO_MOVE` (for example `"myruns/"` and `"myknowledge/analytics/"`). Files
under a root that is missing from `AUTO_MOVE` are only reported.

```python
"""Move artifacts written to doubled paths (AC-1033) back to their intended paths.

Dry run by default; pass --apply to move files. Never overwrites an existing file.
"""

import shutil
import sys
from pathlib import Path

APPLY = "--apply" in sys.argv
SCAN = ("runs", "knowledge", "sandboxes")  # add custom relative root directories here
AUTO_MOVE = ("runs/", "sandboxes/", "knowledge/analytics/")  # add custom runs and analytics roots; others are only reported


def intended_path(path):
    parts = path.parts
    half = (len(parts) - 1) // 2
    if len(parts) % 2 == 0 or half < 2 or parts[:half] != parts[half : 2 * half]:
        return None
    return Path(*parts[half:])


for top in SCAN:
    if not Path(top).is_dir():
        continue
    for doubled in sorted(Path(top).rglob("*")):
        target = intended_path(doubled) if doubled.is_file() else None
        if target is None:
            continue
        if not target.as_posix().startswith(AUTO_MOVE):
            print(f"review manually: {doubled} -> {target}")
            continue
        if target.exists():
            print(f"conflict, left in place: {doubled} (already exists: {target})")
            continue
        print(f"{'moved' if APPLY else 'would move'}: {doubled} -> {target}")
        if APPLY:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(doubled), str(target))
            directory, stop = doubled.parent, target.parent
            while directory != stop and not any(directory.iterdir()):
                directory.rmdir()
                directory = directory.parent
```

## Notes

- `review manually` lines are `knowledge/<scenario>/` copies from 0.4.7
  through 0.5.0. A later release may already have written a current file at the
  intended path, and moving an old `hints.md` or `hint_state.json` into place
  brings old hints back into future prompts. Compare them and move by hand.
- `conflict` lines mean a file already exists at the intended path, usually
  because the run was resumed after an upgrade. The script leaves both copies.
- Weakness reports under `knowledge/<scenario>/weakness_reports/` for affected
  runs came from the fallback analyzer, because the trace could not be found.
  They are not regenerated. Dashboard writeups regenerate on demand once the
  trace is readable.
- Do not use `autoctx analytics rebuild-traces` to recover traces. It rebuilds
  from `events.ndjson` and overwrites both trace copies.
- With a blob store mirror enabled, mirrored keys for these files keep the
  doubled path. The script does not change the mirror.
