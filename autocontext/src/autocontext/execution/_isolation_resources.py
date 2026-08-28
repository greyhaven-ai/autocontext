"""Resource-limit planning for local isolated Python children."""
from __future__ import annotations

import math
import os
from collections.abc import Callable
from typing import Any


def linux_virtual_memory_bytes(*, unavailable_error: type[Exception]) -> int:
    """Return address space already inherited by the forked Linux child."""
    try:
        with open("/proc/self/statm", encoding="ascii") as statm_file:
            fields = statm_file.readline().split()
        page_count = int(fields[0])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (IndexError, OSError, UnicodeError, ValueError) as exc:
        raise unavailable_error(
            "unable to establish inherited Linux address-space usage"
        ) from exc
    if page_count < 1 or not isinstance(page_size, int) or page_size < 1:
        raise unavailable_error(
            "unable to establish inherited Linux address-space usage"
        )
    return page_count * page_size


def apply_resource_limits(
    *,
    timeout_seconds: float,
    max_memory_mb: int,
    max_output_bytes: int,
    platform: str,
    unavailable_error: type[Exception],
    virtual_memory_bytes: Callable[[], int],
    apply_linux_process_limit: Callable[[Any], None],
    apply_descendant_containment: Callable[[Any], None],
) -> None:
    """Apply child limits without relaxing inherited kernel policies."""
    try:
        import resource
    except ImportError as exc:
        raise unavailable_error("process resource limits are unavailable") from exc

    memory_bytes = max_memory_mb * 1024 * 1024
    inherited_address_space_bytes = (
        virtual_memory_bytes() if platform.startswith("linux") else None
    )
    cpu_seconds = max(1, math.ceil(timeout_seconds))
    requested: list[tuple[int, int]] = [
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, max_output_bytes),
        (resource.RLIMIT_NOFILE, 32),
    ]
    memory_limits: tuple[tuple[str, int], ...]
    if inherited_address_space_bytes is not None:
        # A fork already owns the parent's mappings. Treat the configured
        # memory budget as bounded child growth; an absolute limit below the
        # inherited address space prevents even a small helper thread. RLIMIT_AS
        # covers both mmap and data growth, so a separate Linux DATA cap would
        # reintroduce the same inherited-baseline bug.
        memory_limits = (
            ("RLIMIT_AS", inherited_address_space_bytes + memory_bytes),
        )
    else:
        memory_limits = (
            ("RLIMIT_AS", memory_bytes),
            ("RLIMIT_DATA", memory_bytes),
        )
    for name, value in memory_limits:
        limit = getattr(resource, name, None)
        if limit is not None:
            requested.append((limit, value))
    for resource_id, value in requested:
        try:
            soft, hard = resource.getrlimit(resource_id)
            effective = value
            for inherited_limit in (soft, hard):
                if inherited_limit != resource.RLIM_INFINITY:
                    effective = min(effective, inherited_limit)
            if (
                inherited_address_space_bytes is not None
                and resource_id == getattr(resource, "RLIMIT_AS", None)
                and effective <= inherited_address_space_bytes
            ):
                raise unavailable_error(
                    "the inherited address-space limit leaves no child memory allowance"
                )
            resource.setrlimit(resource_id, (effective, effective))
        except (OSError, ValueError):
            # Some kernels expose a limit but do not enforce or permit lowering it.
            continue

    apply_linux_process_limit(resource)
    apply_descendant_containment(resource)
