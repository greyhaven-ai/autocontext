from __future__ import annotations

import sys
from importlib import import_module
from pathlib import Path
from typing import get_args

REPO_ROOT = Path(__file__).resolve().parents[2]
PY_CORE_SRC = REPO_ROOT / "packages" / "python" / "core" / "src"
if str(PY_CORE_SRC) not in sys.path:
    sys.path.insert(0, str(PY_CORE_SRC))

core_package = import_module("autocontext_core")
CompletionResult = core_package.CompletionResult
ContextBudget = core_package.ContextBudget
PromptBundle = core_package.PromptBundle
ProviderError = core_package.ProviderError
build_prompt_bundle = core_package.build_prompt_bundle
estimate_tokens = core_package.estimate_tokens
expected_score = core_package.expected_score
package_role = core_package.package_role
package_topology_version = core_package.package_topology_version
update_elo = core_package.update_elo


def test_python_core_package_identity() -> None:
    assert package_role == "core"
    assert package_topology_version == 1


def test_python_core_reexports_elo_primitives() -> None:
    assert expected_score(1500, 1500) == 0.5
    assert update_elo(1500, 1500, 1) == 1512


def test_python_core_reexports_prompt_budget_helpers() -> None:
    assert estimate_tokens("abcdabcd") == 2

    budget = ContextBudget(max_tokens=20)
    result = budget.apply({"playbook": "12345678901234567890" * 20, "hints": "keep-me"})

    assert result["hints"] == "keep-me"
    assert "truncated for context budget" in result["playbook"]


def test_python_core_reexports_prompt_bundle_assembly() -> None:
    Observation = core_package.Observation

    bundle = build_prompt_bundle(
        scenario_rules="Follow the rules.",
        strategy_interface="Return JSON.",
        evaluation_criteria="Maximize score.",
        previous_summary="",
        observation=Observation(narrative="Observe", state={}, constraints=[]),
        current_playbook="",
        available_tools="",
        semantic_compaction=False,
    )

    assert isinstance(bundle, PromptBundle)
    assert "Follow the rules." in bundle.competitor
    assert "Findings, Root Causes, Actionable Recommendations" in bundle.analyst
    assert "<!-- PLAYBOOK_START -->" in bundle.coach


def test_python_core_reexports_provider_primitives() -> None:
    result = CompletionResult(text="done", model="test-model", usage={"input_tokens": 3}, cost_usd=0.01)

    assert result.text == "done"
    assert isinstance(ProviderError("boom"), Exception)


def test_python_core_reexports_rubric_coherence_helpers() -> None:
    RubricCoherenceResult = core_package.RubricCoherenceResult
    check_rubric_coherence = core_package.check_rubric_coherence

    coherence = check_rubric_coherence("Write a brief but comprehensive and concise explanation.")

    assert isinstance(coherence, RubricCoherenceResult)
    assert coherence.is_coherent is False
    assert "contradictory" in coherence.warnings[0]


def test_python_core_reexports_scenario_value_objects() -> None:
    Observation = core_package.Observation
    Result = core_package.Result
    ReplayEnvelope = core_package.ReplayEnvelope
    GenerationMetrics = core_package.GenerationMetrics
    ExecutionLimits = core_package.ExecutionLimits

    observation = Observation(narrative="Observe", state={"board": "ready"}, constraints=["no network"])
    result = Result(score=0.8, summary="solid", validation_errors=[])
    replay = ReplayEnvelope(scenario="grid_ctf", seed=7, narrative="turn-by-turn")
    metrics = GenerationMetrics(
        generation_index=0,
        mean_score=0.75,
        best_score=0.8,
        elo=1512,
        wins=2,
        losses=1,
        runs=3,
        gate_decision="promote",
    )
    limits = ExecutionLimits(timeout_seconds=30.0, max_memory_mb=1024, network_access=False)

    assert observation.state["board"] == "ready"
    assert result.passed_validation is True
    assert replay.seed == 7
    assert metrics.gate_decision == "promote"
    assert limits.max_memory_mb == 1024


def test_python_core_reexports_judge_value_objects() -> None:
    DisagreementMetrics = core_package.DisagreementMetrics
    JudgeResult = core_package.JudgeResult
    ParseMethod = core_package.ParseMethod

    disagreement = DisagreementMetrics(
        score_std_dev=0.12,
        score_range=(0.7, 0.9),
        sample_scores=[0.7, 0.9],
        is_high_disagreement=True,
        sample_count=2,
    )
    result = JudgeResult(
        score=0.8,
        reasoning="solid",
        dimension_scores={"accuracy": 0.9},
        parse_method="markers",
        disagreement=disagreement,
    )

    assert disagreement.to_dict()["sample_count"] == 2
    assert result.dimension_scores["accuracy"] == 0.9
    assert result.parse_method == "markers"
    assert "markers" in get_args(ParseMethod)


