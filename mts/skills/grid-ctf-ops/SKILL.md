---
name: grid-ctf-ops
description: Operational knowledge for the grid_ctf scenario including strategy playbook, lessons learned, and resource references. Use when generating, evaluating, coaching, or debugging grid_ctf strategies.
---

# Grid Ctf Operational Knowledge

Accumulated knowledge from MTS strategy evolution.

## Operational Lessons

Prescriptive rules derived from what worked and what failed:

- When aggression exceeds 0.7 without proportional defense, win rate drops.
- Defensive anchor above 0.5 stabilizes Elo across generations.
- When aggression exceeds 0.7 without defense ≥ 0.45, win rate drops significantly. Keep aggression ≤ 0.68 unless defense is proportionally high.
- Defensive anchor below 0.40 violates the "at least one defender near base" constraint and risks instant loss.
- Defensive anchor above 0.55 starves capture progress and leads to zero score (primary objective failure).
- A score of 0.0000 indicates the strategy is too passive—aggression must be high enough to generate any capture progress at all.
- When resource_density < 0.2, total commitment (aggression + defense) should not exceed ~1.10; energy starvation causes mid-game collapse.
- Ignoring enemy_spawn_bias wastes forces against concentrated defenses. Always bias path toward the enemy's weaker lane.
- Path_bias formula: set to min(1.0, (1 - enemy_spawn_bias) + 0.2) to exploit asymmetric enemy positioning.
- Tools (threat_assessor, stability_analyzer) must be actively used to validate parameters before deployment—untested strategies fail silently.
- Balanced/mirrored force distribution against an asymmetric enemy is suboptimal; asymmetric response is required.
- Defensive anchor above 0.55 starves capture progress and leads to score stagnation or decline.
- A balanced strategy (aggression=0.58, defense=0.57, path_bias=0.55) achieved 0.7615 with perfect defender survival and 0.88 energy efficiency—proving that moderate, balanced parameters outperform extremes.
- Total commitment (aggression + defense) near 1.15 is effective when resource_density is moderate (~0.4). Higher commitment is sustainable with more resources.
- When resource_density < 0.2, total commitment (aggression + defense) should not exceed ~1.05; energy starvation causes mid-game collapse.
- When enemy_spawn_bias is near 0.5 (balanced), over-biasing path_bias (>0.65) wastes forces by concentrating against an evenly distributed enemy. Keep path_bias in [0.52, 0.62].
- When enemy_spawn_bias > 0.6, exploit the weaker lane with path_bias = min(1.0, (1 - enemy_spawn_bias) + 0.2).
- Capture progress of 0.63 with aggression=0.58 suggests headroom exists—incremental aggression increases (+0.05 to +0.07) should improve capture without destabilizing defense.
- Perfect defender survival (1.00) with defense=0.57 indicates defense can be safely reduced by 0.03–0.05 to fund more aggression, as long as it stays above 0.45.
- Incremental parameter changes (±0.03 to ±0.07) from a proven baseline are safer and more informative than large jumps.
- Energy efficiency of 0.88 confirms the strategy is not over-committing resources; slight increases in total commitment are viable.
- Generation 3 ROLLBACK after 2 retries (score=0.7486, delta=-0.0333, threshold=0.005). Strategy: {"aggression": 0.67, "defense": 0.52, "path_bias": 0.6}. Narrative: Capture phase ended with progress 0.64, defender survival 0.94, and energy efficiency 0.85.. Avoid this approach.

## Bundled Resources

- **Strategy playbook**: See [playbook.md](playbook.md) for the current consolidated strategy guide (Strategy Updates, Prompt Optimizations, Next Generation Checklist)
- **Analysis history**: `knowledge/grid_ctf/analysis/` — per-generation analysis markdown
- **Generated tools**: `knowledge/grid_ctf/tools/` — architect-created Python tools
- **Coach history**: `knowledge/grid_ctf/coach_history.md` — raw coach output across all generations
- **Architect changelog**: `knowledge/grid_ctf/architect/changelog.md` — infrastructure and tooling changes
