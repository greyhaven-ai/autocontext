"""Tests for AC-261: wire session notebook into runtime prompts and cockpit flows.

Covers: NotebookContextWarning, EffectiveContextPreview,
NotebookContextProvider (role-specific injection, guardrails),
and build_prompt_bundle integration with notebook_contexts.
"""

from __future__ import annotations

from autocontext.notebook.types import SessionNotebook

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _full_notebook() -> SessionNotebook:
    return SessionNotebook(
        session_id="sess-1",
        scenario_name="grid_ctf",
        current_objective="Maximize flag captures while defending home base",
        current_hypotheses=[
            "High aggression + moderate defense works best above 0.6 density",
            "Scouting phase should last 2-3 turns before committing",
        ],
        best_run_id="run-42",
        best_generation=5,
        best_score=0.78,
        unresolved_questions=[
            "Does terrain type affect optimal aggression level?",
            "What is the minimum defense needed to prevent flag loss?",
        ],
        operator_observations=[
            "Noticed scores plateau around gen 4-5 in recent runs",
            "Coach hints seem to push aggression too high",
        ],
        follow_ups=[
            "Try balanced aggression=0.6 defense=0.5 next",
            "Investigate terrain-based parameter switching",
        ],
        updated_at="2026-03-14T12:00:00Z",
        created_at="2026-03-13T10:00:00Z",
    )


def _empty_notebook() -> SessionNotebook:
    return SessionNotebook(
        session_id="sess-empty",
        scenario_name="grid_ctf",
    )


def _partial_notebook() -> SessionNotebook:
    return SessionNotebook(
        session_id="sess-partial",
        scenario_name="grid_ctf",
        current_objective="Focus on defense",
        operator_observations=["Defense-heavy strategies seem to plateau early"],
    )


# ===========================================================================
# Role-field mapping
# ===========================================================================


class TestRoleNotebookFields:
    def test_mapping_exists_for_all_roles(self) -> None:
        from autocontext.notebook.context_provider import ROLE_NOTEBOOK_FIELDS

        assert "competitor" in ROLE_NOTEBOOK_FIELDS
        assert "analyst" in ROLE_NOTEBOOK_FIELDS
        assert "coach" in ROLE_NOTEBOOK_FIELDS
        assert "architect" in ROLE_NOTEBOOK_FIELDS

    def test_competitor_fields(self) -> None:
        from autocontext.notebook.context_provider import ROLE_NOTEBOOK_FIELDS

        fields = ROLE_NOTEBOOK_FIELDS["competitor"]
        assert "current_objective" in fields
        assert "current_hypotheses" in fields
        assert "follow_ups" in fields

    def test_analyst_fields(self) -> None:
        from autocontext.notebook.context_provider import ROLE_NOTEBOOK_FIELDS

        fields = ROLE_NOTEBOOK_FIELDS["analyst"]
        assert "current_objective" in fields
        assert "unresolved_questions" in fields
        assert "operator_observations" in fields

    def test_coach_fields(self) -> None:
        from autocontext.notebook.context_provider import ROLE_NOTEBOOK_FIELDS

        fields = ROLE_NOTEBOOK_FIELDS["coach"]
        assert "current_objective" in fields
        assert "follow_ups" in fields
        assert "operator_observations" in fields

    def test_architect_fields(self) -> None:
        from autocontext.notebook.context_provider import ROLE_NOTEBOOK_FIELDS

        fields = ROLE_NOTEBOOK_FIELDS["architect"]
        assert "current_hypotheses" in fields
        assert "unresolved_questions" in fields


# ===========================================================================
# NotebookContextWarning
# ===========================================================================