def test_python_core_reexports_scenario_contract_interface() -> None:
    Observation = core_package.Observation
    Result = core_package.Result
    ScenarioInterface = core_package.ScenarioInterface

    class DemoScenario(ScenarioInterface):
        name = "demo"

        def describe_rules(self) -> str:
            return "rules"

        def describe_strategy_interface(self) -> str:
            return "return json"

        def describe_evaluation_criteria(self) -> str:
            return "maximize score"

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed}

        def get_observation(self, state: dict[str, object], player_id: str):
            return Observation(narrative=f"observe {player_id}", state=dict(state), constraints=[])

        def validate_actions(self, state: dict[str, object], player_id: str, actions: dict[str, object]) -> tuple[bool, str]:
            return True, ""

        def step(self, state: dict[str, object], actions: dict[str, object]) -> dict[str, object]:
            return {**state, **actions, "terminal": True}

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", True))

        def get_result(self, state: dict[str, object]):
            return Result(score=1.0, summary="done")

        def replay_to_narrative(self, replay: list[dict[str, object]]) -> str:
            return f"{len(replay)} events"

        def render_frame(self, state: dict[str, object]) -> dict[str, object]:
            return dict(state)

    scenario = DemoScenario()

    assert isinstance(scenario, ScenarioInterface)
    assert scenario.describe_rules() == "rules"
    assert scenario.get_observation({"board": "ready"}, "challenger").narrative == "observe challenger"
    assert scenario.execute_match({"move": "hold"}, 7).passed_validation is True


def test_python_core_reexports_agent_task_family_contracts() -> None:
    AgentTaskInterface = core_package.AgentTaskInterface
    AgentTaskResult = core_package.AgentTaskResult

    class DemoAgentTask(AgentTaskInterface):
        def get_task_prompt(self, state: dict) -> str:
            return f"solve {state['topic']}"

        def evaluate_output(
            self,
            output: str,
            state: dict,
            reference_context: str | None = None,
            required_concepts: list[str] | None = None,
            calibration_examples: list[dict] | None = None,
            pinned_dimensions: list[str] | None = None,
        ):
            return AgentTaskResult(score=0.8, reasoning=f"accepted {output}")

        def get_rubric(self) -> str:
            return "be accurate"

        def initial_state(self, seed: int | None = None) -> dict:
            return {"seed": seed, "topic": "grid_ctf"}

        def describe_task(self) -> str:
            return "demo task"

    task = DemoAgentTask()
    result = task.evaluate_output("answer", task.initial_state(7))

    assert isinstance(task, AgentTaskInterface)
    assert task.get_task_prompt(task.initial_state()) == "solve grid_ctf"
    assert result.score == 0.8
    assert task.prepare_context({"topic": "grid_ctf"}) == {"topic": "grid_ctf"}
    assert task.validate_context({"topic": "grid_ctf"}) == []
    assert task.revise_output("answer", result, task.initial_state()) == "answer"
    assert task.verify_facts("answer", task.initial_state()) is None


def test_python_core_reexports_artifact_editing_family_contracts() -> None:
    Artifact = core_package.Artifact
    ArtifactDiff = core_package.ArtifactDiff
    ArtifactEditingInterface = core_package.ArtifactEditingInterface
    ArtifactEditingResult = core_package.ArtifactEditingResult
    ArtifactValidationResult = core_package.ArtifactValidationResult

    original = [Artifact(path="README.md", content="old", content_type="text")]
    edited = [
        Artifact(path="README.md", content="new", content_type="text"),
        Artifact(path="notes.md", content="extra", content_type="text"),
    ]

    class DemoArtifactEditing(ArtifactEditingInterface):
        name = "demo-artifact"

        def describe_task(self) -> str:
            return "edit files"

        def get_rubric(self) -> str:
            return "be correct"

        def initial_artifacts(self, seed: int | None = None):
            return list(original)

        def get_edit_prompt(self, artifacts):
            return f"edit {len(artifacts)} files"

        def validate_artifact(self, artifact):
            return ArtifactValidationResult(valid=True, errors=[], warnings=[])

        def evaluate_edits(self, original_artifacts, edited_artifacts):
            diffs = self.compute_diffs(original_artifacts, edited_artifacts)
            return ArtifactEditingResult(
                score=0.8,
                reasoning="accepted",
                dimension_scores={"correctness": 0.9},
                diffs=diffs,
                validation=ArtifactValidationResult(valid=True, errors=[], warnings=[]),
                artifacts_modified=len(diffs),
                artifacts_valid=len(edited_artifacts),
            )

    scenario = DemoArtifactEditing()
    result = scenario.evaluate_edits(original, edited)
    state = scenario.initial_state(seed=7)
    recreated_artifact = Artifact.from_dict(original[0].to_dict())
    recreated_diff = ArtifactDiff.from_dict(result.diffs[0].to_dict())

    assert isinstance(scenario, ArtifactEditingInterface)
    assert scenario.get_edit_prompt(original) == "edit 1 files"
    assert state["seed"] == 7
    assert len(state["artifacts"]) == 1
    assert result.artifacts_modified == 2
    assert result.artifacts_valid == 2
    assert result.validation.valid is True
    assert recreated_artifact.path == "README.md"
    assert recreated_diff.operation == "modify"


