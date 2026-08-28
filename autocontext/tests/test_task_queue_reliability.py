"""Task queue retry accounting, dead-letter policy, and stale recovery (AC-906)."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

from autocontext.storage.sqlite_store import SQLiteStore


def _store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "queue.sqlite3")
    store.migrate(Path(__file__).parent.parent / "migrations")
    return store


def _enqueue(store: SQLiteStore, task_id: str = "task_1") -> str:
    store.enqueue_task(task_id, "spec_a")
    return task_id


class TestAttemptsAccounting:
    def test_fresh_task_has_zero_attempts_and_claim_increments(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        with store.connection() as conn:
            row = conn.execute("SELECT attempts FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["attempts"] == 0
        claimed = store.dequeue_task()
        assert claimed is not None
        assert claimed["attempts"] == 1
        assert claimed["status"] == "running"

    def test_single_statement_claim_returns_full_row(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        claimed = store.dequeue_task()
        assert claimed is not None
        assert claimed["spec_name"] == "spec_a"
        assert store.dequeue_task() is None


class TestRetryAndDeadLetter:
    def test_transient_failure_requeues_below_max_attempts(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        store.dequeue_task()
        store.fail_task("task_1", "provider blip", max_attempts=3, retry_backoff_s=0)
        with store.connection() as conn:
            row = conn.execute("SELECT status, error, attempts FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "pending"
        assert row["error"] == "provider blip"
        assert row["attempts"] == 1

    def test_dead_letters_at_max_attempts(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        for attempt in range(3):
            claimed = store.dequeue_task()
            assert claimed is not None, f"attempt {attempt + 1} should claim"
            store.fail_task("task_1", f"error {attempt + 1}", max_attempts=3, retry_backoff_s=0)
        with store.connection() as conn:
            row = conn.execute("SELECT status, error FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "failed"
        assert row["error"] == "error 3"
        assert store.dequeue_task() is None

    def test_default_fail_stays_terminal(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        store.dequeue_task()
        store.fail_task("task_1", "hard error")
        with store.connection() as conn:
            row = conn.execute("SELECT status FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "failed"


class TestStaleRunningRecovery:
    def test_stale_running_row_returns_to_pending(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        store.dequeue_task()
        recovered = store.requeue_stale_running(older_than_seconds=0)
        assert recovered == 1
        with store.connection() as conn:
            row = conn.execute("SELECT status FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "pending"
        assert store.dequeue_task() is not None

    def test_recent_running_row_untouched(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        store.dequeue_task()
        assert store.requeue_stale_running(older_than_seconds=3600) == 0
        with store.connection() as conn:
            row = conn.execute("SELECT status FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "running"


class TestBackoffAndSweepDeadLetter:
    def test_requeue_backoff_defers_next_claim(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        store.dequeue_task()
        store.fail_task("task_1", "blip", max_attempts=3)
        # default backoff schedules the retry in the future
        assert store.dequeue_task() is None
        with store.connection() as conn:
            row = conn.execute("SELECT status, scheduled_at FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "pending" and row["scheduled_at"] is not None

    def test_sweep_dead_letters_crash_looping_task(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        for _ in range(3):
            assert store.dequeue_task() is not None
            # simulate crash: no fail(), just the startup sweep
            store.requeue_stale_running(older_than_seconds=0, max_attempts=3)
        with store.connection() as conn:
            row = conn.execute("SELECT status, error FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] == "failed"
        assert "crash-looped" in row["error"]
        assert store.dequeue_task() is None


class TestRunnerIntegration:
    def _runner(self, store, provider_response: str = "", fail_times: int = 0):
        from autocontext.execution.task_runner import TaskRunner
        from autocontext.providers.base import CompletionResult, LLMProvider, ProviderError

        class FlakyProvider(LLMProvider):
            def __init__(self) -> None:
                self.calls = 0

            def complete(self, system_prompt, user_prompt, model=None, temperature=0.0, max_tokens=4096):  # type: ignore[no-untyped-def]
                self.calls += 1
                if self.calls <= fail_times:
                    raise ProviderError("transient blip")
                return CompletionResult(text='{"score": 0.95, "reasoning": "ok"}')

            def default_model(self) -> str:
                return "stub"

        provider = FlakyProvider()
        runner = TaskRunner(store=store, provider=provider, model="stub", max_attempts=3, retry_backoff_s=0)
        return runner, provider

    def test_startup_recovers_stranded_tasks(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        store.dequeue_task()  # strand it in running
        runner, _ = self._runner(store)
        runner.stale_running_after_s = 0
        runner.max_consecutive_empty = 1
        runner.run()
        with store.connection() as conn:
            row = conn.execute("SELECT status FROM task_queue WHERE id = 'task_1'").fetchone()
        assert row["status"] in ("completed", "failed")
        assert store.requeue_stale_running(older_than_seconds=3600) == 0

    def test_transient_failure_retries_then_succeeds(self, tmp_path) -> None:
        store = _store(tmp_path)
        _enqueue(store)
        runner, provider = self._runner(store, fail_times=1)
        first = runner.run_once()
        assert first is not None and first["status"] == "pending"
        second = runner.run_once()
        assert second is not None and second["status"] == "completed"
        assert provider.calls >= 2  # first claim failed transiently, second claim completed


class TestBootstrapSchemaParity:
    """Review-caught Critical: bootstrap marked 019 applied without adding the
    column, permanently breaking pip-installed (no migrations dir) DBs. Pin
    per-table column parity between the two schema paths."""

    def test_bootstrap_matches_migrated_schema(self, tmp_path) -> None:
        import sqlite3

        from autocontext.storage.bootstrap_schema import bootstrap_core_schema

        migrated = _store(tmp_path / "migrated")
        boot_path = tmp_path / "boot" / "db.sqlite3"
        boot_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(boot_path)) as conn, conn:
            bootstrap_core_schema(conn)

        def columns(db_path, table):
            with closing(sqlite3.connect(db_path)) as conn, conn:
                return sorted(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))

        with closing(sqlite3.connect(migrated.db_path)) as conn, conn:
            tables = sorted(
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            )
        for table in tables:
            migrated_cols = columns(migrated.db_path, table)
            boot_cols = columns(boot_path, table)
            assert boot_cols == migrated_cols, f"schema drift in table {table}"

    def test_bootstrap_task_queue_supports_dequeue(self, tmp_path) -> None:
        import sqlite3

        from autocontext.storage.bootstrap_schema import bootstrap_core_schema
        from autocontext.storage.sqlite_store import SQLiteStore

        db_path = tmp_path / "boot.sqlite3"
        with closing(sqlite3.connect(db_path)) as conn, conn:
            bootstrap_core_schema(conn)
        store = SQLiteStore(db_path)
        store.enqueue_task("t1", "spec")
        claimed = store.dequeue_task()
        assert claimed is not None and claimed["attempts"] == 1
