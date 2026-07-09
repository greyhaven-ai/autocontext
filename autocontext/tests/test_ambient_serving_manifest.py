"""tests for the ambient serving manifest: the (scenario, role) -> ambient target bridge (AC-893).

A promoted ambient model is slotted in the registry under ``scenario = target.name`` (AC-884
anti-collision), so the generation loop, which resolves by the REAL scenario, never finds it. The
serving manifest carries the (real_scenario, role) -> target mapping that otherwise lived only in
``CharterTarget.selector``, so the serving resolver can bridge to the ambient record.
"""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.ambient.serving_manifest import (
    lookup_serving_entry,
    remove_serving_entry,
    write_serving_entry,
)


def test_write_then_lookup_exact_scenario_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "serving.json"
    write_serving_entry(
        path,
        scenario="grid_ctf",
        role="competitor",
        target_name="competitor-local",
        artifact_id="cand-1",
        backend="mlx",
    )

    entry = lookup_serving_entry(path, scenario="grid_ctf", role="competitor")

    assert entry == {"target_name": "competitor-local", "artifact_id": "cand-1", "backend": "mlx"}


def test_bare_role_wildcard_fallback_serves_every_scenario(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(
        path,
        scenario="*",
        role="competitor",
        target_name="competitor-any",
        artifact_id="cand-9",
        backend="mlxlm",
    )

    # no exact grid_ctf entry, so the "*" bucket answers for any scenario.
    entry = lookup_serving_entry(path, scenario="grid_ctf", role="competitor")

    assert entry == {"target_name": "competitor-any", "artifact_id": "cand-9", "backend": "mlxlm"}


def test_exact_scenario_wins_over_wildcard(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="*", role="competitor", target_name="any", artifact_id="a", backend="mlx")
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="scoped", artifact_id="s", backend="mlxlm")

    entry = lookup_serving_entry(path, scenario="grid_ctf", role="competitor")

    assert entry is not None and entry["target_name"] == "scoped"


def test_lookup_absent_file_returns_none(tmp_path: Path) -> None:
    assert lookup_serving_entry(tmp_path / "missing.json", scenario="grid_ctf", role="competitor") is None


def test_lookup_unknown_role_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="c", artifact_id="a", backend="mlx")

    assert lookup_serving_entry(path, scenario="grid_ctf", role="judge") is None


def test_upsert_supersedes_prior_entry_for_same_scenario_role(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="old", artifact_id="old-a", backend="mlx")
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="new", artifact_id="new-a", backend="mlxlm")

    entry = lookup_serving_entry(path, scenario="grid_ctf", role="competitor")

    assert entry == {"target_name": "new", "artifact_id": "new-a", "backend": "mlxlm"}


def test_write_preserves_other_scenarios_and_roles(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="c", artifact_id="ca", backend="mlx")
    write_serving_entry(path, scenario="othello", role="analyst", target_name="a", artifact_id="aa", backend="mlxlm")

    assert lookup_serving_entry(path, scenario="grid_ctf", role="competitor") is not None
    assert lookup_serving_entry(path, scenario="othello", role="analyst") is not None


def test_remove_deletes_an_entry(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="c", artifact_id="ca", backend="mlx")

    remove_serving_entry(path, scenario="grid_ctf", role="competitor")

    assert lookup_serving_entry(path, scenario="grid_ctf", role="competitor") is None


def test_remove_is_noop_when_absent(tmp_path: Path) -> None:
    path = tmp_path / "missing.json"
    # no exception, no file created.
    remove_serving_entry(path, scenario="grid_ctf", role="competitor")
    assert not path.exists()


def test_remove_leaves_sibling_entries_intact(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="c", artifact_id="ca", backend="mlx")
    write_serving_entry(path, scenario="grid_ctf", role="analyst", target_name="a", artifact_id="aa", backend="mlx")

    remove_serving_entry(path, scenario="grid_ctf", role="competitor")

    assert lookup_serving_entry(path, scenario="grid_ctf", role="competitor") is None
    assert lookup_serving_entry(path, scenario="grid_ctf", role="analyst") is not None


def test_atomic_write_leaves_valid_json_on_disk(tmp_path: Path) -> None:
    path = tmp_path / "serving.json"
    write_serving_entry(path, scenario="grid_ctf", role="competitor", target_name="c", artifact_id="ca", backend="mlx")

    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded == {"grid_ctf": {"competitor": {"target_name": "c", "artifact_id": "ca", "backend": "mlx"}}}
    # no leftover temp files beside the manifest.
    assert [p.name for p in tmp_path.iterdir()] == ["serving.json"]