def test_python_core_reexports_simulation_family_contracts() -> None:
    Action = core_package.Action
    ActionRecord = core_package.ActionRecord
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    ActionTrace = core_package.ActionTrace
    EnvironmentSpec = core_package.EnvironmentSpec
    SimulationInterface = core_package.SimulationInterface
    SimulationResult = core_package.SimulationResult

    inspect = ActionSpec(name="inspect", description="Inspect the board", parameters={"target": "cell"})
    environment = EnvironmentSpec(
        name="demo-sim",
        description="A simple simulation",
        available_actions=[inspect],
        initial_state_description="board ready",
        success_criteria=["finish safely"],
    )
    action = Action(name="inspect", parameters={"target": "cell-1"}, reasoning="check status")
    action_result = ActionResult(success=True, output="ok", state_changes={"terminal": True})
    trace = ActionTrace(
        records=[
            ActionRecord(
                step=1,
                action=action,
                result=action_result,
                state_before={"step": 0},
                state_after={"step": 1, "terminal": True},
            )
        ]
    )

    class DemoSimulation(SimulationInterface):
        name = "demo-sim"

        def describe_scenario(self) -> str:
            return "demo simulation"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "step": 0}

        def get_available_actions(self, state: dict[str, object]):
            return [inspect]

        def execute_action(self, state: dict[str, object], action):
            return action_result, {**state, "step": 1, "terminal": True}

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=1.0,
                reasoning=f"{len(trace.records)} actions",
                dimension_scores={"workflow": 1.0},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=len(trace.records),
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "finish safely"

    scenario = DemoSimulation()
    evaluation = scenario.evaluate_trace(trace, {"terminal": True})
    recreated_trace = ActionTrace.from_dict(trace.to_dict())

    assert isinstance(scenario, SimulationInterface)
    assert scenario.describe_rules().startswith("demo simulation")
    assert "inspect" in scenario.describe_strategy_interface()
    assert scenario.get_observation({"step": 0}, "challenger").constraints == ["max_steps=50"]
    assert scenario.validate_actions({"step": 0}, "challenger", {"actions": [{"name": "inspect", "parameters": {}}]}) == (
        True,
        "ok",
    )
    assert trace.success_rate == 1.0
    assert recreated_trace.actions[0].name == "inspect"
    assert evaluation.workflow_complete is True
    assert evaluation.actions_taken == 1


def test_python_core_reexports_negotiation_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    EnvironmentSpec = core_package.EnvironmentSpec
    HiddenPreferences = core_package.HiddenPreferences
    NegotiationInterface = core_package.NegotiationInterface
    NegotiationResult = core_package.NegotiationResult
    NegotiationRound = core_package.NegotiationRound
    OpponentModel = core_package.OpponentModel
    SimulationResult = core_package.SimulationResult

    offer = ActionSpec(name="offer", description="Make an offer", parameters={"price": "number"})
    environment = EnvironmentSpec(
        name="demo-negotiation",
        description="Negotiate over price",
        available_actions=[offer],
        initial_state_description="start bargaining",
        success_criteria=["reach a deal"],
    )
    preferences = HiddenPreferences(
        priorities={"price": 1.0},
        reservation_value=0.4,
        aspiration_value=0.9,
        batna_description="walk away",
    )
    rounds = [
        NegotiationRound(
            round_number=1,
            offer={"price": 0.6},
            counter_offer={"price": 0.7},
            accepted=False,
            agent_reasoning="start near midpoint",
        )
    ]
    opponent_model = OpponentModel(
        inferred_priorities={"price": 1.0},
        inferred_reservation=0.5,
        strategy_hypothesis="anchoring",
        confidence=0.8,
    )

    class DemoNegotiation(NegotiationInterface):
        name = "demo-negotiation"

        def describe_scenario(self) -> str:
            return "demo negotiation"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "terminal": False}

        def get_available_actions(self, state: dict[str, object]):
            return [offer]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="offer recorded", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.9,
                reasoning="completed",
                dimension_scores={"workflow": 0.9},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "reach a deal"

        def get_hidden_preferences(self, state: dict[str, object]):
            return preferences

        def get_rounds(self, state: dict[str, object]):
            return list(rounds)

        def get_opponent_model(self, state: dict[str, object]):
            return opponent_model

        def update_opponent_model(self, state: dict[str, object], model):
            return {**state, "opponent_model": model.to_dict()}

        def evaluate_negotiation(self, state: dict[str, object]):
            return NegotiationResult(
                score=0.85,
                reasoning="strong deal",
                dimension_scores={"deal_quality": 0.9},
                deal_value=0.75,
                rounds_used=1,
                max_rounds=5,
                opponent_model_accuracy=0.8,
                value_claimed_ratio=0.6,
            )

    scenario = DemoNegotiation()
    recreated_preferences = HiddenPreferences.from_dict(preferences.to_dict())
    recreated_round = NegotiationRound.from_dict(rounds[0].to_dict())
    recreated_model = OpponentModel.from_dict(opponent_model.to_dict())
    evaluation = scenario.evaluate_negotiation({"terminal": True})
    updated_state = scenario.update_opponent_model({"seed": 7}, opponent_model)

    assert isinstance(scenario, NegotiationInterface)
    assert scenario.describe_scenario() == "demo negotiation"
    assert scenario.get_hidden_preferences({}).reservation_value == 0.4
    assert scenario.get_rounds({})[0].round_number == 1
    assert scenario.get_opponent_model({}).strategy_hypothesis == "anchoring"
    assert recreated_preferences.batna_description == "walk away"
    assert recreated_round.counter_offer == {"price": 0.7}
    assert recreated_model.confidence == 0.8
    assert updated_state["opponent_model"]["confidence"] == 0.8
    assert evaluation.deal_value == 0.75
    assert evaluation.value_claimed_ratio == 0.6


