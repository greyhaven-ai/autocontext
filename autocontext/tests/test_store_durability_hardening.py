"""Durability hardening for high-severity stores (AC-903).

Covers: FacetStore, ModelRegistry, pending-playbook staging,
SupervisorStore, and knowledge snapshots. Each store must write
atomically (no `.tmp` leftovers, temp + replace) and degrade on corrupt
state instead of raising on a hot path.
"""

from __future__ import annotations

import json
import logging
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
            MutationEntry.model_validate({"mutation_type": "playbook_update", "generation": 5, "payload": {"summary": "g5"}}),
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


class TestRemainingStores:
    def _spy_replace(self, monkeypatch):
        import os as os_module

        replaced: list[str] = []
        real_replace = os_module.replace

        def spy(src, dst):  # type: ignore[no-untyped-def]
            replaced.append(str(dst))
            return real_replace(src, dst)

        monkeypatch.setattr("autocontext.util.json_io.os.replace", spy)
        return replaced

    def test_self_improve_jsonl_skips_malformed_and_writes_atomic(self, tmp_path, monkeypatch) -> None:
        from autocontext.training.autoresearch.self_improve import _read_jsonl, _write_jsonl

        path = tmp_path / "records.jsonl"
        path.write_text('{"a": 1}\n{torn\n{"b": 2}\n', encoding="utf-8")
        assert _read_jsonl(path) == [{"a": 1}, {"b": 2}]
        replaced = self._spy_replace(monkeypatch)
        _write_jsonl(path, [{"c": 3}])
        assert str(path) in replaced

    def test_dataset_append_guards_torn_trailing_line(self, tmp_path) -> None:
        from autocontext.ambient.datasets import DatasetStore

        store = DatasetStore(tmp_path)
        dataset = store.dataset_path("target_a")
        dataset.parent.mkdir(parents=True, exist_ok=True)
        with dataset.open("w", encoding="utf-8") as fh:
            fh.write('{"partial": tru')
        store.append_records("target_a", [{"ok": 1}])
        lines = dataset.read_text(encoding="utf-8").splitlines()
        assert lines[-1] == '{"ok": 1}'
        assert len(lines) == 2

    def test_blob_registry_corrupt_or_misshapen_degrades(self, tmp_path, caplog) -> None:
        from autocontext.blobstore.registry import BlobRegistry

        path = tmp_path / "registry.json"
        path.write_text("{not json", encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="autocontext.blobstore.registry"):
            assert BlobRegistry.load(path)._entries == {}
        assert "blob registry unreadable" in caplog.text
        caplog.clear()
        path.write_text('{"run_1": "not a dict"}', encoding="utf-8")
        with caplog.at_level(logging.WARNING, logger="autocontext.blobstore.registry"):
            assert BlobRegistry.load(path)._entries == {}
        assert "skipping misshapen blob registry run run_1" in caplog.text

    def test_research_manifest_corrupt_degrades_and_bad_entries_skipped(self, tmp_path) -> None:
        from autocontext.research.persistence import ResearchStore

        store = ResearchStore(tmp_path)
        store._manifest_path.write_text("{not json", encoding="utf-8")
        assert store.brief_count() == 0
        store._manifest_path.write_text('[{"no_keys": true}]', encoding="utf-8")
        assert store.list_briefs("session_x") == []

    def test_charter_save_is_atomic(self, tmp_path, monkeypatch) -> None:
        from autocontext.ambient.charter import (
            Charter,
            CharterBudgets,
            CharterSource,
            CharterTarget,
        )
        from autocontext.ambient.charter_io import load_charter, save_charter

        replaced = self._spy_replace(monkeypatch)
        path = tmp_path / "charter.yaml"
        charter = Charter(
            tier="oss",
            control_surface="local",
            autonomy="propose",
            sources=[CharterSource(name="native", kind="autocontext", enabled=True)],
            targets=[
                CharterTarget(
                    name="competitor-grid",
                    kind="role",
                    selector="competitor@grid_ctf",
                    base_model="Qwen/Qwen2.5-3B-Instruct",
                    method="sft-distill",
                    min_dataset_records=500,
                    eval_suite="grid_ctf_holdout",
                )
            ],
            budgets=CharterBudgets(gpu_hours_per_window=8.0, window_hours=24, disk_quota_gb=200.0),
        )
        save_charter(charter, path)
        assert str(path) in replaced
        assert load_charter(path).tier == "oss"

    def test_lesson_store_wrong_shaped_records_degrade(self, tmp_path) -> None:
        from autocontext.knowledge.lessons import LessonStore

        store = LessonStore(tmp_path / "knowledge", tmp_path / "skills")
        scenario_dir = tmp_path / "knowledge" / "grid_ctf"
        scenario_dir.mkdir(parents=True)
        (scenario_dir / "lessons.json").write_text('[{"wrong": "shape"}]', encoding="utf-8")
        assert store.read_lessons("grid_ctf") == []


