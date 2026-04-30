from __future__ import annotations

import json
from pathlib import Path

from autocontext.investigation.browser_context import InvestigationBrowserContext


def _spec_response() -> str:
    return json.dumps(
        {
            "description": "Investigate checkout errors",
            "environment_description": "Production checkout stack",
            "initial_state_description": "Customers report intermittent 500s during checkout",
            "evidence_pool_description": "Application logs, deployment metadata, and a misleading cron alert",
            "diagnosis_target": "A config regression in the checkout service",
            "success_criteria": ["identify the root cause", "avoid the red herring"],
            "failure_modes": ["follow the cron alert", "stop before enough evidence is gathered"],
            "max_steps": 4,
            "actions": [
                {
                    "name": "inspect_logs",
                    "description": "Inspect logs",
                    "parameters": {},
                    "preconditions": [],
                    "effects": ["log_evidence_collected"],
                },
                {
                    "name": "review_deploy",
                    "description": "Review deploy metadata",
                    "parameters": {},
                    "preconditions": [],
                    "effects": ["deploy_evidence_collected"],
                },
                {
                    "name": "record_diagnosis",
                    "description": "Record final diagnosis",
                    "parameters": {"diagnosis": "string"},
                    "preconditions": ["inspect_logs"],
                    "effects": ["diagnosis_recorded"],
                },
            ],
        }
    )


def _hypothesis_response() -> str:
    return json.dumps(
        {
            "question": "What caused the checkout errors?",
            "hypotheses": [
                {
                    "statement": "A config regression in the checkout service",
                    "confidence": 0.82,
                },
                {"statement": "The cron alert caused the outage", "confidence": 0.21},
            ],
        }
    )