def test_python_core_reexports_investigation_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    EnvironmentSpec = core_package.EnvironmentSpec
    EvidenceChain = core_package.EvidenceChain
    EvidenceItem = core_package.EvidenceItem
    InvestigationInterface = core_package.InvestigationInterface
    InvestigationResult = core_package.InvestigationResult
    SimulationResult = core_package.SimulationResult

    inspect = ActionSpec(name="inspect", description="Inspect the system", parameters={"target": "service"})
    environment = EnvironmentSpec(
        name="demo-investigation",
        description="Investigate an incident",
        available_actions=[inspect],
        initial_state_description="alerts firing",
        success_criteria=["identify root cause"],
    )
    evidence = [
        EvidenceItem(
            id="e-1",
            content="error logs spike on checkout",
            source="logs",
            relevance=0.9,
            is_red_herring=False,
        ),
        EvidenceItem(
            id="e-2",
            content="disk alert on analytics",
            source="monitoring",
            relevance=0.2,
            is_red_herring=True,
        ),
    ]
    chain = EvidenceChain(items=[evidence[0]], reasoning="checkout errors align with the incident")

    class DemoInvestigation(InvestigationInterface):
        name = "demo-investigation"

        def describe_scenario(self) -> str:
            return "demo investigation"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "terminal": False}

        def get_available_actions(self, state: dict[str, object]):
            return [inspect]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="evidence gathered", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.9,
                reasoning="completed",
                dimension_scores={"workflow": 0.9},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "identify root cause"

        def get_evidence_pool(self, state: dict[str, object]):
            return list(evidence)

        def evaluate_evidence_chain(self, chain, state: dict[str, object]) -> float:
            return 0.95 if not chain.contains_red_herring else 0.1

        def evaluate_diagnosis(self, diagnosis: str, evidence_chain, state: dict[str, object]):
            return InvestigationResult(
                score=0.88,
                reasoning="strong diagnosis",
                dimension_scores={"accuracy": 0.9},
                diagnosis=diagnosis,
                evidence_collected=len(evidence_chain.items),
                red_herrings_avoided=1,
                red_herrings_followed=0,
                diagnosis_correct=True,
            )

    scenario = DemoInvestigation()
    recreated_item = EvidenceItem.from_dict(evidence[0].to_dict())
    recreated_chain = EvidenceChain.from_dict(chain.to_dict())
    evaluation = scenario.evaluate_diagnosis("checkout db saturation", chain, {"terminal": True})

    assert isinstance(scenario, InvestigationInterface)
    assert scenario.describe_scenario() == "demo investigation"
    assert scenario.get_evidence_pool({})[0].id == "e-1"
    assert scenario.evaluate_evidence_chain(chain, {}) == 0.95
    assert chain.contains_red_herring is False
    assert recreated_item.source == "logs"
    assert recreated_chain.reasoning.startswith("checkout")
    assert evaluation.diagnosis_correct is True
    assert evaluation.red_herrings_avoided == 1


