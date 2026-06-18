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


def test_campaign_mode_report_counts_only_evidence_backed_items_against_share_budget() -> None:
    from autocontext.analytics.campaign_mode_report import build_campaign_mode_report

    case = _cases()[0]
    report = build_campaign_mode_report(
        campaign_id=case["campaign_id"],
        run_id=case["run_id"],
        scenario_name=case["scenario_name"],
        generated_at=case["generated_at"],
        terminal_state=case["terminal_state"],
        branch_budget_defaults=case["branch_budget_defaults"],
        eval_lanes=case["eval_lanes"],
        branches=case["branches"],
        shared_evidence=[
            {
                "share_id": "without-evidence",
                "from_branch_id": "branch-1",
                "to_branch_ids": [],
                "summary": "No artifact reference yet.",
                "evidence_refs": [],
            },
            {
                "share_id": "with-evidence",
                "from_branch_id": "branch-1",
                "to_branch_ids": [],
                "summary": "This one should fit the prompt budget.",
                "evidence_refs": [{"uri": "artifact://runs/run-1/eval.json", "summary": "passed"}],
            },
        ],
        linked_reports=case["linked_reports"],
        evidence_policy={"max_shared_items": 1, "max_summary_chars": 240},
    )

    assert [item.included for item in report.evidence_sharing.items] == [False, True]


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


def test_campaign_mode_report_file_store_rejects_path_traversal(tmp_path: Path) -> None:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport
    from autocontext.storage.campaign_mode_report_store import write_campaign_mode_report

    report = CampaignModeReport.from_dict(_cases()[0]["expected_report"])
    knowledge_root = tmp_path / "knowledge"

    try:
        write_campaign_mode_report(knowledge_root, "grid_ctf", "../../../outside", report)
    except ValueError:
        pass
    else:
        raise AssertionError("path-traversing run_id was accepted")

    assert not (tmp_path / "outside.json").exists()


def test_campaign_mode_report_rejects_schema_invalid_data() -> None:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport

    expected = _cases()[1]["expected_report"]
    bad_branch = {**expected["branches"][0], "terminal_state": "unknown"}
    missing_budget = {k: v for k, v in expected["branches"][0].items() if k != "budget"}
    negative_budget = {
        **expected["branch_budget_defaults"],
        "max_tokens": -1,
    }
    missing_default_budget_field = {k: v for k, v in expected["branch_budget_defaults"].items() if k != "max_seconds"}
    missing_branch_budget_field = {k: v for k, v in expected["branches"][0]["budget"].items() if k != "max_seconds"}
    missing_schema_version = {k: v for k, v in expected.items() if k != "schema_version"}
    missing_policy_field = {
        **expected["evidence_sharing"],
        "policy": {"max_shared_items": expected["evidence_sharing"]["policy"]["max_shared_items"]},
    }

    for payload in [
        missing_schema_version,
        {**expected, "surprise": True},
        {**expected, "campaign_id": ""},
        {**expected, "branches": [bad_branch]},
        {**expected, "branches": [missing_budget]},
        {**expected, "branches": [{**expected["branches"][0], "budget": missing_branch_budget_field}]},
        {**expected, "branch_budget_defaults": negative_budget},
        {**expected, "branch_budget_defaults": missing_default_budget_field},
        {**expected, "evidence_sharing": missing_policy_field},
    ]:
        try:
            CampaignModeReport.from_dict(payload)
        except ValueError:
            continue
        raise AssertionError("schema-invalid campaign mode report was accepted")
