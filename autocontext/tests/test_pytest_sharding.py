"""The CI shard partition must preserve every test and module exactly once."""
from __future__ import annotations

import runpy
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pytest_shard.py"
partition_modules = runpy.run_path(str(_SCRIPT))["partition_modules"]


def test_shards_cover_modules_once_and_ignore_input_order() -> None:
    modules = {"large.py": 100, "medium.py": 80, "small.py": 20, "tiny.py": 10, "other.py": 30}
    shards = partition_modules(modules, 4)
    assert set.union(*shards) == set(modules)
    assert sum(map(len, shards)) == len(modules)
    assert shards == partition_modules(dict(reversed(list(modules.items()))), 4)
    sizes = [sum(modules[name] for name in shard) for shard in shards]
    assert max(sizes) - min(sizes) <= max(modules.values())


def test_single_shard_keeps_every_module() -> None:
    assert partition_modules({"a": 1, "b": 3}, 1) == [{"a", "b"}]


def test_invalid_shard_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="positive"):
        partition_modules({"a": 1}, 0)