def test_python_core_reexports_workflow_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    CompensationAction = core_package.CompensationAction
    EnvironmentSpec = core_package.EnvironmentSpec
    SideEffect = core_package.SideEffect
    SimulationResult = core_package.SimulationResult
    WorkflowInterface = core_package.WorkflowInterface
    WorkflowResult = core_package.WorkflowResult
    WorkflowStep = core_package.WorkflowStep

    submit = ActionSpec(name="submit", description="Submit the order", parameters={"order_id": "string"})
    environment = EnvironmentSpec(
        name="demo-workflow",
        description="Run a transactional workflow",
        available_actions=[submit],
        initial_state_description="order pending",
        success_criteria=["complete all steps"],
    )
    steps = [
        WorkflowStep(
            name="charge-card",
            description="Charge the credit card",
            idempotent=False,
            reversible=True,
            compensation="refund-card",
        )
    ]
    side_effects = [
        SideEffect(
            step_name="charge-card",
            effect_type="payment",
            description="Captured customer funds",
            reversible=True,
            reversed=False,
        )
    ]

    class DemoWorkflow(WorkflowInterface):
        name = "demo-workflow"

        def describe_scenario(self) -> str:
            return "demo workflow"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "terminal": False}

        def get_available_actions(self, state: dict[str, object]):
            return [submit]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="step completed", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.92,
                reasoning="completed",
                dimension_scores={"workflow": 0.92},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "complete all steps"

        def get_workflow_steps(self):
            return list(steps)

        def execute_step(self, state: dict[str, object], step):
            return ActionResult(success=True, output=f"executed {step.name}", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def execute_compensation(self, state: dict[str, object], step):
            return CompensationAction(
                step_name=step.name,
                compensation_name="refund-card",
                success=True,
                output="refund issued",
            )

        def get_side_effects(self, state: dict[str, object]):
            return list(side_effects)

        def evaluate_workflow(self, state: dict[str, object]):
            return WorkflowResult(
                score=0.9,
                reasoning="contained side effects",
                dimension_scores={"containment": 0.95},
                steps_completed=1,
                steps_total=1,
                retries=0,
                compensations_triggered=1,
                compensations_successful=1,
                side_effects=list(side_effects),
                side_effects_reversed=1,
                side_effects_leaked=0,
            )

    scenario = DemoWorkflow()
    recreated_step = WorkflowStep.from_dict(steps[0].to_dict())
    recreated_effect = SideEffect.from_dict(side_effects[0].to_dict())
    evaluation = scenario.evaluate_workflow({"terminal": True})
    compensation = scenario.execute_compensation({}, steps[0])

    assert isinstance(scenario, WorkflowInterface)
    assert scenario.describe_scenario() == "demo workflow"
    assert scenario.get_workflow_steps()[0].name == "charge-card"
    assert scenario.get_side_effects({})[0].effect_type == "payment"
    assert recreated_step.compensation == "refund-card"
    assert recreated_effect.reversible is True
    assert compensation.success is True
    assert evaluation.compensations_successful == 1
    assert evaluation.side_effects_reversed == 1


def test_python_core_reexports_schema_evolution_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    ContextValidity = core_package.ContextValidity
    EnvironmentSpec = core_package.EnvironmentSpec
    SchemaEvolutionInterface = core_package.SchemaEvolutionInterface
    SchemaEvolutionResult = core_package.SchemaEvolutionResult
    SchemaMutation = core_package.SchemaMutation
    SimulationResult = core_package.SimulationResult

    migrate = ActionSpec(name="migrate", description="Apply schema migration", parameters={"version": "int"})
    environment = EnvironmentSpec(
        name="demo-schema-evolution",
        description="Adapt to schema changes",
        available_actions=[migrate],
        initial_state_description="schema v1",
        success_criteria=["adapt without stale assumptions"],
    )
    mutation = SchemaMutation(
        version=2,
        description="rename customer_id to account_id",
        fields_added=["account_id"],
        fields_removed=["customer_id"],
        fields_modified={"status": "string -> enum"},
        breaking=True,
    )
    validity = [
        ContextValidity(
            assumption="customer_id still exists",
            still_valid=False,
            invalidated_by_version=2,
        )
    ]

    class DemoSchemaEvolution(SchemaEvolutionInterface):
        name = "demo-schema-evolution"

        def describe_scenario(self) -> str:
            return "demo schema evolution"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "schema_version": 1, "terminal": False}

        def get_available_actions(self, state: dict[str, object]):
            return [migrate]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="mutation applied", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.9,
                reasoning="adapted",
                dimension_scores={"workflow": 0.9},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "adapt without stale assumptions"

        def get_mutations(self):
            return [mutation]

        def get_schema_version(self, state: dict[str, object]) -> int:
            value = state.get("schema_version", 1)
            return value if isinstance(value, int) else 1

        def get_mutation_log(self, state: dict[str, object]):
            return [mutation]

        def apply_mutation(self, state: dict[str, object], mutation):
            return {**state, "schema_version": mutation.version}

        def check_context_validity(self, state: dict[str, object], assumptions: list[str]):
            return list(validity)

        def evaluate_adaptation(self, state: dict[str, object]):
            return SchemaEvolutionResult(
                score=0.87,
                reasoning="detected stale context",
                dimension_scores={"detection": 0.9},
                mutations_applied=1,
                stale_assumptions_detected=1,
                stale_assumptions_missed=0,
                recovery_actions_taken=1,
                recovery_actions_successful=1,
            )

    scenario = DemoSchemaEvolution()
    recreated_mutation = SchemaMutation.from_dict(mutation.to_dict())
    recreated_validity = ContextValidity.from_dict(validity[0].to_dict())
    updated_state = scenario.apply_mutation({"schema_version": 1}, mutation)
    evaluation = scenario.evaluate_adaptation(updated_state)

    assert isinstance(scenario, SchemaEvolutionInterface)
    assert scenario.describe_scenario() == "demo schema evolution"
    assert scenario.get_mutations()[0].version == 2
    assert scenario.get_schema_version(updated_state) == 2
    assert scenario.get_mutation_log({})[0].breaking is True
    assert scenario.check_context_validity({}, ["customer_id still exists"])[0].still_valid is False
    assert recreated_mutation.fields_removed == ["customer_id"]
    assert recreated_validity.invalidated_by_version == 2
    assert evaluation.stale_assumptions_detected == 1
    assert evaluation.recovery_actions_successful == 1


