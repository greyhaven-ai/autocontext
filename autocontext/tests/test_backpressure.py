from autocontext.harness.pipeline.gate import BackpressureGate


def test_backpressure_is_deterministic() -> None:
    gate = BackpressureGate(min_delta=0.01)
    decision_a = gate.evaluate(0.5, 0.52, retry_count=0, max_retries=2)
    decision_b = gate.evaluate(0.5, 0.52, retry_count=0, max_retries=2)
    assert decision_a == decision_b
    assert decision_a.decision == "advance"
