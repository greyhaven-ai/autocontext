"""the ambient per-role serving manifest: bridges (real_scenario, role) to the ambient target (AC-893).

A promoted ambient model is slotted in the registry under ``scenario = target.name`` so distinct
targets cannot collide on one scenario slot (AC-884). But the generation loop resolves a local model
by the REAL scenario, so it never finds that ambient record: the (real_scenario, role) -> target
mapping lives only in ``CharterTarget.selector`` ("role@scenario"), which the serving path cannot see.

This manifest is that bridge. The promote stage writes it when it activates an ambient candidate, and
the serving resolver reads it to route the real (scenario, role) request to the slotted target name.

Shape (JSON), keyed by scenario then role::

    { "<scenario or '*'>": { "<role>": {"target_name": str, "artifact_id": str, "backend": str} } }

The ``"*"`` bucket holds a bare-role selector (a role that serves every scenario); an exact-scenario
entry wins over it. This is a tiny opt-in file (off unless ``ambient_serving_manifest_path`` is set),
so it stays pure stdlib: no torch/mlx, no registry coupling.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

_Manifest = dict[str, dict[str, dict[str, str]]]


def _load(path: Path) -> _Manifest:
    """Load the manifest, or an empty one if the file is absent or unreadable."""
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _write_atomic(path: Path, manifest: _Manifest) -> None:
    """Write the manifest via a temp file + ``os.replace`` so a reader never sees a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_serving_entry(
    path: Path,
    *,
    scenario: str,
    role: str,
    target_name: str,
    artifact_id: str,
    backend: str,
) -> None:
    """Upsert the (scenario, role) serving entry, creating parent dirs and writing atomically.

    ``scenario`` is the real scenario the request will arrive under, or ``"*"`` for a bare-role
    selector that serves every scenario. A prior entry for the same (scenario, role) is superseded.
    """
    manifest = _load(path)
    manifest.setdefault(scenario, {})[role] = {
        "target_name": target_name,
        "artifact_id": artifact_id,
        "backend": backend,
    }
    _write_atomic(path, manifest)


def lookup_serving_entry(path: Path, *, scenario: str, role: str) -> dict[str, str] | None:
    """Return the serving entry for (scenario, role), or ``None`` if there is none.

    An exact-scenario entry wins; otherwise the ``"*"`` bare-role bucket answers. An absent file
    (feature never written for this run) returns ``None`` so the caller falls through to default.
    """
    if not path.exists():
        return None
    manifest = _load(path)
    exact = manifest.get(scenario, {}).get(role)
    if exact is not None:
        return exact
    return manifest.get("*", {}).get(role)


def remove_serving_entry(path: Path, *, scenario: str, role: str) -> None:
    """Remove the (scenario, role) entry if present (atomic write); a no-op if absent.

    For rollback; the promote upsert already handles supersede, so this is only needed to retire an
    entry entirely. Leaves sibling roles and scenarios untouched.
    """
    if not path.exists():
        return
    manifest = _load(path)
    roles = manifest.get(scenario)
    if not roles or role not in roles:
        return
    del roles[role]
    if not roles:
        del manifest[scenario]
    _write_atomic(path, manifest)
