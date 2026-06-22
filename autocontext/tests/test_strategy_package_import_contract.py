from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast


def _contract_modules() -> list[dict[str, Any]]:
    contract_path = Path(__file__).resolve().parents[2] / "docs" / "strategy-package-import-contract.json"
    payload = json.loads(contract_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError("strategy-package import contract must be a JSON object")
    modules = payload.get("modules")
    if not isinstance(modules, list):
        raise AssertionError("strategy-package import contract must declare modules")
    return cast(list[dict[str, Any]], modules)


def _snapshot_directory(path: Path) -> list[str]:
    return sorted(str(child.relative_to(path)) for child in path.rglob("*"))


def test_strategy_package_import_contract_declares_python_imports_pure() -> None:
    modules = [entry for entry in _contract_modules() if entry["runtime"] == "python"]
    assert modules
    for entry in modules:
        assert entry["import_time_filesystem_writes"] is False
        assert "Call" in str(entry["runtime_setup"])


def test_strategy_package_imports_do_not_create_runtime_files(tmp_path: Path) -> None:
    src_path = str(Path(__file__).resolve().parents[1] / "src")
    env = {
        **os.environ,
        "PYTHONPATH": f"{src_path}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
    }
    for entry in _contract_modules():
        if entry["runtime"] != "python":
            continue
        module_cwd = tmp_path / str(entry["module"]).replace(".", "_")
        module_cwd.mkdir()
        before = _snapshot_directory(module_cwd)
        subprocess.run(
            [sys.executable, "-c", f"import importlib; importlib.import_module({str(entry['module'])!r})"],
            cwd=module_cwd,
            env=env,
            check=True,
        )
        assert _snapshot_directory(module_cwd) == before
