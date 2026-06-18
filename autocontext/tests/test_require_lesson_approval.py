import inspect
from pathlib import Path

from autocontext.loop.generation_runner import GenerationRunner
from autocontext.loop.stage_helpers.persistence_helpers import _stage_pending_lessons
from autocontext.loop.stage_types import GenerationContext
from autocontext.server.protocol import StartRunCmd
from autocontext.server.run_manager import RunManager
from autocontext.storage.artifacts import ArtifactStore


def test_start_run_cmd_flag_defaults_false() -> None:
    assert StartRunCmd(scenario="grid_ctf", generations=3).require_lesson_approval is False
    cmd = StartRunCmd(scenario="grid_ctf", generations=3, require_lesson_approval=True)
    assert cmd.require_lesson_approval is True


def test_threading_params_present() -> None:
    assert "require_lesson_approval" in inspect.signature(RunManager.start_run).parameters
    assert "require_lesson_approval" in inspect.signature(GenerationRunner.run).parameters
    assert "require_lesson_approval" in GenerationContext.__dataclass_fields__


class _Outputs:
    def __init__(self, lessons: str) -> None:
        self.coach_lessons = lessons


class _Ctx:
    def __init__(self, flag: bool, lessons: str) -> None:
        self.scenario_name = "scn"
        self.generation = 7
        self.previous_best = 0.5
        self.gate_decision = "advance"
        self.require_lesson_approval = flag
        self.outputs = _Outputs(lessons)


def _artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / "claude_skills",
    )


def test_flag_off_is_noop(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _stage_pending_lessons(_Ctx(False, "- avoid X\nuse Y"), artifacts=art)
    assert art.lesson_store.read_lessons("scn") == []


def test_flag_on_stages_pending(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    _stage_pending_lessons(_Ctx(True, "- avoid X\nuse Y"), artifacts=art)
    lessons = art.lesson_store.read_lessons("scn")
    assert sorted(v.text for v in lessons) == ["avoid X", "use Y"]
    assert all(v.meta.approval_status == "pending" for v in lessons)


def test_flag_on_skips_non_advance(tmp_path: Path) -> None:
    art = _artifacts(tmp_path)
    ctx = _Ctx(True, "x")
    ctx.gate_decision = "retry"
    _stage_pending_lessons(ctx, artifacts=art)
    assert art.lesson_store.read_lessons("scn") == []
