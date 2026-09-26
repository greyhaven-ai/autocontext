from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .artifacts import ArtifactStore
    from .factory import artifact_store_from_settings
    from .playbook_approval import (
        approve_pending_playbook,
        read_pending_playbook,
        reject_pending_playbook,
        stage_pending_playbook,
    )
    from .sqlite_store import SQLiteStore

# Resolved on first access so leaf modules such as sqlite_wal can be imported
# without loading the artifact store and the agent stack behind it.
_LAZY_EXPORTS = {
    "ArtifactStore": ".artifacts",
    "SQLiteStore": ".sqlite_store",
    "approve_pending_playbook": ".playbook_approval",
    "artifact_store_from_settings": ".factory",
    "read_pending_playbook": ".playbook_approval",
    "reject_pending_playbook": ".playbook_approval",
    "stage_pending_playbook": ".playbook_approval",
}

__all__ = [
    "ArtifactStore",
    "SQLiteStore",
    "approve_pending_playbook",
    "artifact_store_from_settings",
    "read_pending_playbook",
    "reject_pending_playbook",
    "stage_pending_playbook",
]


def __getattr__(name: str) -> Any:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module_name, __name__), name)
