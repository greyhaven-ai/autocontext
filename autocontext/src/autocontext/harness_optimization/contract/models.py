# AUTO-GENERATED from ts/src/harness-optimization/contract/json-schemas/ — DO NOT EDIT.
# Run: node ts/scripts/sync-python-harness-optimization-schemas.mjs
# CI gate: node ts/scripts/sync-python-harness-optimization-schemas.mjs --check

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, RootModel


class HarnessOptimizationContracts(RootModel[Any]):
    root: Annotated[
        Any,
        Field(
            description='Codegen-only aggregate root; $refs each artifact so one models.py is emitted. Not a runtime schema.',
            title='HarnessOptimizationContracts',
        ),
    ]


class CostExpectation(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    extra_tokens: Annotated[
        int | None, Field(description='Expected additional tokens per run.', ge=0)
    ] = None
    extra_calls: Annotated[
        int | None,
        Field(description='Expected additional model or tool calls per run.', ge=0),
    ] = None
    extra_seconds: Annotated[
        float | None,
        Field(description='Expected additional wall-clock seconds per run.', ge=0.0),
    ] = None


class Parity(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    python: Annotated[
        Literal['implemented', 'pending', 'n_a'],
        Field(
            description='Implementation status of this candidate in the Python package.'
        ),
    ]
    typescript: Annotated[
        Literal['implemented', 'pending', 'n_a'],
        Field(
            description='Implementation status of this candidate in the TypeScript package.'
        ),
    ]
    schema_hash: Annotated[
        str,
        Field(
            description='Content hash of the shared schema the two implementations agree on.'
        ),
    ]


class CandidateEvidence(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    candidate_id: Annotated[
        str,
        Field(description='Stable unique identifier for this candidate.', min_length=1),
    ]
    parent_frontier_id: Annotated[
        str | None,
        Field(
            description='Identifier of the frontier this candidate descends from. May be empty for a root candidate.'
        ),
    ] = None
    mechanism_name: Annotated[
        str,
        Field(
            description='Human-readable name of the mechanism this candidate changes.',
            min_length=1,
        ),
    ]
    mechanism_type: Annotated[
        Literal[
            'deterministic_code',
            'prompt_playbook',
            'tool_wrapper',
            'context_policy',
            'judge_policy',
            'mixed',
        ],
        Field(description='Category of mechanism being changed.'),
    ]
    target_surface: Annotated[
        Literal[
            'prompt',
            'tool',
            'harness_validator',
            'runtime_adapter',
            'artifact_landing',
            'evaluator',
            'routing',
            'docs',
        ],
        Field(description='The surface of the harness this candidate targets.'),
    ]
    hypothesis: Annotated[
        str,
        Field(
            description='The falsifiable claim about why this change should help.',
            min_length=1,
        ),
    ]
    changes: Annotated[
        str,
        Field(
            description='Description of the concrete changes this candidate makes.',
            min_length=1,
        ),
    ]
    changed_artifacts: Annotated[
        list[str] | None,
        Field(
            description='Paths or identifiers of the artifacts this candidate modifies.'
        ),
    ] = None
    fix_cases: Annotated[
        list[str] | None,
        Field(
            description='Named seeds, tasks, or traces this candidate is expected to improve.'
        ),
    ] = None
    regression_cases: Annotated[
        list[str] | None,
        Field(
            description='Named seeds, tasks, or traces this candidate is expected to keep flat.'
        ),
    ] = None
    observed: Annotated[
        str | None,
        Field(
            description='Smoke, replay, or dry-run evidence gathered so far. May be empty at proposal time.'
        ),
    ] = None
    validation_plan: Annotated[
        str,
        Field(
            description='How this candidate will be validated before promotion.',
            min_length=1,
        ),
    ]
    cost_expectation: Annotated[
        CostExpectation | None,
        Field(description='Expected marginal cost of adopting this candidate.'),
    ] = None
    leakage_scope: Annotated[
        list[str] | None,
        Field(
            description='What data the proposer was allowed to inspect, for leakage auditing.'
        ),
    ] = None
    parity: Annotated[
        Parity, Field(description='Cross-language parity status for this candidate.')
    ]


class Components(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    dense_quality_score: Annotated[
        float,
        Field(description='Dense quality signal, typically a judge or rubric score.'),
    ]
    sparse_success_rate: Annotated[
        float,
        Field(
            description='Fraction of target cases the candidate resolved, in [0, 1].',
            ge=0.0,
            le=1.0,
        ),
    ]
    tokens_per_million: Annotated[
        float,
        Field(description='Marginal token cost expressed per million tokens.', ge=0.0),
    ]
    error_rate: Annotated[
        float,
        Field(description='Fraction of runs that errored, in [0, 1].', ge=0.0, le=1.0),
    ]
    score_variance: Annotated[
        float, Field(description='Variance of the score across samples.', ge=0.0)
    ]


class Weights(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    sparse_success_weight: Annotated[
        float, Field(description='Weight applied to the sparse success rate.', ge=0.0)
    ]
    token_cost_weight: Annotated[
        float, Field(description='Weight applied to the marginal token cost.', ge=0.0)
    ]
    error_weight: Annotated[
        float, Field(description='Weight applied to the error rate.', ge=0.0)
    ]
    variance_weight: Annotated[
        float, Field(description='Weight applied to the score variance.', ge=0.0)
    ]


class PromotionScore(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    candidate_id: Annotated[
        str,
        Field(
            description='Stable unique identifier of the candidate this score belongs to.',
            min_length=1,
        ),
    ]
    weight_version: Annotated[
        str,
        Field(
            description='Version tag of the weight set used to compute this score.',
            min_length=1,
        ),
    ]
    components: Annotated[
        Components,
        Field(
            description='The measured components that feed the weighted promotion score.'
        ),
    ]
    weights: Annotated[
        Weights, Field(description='The named weights applied to each component.')
    ]
    score: Annotated[float, Field(description='The computed harness promotion score.')]
    parity: Annotated[
        Parity, Field(description='Cross-language parity status for this candidate.')
    ]


class RepairResult(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    repair_name: Annotated[
        str,
        Field(
            description='Human-readable name of the repair mechanism, e.g. tool_call_json or finish_guard.',
            min_length=1,
        ),
    ]
    status: Annotated[
        Literal['applied', 'skipped', 'not_applicable'],
        Field(
            description='Whether the repair fired: applied, skipped, or not_applicable to this input.'
        ),
    ]
    reason: Annotated[
        str,
        Field(
            description='Human-auditable explanation of why the repair was applied or skipped.',
            min_length=1,
        ),
    ]
    target: Annotated[
        str | None,
        Field(
            description='What was repaired: a path, a tool name, or empty when nothing was targeted.'
        ),
    ] = None
    before: Annotated[
        dict[str, Any] | None,
        Field(description='Pre-repair metadata, e.g. {"valid": false}.'),
    ] = None
    after: Annotated[
        dict[str, Any] | None,
        Field(description='Post-repair metadata, e.g. {"valid": true}.'),
    ] = None
    parity: Annotated[
        Parity, Field(description='Cross-language parity status for this candidate.')
    ]


class IntegrityMetadata(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    run_id: Annotated[
        str,
        Field(
            description='Identifier of the run this integrity record describes.',
            min_length=1,
        ),
    ]
    mode: Annotated[
        Literal['verified', 'exploratory'],
        Field(
            description='Run mode: verified fails closed on leakage, exploratory is marked non-promotion-grade.'
        ),
    ]
    allowed_sources: Annotated[
        list[str], Field(description='Source ids the proposer or evaluator may read.')
    ]
    forbidden_sources: Annotated[
        list[str],
        Field(
            description='Source ids that must never be read, for example holdout or test-split sources.'
        ),
    ]
    required_sources: Annotated[
        list[str],
        Field(
            description='Subset of sources whose status must be known-clean for a verified run to advance.'
        ),
    ]
    web_policy: Annotated[
        Literal['blocked', 'allowlist', 'open'],
        Field(
            description='Web-access policy: blocked forbids all web reads, allowlist permits only listed hosts, open permits any.'
        ),
    ]
    web_allowlist: Annotated[
        list[str] | None,
        Field(
            description='Hosts permitted when web_policy is allowlist. Optional; omitted or empty means no host is permitted.'
        ),
    ] = None
    split_ids: Annotated[
        list[str],
        Field(description='Benchmark or test split manifest ids in play for this run.'),
    ]
    prompt_provenance: Annotated[
        str | None,
        Field(
            description='Where proposer prompts came from. Verified mode requires it non-empty (gate-enforced, not schema).'
        ),
    ] = None
    adapter_capabilities: Annotated[
        list[str],
        Field(
            description='What the runtime or adapter can enforce, for example filesystem sandboxing or network blocking.'
        ),
    ]
    leakage_status: Annotated[
        Literal['clean', 'contaminated', 'unknown'],
        Field(
            description='Computed leakage status: clean, contaminated, or unknown when it cannot be proven clean.'
        ),
    ]
    contamination_reasons: Annotated[
        list[str],
        Field(
            description='Human-readable reasons for a contaminated or unknown status. Empty when clean.'
        ),
    ]


class FrontierMechanism(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    mechanism_id: Annotated[
        str,
        Field(
            description='Stable unique identifier for this frontier mechanism.',
            min_length=1,
        ),
    ]
    candidate_evidence_id: Annotated[
        str,
        Field(
            description='Identifier of the candidate evidence record this mechanism was promoted from.',
            min_length=1,
        ),
    ]
    parent_frontier_id: Annotated[
        str | None,
        Field(
            description='Identifier of the frontier this mechanism descends from. Empty for a root frontier.'
        ),
    ] = None
    mechanism_name: Annotated[
        str,
        Field(
            description='Human-readable name of the promoted mechanism.', min_length=1
        ),
    ]
    mechanism_type: Annotated[
        Literal[
            'deterministic_code',
            'prompt_playbook',
            'tool_wrapper',
            'context_policy',
            'judge_policy',
            'mixed',
        ],
        Field(description='Category of mechanism being changed.'),
    ]
    target_surface: Annotated[
        Literal[
            'prompt',
            'tool',
            'harness_validator',
            'runtime_adapter',
            'artifact_landing',
            'evaluator',
            'routing',
            'docs',
        ],
        Field(description='The surface of the harness this mechanism targets.'),
    ]
    gate_decision: Annotated[
        str, Field(description='The advancement decision that promoted this mechanism.')
    ]
    affected_surfaces: Annotated[
        list[str],
        Field(description='Surfaces this mechanism touches beyond its primary target.'),
    ]
    regression_risks: Annotated[
        list[str],
        Field(description='Known regression risks this mechanism carries forward.'),
    ]
    support_count: Annotated[
        int,
        Field(
            description='Number of runs or generations that support this mechanism.',
            ge=0,
        ),
    ]
    promoted_at_generation: Annotated[
        int,
        Field(
            description='Generation index at which this mechanism was promoted.', ge=0
        ),
    ]


class OrphanMechanism(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    mechanism_id: Annotated[
        str,
        Field(
            description='Stable unique identifier for this orphan mechanism.',
            min_length=1,
        ),
    ]
    candidate_evidence_id: Annotated[
        str,
        Field(
            description='Identifier of the candidate evidence record this mechanism came from.',
            min_length=1,
        ),
    ]
    parent_frontier_id: Annotated[
        str | None,
        Field(
            description='Identifier of the frontier this mechanism descends from. Empty for a root candidate.'
        ),
    ] = None
    mechanism_name: Annotated[
        str,
        Field(
            description='Human-readable name of the orphaned mechanism.', min_length=1
        ),
    ]
    mechanism_type: Annotated[
        Literal[
            'deterministic_code',
            'prompt_playbook',
            'tool_wrapper',
            'context_policy',
            'judge_policy',
            'mixed',
        ],
        Field(description='Category of mechanism being changed.'),
    ]
    target_surface: Annotated[
        Literal[
            'prompt',
            'tool',
            'harness_validator',
            'runtime_adapter',
            'artifact_landing',
            'evaluator',
            'routing',
            'docs',
        ],
        Field(description='The surface of the harness this mechanism targets.'),
    ]
    gate_decision: Annotated[
        str,
        Field(
            description='The gate outcome for this mechanism, such as retry, rollback, or reject.'
        ),
    ]
    failure_family: Annotated[
        str,
        Field(
            description='The family of failure this mechanism belongs to, for clustering orphans.'
        ),
    ]
    rejection_reason: Annotated[
        str, Field(description='Human-readable reason this mechanism was not promoted.')
    ]
    retry_count: Annotated[
        int,
        Field(
            description='Number of times this mechanism was retried before being orphaned.',
            ge=0,
        ),
    ]
    support_count: Annotated[
        int | None,
        Field(
            description='Number of runs or generations that support this mechanism.',
            ge=0,
        ),
    ] = None
    rescued_into_frontier_id: Annotated[
        str | None,
        Field(
            description='Frontier id a later combination rescued this into. Empty while still orphaned.'
        ),
    ] = None


class CalibrationReport(BaseModel):
    model_config = ConfigDict(
        extra='forbid',
    )
    schema_version: Annotated[
        Literal[1],
        Field(
            description='Schema version for forward compatibility. Always 1 for this revision.'
        ),
    ]
    scenario_id: Annotated[
        str,
        Field(
            description='Scenario or family the score series came from.', min_length=1
        ),
    ]
    sample_size: Annotated[
        int, Field(description='Number of score samples in the series (n).', ge=0)
    ]
    mean: Annotated[float, Field(description='Mean of the score series.')]
    variance: Annotated[
        float, Field(description='Sample variance (ddof=1); 0 when n<2.', ge=0.0)
    ]
    std_dev: Annotated[
        float, Field(description='Standard deviation, sqrt of the variance.', ge=0.0)
    ]
    standard_error: Annotated[
        float,
        Field(description='Standard error, std_dev over sqrt(n); 0 when n<2.', ge=0.0),
    ]
    recommended_min_delta: Annotated[
        float,
        Field(
            description='Recommended margin: noise_multiplier times standard_error.',
            ge=0.0,
        ),
    ]
    recommended_trial_count: Annotated[
        int,
        Field(
            description='Trials so the mean SE falls under current_min_delta, capped by budget.',
            ge=1,
        ),
    ]
    current_min_delta: Annotated[
        float, Field(description='The promotion margin currently configured.')
    ]
    margin_vs_noise: Annotated[
        Literal['above_noise', 'below_noise', 'insufficient_data'],
        Field(
            description='Margin vs noise floor: above_noise, below_noise, or insufficient_data (n<2 = no variance estimate).'
        ),
    ]
    sparse_metric_too_noisy: Annotated[
        bool, Field(description='True when the sparse metric is too noisy to gate on.')
    ]
    notes: Annotated[
        str | None, Field(description='Optional human-readable rationale for audit.')
    ] = None
