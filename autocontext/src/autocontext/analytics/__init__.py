"""Aggregate analytics for cross-run facets, signal extraction, and pattern clustering."""

from autocontext.analytics.negative_result_ledger import NegativeResultLedger, build_negative_result_ledger
from autocontext.analytics.progress_report import RunProgressReport, build_run_progress_report
from autocontext.analytics.run_utilization_report import RunUtilizationReport, build_run_utilization_report
from autocontext.analytics.runtime_session_run_trace import runtime_session_log_to_run_trace
from autocontext.analytics.trace_gate_operator_view import (
    TraceGateAnalysisState,
    TraceGateOperatorState,
    TraceGateOperatorView,
    build_trace_gate_operator_view,
    render_trace_gate_operator_view_lines,
)

__all__ = [
    "NegativeResultLedger",
    "RunProgressReport",
    "RunUtilizationReport",
    "TraceGateAnalysisState",
    "TraceGateOperatorState",
    "TraceGateOperatorView",
    "build_negative_result_ledger",
    "build_run_progress_report",
    "build_run_utilization_report",
    "build_trace_gate_operator_view",
    "render_trace_gate_operator_view_lines",
    "runtime_session_log_to_run_trace",
]
