"""Durability hardening for high-severity stores (AC-903).

Covers: FacetStore, ModelRegistry, pending-playbook staging,
SupervisorStore, and knowledge snapshots. Each store must write
atomically (no `.tmp` leftovers, temp + replace) and degrade on corrupt
state instead of raising on a hot path.
"""

from __future__ import annotations

import json
from pathlib import Path

from autocontext.analytics.facets import RunFacet
from autocontext.analytics.store import FacetStore
from autocontext.session.supervisor import Supervisor, SupervisorStore
from autocontext.storage.playbook_approval import read_pending_playbook, stage_pending_playbook
from autocontext.training.model_registry import DistilledModelRecord, ModelRegistry


def _no_tmp_files(root: Path) -> bool:
    return not list(root.rglob("*.tmp"))


def _facet(run_id: str, scenario: str = "grid_ctf") -> RunFacet:
    return RunFacet.model_validate(
        {
            "run_id": run_id,
            "scenario": scenario,
            "scenario_family": "game",
            "agent_provider": "deterministic",
            "executor_mode": "local",
            "total_generations": 1,
            "advances": 1,
            "retries": 0,
            "rollbacks": 0,
            "best_score": 0.5,
            "best_elo": 1000.0,
            "total_duration_seconds": 1.0,
            "total_tokens": 0,
            "total_cost_usd": 0.0,
            "tool_invocations": 0,
            "validation_failures": 0,
            "consultation_count": 0,
            "consultation_cost_usd": 0.0,
            "friction_signals": [],
            "delight_signals": [],
            "events": [],
        }
    )


class TestFacetStore:
    def test_persist_is_atomic(self, tmp_path) -> None:
        store = FacetStore(tmp_path)
        store.persist(_facet("run_a"))
        assert _no_tmp_files(tmp_path)
        assert store.load("run_a") is not None

    def test_corrupt_facet_is_skipped_in_list(self, tmp_path) -> None:
        store = FacetStore(tmp_path)
        store.persist(_facet("run_a"))
        (store.root / "torn.json").write_text("{not json", encoding="utf-8")
        facets = store.list_facets()
        assert [f.run_id for f in facets] == ["run_a"]

    def test_corrupt_facet_load_returns_none(self, tmp_path) -> None:
        store = FacetStore(tmp_path)
        (store.root / "bad.json").write_text("{not json", encoding="utf-8")
        assert store.load("bad") is None


class TestModelRegistry:
    def _record(self, artifact_id: str) -> DistilledModelRecord:
        return DistilledModelRecord.model_validate(
            {
                "artifact_id": artifact_id,
                "scenario": "grid_ctf",
                "scenario_family": "game",
                "backend": "mlx",
                "checkpoint_path": "/tmp/x",
                "runtime_types": ["provider"],
                "activation_state": "candidate",
                "training_metrics": {},
                "provenance": {},
            }
        )

    def test_register_is_atomic(self, tmp_path) -> None:
        registry = ModelRegistry(tmp_path)
        registry.register(self._record("art_a"))
        assert _no_tmp_files(tmp_path)

    def test_corrupt_record_skipped_in_list_and_load(self, tmp_path) -> None:
        registry = ModelRegistry(tmp_path)
        registry.register(self._record("art_a"))
        (tmp_path / "model_registry" / "torn.json").write_text("{not json", encoding="utf-8")
        assert [r.artifact_id for r in registry.list_all()] == ["art_a"]
        assert registry.load("torn") is None


class TestPendingPlaybookStaging:
    def test_orphan_md_without_provenance_unwedges(self, tmp_path) -> None:
        scenario_dir = tmp_path / "grid_ctf"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "playbook.pending.md").write_text("orphan\n", encoding="utf-8")
        view = read_pending_playbook(tmp_path, "grid_ctf")
        assert view["has_pending"] is False
        status = stage_pending_playbook(
            tmp_path,
            "grid_ctf",
            "new content",
            source_run_id="run_1",
            generation=1,
            curator_decision="accept",
        )
        assert status == "pending"
        assert read_pending_playbook(tmp_path, "grid_ctf")["has_pending"] is True

    def test_corrupt_provenance_degrades(self, tmp_path) -> None:
        stage_pending_playbook(
            tmp_path,
            "grid_ctf",
            "content",
            source_run_id="run_1",
            generation=1,
            curator_decision="accept",
        )
        scenario_dir = tmp_path / "grid_ctf"
        (scenario_dir / "playbook.pending.json").write_text("{not json", encoding="utf-8")
        view = read_pending_playbook(tmp_path, "grid_ctf")
        assert view["has_pending"] is True
        assert view["provenance"] is None

    def test_staging_leaves_no_temp(self, tmp_path) -> None:
        stage_pending_playbook(
            tmp_path,
            "grid_ctf",
            "content",
            source_run_id="run_1",
            generation=1,
            curator_decision="accept",
        )
        assert _no_tmp_files(tmp_path)


