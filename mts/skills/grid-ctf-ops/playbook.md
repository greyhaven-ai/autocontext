## Strategy Updates

- **Primary Objective is Capture Progress**: Capture progress is the dominant scoring factor. A purely defensive posture guarantees failure. Always allocate enough aggression to advance toward the enemy flag.
- **Proven Baseline**: The best-scoring strategy so far is `{"aggression": 0.58, "defense": 0.57, "path_bias": 0.55}` (score 0.7615, capture progress 0.63, defender survival 1.00, energy efficiency 0.88). Any new strategy should be an incremental improvement from this anchor, not a radical departure.
- **Environment-Adaptive Tuning**: Current observation is enemy_spawn_bias=0.51 (nearly symmetric) and resource_density=0.437 (moderate). Adjust parameters to match:
  - With balanced enemy spawn, do NOT over-bias path. Keep path_bias in [0.52, 0.62].
  - With moderate resources, total commitment (aggression + defense) can safely reach 1.15–1.25 without energy starvation.
- **Aggression Sweet Spot**: Target aggression in [0.58, 0.68]. Below 0.55 yields insufficient capture progress. Above 0.70 without defense ≥ 0.45 causes win-rate collapse.
- **Minimum Viable Defense**: Always maintain defense ≥ 0.40 (hard constraint: at least one defender near base). Ideal range is [0.45, 0.55]. Above 0.55 starves capture progress. Below 0.40 risks instant loss.
- **Path Bias Rules**:
  - When enemy_spawn_bias > 0.6: set path_bias to min(1.0, (1 - enemy_spawn_bias) + 0.2) to exploit the weaker lane.
  - When enemy_spawn_bias is near 0.5 (balanced): set path_bias in [0.52, 0.62]. Over-biasing against a symmetric enemy wastes forces.
- **Resource-Aware Commitment**:
  - When resource_density < 0.2: cap aggression + defense at ~1.05.
  - When resource_density is 0.3–0.5: allow aggression + defense up to ~1.20.
  - When resource_density > 0.5: allow aggression + defense up to ~1.30.
- **Recommended Parameters for Current Observation** (enemy_spawn_bias=0.51, resource_density=0.437):
  ```json
  {
    "aggression": 0.65,
    "defense": 0.52,
    "path_bias": 0.58
  }
  ```
  - Sum: 0.65 + 0.52 = 1.17 ≤ 1.4 ✓
  - Defense ≥ 0.40 ✓
  - Aggression in sweet spot ✓
  - Path bias appropriate for near-symmetric enemy ✓
  - Increased aggression (+0.07) to push capture progress beyond 0.63
  - Slight defense reduction (-0.05) is safe given perfect defender survival last round

## Prompt Optimizations

- Return concise JSON with exactly three keys: `aggression`, `defense`, `path_bias`, all floats in [0,1].
- Validate constraint: aggression + defense ≤ 1.4 before submission.
- Use threat_assessor tool with the proposed aggression, defense, path_bias values. Reject if risk > 0.65.
- Use stability_analyzer tool with mobility_weight=aggression, corner_weight=path_bias, stability_weight=defense. Target stability_score ≥ 0.45.
- Always cross-reference proposed parameters against the strategy-score registry to ensure incremental improvement.

## Next Generation Checklist

- [ ] Verify capture progress improves beyond 0.63 (primary objective).
- [ ] Verify defender survival remains at 1.00 (hard constraint).
- [ ] Run threat_assessor: expected risk = 0.576 (< 0.65 ✓). Reject if > 0.65.
- [ ] Run stability_analyzer: expected score = 0.65*0.3 + 0.58*0.4 + 0.52*0.3 = 0.195 + 0.232 + 0.156 = 0.583 (≥ 0.45 ✓).
- [ ] If capture progress improves and defender survives, lock parameters and iterate ±0.03 for fine-tuning.
- [ ] If capture progress stalls, increase aggression by +0.03 (max 0.68) and decrease defense by -0.02 (min 0.45).
- [ ] If defender dies, increase defense by +0.05 and decrease aggression by -0.03.
- [ ] Test aggressive variant: aggression=0.68, defense=0.48, path_bias=0.58 (max push within safe bounds).
- [ ] Test conservative variant: aggression=0.60, defense=0.55, path_bias=0.55 (closer to proven baseline).
- [ ] If enemy_spawn_bias shifts above 0.6 in future observations, re-engage asymmetric path_bias formula.