class TestArtifactReaderHardening:
    def _store(self, tmp_path: Path):
        from autocontext.storage.artifacts import ArtifactStore

        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_corrupt_progress_returns_none(self, tmp_path) -> None:
        store = self._store(tmp_path)
        path = store._scenario_dir("grid_ctf") / "progress.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert store.read_progress("grid_ctf") is None

    def test_corrupt_progress_report_skipped_in_latest(self, tmp_path) -> None:
        from autocontext.knowledge.normalized_metrics import (
            CostEfficiency,
            NormalizedProgress,
            RunProgressReport,
        )

        store = self._store(tmp_path)
        good = RunProgressReport(
            run_id="run_good",
            scenario="grid_ctf",
            total_generations=1,
            advances=1,
            rollbacks=0,
            retries=0,
            progress=NormalizedProgress(
                raw_score=0.5,
                normalized_score=0.5,
                score_floor=0.0,
                score_ceiling=1.0,
                pct_of_ceiling=50.0,
            ),
            cost=CostEfficiency(
                total_input_tokens=1,
                total_output_tokens=1,
                total_tokens=2,
                total_cost_usd=0.0,
            ),
        )
        store.write_progress_report("grid_ctf", "run_good", good)
        bad_path = store._progress_report_dir("grid_ctf") / "run_bad.json"
        bad_path.write_text("{not json", encoding="utf-8")
        reports = store.read_latest_progress_reports("grid_ctf", max_reports=5)
        assert len(reports) == 1
        assert store.read_progress_report("grid_ctf", "run_bad") is None

    def test_corrupt_weakness_report_skipped_in_latest(self, tmp_path) -> None:
        store = self._store(tmp_path)
        wr_dir = store._weakness_dir("grid_ctf")
        wr_dir.mkdir(parents=True, exist_ok=True)
        (wr_dir / "run_bad.json").write_text("{not json", encoding="utf-8")
        assert store.read_latest_weakness_reports("grid_ctf", max_reports=5) == []
        assert store.read_weakness_report("grid_ctf", "run_bad") is None

    def test_corrupt_harness_version_returns_empty(self, tmp_path) -> None:
        store = self._store(tmp_path)
        path = store._harness_version_path("grid_ctf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert store.get_harness_version("grid_ctf") == {}

    def test_corrupt_notebook_returns_none(self, tmp_path) -> None:
        store = self._store(tmp_path)
        path = tmp_path / "runs" / "sessions" / "sess_1" / "notebook.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert store.read_notebook("sess_1") is None

    def test_wrong_typed_hint_state_falls_back(self, tmp_path) -> None:
        import json as json_module

        store = self._store(tmp_path)
        path = store._hint_state_path("grid_ctf")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json_module.dumps({"policy": {"max_hints": "seven"}, "hints": "wrong"}), encoding="utf-8")
        manager = store.read_hint_manager("grid_ctf")
        assert manager is not None


class TestReportStoreReaderHardening:
    def test_campaign_report_corrupt_returns_none_and_latest_skips(self, tmp_path) -> None:
        from autocontext.storage.campaign_mode_report_store import (
            campaign_mode_report_path,
            read_campaign_mode_report,
            read_latest_campaign_mode_reports_markdown,
        )

        path = campaign_mode_report_path(tmp_path, "grid_ctf", "run_bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert read_campaign_mode_report(tmp_path, "grid_ctf", "run_bad") is None
        assert read_latest_campaign_mode_reports_markdown(tmp_path, "grid_ctf") == ""

    def test_goal_run_report_corrupt_returns_none(self, tmp_path) -> None:
        from autocontext.storage.goal_run_report_store import goal_run_report_path, read_goal_run_report

        path = goal_run_report_path(tmp_path, "goal_1", "run_bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert read_goal_run_report(tmp_path, "goal_1", "run_bad") is None

    def test_negative_ledger_corrupt_returns_none_and_latest_skips(self, tmp_path) -> None:
        from autocontext.storage.negative_result_ledger_store import (
            negative_result_ledger_path,
            read_latest_negative_result_ledgers_markdown,
            read_negative_result_ledger,
        )

        path = negative_result_ledger_path(tmp_path, "grid_ctf", "run_bad")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        assert read_negative_result_ledger(tmp_path, "grid_ctf", "run_bad") is None
        assert read_latest_negative_result_ledgers_markdown(tmp_path, "grid_ctf") == ""


class TestPendingPlaybookRaceSafety:
    """Review fixes: the read path must never delete files, and a torn clear
    (provenance json without md) must not wedge staging forever."""

    def _stage(self, tmp_path) -> None:
        stage_pending_playbook(
            tmp_path,
            "grid_ctf",
            "content",
            source_run_id="run_1",
            generation=1,
            curator_decision="accept",
        )

    def test_read_does_not_delete_mid_stage_orphan(self, tmp_path) -> None:
        scenario_dir = tmp_path / "grid_ctf"
        scenario_dir.mkdir(parents=True)
        md = scenario_dir / "playbook.pending.md"
        md.write_text("mid-stage\n", encoding="utf-8")
        view = read_pending_playbook(tmp_path, "grid_ctf")
        assert view["has_pending"] is False
        assert md.exists()

    def test_json_orphan_from_torn_clear_unwedges_staging(self, tmp_path) -> None:
        self._stage(tmp_path)
        scenario_dir = tmp_path / "grid_ctf"
        (scenario_dir / "playbook.pending.md").unlink()
        assert read_pending_playbook(tmp_path, "grid_ctf")["has_pending"] is False
        self._stage(tmp_path)
        assert read_pending_playbook(tmp_path, "grid_ctf")["has_pending"] is True

    def test_genuine_pending_still_blocks_staging(self, tmp_path) -> None:
        self._stage(tmp_path)
        import pytest as pytest_module

        with pytest_module.raises(ValueError, match="pending playbook already exists"):
            self._stage(tmp_path)
