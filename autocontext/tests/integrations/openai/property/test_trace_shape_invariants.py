"""Property tests (100 runs): arbitrary requests → trace validates against schema."""
from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from autocontext.integrations.openai._trace_builder import build_success_trace
from autocontext.production_traces.contract.models import ProductionTrace


@given(
    model=st.text(min_size=1, max_size=50).filter(lambda s: s.strip()),
    prompt=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
    prompt_tokens=st.integers(min_value=0, max_value=10000),
    completion_tokens=st.integers(min_value=0, max_value=10000),
    app_id=st.from_regex(r"[a-z0-9][a-z0-9_-]{0,29}", fullmatch=True),
)
@settings(max_examples=100, deadline=None)
def test_success_trace_always_validates(model, prompt, prompt_tokens, completion_tokens, app_id) -> None:
    trace = build_success_trace(
        request_snapshot={"model": model, "messages": [{"role": "user", "content": prompt}], "extra": {}},
        response_usage={
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        response_tool_calls=None,
        identity={},
        timing={"startedAt": "2026-04-21T00:00:00Z", "endedAt": "2026-04-21T00:00:01Z", "latencyMs": 1000},
        env={"environmentTag": "test", "appId": app_id},
        source_info={"emitter": "sdk", "sdk": {"name": "autocontext-py", "version": "0.0.0"}},
        trace_id="01HN0000000000000000000001",
    )
    ProductionTrace.model_validate(trace)
