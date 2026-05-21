# Hermes `autocontext` skill: installation and updates (AC-712)

This is the install / update / versioning reference for the
Hermes-format `autocontext` skill. Use it after you've decided to
wire Hermes to autocontext (see
[hermes-positioning.md](./hermes-positioning.md) for the product
story).

## What ships, where

The single source of truth for the skill content is the Python
helper `autocontext.hermes.skill.render_autocontext_skill()`. A
committed snapshot of its output lives at the repo root:

```
skills/autocontext/
├── SKILL.md
└── references/
    ├── hermes-curator.md
    ├── cli-workflows.md
    ├── mcp-workflows.md
    └── local-training.md
```

CI pins the snapshot to the renderer (see
`autocontext/tests/test_hermes_skill_distribution.py`); a drift
between the two fails the test job.

## Install options

Three install paths are supported. Pick the one that matches how
your Hermes home is provisioned.

### Option A: install via the autocontext CLI (recommended)

If `autoctx` is on the host that runs Hermes, the CLI is the
shortest path. It always renders the current source-of-truth
content (no chance of stale snapshots):

```bash
# Install the skill plus its progressive-disclosure references
uv run autoctx hermes export-skill \
    --output ~/.hermes/skills/autocontext/SKILL.md \
    --with-references \
    --json
```

`--with-references` writes the four reference files into a sibling
`references/` directory. Without it, only `SKILL.md` is written.

Update path: re-run with `--force` to overwrite. The atomic
preflight (AC-702 follow-up) rejects partial updates so the skill
bundle is never half-installed.

### Option B: install from the committed snapshot (no autoctx needed)

If the Hermes host doesn't have Python / `uv`, install from the
committed snapshot via raw URLs:

```bash
# One-off install (single file)
mkdir -p ~/.hermes/skills/autocontext
curl -fsSL \
  https://raw.githubusercontent.com/greyhaven-ai/autocontext/main/skills/autocontext/SKILL.md \
  -o ~/.hermes/skills/autocontext/SKILL.md

# Optional: install the references too
mkdir -p "$HOME/.hermes/skills/autocontext/references"
for ref in hermes-curator cli-workflows mcp-workflows local-training; do
  curl -fsSL \
    "https://raw.githubusercontent.com/greyhaven-ai/autocontext/main/skills/autocontext/references/${ref}.md" \
    -o "$HOME/.hermes/skills/autocontext/references/${ref}.md"
done
```

Update path: re-run the same `curl` commands. Pin a specific commit
SHA in the URL instead of `main` if you want reproducible installs.

### Option C: shallow clone the skill directory

For installs that already use `git` for fleet management:

```bash
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/greyhaven-ai/autocontext.git /tmp/autocontext
cd /tmp/autocontext && git sparse-checkout set skills/autocontext

mkdir -p ~/.hermes/skills/autocontext
cp -r skills/autocontext/* ~/.hermes/skills/autocontext/
```

Update path: `git pull` the sparse checkout, then `cp -r` again.

## Reloading after install

Hermes loads skills at startup and (if your Hermes version supports
it) on `/reload-skills`. Use whichever your Hermes build supports:

```bash
# In a Hermes session:
/reload-skills
```

Or restart the Hermes runtime so it re-scans `~/.hermes/skills/`.

## Versioning

The skill's `version` field lives in the SKILL.md frontmatter:

```yaml
---
name: autocontext
description: ...
version: 1.0.0
---
```

The committed snapshot and the renderer share the same value, so
checking the installed file's `version` matches the upstream by
diffing against the committed copy at the same commit SHA.

There is no separate version manifest; the SKILL.md is its own
manifest. When the rendered skill changes (new sections, new
references, prompt revisions), bump `version` in
`autocontext/src/autocontext/hermes/skill.py` first; the CI
sync test will then force the committed snapshot to be regenerated.

## Update guidance

Treat the installed skill as a cache, not a source. If you edit
`~/.hermes/skills/autocontext/SKILL.md` locally, `autoctx hermes
export-skill --force` will overwrite your edits silently — keep
local customizations in a _separate_ skill name (e.g.
`autocontext-local`) so updates to the upstream don't fork.

To check whether your installed copy is current:

```bash
# Compare your installed SKILL.md to the committed snapshot
diff -u ~/.hermes/skills/autocontext/SKILL.md \
    /path/to/autocontext/skills/autocontext/SKILL.md
```

## Upstream / registry distribution (out of scope for AC-712)

Two paths are deferred to follow-up tickets:

- **Hermes bundled / optional skills**: submit `skills/autocontext/`
  upstream so a fresh Hermes install gets it for free. Requires
  Hermes maintainer review; not yet engaged.
- **`agentskills.io` / Hermes skill hub**: publish under the
  shared registry if/when one stabilizes. The committed snapshot in
  this repo is structured to be the publishable artifact when that
  path opens.

Until those land, options A–C above are the supported install
paths.

## What this skill is _not_

- Not a substitute for Hermes Curator. The skill teaches an
  agent to use autocontext; Curator still owns Hermes skill
  lifecycle. See [hermes-positioning.md](./hermes-positioning.md).
- Not a network dependency at runtime. After install,
  `~/.hermes/skills/autocontext/` is local-only; autocontext does
  not reach back to this repo for updates.
- Not a substitute for the read-only importers (AC-704 / AC-705
  / AC-706) or the advisor surface (AC-708 / AC-709). Those ship
  via `autoctx hermes <subcommand>`; the skill just teaches the
  agent when to invoke them.