class TestNotebookContextWarning:
    def test_construction(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextWarning

        w = NotebookContextWarning(
            field="best_score",
            warning_type="stale_score",
            description="Notebook best score 0.78 is below current run best 0.85",
        )
        assert w.warning_type == "stale_score"
        assert w.field == "best_score"


# ===========================================================================
# EffectiveContextPreview
# ===========================================================================


class TestEffectiveContextPreview:
    def test_construction(self) -> None:
        from autocontext.notebook.context_provider import EffectiveContextPreview

        preview = EffectiveContextPreview(
            session_id="sess-1",
            role_contexts={"competitor": "some context", "analyst": "other context"},
            warnings=[],
            notebook_empty=False,
            created_at="2026-03-14T12:00:00Z",
        )
        assert preview.session_id == "sess-1"
        assert len(preview.role_contexts) == 2
        assert not preview.notebook_empty

    def test_roundtrip(self) -> None:
        from autocontext.notebook.context_provider import (
            EffectiveContextPreview,
            NotebookContextWarning,
        )

        preview = EffectiveContextPreview(
            session_id="sess-2",
            role_contexts={"competitor": "ctx"},
            warnings=[
                NotebookContextWarning(
                    field="best_score", warning_type="stale_score",
                    description="Stale",
                ),
            ],
            notebook_empty=False,
            created_at="2026-03-14T12:00:00Z",
        )
        d = preview.to_dict()
        restored = EffectiveContextPreview.from_dict(d)
        assert restored.session_id == "sess-2"
        assert len(restored.warnings) == 1
        assert restored.warnings[0].warning_type == "stale_score"


# ===========================================================================
# NotebookContextProvider — for_role
# ===========================================================================


class TestNotebookContextProviderForRole:
    def test_competitor_gets_objective_hypotheses_followups(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_full_notebook(), "competitor")

        assert "Maximize flag captures" in ctx
        assert "High aggression" in ctx
        assert "Try balanced aggression" in ctx
        # Analyst-only fields should NOT be present
        assert "terrain type affect" not in ctx
        assert "scores plateau" not in ctx

    def test_analyst_gets_objective_questions_observations(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_full_notebook(), "analyst")

        assert "Maximize flag captures" in ctx
        assert "terrain type affect" in ctx
        assert "scores plateau" in ctx
        # Competitor-only fields should NOT be present
        assert "Try balanced aggression" not in ctx

    def test_coach_gets_objective_followups_observations(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_full_notebook(), "coach")

        assert "Maximize flag captures" in ctx
        assert "Try balanced aggression" in ctx
        assert "scores plateau" in ctx
        # Architect-only hypotheses should be absent
        assert "Scouting phase" not in ctx

    def test_architect_gets_hypotheses_questions(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_full_notebook(), "architect")

        assert "High aggression" in ctx
        assert "terrain type affect" in ctx
        # Competitor-only follow_ups should be absent
        assert "Try balanced aggression" not in ctx
        assert "scores plateau" not in ctx

    def test_unknown_role_returns_empty(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_full_notebook(), "unknown_role")
        assert ctx == ""

    def test_empty_notebook_returns_empty(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_empty_notebook(), "competitor")
        assert ctx == ""

    def test_partial_notebook_skips_empty_fields(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        ctx = provider.for_role(_partial_notebook(), "analyst")

        assert "Focus on defense" in ctx
        assert "plateau early" in ctx
        # Empty fields not rendered
        assert "Hypotheses" not in ctx
        assert "Questions" not in ctx


# ===========================================================================
# NotebookContextProvider — check_warnings
# ===========================================================================


class TestNotebookContextProviderWarnings:
    def test_stale_score_warning(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        warnings = provider.check_warnings(
            _full_notebook(),
            current_best_score=0.85,
        )

        stale_warnings = [w for w in warnings if w.warning_type == "stale_score"]
        assert len(stale_warnings) == 1
        assert "0.78" in stale_warnings[0].description

    def test_no_warning_when_scores_match(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        warnings = provider.check_warnings(
            _full_notebook(),
            current_best_score=0.78,
        )

        stale_warnings = [w for w in warnings if w.warning_type == "stale_score"]
        assert len(stale_warnings) == 0

    def test_no_warning_when_notebook_score_higher(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        warnings = provider.check_warnings(
            _full_notebook(),
            current_best_score=0.50,
        )

        stale_warnings = [w for w in warnings if w.warning_type == "stale_score"]
        assert len(stale_warnings) == 0

    def test_no_warnings_on_empty_notebook(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        warnings = provider.check_warnings(_empty_notebook())
        assert warnings == []

    def test_no_warning_without_current_score(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        warnings = provider.check_warnings(_full_notebook())
        stale_warnings = [w for w in warnings if w.warning_type == "stale_score"]
        assert len(stale_warnings) == 0


# ===========================================================================
# NotebookContextProvider — build_effective_preview
# ===========================================================================


class TestEffectiveContextPreviewBuilder:
    def test_includes_all_role_contexts(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        preview = provider.build_effective_preview(_full_notebook())

        assert "competitor" in preview.role_contexts
        assert "analyst" in preview.role_contexts
        assert "coach" in preview.role_contexts
        assert "architect" in preview.role_contexts
        assert not preview.notebook_empty

    def test_includes_warnings(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        preview = provider.build_effective_preview(
            _full_notebook(), current_best_score=0.90,
        )

        assert len(preview.warnings) > 0

    def test_empty_notebook_flag(self) -> None:
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()
        preview = provider.build_effective_preview(_empty_notebook())

        assert preview.notebook_empty


# ===========================================================================
# build_prompt_bundle integration with notebook_contexts
# ===========================================================================


class TestBuildPromptBundleNotebook:
    def _make_bundle_args(self) -> dict:
        """Minimal args for build_prompt_bundle."""
        from autocontext.scenarios.base import Observation

        return {
            "scenario_rules": "Test rules",
            "strategy_interface": "Test interface",
            "evaluation_criteria": "Test criteria",
            "previous_summary": "Previous gen summary",
            "observation": Observation(
                narrative="Test narrative",
                state={},
                constraints=[],
            ),
            "current_playbook": "Test playbook",
            "available_tools": "None",
        }

    def test_competitor_prompt_contains_notebook_context(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        args = self._make_bundle_args()
        args["notebook_contexts"] = {
            "competitor": "## Session Context\nObjective: Win the game",
        }
        bundle = build_prompt_bundle(**args)
        assert "Objective: Win the game" in bundle.competitor

    def test_analyst_prompt_contains_different_context(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        args = self._make_bundle_args()
        args["notebook_contexts"] = {
            "competitor": "Competitor-only context",
            "analyst": "Analyst-specific observations here",
        }
        bundle = build_prompt_bundle(**args)

        assert "Competitor-only context" in bundle.competitor
        assert "Analyst-specific observations" in bundle.analyst
        # Cross-contamination check
        assert "Analyst-specific observations" not in bundle.competitor
        assert "Competitor-only context" not in bundle.analyst

    def test_no_notebook_no_block(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        args = self._make_bundle_args()
        bundle = build_prompt_bundle(**args)

        assert "Session notebook" not in bundle.competitor.lower()
        assert "Session notebook" not in bundle.analyst.lower()

    def test_empty_dict_no_block(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        args = self._make_bundle_args()
        args["notebook_contexts"] = {}
        bundle = build_prompt_bundle(**args)

        assert "Session notebook" not in bundle.competitor.lower()

    def test_all_roles_receive_their_context(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        args = self._make_bundle_args()
        args["notebook_contexts"] = {
            "competitor": "COMP-CTX-MARKER",
            "analyst": "ANALYST-CTX-MARKER",
            "coach": "COACH-CTX-MARKER",
            "architect": "ARCH-CTX-MARKER",
        }
        bundle = build_prompt_bundle(**args)

        assert "COMP-CTX-MARKER" in bundle.competitor
        assert "ANALYST-CTX-MARKER" in bundle.analyst
        assert "COACH-CTX-MARKER" in bundle.coach
        assert "ARCH-CTX-MARKER" in bundle.architect

    def test_notebook_context_respects_context_budget(self) -> None:
        from autocontext.prompts.templates import build_prompt_bundle

        args = self._make_bundle_args()
        args["context_budget_tokens"] = 20
        args["notebook_contexts"] = {
            "competitor": "NOTEBOOK " * 200,
        }
        bundle = build_prompt_bundle(**args)

        assert "[... truncated for context budget ...]" in bundle.competitor


# ===========================================================================
# Integration: notebook edit → prompt output change
# ===========================================================================


class TestIntegrationNotebookEditChangesPrompt:
    def test_editing_objective_changes_competitor_prompt(self) -> None:
        """Proves that editing notebook fields changes downstream prompt inputs."""
        from autocontext.notebook.context_provider import NotebookContextProvider
        from autocontext.prompts.templates import build_prompt_bundle
        from autocontext.scenarios.base import Observation

        provider = NotebookContextProvider()
        base_args = {
            "scenario_rules": "Rules",
            "strategy_interface": "Interface",
            "evaluation_criteria": "Criteria",
            "previous_summary": "",
            "observation": Observation(narrative="", state={}, constraints=[]),
            "current_playbook": "",
            "available_tools": "",
        }

        # Build with original notebook
        nb = _full_notebook()
        contexts_v1 = {
            role: provider.for_role(nb, role)
            for role in ("competitor", "analyst", "coach", "architect")
        }
        bundle_v1 = build_prompt_bundle(**base_args, notebook_contexts=contexts_v1)

        # Edit the notebook objective
        nb_edited = SessionNotebook(
            session_id=nb.session_id,
            scenario_name=nb.scenario_name,
            current_objective="CHANGED: Focus entirely on defense this generation",
            current_hypotheses=nb.current_hypotheses,
            best_run_id=nb.best_run_id,
            best_generation=nb.best_generation,
            best_score=nb.best_score,
            unresolved_questions=nb.unresolved_questions,
            operator_observations=nb.operator_observations,
            follow_ups=nb.follow_ups,
        )
        contexts_v2 = {
            role: provider.for_role(nb_edited, role)
            for role in ("competitor", "analyst", "coach", "architect")
        }
        bundle_v2 = build_prompt_bundle(**base_args, notebook_contexts=contexts_v2)

        # Competitor prompt should have changed
        assert "Maximize flag captures" in bundle_v1.competitor
        assert "Focus entirely on defense" in bundle_v2.competitor
        assert "Maximize flag captures" not in bundle_v2.competitor

    def test_adding_observation_changes_analyst_prompt(self) -> None:
        """Adding an operator observation changes the analyst prompt."""
        from autocontext.notebook.context_provider import NotebookContextProvider

        provider = NotebookContextProvider()

        nb_before = _partial_notebook()
        ctx_before = provider.for_role(nb_before, "analyst")

        nb_after = SessionNotebook(
            session_id=nb_before.session_id,
            scenario_name=nb_before.scenario_name,
            current_objective=nb_before.current_objective,
            operator_observations=[
                *nb_before.operator_observations,
                "NEW: Terrain seems to matter more than expected",
            ],
        )
        ctx_after = provider.for_role(nb_after, "analyst")

        assert "Terrain seems to matter" not in ctx_before
        assert "Terrain seems to matter" in ctx_after
