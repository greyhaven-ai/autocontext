"""Aggregate analytics for cross-run facets, signal extraction, and pattern clustering."""

from autocontext.analytics.runtime_session_run_trace import runtime_session_log_to_run_trace
from autocontext.analytics.trace_gate_operator_view import (
    TraceGateAnalysisState,
    TraceGateOperatorState,
    TraceGateOperatorView,
    build_trace_gate_operator_view,
    render_trace_gate_operator_view_lines,
)

__all__ = [
    "TraceGateAnalysisState",
    "TraceGateOperatorState",
    "TraceGateOperatorView",
    "build_trace_gate_operator_view",
    "render_trace_gate_operator_view_lines",
    "runtime_session_log_to_run_trace",
]
