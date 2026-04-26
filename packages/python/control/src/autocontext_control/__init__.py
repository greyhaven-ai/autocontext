"""Facade for the future autocontext control-plane artifact."""

from importlib import import_module
from typing import Any

_production_traces_contract = import_module(
    "autocontext.production_traces.contract.models"
)

Sdk: Any = _production_traces_contract.Sdk
TraceSource: Any = _production_traces_contract.TraceSource
Provider: Any = _production_traces_contract.Provider
EnvContext: Any = _production_traces_contract.EnvContext
ToolCall: Any = _production_traces_contract.ToolCall
Error: Any = _production_traces_contract.Error
ProductionOutcome: Any = _production_traces_contract.ProductionOutcome
UsageInfo: Any = _production_traces_contract.UsageInfo
EvalExampleId: Any = _production_traces_contract.EvalExampleId
TrainingRecordId: Any = _production_traces_contract.TrainingRecordId
TraceLinks: Any = _production_traces_contract.TraceLinks
Chosen: Any = _production_traces_contract.Chosen
Routing: Any = _production_traces_contract.Routing
UserIdHash: Any = _production_traces_contract.UserIdHash
EndedAt: Any = _production_traces_contract.EndedAt
Items: Any = _production_traces_contract.Items
SessionIdentifier: Any = _production_traces_contract.SessionIdentifier
Message: Any = _production_traces_contract.Message
TimingInfo: Any = _production_traces_contract.TimingInfo
FeedbackRef: Any = _production_traces_contract.FeedbackRef
RedactionMarker: Any = _production_traces_contract.RedactionMarker
ProductionTrace: Any = _production_traces_contract.ProductionTrace

PACKAGE_ROLE = "control"
PACKAGE_TOPOLOGY_VERSION = 1

package_role = PACKAGE_ROLE
package_topology_version = PACKAGE_TOPOLOGY_VERSION

__all__ = [
    "Chosen",
    "EndedAt",
    "EnvContext",
    "Error",
    "EvalExampleId",
    "FeedbackRef",
    "Items",
    "Message",
    "PACKAGE_ROLE",
    "PACKAGE_TOPOLOGY_VERSION",
    "ProductionOutcome",
    "ProductionTrace",
    "Provider",
    "RedactionMarker",
    "Routing",
    "Sdk",
    "SessionIdentifier",
    "TimingInfo",
    "ToolCall",
    "TraceLinks",
    "TraceSource",
    "TrainingRecordId",
    "UsageInfo",
    "UserIdHash",
    "package_role",
    "package_topology_version",
]
