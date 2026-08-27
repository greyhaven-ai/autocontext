from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

from autocontext.execution.executors.local import LocalExecutor
from autocontext.scenarios.base import ExecutionLimits
from autocontext.scenarios.custom.codegen import generate_scenario_class
from autocontext.scenarios.custom.codegen_security import generated_class_name
from autocontext.scenarios.custom.loader import load_custom_scenario
from autocontext.scenarios.custom.spec import (
    Constraint,
    EnvironmentVariable,
    ScenarioSpec,
    ScoringComponent,
    StrategyParam,
)
from autocontext.scenarios.custom.validator import validate_by_execution, validate_generated_code, validate_spec


def _make_spec(**overrides: object) -> ScenarioSpec:
    defaults: dict[str, object] = {
        "name": "test_scenario",
        "display_name": "Test Scenario",
        "description": "A test scenario for unit tests.",
        "strategy_interface_description": "Return JSON with keys `alpha` and `beta`, floats in [0,1].",
        "evaluation_criteria": "Optimize combined alpha-beta scoring.",
        "strategy_params": [
            StrategyParam(name="alpha", description="Primary factor", min_value=0.0, max_value=1.0, default=0.5),
            StrategyParam(name="beta", description="Secondary factor", min_value=0.0, max_value=1.0, default=0.5),
        ],
        "constraints": [
            Constraint(expression="alpha + beta", operator="<=", threshold=1.5, description="alpha + beta must be <= 1.5"),
        ],
        "environment_variables": [
            EnvironmentVariable(name="difficulty", description="Task difficulty", low=0.2, high=0.8),
        ],
        "scoring_components": [
            ScoringComponent(
                name="effectiveness", description="Overall effectiveness",
                formula_terms={"alpha": 0.6, "beta": 0.4}, noise_range=(-0.05, 0.05),
            ),
            ScoringComponent(
                name="efficiency", description="Resource efficiency",
                formula_terms={"beta": 0.7, "alpha": 0.3}, noise_range=(-0.03, 0.03),
            ),
        ],
        "final_score_weights": {"effectiveness": 0.6, "efficiency": 0.4},
        "win_threshold": 0.55,
        "observation_constraints": ["Balance alpha and beta for optimal results."],
    }
    defaults.update(overrides)
    return ScenarioSpec(**defaults)  # type: ignore[arg-type]


class TestScenarioSpecSerialization:
    def test_scoring_component_defaults_are_real_containers(self) -> None:
        component = ScoringComponent(name="effectiveness", description="Overall effectiveness")

        assert component.formula_terms == {}
        assert component.noise_range == (-0.05, 0.05)

    def test_round_trip(self) -> None:
        spec = _make_spec()
        data = spec.to_dict()
        restored = ScenarioSpec.from_dict(data)
        assert restored.name == spec.name
        assert restored.display_name == spec.display_name
        assert len(restored.strategy_params) == len(spec.strategy_params)
        assert len(restored.scoring_components) == len(spec.scoring_components)
        assert restored.final_score_weights == spec.final_score_weights
        assert restored.win_threshold == spec.win_threshold

    def test_json_round_trip(self) -> None:
        spec = _make_spec()
        json_str = json.dumps(spec.to_dict())
        data = json.loads(json_str)
        restored = ScenarioSpec.from_dict(data)
        assert restored.name == spec.name

    def test_save_and_load(self, tmp_path: Path) -> None:
        spec = _make_spec()
        spec.save(tmp_path)
        loaded = ScenarioSpec.load(tmp_path)
        assert loaded.name == spec.name
        assert loaded.display_name == spec.display_name
        assert len(loaded.strategy_params) == 2


