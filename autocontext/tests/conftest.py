"""Shared test fixtures and helpers."""
from __future__ import annotations

import os

from autocontext.harness.orchestration.dag import RoleDAG
from autocontext.harness.orchestration.types import RoleSpec

# The prescreen tests import scikit-learn at collection time, and numpy/scipy start OpenBLAS and OpenMP
# worker threads as they load. Local execution isolation refuses to fork from a multi-threaded process,
# so pin those pools to the calling thread before any test module is collected.
for _pool in ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS"):
    os.environ.setdefault(_pool, "1")


def make_base_dag() -> RoleDAG:
    """Standard 5-role autocontext DAG used across multiple test modules."""
    return RoleDAG([
        RoleSpec(name="competitor"),
        RoleSpec(name="translator", depends_on=("competitor",)),
        RoleSpec(name="analyst", depends_on=("translator",)),
        RoleSpec(name="architect", depends_on=("translator",)),
        RoleSpec(name="coach", depends_on=("analyst",)),
    ])
