"""register a trained checkpoint as a non-activated registry candidate.

The ambient train stage produces candidates only. Activation (promotion to a
served model) is a separate gated decision in the evaluate/promote stage, so
auto_activate stays False here. The training data lineage is stamped as
produced_by=finetune:<target> so provenance quarantine can exclude this
model's own future outputs from its next lineage.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.ambient.charter import CharterTarget
from autocontext.ambient.training_backend import TrainOutcome
from autocontext.training.model_registry import (
    ModelRegistry,
    TrainingCompletionOutput,
    publish_training_output,
)


def publish_candidate(
    *,
    outcome: TrainOutcome,
    target: CharterTarget,
    scenario: str,
    registry: ModelRegistry,
    artifacts_root: Path,
    run_id: str,
    record_count: int,
) -> str:
    completion = TrainingCompletionOutput(
        run_id=run_id,
        checkpoint_path=str(outcome.checkpoint_path),
        backend=outcome.backend,
        scenario=scenario,
        scenario_family=scenario,
        parameter_count=int(outcome.metrics.get("num_params_m", 0.0) * 1_000_000),
        architecture=target.base_model,
        training_metrics=outcome.metrics,
        data_stats={"num_records": outcome.metrics.get("num_records", 0.0)},
        runtime_types=["provider"],
        # base_model is required by the adapter-serving path (scenario_bound_clients.plan_local_client):
        # an mlxlm adapter record without it cannot be rebuilt into a client and is skipped as broken.
        # record_count stamps the dataset size this candidate was trained on, so the train stage can
        # detect an unchanged manifest and skip a redundant retrain of the same data.
        metadata={
            "produced_by": f"finetune:{target.name}",
            "target": target.name,
            "gpu_hours": outcome.gpu_hours,
            "base_model": target.base_model,
            "record_count": record_count,
        },
    )
    record = publish_training_output(completion, registry, artifacts_root=artifacts_root, auto_activate=False)
    return record.artifact_id
