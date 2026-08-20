from __future__ import annotations

import json
import multiprocessing
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from autocontext.kernel_evolution._file_lock import _locked_append_fd, append_bytes_locked


def _hold_lock(path: str, acquired: Any, release: Any) -> None:
    with _locked_append_fd(Path(path)):
        acquired.set()
        if not release.wait(10):
            raise TimeoutError("test did not release held append lock")


def _append_once(path: str, started: Any, finished: Any) -> None:
    started.set()
    append_bytes_locked(Path(path), b'{"writer":"blocked"}\n')
    finished.set()


def _append_jsonl_records(path: str, writer: int, count: int, start: Any) -> None:
    if not start.wait(10):
        raise TimeoutError("test did not start concurrent writers")
    target = Path(path)
    for sequence in range(count):
        record = {"padding": "x" * 8192, "sequence": sequence, "writer": writer}
        append_bytes_locked(target, (json.dumps(record, sort_keys=True) + "\n").encode())


def _join_successfully(process: Any) -> None:
    process.join(15)
    assert not process.is_alive()
    assert process.exitcode == 0


def test_append_lock_serializes_processes(tmp_path: Path) -> None:
    target = tmp_path / "lineage.jsonl"
    context = multiprocessing.get_context("spawn")
    acquired = context.Event()
    release = context.Event()
    started = context.Event()
    finished = context.Event()
    holder = context.Process(target=_hold_lock, args=(str(target), acquired, release))
    appender = context.Process(target=_append_once, args=(str(target), started, finished))

    holder.start()
    try:
        assert acquired.wait(10)
        appender.start()
        assert started.wait(10)
        assert not finished.wait(0.25)
        release.set()
        assert finished.wait(10)
        _join_successfully(appender)
        _join_successfully(holder)
    finally:
        release.set()
        for process in (appender, holder):
            if process.pid is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(5)

    assert json.loads(target.read_text(encoding="utf-8")) == {"writer": "blocked"}


def test_concurrent_jsonl_appends_remain_complete_and_parseable(tmp_path: Path) -> None:
    target = tmp_path / "lineage.jsonl"
    writer_count = 4
    records_per_writer = 20
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    processes = [
        context.Process(target=_append_jsonl_records, args=(str(target), writer, records_per_writer, start))
        for writer in range(writer_count)
    ]

    for process in processes:
        process.start()
    start.set()
    try:
        for process in processes:
            _join_successfully(process)
    finally:
        for process in processes:
            if process.pid is None:
                continue
            if process.is_alive():
                process.terminate()
            process.join(5)

    records = [json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()]
    assert len(records) == writer_count * records_per_writer
    assert {(record["writer"], record["sequence"]) for record in records} == {
        (writer, sequence) for writer in range(writer_count) for sequence in range(records_per_writer)
    }
    assert all(record["padding"] == "x" * 8192 for record in records)


def test_locked_append_flushes_before_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "lineage.jsonl"
    payload = b'{"durable":true}\n'
    sizes_at_fsync: list[int] = []
    real_fsync = os.fsync

    def record_fsync(fd: int) -> None:
        sizes_at_fsync.append(os.fstat(fd).st_size)
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", record_fsync)

    append_bytes_locked(target, payload)

    assert sizes_at_fsync == [len(payload)]
    assert target.read_bytes() == payload


def test_kernel_evolution_import_does_not_require_fcntl(tmp_path: Path) -> None:
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
        import autocontext.kernel_evolution
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