class TestValidateSpec:
    def test_valid_spec(self) -> None:
        spec = _make_spec()
        errors = validate_spec(spec)
        assert errors == []

    def test_bad_name(self) -> None:
        spec = _make_spec(name="bad name!")
        errors = validate_spec(spec)
        assert any("identifier" in e for e in errors)

    def test_empty_name(self) -> None:
        spec = _make_spec(name="")
        errors = validate_spec(spec)
        assert any("identifier" in e or "empty" in e for e in errors)

    def test_duplicate_params(self) -> None:
        spec = _make_spec(strategy_params=[
            StrategyParam(name="alpha", description="A", default=0.5),
            StrategyParam(name="alpha", description="B", default=0.5),
        ])
        errors = validate_spec(spec)
        assert any("unique" in e for e in errors)

    def test_no_params(self) -> None:
        spec = _make_spec(strategy_params=[])
        errors = validate_spec(spec)
        assert any("at least one" in e for e in errors)

    def test_constraint_refs_unknown_param(self) -> None:
        spec = _make_spec(constraints=[
            Constraint(expression="alpha + unknown", operator="<=", threshold=1.5, description="bad"),
        ])
        errors = validate_spec(spec)
        assert any("unknown" in e for e in errors)

    def test_scoring_refs_unknown_param(self) -> None:
        spec = _make_spec(scoring_components=[
            ScoringComponent(name="bad", description="bad", formula_terms={"nonexistent": 1.0}),
        ], final_score_weights={"bad": 1.0})
        errors = validate_spec(spec)
        assert any("nonexistent" in e for e in errors)

    def test_default_scoring_component_does_not_crash_validation(self) -> None:
        spec = _make_spec(
            scoring_components=[ScoringComponent(name="baseline", description="baseline")],
            final_score_weights={"baseline": 1.0},
        )

        errors = validate_spec(spec)

        assert errors == []

    def test_weights_dont_sum_to_one(self) -> None:
        spec = _make_spec(final_score_weights={"effectiveness": 0.3, "efficiency": 0.3})
        errors = validate_spec(spec)
        assert any("sum to" in e for e in errors)

    def test_weights_ref_unknown_component(self) -> None:
        spec = _make_spec(final_score_weights={"effectiveness": 0.6, "nonexistent": 0.4})
        errors = validate_spec(spec)
        assert any("nonexistent" in e for e in errors)

    def test_param_min_gte_max(self) -> None:
        spec = _make_spec(strategy_params=[
            StrategyParam(name="alpha", description="A", min_value=1.0, max_value=0.5, default=0.5),
            StrategyParam(name="beta", description="B", default=0.5),
        ])
        errors = validate_spec(spec)
        assert any("min_value" in e for e in errors)

    def test_source_shaped_identifiers_are_rejected(self) -> None:
        payload = 'field"\n    import os\n    #'
        spec = _make_spec(
            name=payload,
            family=payload,
            strategy_params=[StrategyParam(name=payload, description="unsafe")],
            constraints=[],
            environment_variables=[EnvironmentVariable(name=payload, description="unsafe")],
            scoring_components=[ScoringComponent(name=payload, description="unsafe")],
            final_score_weights={payload: 1.0},
        )

        errors = "\n".join(validate_spec(spec))

        assert "name must" in errors
        assert "family must" in errors
        assert "strategy_param name" in errors
        assert "environment_variable name" in errors
        assert "scoring_component name" in errors

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_generated_numbers_are_rejected(self, value: float) -> None:
        spec = _make_spec(win_threshold=value)

        assert any("win_threshold" in error for error in validate_spec(spec))
        with pytest.raises(ValueError, match="finite"):
            generate_scenario_class(spec)


