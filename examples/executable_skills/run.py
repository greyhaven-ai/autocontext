"""AC-1028: explicitly enroll and replay a non-game migration in real Docker.

This is a deterministic engineering fixture, not synthesized/live-model evidence
or a benchmark establishing quality, cost savings, or promotion eligibility.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from autocontext.context_bundles.models import ContextBundle
from autocontext.context_bundles.store import ContextBundleStore
from autocontext.execution.executable_skills import (
    SCENARIO,
    SkillSourceEvidence,
    evaluator_identity,
    inspect_executable_skill,
    invoke_executable_skill,
    propose_schema_migration,
)
from autocontext.execution.skill_routing import route_schema_migration
from autocontext.execution.skill_routing_models import SkillRoutingConfig
from autocontext.knowledge.harness_entries import SkillReference
from autocontext.training.model_registry import ModelRegistry

SOURCE = '''def choose_action(state):
    return {"schema_version": 2, "display_name": state["name"],
            "status": "enabled" if state["enabled"] else "disabled"}
'''


def run(output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=False)
    store = ContextBundleStore(output / "knowledge")
    # This demo explicitly creates an empty baseline in a new output directory.
    # The enrollment API itself never bootstraps or changes active state.
    store.bootstrap(ContextBundle.create(scenario=SCENARIO, evaluator_epoch=evaluator_identity(), components=[]))
    active_before = store.active_pointer(SCENARIO)
    evidence = (
        SkillSourceEvidence.create("fixture-example", "example", {
            "input": {"schema_version": 1, "name": "Ada", "enabled": True},
            "expected_output": {"schema_version": 2, "display_name": "Ada", "status": "enabled"},
        }),
        SkillSourceEvidence.create("fixture-counterexample", "counterexample", {
            "input": {"schema_version": 3, "name": "Ada", "enabled": True}, "expected_status": "abstention",
        }),
    )
    bundle = propose_schema_migration(store, SkillReference(entrypoint="choose_action", source=SOURCE),
                                     source_evidence=evidence, run_id="ac1028-fixture")
    inputs = [
        {"schema_version": 1, "name": "Grace", "enabled": False},
        {"schema_version": 1, "name": "Lin", "enabled": True},
        {"schema_version": 3, "name": "Grace", "enabled": False},
    ]
    (output / "inputs.json").write_text(json.dumps(inputs, indent=2) + "\n")
    results = [invoke_executable_skill(store, bundle.digest, json.dumps(value), mode="evaluation") for value in inputs]
    serving = invoke_executable_skill(store, bundle.digest, json.dumps(inputs[0]), mode="serving")
    registry = ModelRegistry(output / "models")
    config = SkillRoutingConfig(enabled=True, bundle_digest=bundle.digest)
    routed = [route_schema_migration(store, registry, json.dumps(value), config=config,
                                     trace_root=output / "traces", mode="evaluation") for value in inputs]
    summary = {
        "evidence_kind": "deterministic fixture; no live model or savings claim",
        "bundle_digest": bundle.digest,
        "artifact_digest": inspect_executable_skill(store, bundle.digest).digest,
        "lifecycle": store.candidate(SCENARIO, bundle.digest).lifecycle.value,
        "model_calls": 0,
        "execution_seconds": sum(result.execution_seconds for result in results),
        "total_cost": "not measured; Docker/CPU/memory/storage costs are not zero",
        "active_pointer_unchanged": store.active_pointer(SCENARIO) == active_before,
        "evaluations": [result.model_dump() for result in results],
        "inactive_serving_attempt": serving.model_dump(),
        "routing": [result.model_dump() for result in routed],
    }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    expected = ["success", "success", "abstention"]
    if ([result.status for result in results] != expected or serving.status != "execution_failure"
            or [result.status for result in routed] != expected or any(result.model_calls for result in routed)):
        raise RuntimeError(f"fixture did not pass; inspect {output / 'summary.json'}")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="A new directory for the fixture artifacts")
    print(json.dumps(run(parser.parse_args().output), indent=2))
