# AUTO-GENERATED from ts/src/harness-optimization/contract/json-schemas/ — DO NOT EDIT.
# Run: node ts/scripts/sync-python-harness-optimization-schemas.mjs
# CI gate: node ts/scripts/sync-python-harness-optimization-schemas.mjs --check

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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
        Literal['implemented', 'pending', 'n_a'] | None,
        Field(
            description='Implementation status of this candidate in the Python package.'
        ),
    ] = None
    typescript: Annotated[
        Literal['implemented', 'pending', 'n_a'] | None,
        Field(
            description='Implementation status of this candidate in the TypeScript package.'
        ),
    ] = None
    schema_hash: Annotated[
        str | None,
        Field(
            description='Content hash of the shared schema the two implementations agree on.'
        ),
    ] = None


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
