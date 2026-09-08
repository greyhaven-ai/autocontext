"""Run a deterministic, module-preserving shard of the collected pytest suite.

Usage (from autocontext/): python scripts/pytest_shard.py 1/4 [pytest arguments]
Modules are balanced by collected test count, with the expensive statistical
calibration module reserved a shard; no timing cache is required.
"""
from __future__ import annotations

import sys
from collections import defaultdict
from collections.abc import Collection, Sequence

import pytest

ISOLATED_MODULES = frozenset({"tests/test_false_promotion_calibration.py"})


def partition_modules(
    modules: dict[str, int], count: int, *, isolated_modules: Collection[str] = (),
) -> list[set[str]]:
    if count < 1:
        raise ValueError("shard count must be positive")
    shards: list[set[str]] = [set() for _ in range(count)]
    sizes = [0] * count
    # A single calibration test takes about 20 minutes under CI coverage.
    # Giving it the full suite's weight keeps ordinary tests on other shards
    # whenever there is spare shard capacity, without dropping any module.
    total = sum(modules.values())
    weights = {name: total if name in isolated_modules else size for name, size in modules.items()}
    for module in sorted(modules, key=lambda name: (-weights[name], name)):
        index = min(range(count), key=lambda candidate: (sizes[candidate], candidate))
        shards[index].add(module)
        sizes[index] += weights[module]
    return shards


class ShardPlugin:
    def __init__(self, index: int, count: int) -> None:
        self.index = index
        self.count = count

    @pytest.hookimpl(trylast=True)
    def pytest_collection_modifyitems(self, config: pytest.Config, items: list[pytest.Item]) -> None:
        modules: dict[str, int] = defaultdict(int)
        for item in items:
            modules[item.nodeid.split("::", 1)[0]] += 1
        selected = partition_modules(modules, self.count, isolated_modules=ISOLATED_MODULES)[self.index - 1]
        kept, removed = [], []
        for item in items:
            (kept if item.nodeid.split("::", 1)[0] in selected else removed).append(item)
        items[:] = kept
        config.hook.pytest_deselected(items=removed)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        index, count = map(int, args[0].split("/"))
        if not 1 <= index <= count:
            raise ValueError
    except (IndexError, ValueError):
        print("usage: pytest_shard.py INDEX/COUNT [pytest arguments] (1 <= INDEX <= COUNT)", file=sys.stderr)
        return 2
    return int(pytest.main(args[1:], plugins=[ShardPlugin(index, count)]))


if __name__ == "__main__":
    raise SystemExit(main())
