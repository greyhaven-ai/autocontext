from __future__ import annotations

from autocontext.execution.executors.gondolin_contract import (
    GondolinBackend,
    GondolinExecutionRequest,
    GondolinExecutionResult,
    GondolinSandboxPolicy,
    GondolinSecretRef,
)


class _FakeGondolinBackend:
    def execute(self, request: GondolinExecutionRequest) -> GondolinExecutionResult:
        return GondolinExecutionResult(
            result={"score": 1.0, "scenario": request.scenario_name},
            replay={"seed": request.seed},
            stdout="ok",
        )


def test_gondolin_contract_defaults_are_deny_by_default() -> None:
    policy = GondolinSandboxPolicy()

    assert policy.allow_network is False
    assert policy.allowed_egress_hosts == ()
    assert policy.secrets == ()


def test_gondolin_contract_uses_secret_references_not_secret_values() -> None:
    policy = GondolinSandboxPolicy(
        secrets=(GondolinSecretRef(name="judge-api-key", env_var="AUTOCONTEXT_JUDGE_API_KEY"),)
    )

    assert policy.secrets[0].name == "judge-api-key"
    assert policy.secrets[0].env_var == "AUTOCONTEXT_JUDGE_API_KEY"
    assert "sk-" not in repr(policy)


def test_gondolin_backend_protocol_can_be_implemented_out_of_tree() -> None:
    backend: GondolinBackend = _FakeGondolinBackend()
    result = backend.execute(
        GondolinExecutionRequest(
            scenario_name="grid_ctf",
            strategy={"move": "north"},
            seed=7,
        )
    )

    assert result.result["score"] == 1.0
    assert result.replay["seed"] == 7