class TestInvestigationEngine:
    def test_iterative_mode_runs_multi_step_llm_session_without_synthetic_scenario(
        self,
        tmp_path: Path,
    ) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        calls: list[tuple[str, str]] = []

        def analysis_llm(system: str, user: str) -> str:
            calls.append((system, user))
            return json.dumps(
                {
                    "question": "What caused checkout errors?",
                    "hypotheses": [
                        {
                            "statement": "A config regression caused checkout errors",
                            "confidence": 0.78,
                            "status": "supported",
                        }
                    ],
                    "evidence": [
                        {
                            "id": f"e{len(calls)}",
                            "kind": "observation",
                            "source": "iterative_session",
                            "summary": "Deployment evidence points at a config regression",
                            "supports": ["h0"],
                            "contradicts": [],
                            "is_red_herring": False,
                        }
                    ],
                    "conclusion": {
                        "best_explanation": "A config regression caused checkout errors",
                        "confidence": 0.78,
                        "limitations": [],
                    },
                    "unknowns": [],
                    "recommended_next_steps": ["Inspect config diff"],
                }
            )

        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: "synthetic path should not run",
            analysis_llm_fn=analysis_llm,
            knowledge_root=tmp_path,
        )

        result = engine.run(
            InvestigationRequest(
                description="Investigate checkout errors",
                max_steps=3,
                mode="iterative",
            )
        )

        investigation_dir = tmp_path / "_investigations" / result.name
        assert result.status == "completed"
        assert result.steps_executed == 3
        assert len(calls) == 3
        assert result.evidence[0].source == "iterative_session"
        assert (investigation_dir / "report.json").exists()
        assert (investigation_dir / "transcript.json").exists()
        assert not (investigation_dir / "scenario.py").exists()

    def test_iterative_mode_records_compaction_ledger_and_events_under_context_pressure(
        self,
        tmp_path: Path,
    ) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest
        from autocontext.loop.events import EventStreamEmitter
        from autocontext.storage import ArtifactStore

        long_signal = "root cause signal from deployment metadata " * 700

        def analysis_llm(_system: str, _user: str) -> str:
            return json.dumps(
                {
                    "question": "What caused checkout errors?",
                    "hypotheses": [
                        {
                            "statement": "A config regression caused checkout errors",
                            "confidence": 0.83,
                            "status": "supported",
                        }
                    ],
                    "evidence": [
                        {
                            "id": "e0",
                            "kind": "observation",
                            "source": "iterative_session",
                            "summary": long_signal,
                            "supports": ["h0"],
                        }
                    ],
                    "conclusion": {
                        "best_explanation": long_signal,
                        "confidence": 0.83,
                    },
                }
            )

        artifacts = ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )
        events_path = tmp_path / "runs" / "events.ndjson"
        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: "synthetic path should not run",
            analysis_llm_fn=analysis_llm,
            knowledge_root=tmp_path / "knowledge",
            artifacts=artifacts,
            events=EventStreamEmitter(events_path),
            context_budget_tokens=64,
        )

        result = engine.run(
            InvestigationRequest(
                description="Investigate checkout errors",
                max_steps=2,
                mode="iterative",
            )
        )

        ledger_path = tmp_path / "runs" / result.id / "compactions.jsonl"
        latest_path = tmp_path / "runs" / result.id / "compactions.latest"
        assert result.status == "completed"
        assert ledger_path.exists()
        assert latest_path.exists()

        event_rows = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
        event_names = [row["event"] for row in event_rows]
        ledger_entries = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]

        assert latest_path.read_text(encoding="utf-8").strip()
        assert ledger_entries[0]["details"]["trigger"] == "context_pressure"
        assert ledger_entries[0]["details"]["run_id"] == result.id
        assert "investigation_started" in event_names
        assert "investigation_step_completed" in event_names
        assert "investigation_completed" in event_names
        assert all(row["payload"].get("run_id") == result.id for row in event_rows)

    def test_runs_from_plain_language_description(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        calls: list[tuple[str, str]] = []

        def spec_llm(system: str, user: str) -> str:
            calls.append((system, user))
            return _spec_response()

        def analysis_llm(system: str, user: str) -> str:
            calls.append((system, user))
            return _hypothesis_response()

        engine = InvestigationEngine(
            spec_llm_fn=spec_llm,
            analysis_llm_fn=analysis_llm,
            knowledge_root=tmp_path,
        )

        result = engine.run(InvestigationRequest(description="Investigate checkout errors"))

        assert result.status == "completed"
        assert result.family == "investigation"
        assert result.question == "What caused the checkout errors?"
        assert len(result.hypotheses) == 2
        assert len(result.evidence) >= 1
        assert result.artifacts.investigation_dir.endswith(result.name)
        assert (tmp_path / "_investigations" / result.name / "spec.json").exists()
        assert (tmp_path / "_investigations" / result.name / "report.json").exists()
        assert len(calls) == 2

    def test_parses_wrapped_and_fenced_spec_json(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        wrapped = "Here's the investigation spec:\n```json\n" + _spec_response() + "\n```\nUse this to continue."

        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: wrapped,
            analysis_llm_fn=lambda *_: _hypothesis_response(),
            knowledge_root=tmp_path,
        )

        result = engine.run(InvestigationRequest(description="Investigate checkout errors"))

        assert result.status == "completed"
        assert result.description == "Investigate checkout errors"

    def test_skips_blocked_actions_until_a_valid_investigation_step_is_available(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        blocked_first = json.dumps(
            {
                "description": "Investigate checkout errors",
                "environment_description": "Production checkout stack",
                "initial_state_description": "Customers report intermittent 500s during checkout",
                "evidence_pool_description": "Application logs and a misleading cron alert",
                "diagnosis_target": "A config regression in the checkout service",
                "success_criteria": ["identify the root cause", "avoid the red herring"],
                "failure_modes": ["follow the cron alert"],
                "max_steps": 4,
                "actions": [
                    {
                        "name": "record_diagnosis",
                        "description": "Record final diagnosis",
                        "parameters": {"diagnosis": "string"},
                        "preconditions": ["inspect_logs has been completed"],
                        "effects": ["diagnosis_recorded"],
                    },
                    {
                        "name": "inspect_logs",
                        "description": "Inspect logs",
                        "parameters": {},
                        "preconditions": [],
                        "effects": ["log_evidence_collected"],
                    },
                ],
            }
        )

        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: blocked_first,
            analysis_llm_fn=lambda *_: _hypothesis_response(),
            knowledge_root=tmp_path,
        )

        result = engine.run(InvestigationRequest(description="Investigate checkout errors"))

        assert result.status == "completed"
        assert result.steps_executed >= 1
        assert len(result.evidence) >= 1

    def test_treats_environmental_preconditions_as_advisory_context(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        advisory_preconditions = json.dumps(
            {
                "description": "Investigate checkout errors",
                "environment_description": "Production checkout stack",
                "initial_state_description": "Customers report intermittent 500s during checkout",
                "evidence_pool_description": "Application logs and a misleading cron alert",
                "diagnosis_target": "A config regression in the checkout service",
                "success_criteria": ["identify the root cause", "avoid the red herring"],
                "failure_modes": ["follow the cron alert"],
                "max_steps": 4,
                "actions": [
                    {
                        "name": "inspect_logs",
                        "description": "Inspect logs",
                        "parameters": {},
                        "preconditions": ["Log aggregation system is accessible"],
                        "effects": ["log_evidence_collected"],
                    },
                    {
                        "name": "record_diagnosis",
                        "description": "Record final diagnosis",
                        "parameters": {"diagnosis": "string"},
                        "preconditions": ["inspect_logs"],
                        "effects": ["diagnosis_recorded"],
                    },
                ],
            }
        )

        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: advisory_preconditions,
            analysis_llm_fn=lambda *_: _hypothesis_response(),
            knowledge_root=tmp_path,
        )

        result = engine.run(InvestigationRequest(description="Investigate checkout errors"))

        assert result.status == "completed"
        assert result.steps_executed >= 1
        assert len(result.evidence) >= 1

    def test_returns_failed_result_when_spec_generation_is_not_json(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: "not json at all",
            analysis_llm_fn=lambda *_: _hypothesis_response(),
            knowledge_root=tmp_path,
        )

        result = engine.run(InvestigationRequest(description="Investigate checkout errors"))

        assert result.status == "failed"
        assert "valid JSON" in (result.error or "")

    def test_includes_browser_context_in_prompts_and_evidence(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        calls: list[tuple[str, str]] = []

        def spec_llm(system: str, user: str) -> str:
            calls.append((system, user))
            return _spec_response()

        def analysis_llm(system: str, user: str) -> str:
            calls.append((system, user))
            return _hypothesis_response()

        engine = InvestigationEngine(
            spec_llm_fn=spec_llm,
            analysis_llm_fn=analysis_llm,
            knowledge_root=tmp_path,
        )

        result = engine.run(
            InvestigationRequest(
                description="Investigate checkout errors",
                browser_context=InvestigationBrowserContext(
                    url="https://example.com/status",
                    title="Status Page",
                    visible_text="Checkout is degraded for some users.",
                    html_path="/tmp/status.html",
                    screenshot_path="/tmp/status.png",
                ),
            )
        )

        assert result.status == "completed"
        assert len(calls) == 2
        assert "Live browser context" in calls[0][1]
        assert "https://example.com/status" in calls[0][1]
        assert "Checkout is degraded for some users." in calls[1][1]
        assert any(item.kind == "browser_snapshot" for item in result.evidence)
        assert any(item.source == "https://example.com/status" for item in result.evidence)

    def test_hypothesis_prompt_uses_clustered_evidence_summary(self, tmp_path: Path) -> None:
        from autocontext.investigation.engine import InvestigationEngine, InvestigationRequest

        captured_user_prompts: list[str] = []

        def analysis_llm(_system: str, user: str) -> str:
            captured_user_prompts.append(user)
            return _hypothesis_response()

        engine = InvestigationEngine(
            spec_llm_fn=lambda *_: _spec_response(),
            analysis_llm_fn=analysis_llm,
            knowledge_root=tmp_path,
        )

        result = engine.run(InvestigationRequest(description="Investigate checkout errors"))

        assert result.status == "completed"
        assert captured_user_prompts
        prompt = captured_user_prompts[0]
        assert "Evidence clusters" in prompt
        assert "Potential red herrings" in prompt
        assert "Diagnosis target:" not in prompt
        assert "A config regression in the checkout service" not in prompt
