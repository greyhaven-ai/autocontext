from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "docs" / "negative-result-ledger-parity-fixture.json"
APPLICABILITY_FIXTURE_PATH = ROOT / "docs" / "negative-result-applicability-parity-fixture.json"


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def _applicability_fixture() -> dict[str, Any]:
    return json.loads(APPLICABILITY_FIXTURE_PATH.read_text(encoding="utf-8"))


def test_build_negative_result_ledger_matches_shared_fixture() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger

    for case in _cases():
        ledger = build_negative_result_ledger(
            run_id=case["run_id"],
            events=case["events"],
            generated_at=case["generated_at"],
            scenario_name="legacy-fixture",
            context_bundle_digest="sha256:fixture",
            evaluator_epoch="fixture-eval",
        )

        expected = case["expected_ledger"]
        assert ledger.schema_version == 2
        assert [entry.result_id for entry in ledger.entries] == [entry["result_id"] for entry in expected["entries"]]
        assert all(entry.applicability_scope == "exact_bundle" for entry in ledger.entries)
        assert ledger.failure_mode_summary == type(ledger).from_dict(expected).failure_mode_summary


def test_negative_result_ledger_round_trips_shared_json() -> None:
    from autocontext.analytics.negative_result_ledger import NegativeResultLedger

    for case in _cases():
        migrated = NegativeResultLedger.from_dict(case["expected_ledger"])
        assert migrated.schema_version == 2
        assert all(entry.applicability_scope == "context_unknown" for entry in migrated.entries)
        assert NegativeResultLedger.from_dict(migrated.to_dict()) == migrated


def test_negative_result_lessons_distinguish_noise_caution_and_hard_bans() -> None:
    from autocontext.analytics.negative_result_ledger import NegativeResultLedger, render_negative_result_lessons

    caution = NegativeResultLedger.from_dict(_cases()[0]["expected_ledger"])
    noise = NegativeResultLedger.from_dict(_cases()[1]["expected_ledger"])
    hard_ban = NegativeResultLedger.from_dict(_cases()[2]["expected_ledger"])

    caution_text = render_negative_result_lessons(caution)
    assert "Caution:" in caution_text
    assert "not a ban" in caution_text
    assert "Replay diverged at turn 6" in caution_text

    assert render_negative_result_lessons(noise) == ""

    hard_ban_text = render_negative_result_lessons(hard_ban)
    assert "Caution:" in hard_ban_text
    assert "legacy evidence has unknown context" in hard_ban_text
    assert "unsafe_action" in hard_ban_text
    assert "evt-hard-1" in hard_ban_text
    assert "evt-hard-2" in hard_ban_text


def test_file_store_persists_negative_result_ledger(tmp_path: Path) -> None:
    from autocontext.analytics.negative_result_ledger import NegativeResultLedger
    from autocontext.storage.negative_result_ledger_store import (
        read_latest_negative_result_ledgers_markdown,
        read_negative_result_ledger,
        write_negative_result_ledger,
    )

    expected = _cases()[2]["expected_ledger"]
    ledger = NegativeResultLedger.from_dict(expected)

    write_negative_result_ledger(tmp_path / "knowledge", "grid_ctf", ledger.run_id, ledger)

    restored = read_negative_result_ledger(tmp_path / "knowledge", "grid_ctf", ledger.run_id)
    assert isinstance(restored, NegativeResultLedger)
    assert restored == ledger
    assert "legacy evidence has unknown context" in read_latest_negative_result_ledgers_markdown(
        tmp_path / "knowledge", "grid_ctf"
    )


def test_negative_result_ledger_rejects_schema_invalid_data() -> None:
    from autocontext.analytics.negative_result_ledger import NegativeResultLedger

    expected = _cases()[0]["expected_ledger"]
    bad_entry = {**expected["entries"][0], "disposition": "maybe"}
    missing_branch = {k: v for k, v in expected["entries"][0].items() if k != "branch_id"}
    negative_generation = {**expected["entries"][0], "generation_index": -1}

    for payload in [
        {**expected, "surprise": True},
        {**expected, "run_id": ""},
        {**expected, "entries": [bad_entry]},
        {**expected, "entries": [missing_branch]},
        {**expected, "entries": [negative_generation]},
    ]:
        try:
            NegativeResultLedger.from_dict(payload)
        except ValueError:
            continue
        raise AssertionError("schema-invalid negative-result ledger was accepted")


