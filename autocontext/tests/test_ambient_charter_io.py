from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.ambient.charter import Charter, CharterBudgets, CharterSource, CharterTarget
from autocontext.ambient.charter_io import CharterLoadError, load_charter, save_charter


def _charter() -> Charter:
    return Charter(
        tier="oss",
        sources=[CharterSource(name="native", kind="autocontext")],
        targets=[
            CharterTarget(
                name="t1",
                kind="role",
                selector="competitor@grid_ctf",
                base_model="Qwen/Qwen2.5-3B-Instruct",
                min_dataset_records=10,
                eval_suite="grid_ctf_holdout",
            )
        ],
        budgets=CharterBudgets(gpu_hours_per_window=1.0, window_hours=24, disk_quota_gb=10.0),
    )


def test_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "ambient-charter.yaml"
    save_charter(_charter(), path)
    loaded = load_charter(path)
    assert loaded == _charter()


def test_missing_file_raises_load_error(tmp_path: Path) -> None:
    with pytest.raises(CharterLoadError, match="not found"):
        load_charter(tmp_path / "absent.yaml")


def test_invalid_yaml_raises_load_error(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("tier: [unclosed", encoding="utf-8")
    with pytest.raises(CharterLoadError, match="parse"):
        load_charter(path)


def test_schema_violation_raises_load_error_with_detail(tmp_path: Path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("tier: oss\nsources: []\ntargets: []\n", encoding="utf-8")
    with pytest.raises(CharterLoadError, match="budgets"):
        load_charter(path)