def test_python_core_reexports_tool_fragility_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    EnvironmentSpec = core_package.EnvironmentSpec
    FailureAttribution = core_package.FailureAttribution
    SimulationResult = core_package.SimulationResult
    ToolContract = core_package.ToolContract
    ToolDrift = core_package.ToolDrift
    ToolFragilityInterface = core_package.ToolFragilityInterface
    ToolFragilityResult = core_package.ToolFragilityResult

    invoke = ActionSpec(name="invoke", description="Call the external tool", parameters={"tool": "string"})
    environment = EnvironmentSpec(
        name="demo-tool-fragility",
        description="Adapt to drifting tool contracts",
        available_actions=[invoke],
        initial_state_description="tool v1 available",
        success_criteria=["adapt after tool drift"],
    )
    contract = ToolContract(
        tool_name="ledger.lookup",
        version=1,
        input_schema={"account_id": "string"},
        output_schema={"balance": "number"},
        description="Lookup account balance",
    )
    drift = ToolDrift(
        tool_name="ledger.lookup",
        from_version=1,
        to_version=2,
        description="rename account_id to customer_id",
        drift_type="schema_change",
        breaking=True,
    )
    attribution = FailureAttribution(
        step=1,
        failure_class="tool_failure",
        description="tool rejected stale input schema",
        tool_name="ledger.lookup",
        recoverable=True,
    )

    class DemoToolFragility(ToolFragilityInterface):
        name = "demo-tool-fragility"

        def describe_scenario(self) -> str:
            return "demo tool fragility"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "tool_version": 1, "terminal": False}

        def get_available_actions(self, state: dict[str, object]):
            return [invoke]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="tool invoked", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.91,
                reasoning="adapted after drift",
                dimension_scores={"fragility": 0.91},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "adapt after tool drift"

        def get_tool_contracts(self, state: dict[str, object]):
            return [contract]

        def get_drift_log(self, state: dict[str, object]):
            return [drift]

        def inject_drift(self, state: dict[str, object], drift):
            return {**state, "tool_version": drift.to_version}

        def attribute_failure(self, state: dict[str, object], step: int, error: str):
            return attribution

        def evaluate_fragility(self, state: dict[str, object]):
            return ToolFragilityResult(
                score=0.88,
                reasoning="detected tool drift quickly",
                dimension_scores={"adaptation": 0.9},
                drifts_injected=1,
                drifts_detected=1,
                drifts_adapted=1,
                wasted_attempts=0,
                failure_attributions=[attribution],
            )

    scenario = DemoToolFragility()
    recreated_contract = ToolContract.from_dict(contract.to_dict())
    recreated_drift = ToolDrift.from_dict(drift.to_dict())
    recreated_attribution = FailureAttribution.from_dict(attribution.to_dict())
    updated_state = scenario.inject_drift({"tool_version": 1}, drift)
    failure = scenario.attribute_failure(updated_state, 1, "missing customer_id")
    evaluation = scenario.evaluate_fragility(updated_state)

    assert isinstance(scenario, ToolFragilityInterface)
    assert scenario.describe_scenario() == "demo tool fragility"
    assert scenario.get_tool_contracts({})[0].tool_name == "ledger.lookup"
    assert scenario.get_drift_log({})[0].breaking is True
    assert updated_state["tool_version"] == 2
    assert failure.failure_class == "tool_failure"
    assert recreated_contract.output_schema == {"balance": "number"}
    assert recreated_drift.drift_type == "schema_change"
    assert recreated_attribution.recoverable is True
    assert evaluation.drifts_detected == 1
    assert evaluation.failure_attributions[0].tool_name == "ledger.lookup"