def test_contextual_applicability_scopes_bans_and_triggers_retests() -> None:
    from autocontext.analytics.negative_result_ledger import (
        NegativeComponentDependency,
        NegativeResultApplicabilityContext,
        build_negative_result_ledger,
        evaluate_negative_result_applicability,
        render_negative_result_lessons,
    )

    ledger = build_negative_result_ledger(
        run_id="run-contextual",
        generated_at="2026-08-17T12:00:00Z",
        scenario_name="grid_ctf",
        context_bundle_digest="sha256:bundle-a",
        context_bundle_family="grid-family",
        evaluator_epoch="eval-7",
        verifier_digest="sha256:verifier-a",
        component_dependencies=[
            NegativeComponentDependency(component_kind="tool", key="move", digest="sha256:move-a")
        ],
        environment_fingerprint="linux-amd64:v1",
        events=[
            {
                "event_id": "neg-contextual",
                "event_type": "branch_rejected",
                "timestamp": "2026-08-17T11:00:00Z",
                "branch_id": "branch-red",
                "payload": {
                    "failure_kind": "unsafe_action",
                    "disposition": "hard_ban",
                    "reason": "Verifier rejected the action.",
                    "evidence_expires_at": "2026-09-01T00:00:00Z",
                    "evidence_refs": [{"uri": "evidence.json", "summary": "Violation reproduced."}],
                },
            }
        ],
    )
    current = NegativeResultApplicabilityContext(
        scenario_name="grid_ctf",
        context_bundle_digest="sha256:bundle-a",
        context_bundle_family="grid-family",
        evaluator_epoch="eval-7",
        verifier_digest="sha256:verifier-a",
        component_digests={"tool:move": "sha256:move-a"},
        environment_fingerprint="linux-amd64:v1",
        observed_at="2026-08-17T12:00:00Z",
    )

    applicable = evaluate_negative_result_applicability(ledger.entries[0], current)
    assert applicable.state == "applicable"
    assert applicable.effective_disposition == "hard_ban"
    assert "Hard ban:" in render_negative_result_lessons(ledger, applicability_context=current)

    stale = current.model_copy(update={"evaluator_epoch": "eval-8"})
    decision = evaluate_negative_result_applicability(ledger.entries[0], stale)
    assert decision.state == "retest_due"
    assert decision.effective_disposition == "caution"
    assert "evaluator epoch changed" in render_negative_result_lessons(ledger, applicability_context=stale)


def test_successful_retest_supersedes_prior_result_without_erasing_history() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger, link_negative_result_retest

    context = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
    }
    original = build_negative_result_ledger(
        run_id="run-retest",
        generated_at="2026-08-17T12:00:00Z",
        **context,
        events=[
            {
                "event_id": "neg-original",
                "event_type": "branch_rejected",
                "branch_id": "branch-red",
                "payload": {
                    "reason": "Old verifier rejected this branch.",
                    "evidence_refs": [{"uri": "old.json", "summary": "Old failure."}],
                },
            }
        ],
    )
    retest = build_negative_result_ledger(
        run_id="run-retest-2",
        generated_at="2026-08-18T12:00:00Z",
        **context,
        events=[
            {
                "event_id": "neg-retest",
                "event_type": "candidate_rejected",
                "branch_id": "branch-red-retest",
                "payload": {
                    "reason": "Retest did not reproduce the failure.",
                    "retest_of_result_id": "neg-original",
                    "retest_outcome": "not_reproduced",
                    "disposition": "noise",
                    "evidence_refs": [{"uri": "new.json", "summary": "Replay passed."}],
                },
            }
        ],
    ).entries[0]

    updated = link_negative_result_retest(original, original_result_id="neg-original", retest_entry=retest)

    assert [entry.result_id for entry in updated.entries] == ["neg-original", "neg-retest"]
    assert updated.entries[0].superseded_by_result_id == "neg-retest"
    assert updated.to_markdown().endswith("## Prompt Lessons\n- None\n")


def test_shared_applicability_and_retest_fixture() -> None:
    from autocontext.analytics.negative_result_ledger import (
        NegativeResultApplicabilityContext,
        NegativeResultEntry,
        NegativeResultLedger,
        evaluate_negative_result_applicability,
        link_negative_result_retest,
    )

    fixture = _applicability_fixture()
    ledger = NegativeResultLedger.from_dict(fixture["ledger"])
    contexts = {
        name: NegativeResultApplicabilityContext.from_dict(value)
        for name, value in fixture["contexts"].items()
    }
    entries = {entry.result_id: entry for entry in ledger.entries}

    for case in fixture["decisions"]:
        actual = evaluate_negative_result_applicability(entries[case["result_id"]], contexts[case["context"]])
        assert actual.to_dict() == case["expected"]

    retest = fixture["successful_retest"]
    updated = link_negative_result_retest(
        ledger,
        original_result_id=retest["original_result_id"],
        retest_entry=NegativeResultEntry.from_dict(retest["entry"]),
    )
    original = next(entry for entry in updated.entries if entry.result_id == retest["original_result_id"])
    assert original.superseded_by_result_id == retest["expected_superseded_by_result_id"]
