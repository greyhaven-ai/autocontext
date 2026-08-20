from __future__ import annotations

import multiprocessing
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock


def _hold_lock(path: str, acquired: Any, release: Any) -> None:
    with advisory_path_lock(Path(path)):
        acquired.set()
        if not release.wait(10):
            raise TimeoutError("test did not release held advisory lock")


def _acquire_lock(path: str, started: Any, acquired: Any) -> None:
    started.set()
    with advisory_path_lock(Path(path)):
        acquired.set()


def _join_successfully(process: Any) -> None:
    process.join(15)
    assert not process.is_alive()
    assert process.exitcode == 0


def test_advisory_path_lock_serializes_processes(tmp_path: Path) -> None:
    target = tmp_path / "state.lock"
    context = multiprocessing.get_context("spawn")
    holder_acquired = context.Event()
    release = context.Event()
    waiter_started = context.Event()
    waiter_acquired = context.Event()
    holder = context.Process(target=_hold_lock, args=(str(target), holder_acquired, release))
    waiter = context.Process(target=_acquire_lock, args=(str(target), waiter_started, waiter_acquired))

    holder.start()
    try:
        assert holder_acquired.wait(10)
        waiter.start()
        assert waiter_started.wait(10)
        assert not waiter_acquired.wait(0.25)
        release.set()
        assert waiter_acquired.wait(10)
        _join_successfully(waiter)
        _join_successfully(holder)
    finally:
        release.set()
        for process in (waiter, holder):
            if process.pid is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(5)


def test_public_runtime_imports_do_not_require_fcntl(tmp_path: Path) -> None:
    script = textwrap.dedent(
        """
        import builtins
        import importlib.abc
        import sys

        real_import = builtins.__import__

        def import_without_fcntl(name, globals=None, locals=None, fromlist=(), level=0):
            if name == "fcntl" or name.startswith("fcntl."):
                raise ModuleNotFoundError("fcntl intentionally unavailable")
            return real_import(name, globals, locals, fromlist, level)

        class NoFcntlFinder(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path, target=None):
                if fullname == "fcntl" or fullname.startswith("fcntl."):
                    raise ModuleNotFoundError("fcntl intentionally unavailable")
                return None

        sys.modules.pop("fcntl", None)
        sys.meta_path.insert(0, NoFcntlFinder())
        builtins.__import__ = import_without_fcntl

        import autocontext.context_bundles
        import autocontext.execution
        import autocontext.kernel_evolution
        from autocontext.audit.campaign_audit_store import CampaignAuditStore
        from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry
        """
    )
    package_root = Path(__file__).parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(package_root / "src"), env.get("PYTHONPATH"))))

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