class TestSupervisorStore:
    def test_save_is_atomic(self, tmp_path) -> None:
        store = SupervisorStore(tmp_path / "supervisor.json")
        store.save(Supervisor())
        assert _no_tmp_files(tmp_path)

    def test_corrupt_state_restore_is_noop(self, tmp_path) -> None:
        path = tmp_path / "supervisor.json"
        path.write_text("{not json", encoding="utf-8")
        store = SupervisorStore(path)
        supervisor = Supervisor()
        store.restore(supervisor)
        assert supervisor._entries == {}

    def test_bad_entry_is_skipped_on_restore(self, tmp_path) -> None:
        path = tmp_path / "supervisor.json"
        path.write_text(json.dumps({"sid1": {"not": "an entry"}}), encoding="utf-8")
        store = SupervisorStore(path)
        supervisor = Supervisor()
        store.restore(supervisor)
        assert supervisor._entries == {}


class TestSnapshotRestore:
    def test_corrupt_hint_state_snapshot_does_not_break_restore(self, tmp_path) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        store = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        scenario_dir = store._scenario_dir("grid_ctf")
        snapshot_dir = scenario_dir / "snapshots" / "run_src"
        snapshot_dir.mkdir(parents=True)
        (snapshot_dir / "playbook.md").write_text("snapshot playbook\n", encoding="utf-8")
        (snapshot_dir / "hint_state.json").write_text("{not json", encoding="utf-8")
        assert store.restore_knowledge_snapshot("grid_ctf", "run_src") is True
        assert "snapshot playbook" in (scenario_dir / "playbook.md").read_text(encoding="utf-8")


class TestMutationStores:
    def test_harness_mutation_write_is_atomic(self, tmp_path, monkeypatch) -> None:
        import os as os_module

        from autocontext.harness.mutations.spec import HarnessMutation, MutationType
        from autocontext.harness.mutations.store import MutationStore

        replaced: list[str] = []
        real_replace = os_module.replace

        def spy(src, dst):  # type: ignore[no-untyped-def]
            replaced.append(str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr("autocontext.util.json_io.os.replace", spy)
        store = MutationStore(tmp_path)
        mutation = HarnessMutation(mutation_type=MutationType.PROMPT_FRAGMENT, content="c", generation=1)
        store.save("grid_ctf", [mutation])
        assert not list(tmp_path.rglob("*.tmp"))
        assert len(store.load("grid_ctf")) == 1
        assert any(dst.endswith("mutations.json") for dst in replaced)

    def test_mutation_log_truncate_is_atomic_and_preserves_tail(self, tmp_path, monkeypatch) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry, MutationLog

        log = MutationLog(tmp_path, max_entries=2)
        for gen in range(1, 5):
            log.append(
                "grid_ctf",
                MutationEntry.model_validate(
                    {"mutation_type": "playbook_update", "generation": gen, "payload": {"summary": f"g{gen}"}}
                ),
            )
        entries = log.read("grid_ctf")
        assert [e.generation for e in entries] == [3, 4]
        assert not list(tmp_path.rglob("*.tmp"))
        import os as os_module

        replaced: list[str] = []
        real_replace = os_module.replace

        def spy(src, dst):  # type: ignore[no-untyped-def]
            replaced.append(str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr("autocontext.util.json_io.os.replace", spy)
        log.append(
            "grid_ctf",
            MutationEntry.model_validate(
                {"mutation_type": "playbook_update", "generation": 5, "payload": {"summary": "g5"}}
            ),
        )
        assert any(dst.endswith(".jsonl") for dst in replaced)

    def test_mutation_log_read_skips_wrong_typed_line(self, tmp_path) -> None:
        from autocontext.knowledge.mutation_log import MutationEntry, MutationLog

        log = MutationLog(tmp_path, max_entries=10)
        log.append(
            "grid_ctf",
            MutationEntry.model_validate({"mutation_type": "playbook_update", "generation": 1, "payload": {"summary": "ok"}}),
        )
        with log._log_path("grid_ctf").open("a", encoding="utf-8") as fh:
            fh.write('{"mutation_type": "playbook_update", "generation": "not_an_int", "payload": {}}\n')
        entries = log.read("grid_ctf")
        assert [e.payload.get("summary") for e in entries] == ["ok"]
