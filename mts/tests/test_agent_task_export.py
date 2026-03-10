"""Tests for agent task export and search indexing."""

from __future__ import annotations

from mts.knowledge.export import SkillPackage, export_agent_task_skill


def _make_example_outputs() -> list[dict]:
    return [
        {"output": "Great answer", "score": 0.95, "reasoning": "Thorough and accurate"},
        {"output": "Okay answer", "score": 0.70, "reasoning": "Partially correct"},
        {"output": "Weak answer", "score": 0.30, "reasoning": "Missing key points"},
    ]


def _make_agent_task_package(**overrides) -> SkillPackage:
    defaults = dict(
        scenario_name="test_task",
        display_name="Test Task",
        description="A test agent task",
        playbook="Follow the rubric.",
        lessons=["Be concise", "Cite sources"],
        best_strategy={"approach": "structured"},
        best_score=0.85,
        best_elo=1600.0,
        hints="Focus on clarity",
        task_prompt="Write a summary of the article.",
        judge_rubric="Score based on accuracy and completeness.",
        example_outputs=_make_example_outputs(),
        output_format="free_text",
    )
    defaults.update(overrides)
    return SkillPackage(**defaults)


class TestSkillPackageAgentTaskMarkdown:
    def test_includes_task_section(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "## Task" in md
        assert "Write a summary of the article." in md

    def test_includes_evaluation_criteria(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "## Evaluation Criteria" in md
        assert "Score based on accuracy and completeness." in md

    def test_includes_example_outputs(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "## Example Outputs" in md
        assert "Great answer" in md
        assert "Okay answer" in md
        assert "Weak answer" in md

    def test_example_outputs_use_details_blocks(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "<details>" in md
        assert "<summary>" in md
        assert "</details>" in md
        assert "score: 0.95" in md

    def test_example_outputs_include_reasoning(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "Thorough and accurate" in md
        assert "**Reasoning:**" in md

    def test_best_strategy_as_text_block(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        # Agent tasks use ``` not ```json
        assert "```\n{" in md
        assert "```json" not in md

    def test_limits_to_three_examples(self) -> None:
        outputs = _make_example_outputs() + [
            {"output": "Fourth", "score": 0.10, "reasoning": "Bad"},
        ]
        pkg = _make_agent_task_package(example_outputs=outputs)
        md = pkg.to_skill_markdown()
        assert "Fourth" not in md
        assert md.count("<details>") == 3

    def test_includes_lessons(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "## Operational Lessons" in md
        assert "Be concise" in md

    def test_includes_playbook(self) -> None:
        pkg = _make_agent_task_package()
        md = pkg.to_skill_markdown()
        assert "## Playbook" in md
        assert "Follow the rubric." in md


class TestSkillPackageBackwardCompat:
    def test_no_agent_task_fields_renders_normally(self) -> None:
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Capture the flag",
            playbook="Standard playbook",
            lessons=["lesson1"],
            best_strategy={"key": "val"},
            best_score=0.9,
            best_elo=1700.0,
            hints="some hints",
        )
        md = pkg.to_skill_markdown()
        assert "## Task" not in md
        assert "## Evaluation Criteria" not in md
        assert "```json" in md

    def test_to_dict_without_agent_fields(self) -> None:
        pkg = SkillPackage(
            scenario_name="test",
            display_name="Test",
            description="desc",
            playbook="pb",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
        )
        d = pkg.to_dict()
        assert "task_prompt" not in d
        assert "judge_rubric" not in d

    def test_to_dict_with_agent_fields(self) -> None:
        pkg = _make_agent_task_package()
        d = pkg.to_dict()
        assert d["task_prompt"] == "Write a summary of the article."
        assert d["judge_rubric"] == "Score based on accuracy and completeness."
        assert len(d["example_outputs"]) == 3
        assert d["output_format"] == "free_text"


class TestExportAgentTaskSkill:
    def test_creates_proper_package(self) -> None:
        pkg = export_agent_task_skill(
            scenario_name="write_summary",
            task_prompt="Summarize this.",
            judge_rubric="Accuracy matters.",
            output_format="free_text",
            playbook="Be thorough.",
            lessons=["Keep it short"],
            best_outputs=[
                {"output": "Good summary", "score": 0.9, "reasoning": "Accurate"},
            ],
            hints="Focus on key points",
        )
        assert pkg.scenario_name == "write_summary"
        assert pkg.task_prompt == "Summarize this."
        assert pkg.judge_rubric == "Accuracy matters."
        assert pkg.output_format == "free_text"
        assert pkg.best_score == 0.9
        assert pkg.hints == "Focus on key points"
        assert pkg.display_name == "Write Summary"

    def test_empty_outputs(self) -> None:
        pkg = export_agent_task_skill(
            scenario_name="empty_task",
            task_prompt="Do something.",
            judge_rubric="Judge it.",
            output_format="json_schema",
            playbook="",
            lessons=[],
            best_outputs=[],
        )
        assert pkg.best_score == 0.0
        assert pkg.example_outputs is None

    def test_renders_valid_markdown(self) -> None:
        pkg = export_agent_task_skill(
            scenario_name="md_test",
            task_prompt="Write code.",
            judge_rubric="Must compile.",
            output_format="code",
            playbook="Use Python.",
            lessons=["Test first"],
            best_outputs=[
                {"output": "print('hi')", "score": 1.0, "reasoning": "Works"},
            ],
        )
        md = pkg.to_skill_markdown()
        assert "## Task" in md
        assert "## Evaluation Criteria" in md
        assert "## Example Outputs" in md


class TestReferenceContextExport:
    def test_reference_context_in_markdown(self) -> None:
        pkg = _make_agent_task_package(reference_context="RLM means Recursive Language Model")
        md = pkg.to_skill_markdown()
        assert "## Reference Context" in md
        assert "RLM means Recursive Language Model" in md

    def test_reference_context_in_dict(self) -> None:
        pkg = _make_agent_task_package(reference_context="Some context")
        d = pkg.to_dict()
        assert d["reference_context"] == "Some context"

    def test_no_reference_context_not_in_dict(self) -> None:
        pkg = _make_agent_task_package()
        d = pkg.to_dict()
        assert "reference_context" not in d

    def test_export_agent_task_skill_with_reference_context(self) -> None:
        pkg = export_agent_task_skill(
            scenario_name="ref_test",
            task_prompt="Write about X",
            judge_rubric="Check X",
            output_format="free_text",
            playbook="Be accurate",
            lessons=[],
            best_outputs=[],
            reference_context="X is a specific thing",
        )
        assert pkg.reference_context == "X is a specific thing"
        md = pkg.to_skill_markdown()
        assert "## Reference Context" in md


class TestSearchIndexAgentTaskFields:
    def test_keyword_score_includes_task_fields(self) -> None:
        """Verify the search scorer weights task_prompt and judge_rubric fields."""
        from mts.knowledge.search import _keyword_score

        entry = {
            "name": "my_task",
            "display_name": "My Task",
            "description": "A task",
            "strategy_interface": "",
            "evaluation_criteria": "",
            "lessons": "",
            "playbook_excerpt": "",
            "hints": "",
            "task_prompt": "summarize the financial report",
            "judge_rubric": "accuracy and completeness scoring",
        }
        score, reasons = _keyword_score(["summarize", "financial"], entry)
        assert score > 0
        assert any("task_prompt" in r for r in reasons)

    def test_empty_task_fields_no_error(self) -> None:
        from mts.knowledge.search import _keyword_score

        entry = {
            "name": "basic",
            "display_name": "Basic",
            "description": "test",
            "strategy_interface": "",
            "evaluation_criteria": "",
            "lessons": "",
            "playbook_excerpt": "",
            "hints": "",
            "task_prompt": "",
            "judge_rubric": "",
        }
        score, _ = _keyword_score(["something"], entry)
        assert score == 0.0


class TestHarnessInSkillPackage:
    """MTS-93: harness field in SkillPackage."""

    def test_to_dict_includes_harness(self) -> None:
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Capture the flag",
            playbook="pb",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
            harness={"validate_move": "def validate_move(): ..."},
        )
        d = pkg.to_dict()
        assert "harness" in d
        assert d["harness"]["validate_move"] == "def validate_move(): ..."

    def test_to_dict_empty_harness(self) -> None:
        pkg = SkillPackage(
            scenario_name="test",
            display_name="Test",
            description="desc",
            playbook="pb",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
        )
        d = pkg.to_dict()
        assert d["harness"] == {}

    def test_skill_markdown_includes_harness_section(self) -> None:
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Capture the flag",
            playbook="pb",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
            harness={"validate_move": "def validate_move(): ..."},
        )
        md = pkg.to_skill_markdown()
        assert "## Harness Validators" in md
        assert "### validate_move" in md
        assert "def validate_move(): ..." in md

    def test_skill_markdown_no_harness_section_when_empty(self) -> None:
        pkg = SkillPackage(
            scenario_name="grid_ctf",
            display_name="Grid Ctf",
            description="Capture the flag",
            playbook="pb",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
        )
        md = pkg.to_skill_markdown()
        assert "## Harness Validators" not in md
