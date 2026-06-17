from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "docs" / "campaign-mode-report-parity-fixture.json"


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def test_build_campaign_mode_report_matches_shared_fixture() -> None:
    from autocontext.analytics.campaign_mode_report import build_campaign_mode_report

    for case in _cases():
        report = build_campaign_mode_report(
            campaign_id=case["campaign_id"],
            run_id=case["run_id"],
            scenario_name=case["scenario_name"],
            generated_at=case["generated_at"],
            terminal_state=case["terminal_state"],
            branch_budget_defaults=case["branch_budget_defaults"],
            eval_lanes=case["eval_lanes"],
            branches=case["branches"],
            shared_evidence=case["shared_evidence"],
            linked_reports=case["linked_reports"],
            evidence_policy=case.get("evidence_policy"),
        )

        assert report.to_dict() == case["expected_report"]


def test_campaign_mode_report_round_trips_shared_json() -> None:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport

    for case in _cases():
        expected = case["expected_report"]
        assert CampaignModeReport.from_dict(expected).to_dict() == expected


def test_campaign_mode_report_renders_only_included_evidence() -> None:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport, render_campaign_evidence_share

    report = CampaignModeReport.from_dict(_cases()[1]["expected_report"])

    rendered = render_campaign_evidence_share(report)

    assert "share-safe-1" in rendered
    assert "share-risky-1" not in rendered
    assert "Safe branch passed both eval lanes" in rendered


def test_campaign_mode_report_file_store_persists_report(tmp_path: Path) -> None:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport
    from autocontext.storage.campaign_mode_report_store import (
        read_campaign_mode_report,
        read_latest_campaign_mode_reports_markdown,
        write_campaign_mode_report,
    )

    report = CampaignModeReport.from_dict(_cases()[1]["expected_report"])

    write_campaign_mode_report(tmp_path / "knowledge", "grid_ctf", report.run_id, report)

    restored = read_campaign_mode_report(tmp_path / "knowledge", "grid_ctf", report.run_id)
    assert isinstance(restored, CampaignModeReport)
    assert restored.to_dict() == report.to_dict()
    assert "Campaign Mode Report" in read_latest_campaign_mode_reports_markdown(tmp_path / "knowledge", "grid_ctf")


def test_campaign_mode_report_rejects_schema_invalid_data() -> None:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport

    expected = _cases()[1]["expected_report"]
    bad_branch = {**expected["branches"][0], "terminal_state": "unknown"}
    missing_budget = {k: v for k, v in expected["branches"][0].items() if k != "budget"}
    negative_budget = {
        **expected["branch_budget_defaults"],
        "max_tokens": -1,
    }

    for payload in [
        {**expected, "surprise": True},
        {**expected, "campaign_id": ""},
        {**expected, "branches": [bad_branch]},
        {**expected, "branches": [missing_budget]},
        {**expected, "branch_budget_defaults": negative_budget},
    ]:
        try:
            CampaignModeReport.from_dict(payload)
        except ValueError:
            continue
        raise AssertionError("schema-invalid campaign mode report was accepted")
