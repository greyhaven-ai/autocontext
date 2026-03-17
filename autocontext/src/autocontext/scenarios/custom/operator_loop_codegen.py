from __future__ import annotations

from autocontext.scenarios.custom.operator_loop_spec import OperatorLoopSpec

OPERATOR_LOOP_SCAFFOLDING_UNSUPPORTED = (
    "operator_loop scenarios are intentionally not scaffolded into harness-owned "
    "executable runtimes; use family metadata, datasets, tools, or live-agent "
    "experiments instead"
)

def generate_operator_loop_class(spec: OperatorLoopSpec, name: str) -> str:
    del spec, name
    raise NotImplementedError(OPERATOR_LOOP_SCAFFOLDING_UNSUPPORTED)
