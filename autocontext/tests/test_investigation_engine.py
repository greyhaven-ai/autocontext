from __future__ import annotations

import json
from pathlib import Path


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
