"""Simulation engine — Python parity with TS SimulationEngine (AC-453).

Takes a plain-language description, builds a simulation spec via LLM,
executes trajectories/sweeps, and returns structured findings.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _generate_id() -> str:
    return f"sim_{uuid.uuid4().hex[:12]}"


def _derive_name(description: str) -> str:
    words = re.sub(r"[^a-z0-9\s]", "", description.lower()).split()
    return "_".join(w for w in words if len(w) > 2)[:4] or "simulation"


class SimulationEngine:
    """Plain-language simulation engine with sweep/replay/compare."""

    def __init__(self, llm_fn: Callable[[str, str], str], knowledge_root: Path) -> None:
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

        try:
            family = self._infer_family(description)
            spec = self._build_spec(description, family)
            if variables:
                spec.update(variables)

            source = self._generate_source(spec, name, family)
            scenario_dir = self._persist(name, family, spec, source)

            if sweep:
                sweep_result = self._execute_sweep(source, name, sweep, max_steps)
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
                "variables": variables or {},
                "sweep": sweep_result,
                "summary": summary,
                "artifacts": {
                    "scenario_dir": str(scenario_dir),
                    "report_path": str(scenario_dir / "report.json"),
                },
                "warnings": warnings,
            }
            (scenario_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
            return report

        except Exception as exc:
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
        sim_dir = self.knowledge_root / "_simulations" / id
        report_path = sim_dir / "report.json"
        if not report_path.exists():
            return {"status": "failed", "error": f"Simulation '{id}' not found", "name": id}

        original = json.loads(report_path.read_text(encoding="utf-8"))
        original_score = original.get("summary", {}).get("score", 0)

        source_path = sim_dir / "scenario.py"
        if not source_path.exists():
            return {"status": "failed", "error": f"Source not found for '{id}'", "name": id}

        source = source_path.read_text(encoding="utf-8")
        merged_vars = {**(original.get("variables") or {}), **(variables or {})}
        result = self._execute_single(source, id, 42, max_steps)

        replay_report = {
            **original,
            "id": _generate_id(),
            "summary": result,
            "variables": merged_vars,
            "replay_of": id,
            "original_score": original_score,
            "score_delta": round(result["score"] - original_score, 4),
            "status": "completed",
        }

        replay_path = sim_dir / f"replay_{replay_report['id']}.json"
        replay_path.write_text(json.dumps(replay_report, indent=2), encoding="utf-8")
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

        left_score = left_report.get("summary", {}).get("score", 0)
        right_score = right_report.get("summary", {}).get("score", 0)
        score_delta = round(right_score - left_score, 4)

        left_vars = left_report.get("variables", {})
        right_vars = right_report.get("variables", {})
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

        likely_drivers = [k for k, v in variable_deltas.items() if v.get("delta") and abs(v["delta"]) > 0]
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
            return json.loads(trimmed[start:end])
        except (ValueError, json.JSONDecodeError):
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

    def _persist(self, name: str, family: str, spec: dict[str, Any], source: str) -> Path:
        sim_dir = self.knowledge_root / "_simulations" / name
        sim_dir.mkdir(parents=True, exist_ok=True)
        (sim_dir / "spec.json").write_text(json.dumps({"name": name, "family": family, **spec}, indent=2), encoding="utf-8")
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
        self, source: str, name: str, sweep: list[dict[str, Any]], max_steps: int | None,
    ) -> dict[str, Any]:
        combos = self._cartesian(sweep)
        results = []
        for i, variables in enumerate(combos):
            r = self._execute_single(source, name, i, max_steps)
            results.append({"variables": variables, **r})
        return {"dimensions": sweep, "runs": len(results), "results": results}

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
        path = self.knowledge_root / "_simulations" / name / "report.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

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
