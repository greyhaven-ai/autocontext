## Strategy Playbook (Consolidated)

### Core Principles (Priority Order)
1. **VERIFY ENVIRONMENT FIRST**: Before ANY parameter selection, read `resource_density` and `enemy_spawn_bias` from the observation state. Call `config_constants.py` to confirm tier, ceiling, and ranges. Cross-check against the observation narrative.
2. **Commitment ceiling is non-negotiable**: Total commitment (aggression + defense) must NEVER exceed the tier ceiling. This is the #1 cause of catastrophic failure (zero scores).
3. **Never transfer parameters across resource tiers**: Parameters validated at one density tier produce zero scores at different tiers. ALWAYS select parameters within the current tier's ranges.
4. **Defensive anchor**: Defense must remain in [0.45, 0.55]. Below 0.45 risks base loss; above 0.55 starves capture.
5. **Capture is primary**: Aggression must be ≥ 0.48 to generate meaningful capture progress. Zero capture = zero score.
6. **Efficiency is the competitive edge**: Score ≈ capture_progress + (energy_efficiency - 0.5) × 0.39. Losing 4% efficiency costs ~1.5 score points.
7. **Use commitment headroom aggressively in moderate+ tiers**: A 16% buffer is wasted capacity. Target 4–6% buffer below ceiling for moderate tier; 5% buffer for critical_low.
8. **Incremental tuning only**: Change parameters by ±0.02 to ±0.05 per generation within the same tier.
9. **Validate before deploying**: Run ALL validation tools. Risk > 0.65 or stability < 0.45 = do not deploy.

### Mandatory Pre-Deployment Checklist
1. Read observation state: `resource_density` and `enemy_spawn_bias`
2. Call `config_constants.py` → confirm tier, ceiling, parameter ranges
3. Call `strategy_recommender.py.py` → get proven baseline recommendation
4. Select parameters within tier ranges
5. Call `energy_budget_validator.py` → must return valid=true
6. Call `stability_analyzer.py.py` → stability must be ≥ 0.45
7. Call `threat_assessor.py` → risk must be < 0.65
8. Only deploy if ALL tools pass

### Scoring Formula
```
score ≈ capture_progress + (energy_efficiency - 0.5) × 0.39
```
| Efficiency | Bonus |
|-----------|-------|
| 90% | +0.156 |
| 89% | +0.152 |
| 88% | +0.148 |
| 85% | +0.137 |

### Resource Tier Table
| resource_density | Tier | Ceiling | Agg range | Def range | Safe total target |
|-----------------|------|---------|-----------|-----------|-------------------|
| < 0.20 | critical_low | 1.05 | 0.48–0.53 | 0.45–0.52 | ≤ 1.00 |
| 0.20–0.40 | low | 1.15 | 0.50–0.60 | 0.45–0.55 | ≤ 1.10 |
| 0.40–0.60 | moderate | 1.20 | 0.52–0.65 | 0.48–0.55 | ≤ 1.15 |
| > 0.60 | high | 1.35 | 0.55–0.70 | 0.50–0.55 | ≤ 1.30 |

### Path_bias by Enemy Spawn Bias
| enemy_spawn_bias | path_bias (density < 0.2) | path_bias (density ≥ 0.2) |
|-----------------|--------------------------|--------------------------|
| ≤ 0.55 | 0.50–0.52 | 0.50–0.55 |
| 0.55–0.65 | 0.47–0.50 | 0.48–0.52 |
| > 0.65 | 0.45–0.48 | 0.45–0.50 |

### Proven Baselines
| Conditions | Parameters | Score | Metrics |
|-----------|-----------|-------|---------|
| density≈0.147, bias≈0.648 | agg=0.50, def=0.50, pb=0.48 | **0.7198** | Capture: 0.56, Defender: 1.00, Energy: 0.90 |
| density≈0.437, bias≈0.51 | agg=0.58, def=0.57, pb=0.55 | **0.7615** | Capture: 0.63, Defender: 1.00, Energy: 0.88 |
| density≈0.437, bias≈0.51 | agg=0.53, def=0.48, pb=0.47 | **0.7203** | Capture: 0.57, Defender: 0.98, Energy: 0.89 |

