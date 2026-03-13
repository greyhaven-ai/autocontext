## Strategy Updates

### Tier Classification (MANDATORY FIRST STEP)
- **Read resource_density from observation state before ANY parameter selection.**
- Critical_low tier: resource_density < 0.20 → commitment ceiling 1.05, target ≤ 1.00.
- Low tier: resource_density 0.20–0.39 → commitment ceiling 1.10, target ≤ 1.05.
- Moderate tier: resource_density 0.40–0.60 → commitment ceiling 1.20, target ≤ 1.15.
- High tier: resource_density > 0.60 → commitment ceiling 1.40, target ≤ 1.35.

### Current Environment: Critical_Low Tier
- Resource density: 0.147 (critical_low)
- Enemy spawn bias: 0.648 (asymmetric)
- **Best proven strategy for this tier:**

```json
{
  "aggression": 0.56,
  "defense": 0.48,
  "path_bias": 0.52
}
```
- Total commitment: 1.04 (within ceiling 1.05)
- Best score: 0.7374 at density≈0.147, bias≈0.648

### Tier-Specific Baselines (Deploy When Conditions Match)

| Tier | Baseline | Commitment | Best Score | Conditions |
|------|----------|------------|------------|------------|
| Critical_low (conservative) | {0.50, 0.50, 0.48} | 1.00 | 0.7198 | density≈0.147, bias≈0.648 |
| Critical_low (optimized) | {0.56, 0.48, 0.52} | 1.04 | 0.7374 | density≈0.147, bias≈0.648 |
| Moderate | {0.58, 0.57, 0.55} | 1.15 | 0.7615 | density≈0.437, bias≈0.51 |

### Next Optimization Direction for Critical_Low
The current best (0.56/0.48/0.52) scores 0.7374 but has two risk factors:
1. Defense at 0.48 is at the lower edge — increasing to 0.50 may improve defender survivability and efficiency
2. Path_bias at 0.52 exceeds the critical_low guideline of ≤0.50 — reducing may save energy

**Primary exploration target for Generation 3:**
```json
{
  "aggression": 0.55,
  "defense": 0.50,
  "path_bias": 0.48
}
```
- Total commitment: 1.05 (at ceiling, maximizes resource utilization)
- Rebalances defense upward (+0.02) while keeping commitment maximal
- Path_bias reduced to tier-appropriate 0.48
- Expected: improved efficiency from better defense allocation, maintained capture progress

**Secondary exploration target (if primary fails):**
```json
{
  "aggression": 0.52,
  "defense": 0.50,
  "path_bias": 0.48
}
```
- Total commitment: 1.02 (safe 3% buffer)
- Minimal deviation from conservative baseline
- Fallback if rebalancing doesn't improve score

### Defensive Anchor Rule
- Always maintain at least one defender near base (hard constraint).
- Defense parameter must stay in [0.45, 0.55] for most tiers.
- **Exception: moderate tier allows defense up to 0.57 when total commitment stays ≤ 1.15** (proven at 0.7615 score).
- Perfect defender survival (1.00) signals over-allocation; target 0.95–0.99.
- Defense at 0.48 is acceptable but 0.50 is preferred for critical_low tier.

### Aggression Guidelines
- Minimum aggression: 0.48 (below this, capture progress approaches zero).
- Moderate tier optimal aggression range: [0.56, 0.60].
- Critical_low tier proven aggression range: [0.50, 0.56].
- Critical_low tier aggression cap: 0.57 (commitment constraint limits further increase).
- Never exceed 0.65 aggression without defense ≥ 0.52.

### Path Bias Rules
- Balanced enemy (bias ≤ 0.55): path_bias in [0.50, 0.55].
- Asymmetric enemy (bias > 0.60): path_bias in [0.45, 0.50].
- In critical_low tier, prefer path_bias ≤ 0.50 to conserve energy.
- Note: path_bias 0.52 worked at 0.7374 in critical_low, so the cap is a guideline not a hard rule.
- In moderate tier with balanced enemy, 0.55 is proven optimal.

### Commitment Budget Rules
- Total commitment = aggression + defense.
- Must not exceed tier ceiling (hard constraint).
- Should target tier target value (soft constraint, provides safety buffer).
- Buffer > 16% of ceiling wastes capacity. Buffer < 4% risks energy starvation.
- Optimal buffer for critical_low: 0–5% below ceiling (0.00–0.05 headroom).
- Optimal buffer for moderate/high tiers: 4–6% below ceiling.
- At critical_low, commitment of 1.04 is proven safe. Commitment of 1.05 is at ceiling but viable.

### BLOCKED Strategies (Do Not Reuse)
- {agg: 0.62, def: 0.52, pb: 0.58} — failed 4+ consecutive times in critical_low tier (commitment 1.14 exceeds critical_low ceiling of 1.05). Causes energy starvation and score regression. Also suboptimal in moderate tier.

### Optimization Protocol
- When conditions exactly match a proven baseline and no improvement is needed, deploy directly.
- When optimizing, use incremental changes: ±0.02 per generation from best proven strategy within same tier.
- After any zero score, RESET to proven baseline for current tier immediately.
- After rollback, do NOT re-deploy the rolled-back strategy. Choose a different direction.
- Track which parameter adjustments improve vs. degrade: build directional knowledge.

## Prompt Optimizations
- Return concise JSON with three keys: `aggression`, `defense`, `path_bias`.
- Validate hard constraint: aggression + defense ≤ 1.4 (system limit).
- Validate tier-specific constraint before submission.
- Run energy_budget_validator and threat_assessor before every deployment.

## Next Generation Checklist
1. ☐ Read resource_density and enemy_spawn_bias from observation state.
2. ☐ Classify tier (critical_low / low / moderate / high).
3. ☐ Check if conditions match a proven baseline — use best proven as starting point.
4. ☐ Select parameters appropriate for tier, applying ±0.02 incremental optimization.
5. ☐ Verify total commitment ≤ tier ceiling AND within acceptable buffer range.
6. ☐ Verify path_bias matches enemy symmetry level and tier guidelines.
7. ☐ Run energy_budget_validator: must pass with no FAIL warnings.
8. ☐ Run threat_assessor: risk must be ≤ 0.65.
9. ☐ Confirm defense ∈ [0.45, 0.57] (upper bound applies only in moderate tier with proven justification).
10. ☐ Confirm aggression ≥ 0.48.
11. ☐ If optimizing, change ≤ 0.02 from last successful baseline in same tier.
12. ☐ Verify strategy is not in BLOCKED list.