def test_python_core_reexports_operator_loop_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    ClarificationRequest = core_package.ClarificationRequest
    EnvironmentSpec = core_package.EnvironmentSpec
    EscalationEvent = core_package.EscalationEvent
    OperatorLoopInterface = core_package.OperatorLoopInterface
    OperatorLoopResult = core_package.OperatorLoopResult
    SimulationResult = core_package.SimulationResult

    approve = ActionSpec(name="approve", description="Approve the deployment", parameters={"ticket": "string"})
    environment = EnvironmentSpec(
        name="demo-operator-loop",
        description="Decide when to escalate or clarify",
        available_actions=[approve],
        initial_state_description="pending approval",
        success_criteria=["escalate only when necessary"],
    )
    clarification = ClarificationRequest(
        question="Is the maintenance window approved?",
        context="production deploy for payment-api",
        urgency="high",
        metadata={"ticket": "chg-123"},
    )
    escalation = EscalationEvent(
        step=2,
        reason="missing maintenance approval",
        severity="critical",
        context="production deploy for payment-api",
        was_necessary=True,
        metadata={"ticket": "chg-123"},
    )

    class DemoOperatorLoop(OperatorLoopInterface):
        name = "demo-operator-loop"

        def describe_scenario(self) -> str:
            return "demo operator loop"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "terminal": False, "escalations": 0, "clarifications": 0}

        def get_available_actions(self, state: dict[str, object]):
            return [approve]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="decision recorded", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.9,
                reasoning="judged escalation boundary correctly",
                dimension_scores={"judgment": 0.9},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "escalate only when necessary"

        def get_escalation_log(self, state: dict[str, object]):
            return [escalation]

        def get_clarification_log(self, state: dict[str, object]):
            return [clarification]

        def escalate(self, state: dict[str, object], event):
            return {**state, "escalations": 1}

        def request_clarification(self, state: dict[str, object], request):
            return {**state, "clarifications": 1}

        def evaluate_judgment(self, state: dict[str, object]):
            return OperatorLoopResult(
                score=0.89,
                reasoning="escalated when operator approval was required",
                dimension_scores={"escalation": 0.93},
                total_actions=2,
                escalations=1,
                necessary_escalations=1,
                unnecessary_escalations=0,
                missed_escalations=0,
                clarifications_requested=1,
            )

    scenario = DemoOperatorLoop()
    recreated_clarification = ClarificationRequest.from_dict(clarification.to_dict())
    recreated_escalation = EscalationEvent.from_dict(escalation.to_dict())
    escalated_state = scenario.escalate({}, escalation)
    clarified_state = scenario.request_clarification({}, clarification)
    evaluation = scenario.evaluate_judgment({"terminal": True})

    assert isinstance(scenario, OperatorLoopInterface)
    assert scenario.describe_scenario() == "demo operator loop"
    assert scenario.get_escalation_log({})[0].severity == "critical"
    assert scenario.get_clarification_log({})[0].question.startswith("Is the maintenance")
    assert escalated_state["escalations"] == 1
    assert clarified_state["clarifications"] == 1
    assert recreated_clarification.metadata == {"ticket": "chg-123"}
    assert recreated_escalation.was_necessary is True
    assert evaluation.necessary_escalations == 1
    assert evaluation.clarifications_requested == 1


def test_python_core_reexports_coordination_family_contracts() -> None:
    ActionResult = core_package.ActionResult
    ActionSpec = core_package.ActionSpec
    CoordinationInterface = core_package.CoordinationInterface
    CoordinationResult = core_package.CoordinationResult
    EnvironmentSpec = core_package.EnvironmentSpec
    HandoffRecord = core_package.HandoffRecord
    SimulationResult = core_package.SimulationResult
    WorkerContext = core_package.WorkerContext

    merge = ActionSpec(name="merge", description="Merge worker outputs", parameters={"run_id": "string"})
    environment = EnvironmentSpec(
        name="demo-coordination",
        description="Coordinate workers with partial context",
        available_actions=[merge],
        initial_state_description="two workers have partial context",
        success_criteria=["handoff cleanly and merge outputs"],
    )
    workers = [
        WorkerContext(
            worker_id="worker-a",
            role="researcher",
            context_partition={"customer": "acme"},
            visible_data=["customer"],
            metadata={"team": "alpha"},
        ),
        WorkerContext(
            worker_id="worker-b",
            role="writer",
            context_partition={"draft": "pending"},
            visible_data=["draft"],
            metadata={"team": "beta"},
        ),
    ]
    handoff = HandoffRecord(
        from_worker="worker-a",
        to_worker="worker-b",
        content="customer context summarized",
        quality=0.95,
        step=1,
        metadata={"channel": "async"},
    )

    class DemoCoordination(CoordinationInterface):
        name = "demo-coordination"

        def describe_scenario(self) -> str:
            return "demo coordination"

        def describe_environment(self):
            return environment

        def initial_state(self, seed: int | None = None) -> dict[str, object]:
            return {"seed": seed, "handoffs": 0, "merged": False, "terminal": False}

        def get_available_actions(self, state: dict[str, object]):
            return [merge]

        def execute_action(self, state: dict[str, object], action):
            return ActionResult(success=True, output="outputs merged", state_changes={"terminal": True}), {
                **state,
                "terminal": True,
            }

        def is_terminal(self, state: dict[str, object]) -> bool:
            return bool(state.get("terminal", False))

        def evaluate_trace(self, trace, final_state: dict[str, object]):
            return SimulationResult(
                score=0.92,
                reasoning="workers coordinated successfully",
                dimension_scores={"coordination": 0.92},
                workflow_complete=bool(final_state.get("terminal", False)),
                actions_taken=1,
                actions_successful=1,
            )

        def get_rubric(self) -> str:
            return "handoff cleanly and merge outputs"

        def get_worker_contexts(self, state: dict[str, object]):
            return list(workers)

        def get_handoff_log(self, state: dict[str, object]):
            return [handoff]

        def record_handoff(self, state: dict[str, object], handoff):
            return {**state, "handoffs": 1}

        def merge_outputs(self, state: dict[str, object], worker_outputs: dict[str, str]):
            return {**state, "merged": bool(worker_outputs), "terminal": True}

        def evaluate_coordination(self, state: dict[str, object]):
            return CoordinationResult(
                score=0.9,
                reasoning="avoided duplication and merged cleanly",
                dimension_scores={"merge": 0.94},
                workers_used=2,
                handoffs_completed=1,
                duplication_rate=0.0,
                merge_conflicts=0,
            )

    scenario = DemoCoordination()
    recreated_worker = WorkerContext.from_dict(workers[0].to_dict())
    recreated_handoff = HandoffRecord.from_dict(handoff.to_dict())
    handed_off_state = scenario.record_handoff({}, handoff)
    merged_state = scenario.merge_outputs({}, {"worker-a": "facts", "worker-b": "draft"})
    evaluation = scenario.evaluate_coordination({"terminal": True})

    assert isinstance(scenario, CoordinationInterface)
    assert scenario.describe_scenario() == "demo coordination"
    assert scenario.get_worker_contexts({})[0].worker_id == "worker-a"
    assert scenario.get_handoff_log({})[0].quality == 0.95
    assert handed_off_state["handoffs"] == 1
    assert merged_state["merged"] is True
    assert recreated_worker.metadata == {"team": "alpha"}
    assert recreated_handoff.metadata == {"channel": "async"}
    assert evaluation.workers_used == 2
    assert evaluation.merge_conflicts == 0


