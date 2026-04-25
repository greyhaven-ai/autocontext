from __future__ import annotations

import importlib
import json
from pathlib import Path


def _contract_modules() -> list[dict[str, object]]:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "strategy-package-import-contract.json"
    return json.loads(contract_path.read_text(encoding="utf-8"))["modules"]


def _snapshot_directory(path: Path) -> list[str]:
    return sorted(str(child.relative_to(path)) for child in path.rglob("*"))


def test_strategy_package_import_contract_declares_python_imports_pure() -> None:
    modules = [entry for entry in _contract_modules() if entry["runtime"] == "python"]
    assert modules
    for entry in modules:
        assert entry["import_time_filesystem_writes"] is False
        assert "Call" in str(entry["runtime_setup"])


def test_strategy_package_imports_do_not_create_runtime_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    for entry in _contract_modules():
        if entry["runtime"] != "python":
            continue
        before = _snapshot_directory(tmp_path)
        importlib.import_module(str(entry["module"]))
        assert _snapshot_directory(tmp_path) == before
