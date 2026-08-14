"""Compatibility exports for the pre-AC-946 mutation-log path."""

from autocontext.storage.context_mutation_log import (
    MUTATION_TYPES,
    Checkpoint,
    MutationEntry,
    MutationLog,
)

__all__ = ["MUTATION_TYPES", "Checkpoint", "MutationEntry", "MutationLog"]