def test_python_core_reexports_storage_row_contracts() -> None:
    RunRow = core_package.RunRow
    GenerationMetricsRow = core_package.GenerationMetricsRow
    MatchRow = core_package.MatchRow
    KnowledgeSnapshotRow = core_package.KnowledgeSnapshotRow
    AgentOutputRow = core_package.AgentOutputRow
    HumanFeedbackRow = core_package.HumanFeedbackRow
    TaskQueueRow = core_package.TaskQueueRow

    run_row = {
        "run_id": "run-1",
        "scenario": "grid_ctf",
        "target_generations": 3,
        "executor_mode": "local",
        "status": "running",
        "created_at": "2026-01-01T00:00:00Z",
    }
    generation_row = {
        "run_id": "run-1",
        "generation_index": 0,
        "mean_score": 0.75,
        "best_score": 0.8,
        "elo": 1512.0,
        "wins": 2,
        "losses": 1,
        "gate_decision": "promote",
        "status": "completed",
        "duration_seconds": 12.0,
        "scoring_backend": "elo",
        "rating_uncertainty": None,
        "dimension_summary_json": '{"accuracy": 0.9}',
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:01Z",
    }
    match_row = {
        "id": 1,
        "run_id": "run-1",
        "generation_index": 0,
        "seed": 7,
        "score": 0.75,
        "winner": "candidate",
        "strategy_json": "{}",
        "replay_json": "{}",
        "passed_validation": 1,
        "validation_errors": "",
        "created_at": "2026-01-01T00:00:02Z",
    }
    knowledge_snapshot_row = {
        "scenario": "grid_ctf",
        "run_id": "run-1",
        "best_score": 0.8,
        "best_elo": 1512.0,
        "playbook_hash": "abc123",
        "agent_provider": "deterministic",
        "rlm_enabled": 0,
        "scoring_backend": "elo",
        "rating_uncertainty": None,
        "created_at": "2026-01-01T00:00:03Z",
    }
    agent_output_row = {
        "id": 2,
        "run_id": "run-1",
        "generation_index": 0,
        "role": "competitor",
        "content": "answer",
        "created_at": "2026-01-01T00:00:04Z",
    }
    human_feedback_row = {
        "id": 3,
        "scenario_name": "grid_ctf",
        "agent_output": "answer",
        "human_score": 0.8,
        "human_notes": "solid",
        "generation_id": "run-1:0",
        "created_at": "2026-01-01T00:00:05Z",
    }
    task_queue_row = {
        "id": "task-1",
        "spec_name": "grid_ctf",
        "priority": 1,
        "config_json": None,
        "status": "pending",
        "scheduled_at": None,
        "started_at": None,
        "completed_at": None,
        "best_score": None,
        "best_output": None,
        "total_rounds": 0,
        "met_threshold": 0,
        "result_json": None,
        "error": None,
        "created_at": "2026-01-01T00:00:06Z",
    }

    assert run_row["scenario"] == "grid_ctf"
    assert generation_row["elo"] == 1512.0
    assert match_row["winner"] == "candidate"
    assert knowledge_snapshot_row["playbook_hash"] == "abc123"
    assert agent_output_row["role"] == "competitor"
    assert human_feedback_row["human_notes"] == "solid"
    assert task_queue_row["status"] == "pending"
    assert "scenario" in RunRow.__annotations__
    assert "elo" in GenerationMetricsRow.__annotations__
    assert "validation_errors" in MatchRow.__annotations__
    assert "playbook_hash" in KnowledgeSnapshotRow.__annotations__
    assert "content" in AgentOutputRow.__annotations__
    assert "human_notes" in HumanFeedbackRow.__annotations__
    assert "spec_name" in TaskQueueRow.__annotations__
