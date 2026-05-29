"""AC-728 contract probes, Python parity.

Mirrors ``ts/src/control-plane/contract-probes/index.ts``. Probes are
pure functions returning Pydantic result models so they compose with
the same trace-replay surfaces the TS probes use.

Two module groups:

- ``_base``: directory / terminal / service / artifact (slice 1, PR
  #957). Every observation field is non-optional so the silent-pass
  shape cannot arise on these four.
- ``_advanced``: cleanup / distributed / media (slice 2, TS PRs
  #983 / #985 / #987). Each emits explicit ``missing-observation``
  failure kinds: a declared expectation without its matching
  observation must fail loudly.
"""

from ._advanced import (
    CleanupContractFailure,
    CleanupContractFailureKind,
    CleanupContractProbeInputs,
    CleanupContractProbeResult,
    CleanupFileEntry,
    DistributedContractFailure,
    DistributedContractFailureKind,
    DistributedContractProbeInputs,
    DistributedContractProbeResult,
    DistributedRankReport,
    MediaContractFailure,
    MediaContractFailureKind,
    MediaContractProbeInputs,
    MediaContractProbeResult,
    probe_cleanup_contract,
    probe_distributed_contract,
    probe_media_contract,
)
from ._base import (
    ArtifactContractFailure,
    ArtifactContractFailureKind,
    ArtifactContractProbeInputs,
    ArtifactContractProbeResult,
    DirectoryContractFailure,
    DirectoryContractFailureKind,
    DirectoryContractProbeInputs,
    DirectoryContractProbeResult,
    ServiceContractFailure,
    ServiceContractFailureKind,
    ServiceContractProbeInputs,
    ServiceContractProbeResult,
    ServiceEndpointObservation,
    ServiceEndpointProtocol,
    TerminalContractFailure,
    TerminalContractFailureKind,
    TerminalContractProbeInputs,
    TerminalContractProbeResult,
    probe_artifact_contract,
    probe_directory_contract,
    probe_service_contract,
    probe_terminal_contract,
)

__all__ = [
    "ArtifactContractFailure",
    "ArtifactContractFailureKind",
    "ArtifactContractProbeInputs",
    "ArtifactContractProbeResult",
    "CleanupContractFailure",
    "CleanupContractFailureKind",
    "CleanupContractProbeInputs",
    "CleanupContractProbeResult",
    "CleanupFileEntry",
    "DirectoryContractFailure",
    "DirectoryContractFailureKind",
    "DirectoryContractProbeInputs",
    "DirectoryContractProbeResult",
    "DistributedContractFailure",
    "DistributedContractFailureKind",
    "DistributedContractProbeInputs",
    "DistributedContractProbeResult",
    "DistributedRankReport",
    "MediaContractFailure",
    "MediaContractFailureKind",
    "MediaContractProbeInputs",
    "MediaContractProbeResult",
    "ServiceContractFailure",
    "ServiceContractFailureKind",
    "ServiceContractProbeInputs",
    "ServiceContractProbeResult",
    "ServiceEndpointObservation",
    "ServiceEndpointProtocol",
    "TerminalContractFailure",
    "TerminalContractFailureKind",
    "TerminalContractProbeInputs",
    "TerminalContractProbeResult",
    "probe_artifact_contract",
    "probe_cleanup_contract",
    "probe_directory_contract",
    "probe_distributed_contract",
    "probe_media_contract",
    "probe_service_contract",
    "probe_terminal_contract",
]