### Failed Strategies (Do Not Repeat)
| Parameters | Conditions | Score | Failure |
|-----------|-----------|-------|---------|
| agg=0.60, def=0.55, pb=0.53 | density≈0.147 | **0.0000** | Cross-tier transfer. Total 1.15 vs ceiling 1.05. |
| agg=0.58, def=0.57, pb=0.55 | density≈0.147 | **0.0000** | Cross-tier transfer. Total 1.15 vs ceiling 1.05. |
| agg=0.67, def=0.52, pb=0.60 | density≈0.437 | 0.7486 | Over-aggression; defender 0.94, energy 0.85. |
| agg=0.62, def=0.52, pb=0.58 | density≈0.437 | 0.7369 | Over-committed asymmetrically; underperformed balanced baseline. |

### Current Environment: Critical_Low Tier (density=0.147, bias=0.648)

**⚠️ CRITICAL WARNING**: Two consecutive zero scores were caused by deploying moderate-tier parameters (total=1.15) in a critical_low environment (ceiling=1.05). The environment density is **0.147**, NOT 0.437. Always verify with `config_constants.py` before parameter selection.

**Recovery Strategy (IMMEDIATE — DEPLOY NOW)**:
Deploy the proven baseline that scored 0.7198 at these exact conditions:
```json
{"aggression": 0.50, "defense": 0.50, "path_bias": 0.48}
```
- Total: 1.00 (5% buffer below 1.05 ceiling)
- Expected: Score ≈ 0.72, Capture 0.56, Defender 1.00, Energy 0.90

**Next Optimization (Gen+1, only if recovery score ≥ 0.71)**:
```json
{"aggression": 0.52, "defense": 0.49, "path_bias": 0.47}
```
- Total: 1.01 (4% buffer below 1.05 ceiling)
- Rationale: Defense=0.50 produced perfect defender survival (1.00), indicating over-allocation. Shift 0.01 from defense to aggression. Reduce path_bias by 0.01 for efficiency.
- Expected: Score ≈ 0.73, Capture ≈ 0.58, Defender ≥ 0.97, Energy ≈ 0.90

**Gen+2 (only if Gen+1 score ≥ 0.72)**:
```json
{"aggression": 0.53, "defense": 0.48, "path_bias": 0.47}
```
- Total: 1.01 (still 4% buffer)
- Rationale: Continue incremental aggression rebalancing. Path_bias capped at 0.50 max for critical_low (energy-expensive force projection).

### Zero-Score Recovery Protocol
1. STOP. Do not incrementally tweak failed parameters.
2. Read the ACTUAL observation state (resource_density, enemy_spawn_bias).
3. Call `config_constants.py` to identify the correct tier and ceiling.
4. Compare observed resource_density against the tier table — do NOT assume the tier from memory.
5. Look up proven baseline for the current tier from the Proven Baselines table.
6. Deploy the proven baseline exactly as documented.
7. Run ALL validation tools to confirm before deployment.
8. Only after recovery succeeds, begin incremental optimization (±0.02 per generation).

### Moderate Tier Optimization Path (density≈0.437, bias≈0.51)
**NOTE**: Only use these when resource_density is ACTUALLY in [0.40, 0.60). Verify with `config_constants.py` first.

**Baseline**: agg=0.58, def=0.57, pb=0.55 (score=0.7615, total=1.15)
- Note: def=0.57 is above tier range max of 0.55 but worked historically. Prefer def=0.55 for safety.

**Gen 2**: Use commitment headroom — target 6% buffer:
```json
{"aggression": 0.58, "defense": 0.55, "path_bias": 0.53}
```
- Total: 1.13 (6% buffer below 1.20 ceiling)
- Rationale: Aggression matches historical best (0.58). Defense adjusted to 0.55 (within tier range). Path_bias centered in recommended range.
- Expected: Score ≈ 0.74–0.76

**Gen 3 (if Gen 2 ≥ 0.74)**: Match proven best path_bias:
```json
{"aggression": 0.58, "defense": 0.55, "path_bias": 0.55}
```
- Total: 1.13 (same commitment, shift path_bias to match proven best)
- Expected: Score ≈ 0.76

**Gen 4 (if Gen 3 ≥ 0.76)**: Explore aggression ceiling:
```json
{"aggression": 0.60, "defense": 0.55, "path_bias": 0.53}
```
- Total: 1.15 (4% buffer)
- Expected: Score ≈ 0.77 if capture scales with aggression
