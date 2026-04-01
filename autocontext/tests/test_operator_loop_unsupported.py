"""AC-432: operator_loop is now a fully runnable family.

Tests verify that operator_loop can be created and executed end-to-end,
with proper escalation judgment evaluation via generated code.
"""

import ast


def test_family_classifier_detects_operator_loop():
    """Classifier should recognize operator_loop signals."""
    from autocontext.scenarios.custom.family_classifier import classify_scenario_family

    result = classify_scenario_family(
        "Build a scenario that tests when an agent should escalate to a human "
        "operator versus acting autonomously, including clarification requests"
    )
    assert result.family_name == "operator_loop"


def test_operator_loop_codegen_generates_valid_source():
    """Codegen must generate syntactically valid Python source."""
    from autocontext.scenarios.custom.operator_loop_codegen import generate_operator_loop_class
    from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec
    from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

    spec = OperatorLoopSpec(
        description="Test escalation judgment in deployment",
        environment_description="Production env",
        initial_state_description="Deployment pending",
        escalation_policy={"escalation_threshold": "high", "max_escalations": 3},
        success_criteria=["correct judgment"],
        failure_modes=["over-escalation"],
        actions=[
            SimulationActionSpecModel(
                name="check_logs", description="Check logs",
                parameters={}, preconditions=[], effects=["logs_checked"],
            ),
            SimulationActionSpecModel(
                name="deploy", description="Deploy",
                parameters={}, preconditions=["check_logs"], effects=["deployed"],
            ),
        ],
        max_steps=10,
    )

    source = generate_operator_loop_class(spec, "deploy_judgment")

    # Must be syntactically valid Python
    ast.parse(source)

    # Must contain key methods
    assert "def escalate(" in source
    assert "def request_clarification(" in source
    assert "def evaluate_judgment(" in source
    assert "def get_escalation_log(" in source
    assert "def get_clarification_log(" in source
    assert "OperatorLoopInterface" in source


def test_operator_loop_codegen_source_executes():
    """Generated code should be loadable and produce a working scenario."""
    from autocontext.scenarios.custom.operator_loop_codegen import generate_operator_loop_class
    from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec
    from autocontext.scenarios.custom.simulation_spec import SimulationActionSpecModel

    spec = OperatorLoopSpec(
        description="Escalation test",
        environment_description="Test env",
        initial_state_description="Initial",
        escalation_policy={"escalation_threshold": "medium", "max_escalations": 5},
        success_criteria=["good judgment"],
        failure_modes=["bad judgment"],
        actions=[
            SimulationActionSpecModel(
                name="step_a", description="Step A",
                parameters={}, preconditions=[], effects=["a_done"],
            ),
            SimulationActionSpecModel(
                name="step_b", description="Step B",
                parameters={}, preconditions=["step_a"], effects=["b_done"],
            ),
        ],
        max_steps=10,
    )

    source = generate_operator_loop_class(spec, "exec_test")

    # Execute the source to get the class
    namespace: dict = {}
    exec(source, namespace)  # noqa: S102

    # Find the generated class
    cls = None
    for obj in namespace.values():
        if isinstance(obj, type) and hasattr(obj, "name") and getattr(obj, "name", None) == "exec_test":
            cls = obj
            break
    assert cls is not None, "Generated class not found"

    instance = cls()

    # Test basic scenario methods
    state = instance.initial_state(42)
    assert state["escalation_log"] == []
    assert state["autonomous_actions"] == 0

    # Execute an autonomous action
    from autocontext.scenarios.simulation import Action
    result, new_state = instance.execute_action(state, Action(name="step_a", parameters={}))
    assert result.success is True
    assert new_state["autonomous_actions"] == 1

    # Precondition enforcement
    result2, _ = instance.execute_action(state, Action(name="step_b", parameters={}))
    assert result2.success is False

    # Escalation
    from autocontext.scenarios.operator_loop import EscalationEvent
    esc_state = instance.escalate(new_state, EscalationEvent(
        step=2, reason="suspicious logs", severity="high",
        context="errors detected", was_necessary=True,
    ))
    assert len(esc_state["escalation_log"]) == 1

    # Clarification
    from autocontext.scenarios.operator_loop import ClarificationRequest
    clar_state = instance.request_clarification(esc_state, ClarificationRequest(
        question="Should we proceed?", context="errors found", urgency="high",
    ))
    assert len(clar_state["clarification_log"]) == 1

    # Judgment evaluation
    judgment = instance.evaluate_judgment(clar_state)
    assert judgment.score > 0
    assert judgment.score <= 1.0
    assert judgment.escalations == 1
    assert judgment.necessary_escalations == 1
    assert judgment.unnecessary_escalations == 0
    assert judgment.clarifications_requested == 1
    assert judgment.dimension_scores["escalation_precision"] == 1.0


def test_operator_loop_family_registered():
    """Family metadata should exist."""
    from autocontext.scenarios.families import get_family

    family = get_family("operator_loop")
    assert family.name == "operator_loop"
    assert family.evaluation_mode == "judgment_evaluation"


def test_operator_loop_pipeline_registered():
    """Pipeline should exist for spec validation."""
    from autocontext.scenarios.custom.family_pipeline import has_pipeline

    assert has_pipeline("operator_loop")


def test_operator_loop_creator_is_functional():
    """Creator should be importable without the UNSUPPORTED constant."""
    from autocontext.scenarios.custom.creator_registry import create_for_family

    # Should be constructable via registry
    creator = create_for_family(
        "operator_loop",
        llm_fn=lambda s, u: "",
        knowledge_root=__import__("pathlib").Path("/tmp/test"),
    )
    assert creator is not None
