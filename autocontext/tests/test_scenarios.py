from autocontext.scenarios.grid_ctf import GridCtfScenario
from autocontext.scenarios.othello import OthelloScenario


def test_grid_ctf_validation_and_execution() -> None:
    scenario = GridCtfScenario()
    state = scenario.initial_state(seed=42)
    valid, _ = scenario.validate_actions(state, "challenger", {"aggression": 0.7, "defense": 0.6, "path_bias": 0.4})
    assert valid
    result = scenario.execute_match({"aggression": 0.7, "defense": 0.6, "path_bias": 0.4}, seed=42)
    assert result.passed_validation
    assert 0.0 <= result.score <= 1.0


def test_othello_works_for_scenario_swap_contract() -> None:
    scenario = OthelloScenario()
    result = scenario.execute_match({"mobility_weight": 0.5, "corner_weight": 0.5, "stability_weight": 0.5}, seed=17)
    assert result.passed_validation
    assert 0.0 <= result.score <= 1.0