class TestCodegen:
    def test_produces_parseable_python(self) -> None:
        spec = _make_spec()
        source = generate_scenario_class(spec)
        ast.parse(source)

    def test_validate_generated_code(self) -> None:
        spec = _make_spec()
        source = generate_scenario_class(spec)
        errors = validate_generated_code(source)
        assert errors == []

    def test_bad_code_detected(self) -> None:
        errors = validate_generated_code("def broken(")
        assert len(errors) > 0

    def test_class_name_correct(self) -> None:
        spec = _make_spec(name="my_cool_game")
        source = generate_scenario_class(spec)
        assert "class MyCoolGameScenario" in source

    def test_imports_present(self) -> None:
        spec = _make_spec()
        source = generate_scenario_class(spec)
        assert "from autocontext.scenarios.base import" in source
        assert "import random" in source

    @pytest.mark.parametrize(
        "payload",
        [
            "quote'\"value",
            "line one\nline two",
            'value"\n    injected = True\n    #',
            'value"\n            import os\n            class Injected:\n                pass\n            #',
        ],
    )
    def test_untrusted_values_remain_literals(self, payload: str) -> None:
        spec = _make_spec(
            name=payload,
            family=payload,
            display_name=payload,
            description=payload,
            strategy_interface_description=payload,
            evaluation_criteria=payload,
            strategy_params=[
                StrategyParam(name="alpha", description=payload),
                StrategyParam(name=payload, description=payload),
            ],
            constraints=[
                Constraint(
                    expression="alpha",
                    operator="<=",
                    threshold=1.0,
                    description=payload,
                ),
            ],
            environment_variables=[EnvironmentVariable(name=payload, description=payload)],
            scoring_components=[
                ScoringComponent(
                    name=payload,
                    description=payload,
                    formula_terms={"alpha": 0.5, payload: 0.5},
                ),
            ],
            final_score_weights={payload: 1.0},
            observation_constraints=[payload],
        )

        source = generate_scenario_class(spec)
        tree = ast.parse(source)

        assert validate_generated_code(source) == []
        assert payload in {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
        scenario_classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        assert len(scenario_classes) == 1
        assert not any(
            isinstance(node, ast.Import | ast.ImportFrom | ast.ClassDef)
            for node in ast.walk(scenario_classes[0])
            if node is not scenario_classes[0]
        )
        compile(source, "<generated-scenario>", "exec")

    @pytest.mark.parametrize(
        "class_body",
        [
            "    injected = True\n",
            "    name = 'overridden'\n",
            "    import os\n",
            "    class Injected:\n        pass\n",
        ],
    )
    def test_structural_validation_rejects_injected_class_body(self, class_body: str) -> None:
        source = generate_scenario_class(_make_spec())
        injected = source.replace(
            '    name = "test_scenario"\n',
            '    name = "test_scenario"\n' + class_body,
            1,
        )

        assert validate_generated_code(injected)

    @pytest.mark.parametrize(
        "payload",
        [
            "name'\"",
            "name\nimport os",
            "name\nclass Injected:\n    pass",
        ],
    )
    def test_generated_class_names_are_safe_identifiers(self, payload: str) -> None:
        class_name = generated_class_name(payload, "Scenario")

        assert class_name.isidentifier()
        ast.parse(f"class {class_name}:\n    pass\n")


class TestGeneratedScenarioExecution:
    def _load_class(self, spec: ScenarioSpec, tmp_path: Path) -> type:
        source = generate_scenario_class(spec)
        scenario_dir = tmp_path / spec.name
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario.py").write_text(source)
        return load_custom_scenario(tmp_path, spec.name)

    def test_execute_match(self, tmp_path: Path) -> None:
        spec = _make_spec()
        cls = self._load_class(spec, tmp_path)
        instance = cls()
        result = instance.execute_match(strategy={"alpha": 0.5, "beta": 0.5}, seed=42)
        assert 0.0 <= result.score <= 1.0
        assert result.winner in ("challenger", "incumbent")

    def test_deterministic(self, tmp_path: Path) -> None:
        spec = _make_spec()
        cls = self._load_class(spec, tmp_path)
        instance = cls()
        r1 = instance.execute_match(strategy={"alpha": 0.6, "beta": 0.4}, seed=123)
        r2 = instance.execute_match(strategy={"alpha": 0.6, "beta": 0.4}, seed=123)
        assert r1.score == r2.score

    def test_validation_errors(self, tmp_path: Path) -> None:
        spec = _make_spec()
        cls = self._load_class(spec, tmp_path)
        errors = validate_by_execution(cls, spec, seeds=3)
        assert errors == []

    def test_invalid_strategy_rejected(self, tmp_path: Path) -> None:
        spec = _make_spec()
        cls = self._load_class(spec, tmp_path)
        instance = cls()
        result = instance.execute_match(strategy={"alpha": 5.0, "beta": 0.5}, seed=1)
        assert result.score == 0.0

    def test_missing_param_rejected(self, tmp_path: Path) -> None:
        spec = _make_spec()
        cls = self._load_class(spec, tmp_path)
        instance = cls()
        result = instance.execute_match(strategy={"alpha": 0.5}, seed=1)
        assert result.score == 0.0


class TestDynamicLoader:
    def test_loads_and_registers_in_sys_modules(self, tmp_path: Path) -> None:
        spec = _make_spec()
        source = generate_scenario_class(spec)
        scenario_dir = tmp_path / spec.name
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario.py").write_text(source)

        module_name = f"autocontext.scenarios.custom.generated.{spec.name}"
        if module_name in sys.modules:
            del sys.modules[module_name]

        cls = load_custom_scenario(tmp_path, spec.name)
        assert module_name in sys.modules
        assert cls.name == spec.name  # type: ignore[attr-defined]

    def test_scenario_registry_insertion(self, tmp_path: Path) -> None:
        from autocontext.scenarios import SCENARIO_REGISTRY

        spec = _make_spec(name="test_registry_insert")
        source = generate_scenario_class(spec)
        scenario_dir = tmp_path / spec.name
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario.py").write_text(source)

        cls = load_custom_scenario(tmp_path, spec.name)
        SCENARIO_REGISTRY[spec.name] = cls
        assert spec.name in SCENARIO_REGISTRY

        # Cleanup
        del SCENARIO_REGISTRY[spec.name]

    def test_local_executor_runs_dynamic_custom_scenario_in_subprocess(self, tmp_path: Path) -> None:
        spec = _make_spec(name="test_subprocess_dynamic_loader")
        source = generate_scenario_class(spec)
        scenario_dir = tmp_path / spec.name
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario.py").write_text(source)

        scenario = load_custom_scenario(tmp_path, spec.name)()
        executor = LocalExecutor()

        result, replay = executor.execute(
            scenario=scenario,
            strategy={"alpha": 0.5, "beta": 0.5},
            seed=42,
            limits=ExecutionLimits(timeout_seconds=10.0, max_memory_mb=256),
        )

        assert 0.0 <= result.score <= 1.0
        assert replay.scenario == spec.name
