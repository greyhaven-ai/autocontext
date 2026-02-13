## Strategy Updates

### Core Principles
- **Resource-adaptive commitment**: Total commitment (aggression + defense) ceiling scales with resource_density. See commitment ceiling table below.
- **Defensive anchor is non-negotiable**: Defense must remain in [0.45, 0.55]. Below 0.45 risks base loss; above 0.55 starves capture progress.
- **Primary objective is capture progress**: Aggression must be ≥ 0.48 to generate meaningful capture.
- **Energy conservation in scarce environments**: When resource_density < 0.2, total commitment MUST stay ≤ 1.05. Energy starvation causes mid-game collapse and zero scores. This is the #1 failure mode.
- **Enemy symmetry awareness**: Adapt path_bias to enemy_spawn_bias using the selection table below.
- **Incremental improvement from proven baselines**: Change parameters by ±0.03 to ±0.06 per generation. Large jumps risk regressions.

### Resource-Adaptive Commitment Ceilings
| resource_density | Max total commitment | Aggression range | Defense range | Notes |
|-----------------|---------------------|-----------------|--------------|-------|
| < 0.20          | **1.05**            | 0.48–0.53       | 0.45–0.52    | CRITICAL: Energy starvation zone. Conservative only. |
| 0.20–0.40       | 1.15                | 0.50–0.60       | 0.45–0.55    | |
| 0.40–0.60       | 1.20                | 0.52–0.65       | 0.48–0.55    | |
| > 0.60          | 1.35                | 0.55–0.70       | 0.50–0.55    | |

### Path_bias Selection by Enemy Spawn Bias
| enemy_spawn_bias | path_bias range | Notes |
|-----------------|----------------|-------|
| 0.45–0.55 (balanced) | 0.50–0.55 | No asymmetric exploitation needed |
| 0.55–0.65 (moderate) | 0.45–0.55 | Slight bias toward weaker lane; reduce by 0.05 if resource_density < 0.2 |
| > 0.65 (strong)       | 0.45–0.60 | Exploit weak lane cautiously; cap at 0.50 if resource_density < 0.2 |

### Proven Baselines
| Conditions | Parameters | Score | Notes |
|-----------|-----------|-------|-------|
| resource_density≈0.437, enemy_spawn_bias≈0.51 | aggression=0.52, defense=0.50, path_bias=0.52 | 0.7343 | Capture: 0.59, Defender survival: 0.99, Energy efficiency: 0.89 |
| resource_density≈0.147, enemy_spawn_bias≈0.648 | aggression=0.48, defense=0.52, path_bias=0.45 | Untested (recovery) | Conservative low-resource baseline |

### Recommended Strategy by Environment
**For resource_density ≈ 0.437, enemy_spawn_bias ≈ 0.51:**
```json
{
  "aggression": 0.58,
  "defense": 0.52,
  "path_bias": 0.52
}
```
- Total commitment: 1.10 (within 1.20 ceiling)
- Rationale: Push capture progress from 0.59 toward 0.63-0.65

**For resource_density < 0.20 (e.g., 0.147), enemy_spawn_bias ≈ 0.648:**
```json
{
  "aggression": 0.48,
  "defense": 0.52,
  "path_bias": 0.45
}
```
- Total commitment: 1.00 (safely below 1.05 ceiling)
- Threat risk ≈ 0.487 (below 0.65 threshold)
- Rationale: Prevent energy starvation; slight path bias toward enemy's weaker lane without over-concentration

### Recovery Priority Order (Low-Resource: resource_density < 0.2)
1. **Ensure non-zero capture progress**: Aggression must be ≥ 0.45
2. **Guarantee defender survival**: Defense must be ≥ 0.45 (preferably ≥ 0.50)
3. **Maintain energy sustainability**: Total commitment ≤ 1.05
4. **Exploit enemy weakness cautiously**: Path_bias in [0.40, 0.50]

### Adjustment Protocol
- **If score improves**: Increment aggression by +0.03 to +0.05; keep defense stable
- **If score stagnates (within ±0.01)**: Try path_bias ±0.03 or defense ±0.02
- **If score drops by > 0.03**: Rollback to previous best; reduce change magnitude to ±0.02
- **If score drops to zero (EMERGENCY)**: 
  - Immediately check resource_density and apply appropriate commitment ceiling
  - Reset to conservative parameters for current resource level
  - If resource_density < 0.2: aggression=0.48, defense=0.52, path_bias=0.50, total=1.00
  - If resource_density ≥ 0.2: aggression=0.50, defense=0.50, path_bias=0.50, total=1.00
  - Run threat_assessor and stability_analyzer before redeployment

### Critical Lesson (Gen 4 Failure)
Applying moderate-resource parameters (validated at resource_density≈0.437) to a low-resource environment (resource_density=0.147) caused catastrophic energy starvation and a zero score. **Always read resource_density first and select commitment ceiling accordingly.** Never assume previous parameters transfer across resource regimes.

## Prompt Optimizations

- Return concise JSON with exactly three keys: `aggression`, `defense`, `path_bias`
- **Always read current `resource_density` and `enemy_spawn_bias` FIRST** before selecting parameters
- Use resource-adaptive commitment ceiling table to set total commitment bounds
- Use path_bias selection table based on enemy_spawn_bias
- Validate with threat_assessor (risk < 0.65) and stability_analyzer (stability > 0.45) before deployment
- Constraint: aggression + defense ≤ 1.4 (hard system limit); practical ceiling from resource table

## Next Generation Checklist

1. Read observation state: resource_density and enemy_spawn_bias
2. Look up commitment ceiling from resource-adaptive table
3. Select appropriate proven baseline for current conditions (or nearest match)
4. Propose parameters within allowed ranges for current resource tier
5. Run threat_assessor: abort if risk > 0.65
6. Run stability_analyzer: abort if stability < 0.45
7. Verify defense ∈ [0.45, 0.55]
8. Verify aggression ≥ 0.48 (minimum for capture progress)
9. Verify total commitment ≤ resource-adaptive ceiling
10. Verify path_bias matches enemy_spawn_bias table
11. If improving from a proven baseline, change by ±0.03 to ±0.06 only
12. If score was zero in previous generation, use emergency reset protocol
13. Record strategy, conditions, and score in baselines table for future reference
