---
name: grid-ctf-ops
description: Operational knowledge for the grid_ctf scenario including strategy playbook, lessons learned, and resource references. Use when generating, evaluating, coaching, or debugging grid_ctf strategies.
---

# Grid Ctf Operational Knowledge

Accumulated knowledge from AutoContext strategy evolution.

## Operational Lessons

Prescriptive rules derived from what worked and what failed:

- Cross-tier parameter transfer is the #1 catastrophic failure mode. Parameters validated at one resource_density tier produce zero scores at different tiers. ALWAYS verify tier before parameter selection.
- When resource_density < 0.20 (critical_low), total commitment (aggression + defense) MUST stay ≤ 1.05. Target ≤ 1.00 for a 5% safety buffer. Exceeding by even 10% causes energy starvation and zero scores.
- When resource_density is moderate (0.40–0.60), total commitment ceiling is 1.20. Target 4–6% buffer (≤ 1.15). A 16% buffer wastes capacity.
- Defense must stay in [0.45, 0.55]. Below 0.45 risks base loss; above 0.55 starves capture progress.
- Aggression must be ≥ 0.48 to generate meaningful capture progress. Zero capture = zero score.
- The scoring formula (score ≈ capture + (efficiency - 0.5) × 0.39) makes efficiency extremely valuable. Losing 4% efficiency costs ~1.5 score points.
- Balanced strategy (agg=0.58, def=0.57, pb=0.55) achieved 0.7615 at density≈0.437, bias≈0.51 — moderate balanced parameters outperform extremes within the correct tier.
- Conservative baseline (agg=0.50, def=0.50, pb=0.48) scored 0.7198 at density≈0.147, bias≈0.648 — proven viable in critical_low tier.
- Over-aggression (agg ≥ 0.65) without proportional defense (≥ 0.52) causes defender survival drops and energy efficiency decline. Optimal moderate-tier aggression is [0.56, 0.60].
- Generation 3 rollback (agg=0.67, def=0.52, pb=0.60, score=0.7486) and Gen 2 rollback (agg=0.62, def=0.52, pb=0.58, score=0.7369) both confirm over-commitment underperforms.
- Perfect defender survival (1.00) signals defensive over-allocation. Optimal target is 0.95–0.99, freeing resources for capture.
- Incremental changes (±0.02 to ±0.05) from a proven baseline within the same resource tier are the only validated safe optimization method. Large jumps (±0.09+) led to rollbacks.
- After a zero score, RESET to the proven baseline for the current tier. Do NOT incrementally tweak failed parameters.
- Path_bias in low-resource environments: cap at 0.50. Concentrated force projection is energy-expensive.
- Path_bias for balanced enemy (bias ≤ 0.55): use [0.50, 0.55]. For asymmetric enemy (bias > 0.6): use [0.45, 0.50].
- Energy efficiency of 0.90 at commitment=1.00 in critical_low confirms the ceiling is accurate; incremental increases to 1.01–1.03 are viable.
- All validation tools (config_constants, energy_budget_validator, stability_analyzer, threat_assessor) MUST be run before deployment. Risk > 0.65 or stability < 0.45 predicts poor performance.
- Recovery priority after zero score: (1) non-zero capture, (2) defender survival, (3) energy sustainability, (4) optimize capture progress.
- The observation narrative is the authoritative source for environment data. Always read resource_density and enemy_spawn_bias from the actual observation state.
- When conditions exactly match a proven baseline, deploy it directly rather than converging incrementally.
- When aggression exceeds 0.7 without proportional defense, win rate drops.
- Defensive anchor above 0.5 stabilizes Elo across generations.
- Generation 2 ROLLBACK after 2 retries (score=0.7369, delta=-0.0461, threshold=0.005). Strategy: {"aggression": 0.62, "defense": 0.52, "path_bias": 0.58}. Narrative: Capture phase ended with progress 0.61, defender survival 0.96, and energy efficiency 0.87.. Avoid this approach.
- Generation 3 ROLLBACK after 2 retries (score=0.7339, delta=-0.0491, threshold=0.005). Strategy: {"aggression": 0.62, "defense": 0.52, "path_bias": 0.58}. Narrative: Capture phase ended with progress 0.61, defender survival 0.96, and energy efficiency 0.87.. Avoid this approach.
- Generation 4 ROLLBACK after 2 retries (score=0.7669, delta=-0.0161, threshold=0.005). Strategy: {"aggression": 0.62, "defense": 0.52, "path_bias": 0.58}. Narrative: Capture phase ended with progress 0.66, defender survival 0.96, and energy efficiency 0.87.. Avoid this approach.
- Generation 5 ROLLBACK after 2 retries (score=0.7396, delta=-0.0434, threshold=0.005). Strategy: {"aggression": 0.62, "defense": 0.52, "path_bias": 0.58}. Narrative: Capture phase ended with progress 0.62, defender survival 0.96, and energy efficiency 0.87.. Avoid this approach.

## Bundled Resources

- **Strategy playbook**: See [playbook.md](playbook.md) for the current consolidated strategy guide (Strategy Updates, Prompt Optimizations, Next Generation Checklist)
- **Analysis history**: `knowledge/grid_ctf/analysis/` — per-generation analysis markdown
- **Generated tools**: `knowledge/grid_ctf/tools/` — architect-created Python tools
- **Coach history**: `knowledge/grid_ctf/coach_history.md` — raw coach output across all generations
- **Architect changelog**: `knowledge/grid_ctf/architect/changelog.md` — infrastructure and tooling changes
