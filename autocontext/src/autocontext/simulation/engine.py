"""Simulation engine — Python parity with TS SimulationEngine (AC-453).

Takes a plain-language description, builds a simulation spec via LLM,
executes trajectories/sweeps, and returns structured findings.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any

from autocontext.agents.types import LlmFn
from autocontext.util.json_io import read_json, write_json

logger = logging.getLogger(__name__)


def _generate_id() -> str:
    return f"sim_{uuid.uuid4().hex[:12]}"


def _derive_name(description: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", "", description.lower()).split()
    return "_".join(w for w in words if len(w) > 2)[:4] or "simulation"


class SimulationEngine:
    """Plain-language simulation engine with sweep/replay/compare."""

    def __init__(self, llm_fn: LlmFn, knowledge_root: Path) -> None:
        self.llm_fn = llm_fn
        self.knowledge_root = knowledge_root

    # ------------------------------------------------------------------
    # Run
    # ------------------------------------------------------------------

    def run(
        self,
        description: str,
        *,
        variables: dict[str, Any] | None = None,
        sweep: list[dict[str, Any]] | None = None,
        runs: int = 1,
        max_steps: int | None = None,
        save_as: str | None = None,
    ) -> dict[str, Any]:
        sim_id = _generate_id()
        name = save_as or _derive_name(description)
        resolved_variables = variables or {}

        try:
            family = self._infer_family(description)
            spec = self._apply_variables(self._build_spec(description, family), resolved_variables)

            source = self._generate_source(spec, name, family)
            scenario_dir = self._persist(name, family, spec, source)

            if sweep:
                sweep_result = self._execute_sweep(
                    description,
                    family,
                    name,
                    spec,
                    sweep,
                    max_steps,
                    scenario_dir,
                    resolved_variables,
                    runs,
                )
                summary = self._aggregate_sweep(sweep_result)
            else:
                results = [self._execute_single(source, name, seed, max_steps) for seed in range(runs)]
                summary = self._aggregate_runs(results)
                sweep_result = None

            assumptions = self._build_assumptions(spec, family)
            warnings = self._build_warnings(family)

            report = {
                "id": sim_id,
                "name": name,
                "family": family,
                "status": "completed",
                "description": description,
                "assumptions": assumptions,
                "variables": resolved_variables,
                "sweep": sweep_result,
                "summary": summary,
                "execution": {
                    "runs": max(1, runs),
                    "max_steps": max_steps,
                    "sweep": sweep or [],
                },
                "artifacts": {
                    "scenario_dir": str(scenario_dir),
                    "report_path": str(scenario_dir / "report.json"),
                },
                "warnings": warnings,
            }
            write_json(scenario_dir / "report.json", report)
            return report

        except Exception as exc:
            logger.debug("simulation.engine: caught Exception", exc_info=True)
            return {
                "id": sim_id, "name": name, "family": "simulation",
                "status": "failed", "description": description,
                "assumptions": [], "variables": variables or {},
                "sweep": None,
                "summary": {"score": 0, "reasoning": str(exc), "dimension_scores": {}},
                "artifacts": {"scenario_dir": "", "report_path": ""},
                "warnings": [], "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Replay
    # ------------------------------------------------------------------

    def replay(
        self,
        id: str,
        *,
        variables: dict[str, Any] | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        resolved = self._resolve_report(id)
        if resolved is None:
            return {"status": "failed", "error": f"Simulation '{id}' not found", "name": id}

        original, sim_dir = resolved
        original_score = original.get("summary", {}).get("score", 0)
        merged_vars = {**(original.get("variables") or {}), **(variables or {})}
        family = original.get("family", "simulation")
        spec = self._load_spec(sim_dir)
        if spec is None:
            return {"status": "failed", "error": f"Spec not found for '{id}'", "name": id}

        execution = self._resolve_execution_config(original)
        replay_max_steps = max_steps if max_steps is not None else execution["max_steps"]
        runs = execution["runs"]

        if original.get("sweep"):
            sweep_result = self._replay_sweep(
                original=original,
                scenario_dir=sim_dir,
                family=family,
                base_name=original.get("name", id),
                base_spec=spec,
                overrides=variables or {},
                max_steps=replay_max_steps,
                runs=runs,
            )
            result = self._aggregate_sweep(sweep_result)
        else:
            source = self._load_source(sim_dir, spec, original.get("name", id), family, merged_vars)
            reruns = [self._execute_single(source, id, seed, replay_max_steps) for seed in range(runs)]
            result = self._aggregate_runs(reruns)
            sweep_result = None

        replay_report = {
            **original,
            "id": _generate_id(),
            "summary": result,
            "variables": merged_vars,
            "sweep": sweep_result,
            "replay_of": id,
            "original_score": original_score,
            "score_delta": round(result["score"] - original_score, 4),
            "status": "completed",
            "execution": {
                "runs": runs,
                "max_steps": replay_max_steps,
                "sweep": execution["sweep"],
            },
        }

        replay_path = sim_dir / f"replay_{replay_report['id']}.json"
        write_json(replay_path, replay_report)
        replay_report["artifacts"] = {
            "scenario_dir": str(sim_dir),
            "report_path": str(replay_path),
        }
        return replay_report

    # ------------------------------------------------------------------
    # Compare
    # ------------------------------------------------------------------

    def compare(self, left: str, right: str) -> dict[str, Any]:
        left_report = self._load_report(left)
        right_report = self._load_report(right)

        if not left_report or not right_report:
            missing = left if not left_report else right
            return {"status": "failed", "error": f"Simulation '{missing}' not found"}

        if left_report.get("family") != right_report.get("family"):
            return {
                "status": "failed",
                "error": (
                    "Cannot compare simulations across different families "
                    f"({left_report.get('family')} vs {right_report.get('family')})"
                ),
            }

        left_score = left_report.get("summary", {}).get("score", 0)
        right_score = right_report.get("summary", {}).get("score", 0)
        score_delta = round(right_score - left_score, 4)

        left_vars = self._collect_compare_variables(left_report)
        right_vars = self._collect_compare_variables(right_report)
        all_keys = set(list(left_vars.keys()) + list(right_vars.keys()))
        variable_deltas: dict[str, Any] = {}
        for key in all_keys:
            lv, rv = left_vars.get(key), right_vars.get(key)
            delta = round(rv - lv, 4) if isinstance(lv, (int, float)) and isinstance(rv, (int, float)) else None
            variable_deltas[key] = {"left": lv, "right": rv, "delta": delta}

        left_dims = left_report.get("summary", {}).get("dimension_scores", {})
        right_dims = right_report.get("summary", {}).get("dimension_scores", {})
        dim_keys = set(list(left_dims.keys()) + list(right_dims.keys()))
        dimension_deltas: dict[str, Any] = {}
        for key in dim_keys:
            lv, rv = left_dims.get(key, 0), right_dims.get(key, 0)
            dimension_deltas[key] = {"left": lv, "right": rv, "delta": round(rv - lv, 4)}

        likely_drivers = [
            key
            for key, value in variable_deltas.items()
            if not self._values_equal(value.get("left"), value.get("right"))
        ]
        likely_drivers += [k for k, v in dimension_deltas.items() if abs(v["delta"]) > 0.05 and k not in likely_drivers]

        direction = "improved" if score_delta > 0 else "regressed" if score_delta < 0 else "unchanged"
        summary = (
            f"Score {direction} by {abs(score_delta):.4f} "
            f"({left_score:.2f} → {right_score:.2f}). "
            f"{len(variable_deltas)} variable(s), {len(likely_drivers)} likely driver(s)."
        )

        return {
            "status": "completed",
            "left": {"name": left, "score": left_score, "variables": left_vars},
            "right": {"name": right, "score": right_score, "variables": right_vars},
            "score_delta": score_delta,
            "variable_deltas": variable_deltas,
            "dimension_deltas": dimension_deltas,
            "likely_drivers": likely_drivers,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _infer_family(self, description: str) -> str:
        lower = description.lower()
        if re.search(r"escalat|operator|human.in.the.loop|clarification", lower):
            return "operator_loop"
        return "simulation"

    def _build_spec(self, description: str, family: str) -> dict[str, Any]:
        system = (
            f"You are a simulation designer. Produce a {family} spec as JSON.\n"
            "Required: description, environment_description, initial_state_description, "
            "success_criteria, failure_modes, max_steps, actions.\n"
            "Output ONLY JSON."
        )
        text = self.llm_fn(system, f"Simulate: {description}")
        try:
            trimmed = text.strip()
            start = trimmed.index("{")
            end = trimmed.rindex("}") + 1
            parsed = json.loads(trimmed[start:end])
            if isinstance(parsed, dict):
                return parsed
        except (ValueError, json.JSONDecodeError):
            logger.debug("simulation.engine: suppressed ValueError, json.JSONDecodeError)", exc_info=True)

        return {
            "description": description,
            "environment_description": "Simulated environment",
            "initial_state_description": "Initial state",
            "success_criteria": ["achieve objective"],
            "failure_modes": ["timeout"],
            "max_steps": 10,
            "actions": [{"name": "act", "description": "Take action", "parameters": {}, "preconditions": [], "effects": []}],
        }

    def _generate_source(self, spec: dict[str, Any], name: str, family: str) -> str:
        if family == "operator_loop":
            from autocontext.scenarios.custom.operator_loop_codegen import generate_operator_loop_class
            from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec
            from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel
            ol_spec = OperatorLoopSpec(
                description=spec.get("description", ""),
                environment_description=spec.get("environment_description", ""),
                initial_state_description=spec.get("initial_state_description", ""),
                escalation_policy=spec.get("escalation_policy", {"escalation_threshold": "medium", "max_escalations": 5}),
                success_criteria=spec.get("success_criteria", []),
                failure_modes=spec.get("failure_modes", []),
                actions=[SimulationActionSpecModel(**a) for a in spec.get("actions", [])],
                max_steps=spec.get("max_steps", 10),
            )
            return generate_operator_loop_class(ol_spec, name)
        else:
            from autocontext.scenarios.custom.simulation_codegen import generate_simulation_class
            from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel, SimulationSpec
            sim_spec = SimulationSpec(
                description=spec.get("description", ""),
                environment_description=spec.get("environment_description", ""),
                initial_state_description=spec.get("initial_state_description", ""),
                success_criteria=spec.get("success_criteria", []),
                failure_modes=spec.get("failure_modes", []),
                actions=[SimulationActionSpecModel(**a) for a in spec.get("actions", [])],
                max_steps=spec.get("max_steps", 10),
            )
            return generate_simulation_class(sim_spec, name)

    def _persist(
        self,
        name: str,
        family: str,
        spec: dict[str, Any],
        source: str,
        scenario_dir: Path | None = None,
    ) -> Path:
        sim_dir = scenario_dir or self.knowledge_root / "_simulations" / name
        sim_dir.mkdir(parents=True, exist_ok=True)
        write_json(sim_dir / "spec.json", {"name": name, "family": family, **spec})
        (sim_dir / "scenario.py").write_text(source, encoding="utf-8")
        from autocontext.scenarios.families import get_family_marker
        (sim_dir / "scenario_type.txt").write_text(get_family_marker(family), encoding="utf-8")
        return sim_dir

    def _execute_single(self, source: str, name: str, seed: int, max_steps: int | None = None) -> dict[str, Any]:
        mod_name = f"autocontext._sim_gen.{name}_{seed}"
        spec = importlib.util.spec_from_loader(mod_name, loader=None)
        assert spec is not None
        mod = importlib.util.module_from_spec(spec)
        exec(source, mod.__dict__)  # noqa: S102
        sys.modules[mod_name] = mod

        # Find the scenario class
        from autocontext.scenarios.simulation import SimulationInterface
        cls = None
        for attr_name in dir(mod):
            attr = getattr(mod, attr_name)
            if isinstance(attr, type) and issubclass(attr, SimulationInterface) and attr is not SimulationInterface:
                cls = attr
                break
        if cls is None:
            # Try operator_loop interface
            from autocontext.scenarios.operator_loop import OperatorLoopInterface
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if isinstance(attr, type) and issubclass(attr, OperatorLoopInterface) and attr is not OperatorLoopInterface:
                    cls = attr
                    break

        if cls is None:
            return {"score": 0, "reasoning": "No scenario class found", "dimension_scores": {}}

        instance = cls()
        state = instance.initial_state(seed)
        limit = max_steps or getattr(instance, "max_steps", lambda: 20)()
        records: list[dict[str, Any]] = []

        from autocontext.scenarios.simulation import Action, ActionRecord, ActionResult, ActionTrace
        step_num = 0
        for _ in range(limit):
            if instance.is_terminal(state):
                break
            actions = instance.get_available_actions(state)
            if not actions:
                break
            action = Action(name=actions[0].name, parameters={})
            state_before = dict(state)
            result, state = instance.execute_action(state, action)
            step_num += 1
            records.append({
                "step": step_num,
                "action": action.name,
                "success": result.success,
                "state_before": state_before,
                "state_after": dict(state),
            })

        trace = ActionTrace(records=[
            ActionRecord(
                step=r["step"],
                action=Action(name=r["action"], parameters={}),
                result=ActionResult(success=r["success"], output="", state_changes={}),
                state_before=r["state_before"],
                state_after=r["state_after"],
            )
            for r in records
        ])
        eval_result = instance.evaluate_trace(trace, state)
        return {
            "score": round(eval_result.score, 4),
            "reasoning": eval_result.reasoning,
            "dimension_scores": eval_result.dimension_scores,
        }

    def _execute_sweep(
        self,
        description: str,
        family: str,
        name: str,
        base_spec: dict[str, Any],
        sweep: list[dict[str, Any]],
        max_steps: int | None,
        scenario_dir: Path,
        base_variables: dict[str, Any],
        runs: int,
    ) -> dict[str, Any]:
        combos = self._cartesian(sweep)
        results = []
        for i, variables in enumerate(combos):
            merged_variables = {**base_variables, **variables}
            variant_name = f"{name}__sweep_{i + 1}"
            variant_spec = self._apply_variables(base_spec, merged_variables)
            source = self._generate_source(variant_spec, variant_name, family)
            variant_dir = scenario_dir / "sweep" / str(i + 1)
            self._persist(variant_name, family, variant_spec, source, variant_dir)
            reruns = [self._execute_single(source, variant_name, seed, max_steps) for seed in range(max(1, runs))]
            aggregate = self._aggregate_runs(reruns)
            results.append({"variables": merged_variables, **aggregate})
        return {"dimensions": sweep, "runs": len(results) * max(1, runs), "results": results}

    def _aggregate_runs(self, results: list[dict[str, Any]]) -> dict[str, Any]:
        if not results:
            return {"score": 0, "reasoning": "No runs", "dimension_scores": {}}
        if len(results) == 1:
            return results[0]
        avg = round(sum(r["score"] for r in results) / len(results), 4)
        best = max(results, key=lambda r: r["score"])
        worst = min(results, key=lambda r: r["score"])
        return {
            "score": avg,
            "reasoning": f"Average across {len(results)} runs",
            "dimension_scores": results[0].get("dimension_scores", {}),
            "best_case": {"score": best["score"], "variables": {}},
            "worst_case": {"score": worst["score"], "variables": {}},
        }

    def _aggregate_sweep(self, sweep: dict[str, Any]) -> dict[str, Any]:
        results = sweep.get("results", [])
        if not results:
            return {"score": 0, "reasoning": "No sweep runs", "dimension_scores": {}}
        avg = round(sum(r["score"] for r in results) / len(results), 4)
        best = max(results, key=lambda r: r["score"])
        worst = min(results, key=lambda r: r["score"])
        return {
            "score": avg,
            "reasoning": f"Sweep: {len(results)} runs",
            "dimension_scores": results[0].get("dimension_scores", {}),
            "best_case": {"score": best["score"], "variables": best.get("variables", {})},
            "worst_case": {"score": worst["score"], "variables": worst.get("variables", {})},
        }

    def _build_assumptions(self, spec: dict[str, Any], family: str) -> list[str]:
        assumptions = [f"Modeled as {family} with {len(spec.get('actions', []))} actions"]
        if spec.get("max_steps"):
            assumptions.append(f"Bounded to {spec['max_steps']} steps")
        criteria = spec.get("success_criteria", [])
        if criteria:
            assumptions.append(f"Success: {', '.join(criteria)}")
        assumptions.append("Agent selects actions greedily")
        assumptions.append("Environment is deterministic given same seed")
        return assumptions

    def _build_warnings(self, family: str) -> list[str]:
        return [
            "Model-driven result only; not empirical evidence.",
            f"Simulated using the {family} family.",
            "Outcomes depend on LLM-generated spec quality.",
        ]

    def _load_report(self, name: str) -> dict[str, Any] | None:
        resolved = self._resolve_report(name)
        if resolved is None:
            return None
        report, _scenario_dir = resolved
        return report

    def _resolve_report(self, name: str) -> tuple[dict[str, Any], Path] | None:
        simulations_root = self.knowledge_root / "_simulations"
        report_path = simulations_root / name / "report.json"
        if report_path.exists():
            return read_json(report_path), report_path.parent

        if not simulations_root.exists():
            return None

        for scenario_dir in simulations_root.iterdir():
            if not scenario_dir.is_dir() or scenario_dir.name.startswith("_"):
                continue
            replay_path = scenario_dir / f"replay_{name}.json"
            if replay_path.exists():
                return read_json(replay_path), scenario_dir

        return None

    def _load_spec(self, scenario_dir: Path) -> dict[str, Any] | None:
        spec_path = scenario_dir / "spec.json"
        if not spec_path.exists():
            return None
        payload = read_json(spec_path)
        if not isinstance(payload, dict):
            return None
        payload.pop("name", None)
        payload.pop("family", None)
        return payload

    def _load_source(
        self,
        scenario_dir: Path,
        spec: dict[str, Any],
        name: str,
        family: str,
        variables: dict[str, Any],
    ) -> str:
        source_path = scenario_dir / "scenario.py"
        if not variables and source_path.exists():
            return source_path.read_text(encoding="utf-8")

        updated_spec = self._apply_variables(spec, variables)
        return self._generate_source(updated_spec, name, family)

    def _apply_variables(self, spec: dict[str, Any], variables: dict[str, Any] | None) -> dict[str, Any]:
        updated = deepcopy(spec)
        if not variables:
            return updated

        simulation_variables = dict(updated.get("simulation_variables", {}))
        for key, value in variables.items():
            if key in {"max_steps", "maxSteps"} and isinstance(value, (int, float)):
                updated["max_steps"] = int(value)
            else:
                simulation_variables[key] = value

        if simulation_variables:
            updated["simulation_variables"] = simulation_variables
        return updated

    def _resolve_execution_config(self, report: dict[str, Any]) -> dict[str, Any]:
        execution = report.get("execution") or {}
        if execution:
            return {
                "runs": max(1, int(execution.get("runs", 1))),
                "max_steps": execution.get("max_steps"),
                "sweep": execution.get("sweep") or [],
            }

        sweep = report.get("sweep")
        if sweep and sweep.get("results"):
            result_count = max(1, len(sweep.get("results", [])))
            runs = max(1, round(float(sweep.get("runs", result_count)) / result_count))
            return {
                "runs": runs,
                "max_steps": None,
                "sweep": sweep.get("dimensions") or [],
            }

        return {"runs": 1, "max_steps": None, "sweep": []}

    def _replay_sweep(
        self,
        *,
        original: dict[str, Any],
        scenario_dir: Path,
        family: str,
        base_name: str,
        base_spec: dict[str, Any],
        overrides: dict[str, Any],
        max_steps: int | None,
        runs: int,
    ) -> dict[str, Any]:
        original_sweep = original.get("sweep") or {}
        original_results = original_sweep.get("results") or []
        results = []

        for i, cell in enumerate(original_results):
            cell_variables = {**(cell.get("variables") or {}), **overrides}
            variant_name = f"{base_name}__sweep_{i + 1}"
            variant_spec = self._apply_variables(base_spec, cell_variables)
            source = self._generate_source(variant_spec, variant_name, family)
            variant_dir = scenario_dir / "sweep" / str(i + 1)
            self._persist(variant_name, family, variant_spec, source, variant_dir)
            reruns = [self._execute_single(source, variant_name, seed, max_steps) for seed in range(max(1, runs))]
            aggregate = self._aggregate_runs(reruns)
            results.append({"variables": cell_variables, **aggregate})

        return {
            "dimensions": original_sweep.get("dimensions") or [],
            "runs": len(results) * max(1, runs),
            "results": results,
        }

    def _collect_compare_variables(self, report: dict[str, Any]) -> dict[str, Any]:
        merged = dict(report.get("variables") or {})
        sweep = report.get("sweep") or {}
        results = sweep.get("results") or []
        if not results:
            return merged

        value_sets: dict[str, list[Any]] = {}
        for result in results:
            for key, value in (result.get("variables") or {}).items():
                entries = value_sets.setdefault(key, [])
                if not any(self._values_equal(existing, value) for existing in entries):
                    entries.append(value)

        for key, values in value_sets.items():
            if key in merged and len(values) == 1 and self._values_equal(merged[key], values[0]):
                continue
            merged[key] = values[0] if len(values) == 1 else values

        return merged

    def _values_equal(self, left: Any, right: Any) -> bool:
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    def _cartesian(self, dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not dimensions:
            return [{}]
        first, rest = dimensions[0], dimensions[1:]
        rest_combos = self._cartesian(rest)
        combos = []
        for val in first.get("values", []):
            for rc in rest_combos:
                combos.append({first["name"]: val, **rc})
        return combos
