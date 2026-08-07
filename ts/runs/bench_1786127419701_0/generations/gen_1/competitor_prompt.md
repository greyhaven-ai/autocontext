Describe your strategy for the grid_ctf scenario. Return JSON with the strategy parameters.

Scenario Rules:
20x20 capture-the-flag map with fog of war and three unit archetypes (Scout, Soldier, Commander). Preserve at least one defender near base.

Strategy Interface:
Return JSON object with keys `aggression`, `defense`, and `path_bias`, all floats in [0,1]. Constraint: aggression + defense <= 1.4.

Evaluation Criteria:
Primary objective is capture progress. Secondary objectives are defender survivability and resource efficiency.

Current Playbook:
<!-- PLAYBOOK_START -->
## Strategy Updates

- Keep defensive anchor.
- Balance aggression with proportional defense.

<!-- PLAYBOOK_END -->

<!-- LESSONS_START -->
- When aggression exceeds 0.7 without proportional defense, win rate drops.
<!-- LESSONS_END -->

<!-- COMPETITOR_HINTS_START -->
- Try aggression=0.60 with defense=0.55 for balanced scoring.
<!-- COMPETITOR_HINTS_END -->


Prior Session Reports:
# Session Report: bench_1786127419253_0
**Scenario:** grid_ctf | **Duration:** 0s

## Results
- Score: 0.7340 → 0.7340 (Δ +0.0000)
- Elo: 1014.3 → 1014.3
- Generations: 1 (1 advances, 0 retries, 0 rollbacks)
- Exploration mode: linear

## Top Improvements
| 1 | +0.7340 | Score improved to 0.7340 |

## Dead Ends Discovered
0 dead ends identified.

[... condensed recent history ...]

Respond with JSON only. Include the strategy fields required by the strategy interface.
