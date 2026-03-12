# Demo Data

This directory can hold pre-generated runs for demos where live execution time is constrained.

Use:

```bash
uv run mts run --scenario grid_ctf --gens 3 --run-id demo_seed_grid
uv run mts run --scenario othello --gens 2 --run-id demo_seed_othello
```

Then copy selected run folders from `runs/` into this directory if needed for offline presentations.
