from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "docs" / "negative-result-ledger-parity-fixture.json"
APPLICABILITY_FIXTURE_PATH = ROOT / "docs" / "negative-result-applicability-parity-fixture.json"
ROUNDING_FIXTURE_PATH = ROOT / "fixtures" / "numeric-rounding-parity.json"
ID_FIXTURE_PATH = ROOT / "fixtures" / "negative-result-id-parity.json"


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


def test_idless_events_use_shared_content_derived_ids_and_retests_target_only_one_result() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger, link_negative_result_retest

    fixture = json.loads(ID_FIXTURE_PATH.read_text(encoding="utf-8"))
    defaults = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
    }
    ledger = build_negative_result_ledger(
        run_id="run-idless",
        generated_at="2026-08-17T12:00:00Z",
        events=fixture["events"],
        **defaults,
    )

    result_ids = [entry.result_id for entry in ledger.entries]
    assert result_ids == fixture["expected_result_ids"]
    assert len(set(result_ids)) == len(result_ids)
    replay = build_negative_result_ledger(
        run_id="run-idless",
        generated_at="2026-08-17T12:00:00Z",
        events=fixture["events"],
        **defaults,
    )
    assert [entry.result_id for entry in replay.entries] == result_ids

    retest = build_negative_result_ledger(
        run_id="run-idless-retest",
        generated_at="2026-08-18T12:00:00Z",
        events=[
            {
                "event_type": "candidate_rejected",
                "timestamp": "2026-08-18T10:00:00Z",
                "branch_id": "aa-retest",
                "payload": {
                    "result_id": "retest-first-idless-result",
                    "disposition": "caution",
                    "reason": "Controlled replay did not reproduce the first failure.",
                    "retest_of_result_id": result_ids[0],
                    "retest_outcome": "not_reproduced",
                    "evaluated_seeds": ["seed-1"],
                    "evidence_refs": [{"uri": "retest.json", "summary": "Controlled replay passed."}],
                },
            }
        ],
        **defaults,
    ).entries[0]
    updated = link_negative_result_retest(ledger, original_result_id=result_ids[0], retest_entry=retest)

    assert updated.entries[0].superseded_by_result_id == retest.result_id
    assert all(entry.superseded_by_result_id is None for entry in updated.entries[1:3])


def test_duplicate_result_ids_fail_closed_on_build_parse_and_link() -> None:
    from autocontext.analytics.negative_result_ledger import (
        NegativeResultLedger,
        build_negative_result_ledger,
        link_negative_result_retest,
    )

    defaults = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
    }
    duplicate_events = [
        {
            "event_id": "duplicate-negative-result",
            "event_type": "branch_rejected",
            "branch_id": branch_id,
            "payload": {"reason": f"Failure on {branch_id}."},
        }
        for branch_id in ("branch-a", "branch-b")
    ]
    with pytest.raises(ValueError, match="duplicate negative result ID"):
        build_negative_result_ledger(
            run_id="run-duplicate-build",
            generated_at="2026-08-17T12:00:00Z",
            events=duplicate_events,
            **defaults,
        )

    fixture = json.loads(ID_FIXTURE_PATH.read_text(encoding="utf-8"))
    ledger = build_negative_result_ledger(
        run_id="run-valid",
        generated_at="2026-08-17T12:00:00Z",
        events=fixture["events"],
        **defaults,
    )
    duplicate_payload = ledger.to_dict()
    duplicate_payload["entries"] = [duplicate_payload["entries"][0], duplicate_payload["entries"][0]]
    with pytest.raises(ValueError, match="duplicate negative result ID"):
        NegativeResultLedger.from_dict(duplicate_payload)

    malformed_ledger = ledger.model_copy(update={"entries": [ledger.entries[0], ledger.entries[0]]})
    with pytest.raises(ValueError, match="duplicate negative result ID"):
        link_negative_result_retest(
            malformed_ledger,
            original_result_id=ledger.entries[0].result_id,
            retest_entry=ledger.entries[1],
        )


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
        trial_cohort="cohort-a",
        component_dependencies=[NegativeComponentDependency(component_kind="tool", key="move", digest="sha256:move-a")],
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
        trial_cohort="cohort-a",
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

    other_cohort = current.model_copy(update={"trial_cohort": "cohort-b"})
    decision = evaluate_negative_result_applicability(ledger.entries[0], other_cohort)
    assert decision.state == "retest_due"
    assert decision.reason == "trial cohort changed"


def test_successful_retest_supersedes_prior_result_without_erasing_history() -> None:
    from autocontext.analytics.negative_result_ledger import (
        NegativeComponentDependency,
        build_negative_result_ledger,
        link_negative_result_retest,
    )

    context = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
        "verifier_digest": "sha256:verifier-a",
        "trial_cohort": "cohort-a",
        "component_dependencies": [NegativeComponentDependency(component_kind="tool", key="move", digest="sha256:move-a")],
        "environment_fingerprint": "linux-amd64:v1",
    }
    original = build_negative_result_ledger(
        run_id="run-retest",
        generated_at="2026-08-17T12:00:00Z",
        **context,
        events=[
            {
                "event_id": "neg-original",
                "event_type": "branch_rejected",
                "timestamp": "2026-08-17T11:00:00Z",
                "branch_id": "branch-red",
                "payload": {
                    "reason": "Old verifier rejected this branch.",
                    "disposition": "hard_ban",
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
                "timestamp": "2026-08-18T11:00:00Z",
                "branch_id": "branch-red-retest",
                "payload": {
                    "reason": "Retest did not reproduce the failure.",
                    "retest_of_result_id": "neg-original",
                    "retest_outcome": "not_reproduced",
                    "disposition": "caution",
                    "evaluated_seeds": ["seed-1"],
                    "evidence_refs": [{"uri": "new.json", "summary": "Replay passed."}],
                },
            }
        ],
    ).entries[0]

    updated = link_negative_result_retest(original, original_result_id="neg-original", retest_entry=retest)

    assert [entry.result_id for entry in updated.entries] == ["neg-original", "neg-retest"]
    assert updated.entries[0].superseded_by_result_id == "neg-retest"
    rendered = updated.to_markdown()
    assert "neg-retest" in rendered
    assert "Hard ban:" not in rendered


def test_unrelated_retests_do_not_supersede_negative_results() -> None:
    from autocontext.analytics.negative_result_ledger import (
        NegativeComponentDependency,
        build_negative_result_ledger,
        link_negative_result_retest,
        render_negative_result_lessons,
    )

    dependency = NegativeComponentDependency(component_kind="tool", key="move", digest="sha256:move-a")
    defaults = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
        "verifier_digest": "sha256:verifier-a",
        "trial_cohort": "cohort-a",
        "component_dependencies": [dependency],
        "environment_fingerprint": "linux-amd64:v1",
    }
    original = build_negative_result_ledger(
        run_id="run-hard-ban",
        generated_at="2026-08-17T12:00:00Z",
        **defaults,
        events=[
            {
                "event_id": "neg-hard-ban",
                "event_type": "branch_rejected",
                "timestamp": "2026-08-17T11:00:00Z",
                "branch_id": "branch-red",
                "payload": {
                    "disposition": "hard_ban",
                    "reason": "The active verifier rejected this branch.",
                    "evidence_refs": [{"uri": "old.json", "summary": "Violation reproduced."}],
                },
            }
        ],
    )
    matching_retest = build_negative_result_ledger(
        run_id="run-retest",
        generated_at="2026-08-18T12:00:00Z",
        **defaults,
        events=[
            {
                "event_id": "neg-retest",
                "event_type": "candidate_rejected",
                "timestamp": "2026-08-18T11:00:00Z",
                "branch_id": "branch-retest",
                "payload": {
                    "disposition": "caution",
                    "reason": "The failure did not reproduce.",
                    "retest_of_result_id": "neg-hard-ban",
                    "retest_outcome": "not_reproduced",
                    "evaluated_seeds": ["seed-1"],
                    "evidence_refs": [{"uri": "new.json", "summary": "Replay passed."}],
                },
            }
        ],
    ).entries[0]

    changed_contexts = [
        matching_retest.context.model_copy(update={"context_bundle_digest": "sha256:bundle-b"}),
        matching_retest.context.model_copy(update={"evaluator_epoch": "eval-8"}),
        matching_retest.context.model_copy(update={"trial_cohort": "cohort-b"}),
        matching_retest.context.model_copy(
            update={
                "component_dependencies": [NegativeComponentDependency(component_kind="tool", key="move", digest="sha256:move-b")]
            }
        ),
    ]
    caution = original.model_copy(update={"entries": [original.entries[0].model_copy(update={"disposition": "caution"})]})
    for recorded in [original, caution]:
        for changed_context in changed_contexts:
            retest = matching_retest.model_copy(update={"context": changed_context})
            updated = link_negative_result_retest(
                recorded,
                original_result_id="neg-hard-ban",
                retest_entry=retest,
            )
            assert updated.entries[0].superseded_by_result_id is None
            assert "neg-hard-ban" in render_negative_result_lessons(updated)


def test_retest_supersession_requires_substantive_later_non_noise_evidence_and_safety_authority() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger, link_negative_result_retest

    defaults = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
    }
    original = build_negative_result_ledger(
        run_id="run-safety-original",
        generated_at="2026-08-17T12:00:00Z",
        **defaults,
        events=[
            {
                "event_id": "neg-safety",
                "event_type": "branch_rejected",
                "timestamp": "2026-08-17T11:00:00Z",
                "branch_id": "branch-safety",
                "payload": {
                    "disposition": "hard_ban",
                    "reason": "Safety verifier rejected this branch.",
                    "safety_policy_authority": "safety:v1",
                    "evidence_refs": [{"uri": "original.json", "summary": "Violation reproduced."}],
                },
            }
        ],
    )
    retest = build_negative_result_ledger(
        run_id="run-safety-retest",
        generated_at="2026-08-18T12:00:00Z",
        **defaults,
        events=[
            {
                "event_id": "neg-safety-retest",
                "event_type": "candidate_rejected",
                "timestamp": "2026-08-18T11:00:00Z",
                "branch_id": "branch-safety-retest",
                "payload": {
                    "disposition": "caution",
                    "reason": "Controlled replay did not reproduce the failure.",
                    "safety_policy_authority": "safety:v1",
                    "retest_of_result_id": "neg-safety",
                    "retest_outcome": "not_reproduced",
                    "evaluated_seeds": ["seed-1"],
                    "evidence_refs": [{"uri": "retest.json", "summary": "Controlled replay passed."}],
                },
            }
        ],
    ).entries[0]

    accepted = link_negative_result_retest(original, original_result_id="neg-safety", retest_entry=retest)
    assert accepted.entries[0].superseded_by_result_id == "neg-safety-retest"

    invalid_retests = [
        retest.model_copy(update={"evidence_refs": []}),
        retest.model_copy(update={"evaluated_seeds": [], "evaluated_probes": []}),
        retest.model_copy(update={"occurred_at": "2026-08-16T11:00:00Z"}),
        retest.model_copy(update={"disposition": "noise"}),
        retest.model_copy(update={"safety_policy_authority": None}),
        retest.model_copy(update={"safety_policy_authority": "safety:v2"}),
    ]
    for invalid_retest in invalid_retests:
        updated = link_negative_result_retest(
            original,
            original_result_id="neg-safety",
            retest_entry=invalid_retest,
        )
        assert updated.entries[0].superseded_by_result_id is None


def test_expiry_comparison_normalizes_naive_and_aware_iso_timestamps() -> None:
    from autocontext.analytics.negative_result_ledger import (
        NegativeResultApplicabilityContext,
        build_negative_result_ledger,
        evaluate_negative_result_applicability,
    )

    ledger = build_negative_result_ledger(
        run_id="run-expiry",
        generated_at="2026-08-17T12:00:00Z",
        scenario_name="grid_ctf",
        context_bundle_digest="sha256:bundle-a",
        evaluator_epoch="eval-7",
        events=[
            {
                "event_id": "neg-expiry",
                "event_type": "branch_rejected",
                "timestamp": "2026-08-17T11:00:00Z",
                "branch_id": "branch-expiry",
                "payload": {
                    "reason": "Time-limited negative result.",
                    "evidence_expires_at": "2026-08-18T00:00:00Z",
                    "evidence_refs": [{"uri": "expiry.json", "summary": "Failure reproduced."}],
                },
            }
        ],
    )
    current = NegativeResultApplicabilityContext(
        scenario_name="grid_ctf",
        context_bundle_digest="sha256:bundle-a",
        evaluator_epoch="eval-7",
        observed_at="2026-08-18T00:00:00",
    )

    assert evaluate_negative_result_applicability(ledger.entries[0], current).state == "retest_due"
    naive_expiry = ledger.entries[0].model_copy(update={"evidence_expires_at": "2026-08-18T00:00:00"})
    aware_current = current.model_copy(update={"observed_at": "2026-08-18T00:00:00+00:00"})
    assert evaluate_negative_result_applicability(naive_expiry, aware_current).state == "retest_due"


def test_negative_result_score_delta_uses_shared_half_away_from_zero_rounding() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger

    fixture = json.loads(ROUNDING_FIXTURE_PATH.read_text(encoding="utf-8"))
    for case in fixture["cases"]:
        events = [
            {
                "event_id": f"{case['name']}-explicit",
                "event_type": "branch_rejected",
                "branch_id": "branch-explicit",
                "payload": {"reason": "Explicit delta.", "score_delta": case["value"]},
            },
            {
                "event_id": f"{case['name']}-derived",
                "event_type": "branch_rejected",
                "branch_id": "branch-derived",
                "payload": {"reason": "Derived delta.", "score": case["value"], "baseline_score": 0},
            },
        ]
        ledger = build_negative_result_ledger(
            run_id=f"rounding-{case['name']}",
            generated_at="2026-08-17T12:00:00Z",
            scenario_name="grid_ctf",
            context_bundle_digest="sha256:bundle-a",
            evaluator_epoch="eval-7",
            events=events,
        )
        assert [entry.score_delta for entry in ledger.entries] == [case["expected"], case["expected"]]


@pytest.mark.parametrize("nonfinite", [float("nan"), float("inf"), float("-inf")])
def test_negative_result_score_delta_ignores_nonfinite_inputs(nonfinite: float) -> None:
    from autocontext.analytics.negative_result_ledger import NegativeResultLedger, build_negative_result_ledger

    ledger = build_negative_result_ledger(
        run_id="nonfinite-delta",
        generated_at="2026-08-17T12:00:00Z",
        scenario_name="grid_ctf",
        context_bundle_digest="sha256:bundle-a",
        evaluator_epoch="eval-7",
        events=[
            {
                "event_id": "nonfinite-explicit",
                "event_type": "branch_rejected",
                "branch_id": "branch-explicit",
                "payload": {"reason": "Explicit non-finite delta.", "score_delta": nonfinite},
            },
            {
                "event_id": "nonfinite-derived",
                "event_type": "branch_rejected",
                "branch_id": "branch-derived",
                "payload": {"reason": "Derived non-finite delta.", "score": nonfinite, "baseline_score": 0},
            },
        ],
    )

    assert [entry.score_delta for entry in ledger.entries] == [None, None]

    persisted = json.loads(json.dumps(_cases()[0]["expected_ledger"]))
    persisted["entries"][0]["score_delta"] = nonfinite
    with pytest.raises(ValueError, match="finite"):
        NegativeResultLedger.from_dict(persisted)


def test_retest_result_id_must_be_unique_within_the_ledger() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger, link_negative_result_retest

    defaults = {
        "scenario_name": "grid_ctf",
        "context_bundle_digest": "sha256:bundle-a",
        "evaluator_epoch": "eval-7",
    }
    ledger = build_negative_result_ledger(
        run_id="run-original",
        generated_at="2026-08-17T12:00:00Z",
        **defaults,
        events=[
            {
                "event_id": "duplicate-result",
                "event_type": "branch_rejected",
                "branch_id": "branch-original",
                "payload": {"reason": "Original failure."},
            }
        ],
    )
    duplicate_retest = build_negative_result_ledger(
        run_id="run-retest",
        generated_at="2026-08-18T12:00:00Z",
        **defaults,
        events=[
            {
                "event_id": "duplicate-result",
                "event_type": "candidate_rejected",
                "branch_id": "branch-retest",
                "payload": {
                    "reason": "Failure did not reproduce.",
                    "retest_of_result_id": "duplicate-result",
                    "retest_outcome": "not_reproduced",
                },
            }
        ],
    ).entries[0]

    try:
        link_negative_result_retest(
            ledger,
            original_result_id="duplicate-result",
            retest_entry=duplicate_retest,
        )
    except ValueError as exc:
        assert str(exc) == "retest result already exists: duplicate-result"
    else:
        raise AssertionError("duplicate retest result ID was accepted")


def test_render_filters_superseded_entries_before_applying_limit() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger, render_negative_result_lessons

    ledger = build_negative_result_ledger(
        run_id="run-render-limit",
        generated_at="2026-08-17T12:00:00Z",
        scenario_name="grid_ctf",
        context_bundle_digest="sha256:bundle-a",
        evaluator_epoch="eval-7",
        events=[
            {
                "event_id": result_id,
                "event_type": "branch_rejected",
                "branch_id": f"branch-{result_id}",
                "payload": {
                    "disposition": "hard_ban",
                    "reason": f"Failure {result_id}.",
                    "evidence_refs": [{"uri": f"{result_id}.json", "summary": "Violation reproduced."}],
                },
            }
            for result_id in ["a-superseded", "b-active"]
        ],
    )
    ledger = ledger.model_copy(
        update={
            "entries": [
                ledger.entries[0].model_copy(update={"superseded_by_result_id": "a-retest"}),
                ledger.entries[1],
            ]
        }
    )

    rendered = render_negative_result_lessons(ledger, max_entries=1)

    assert "b-active" in rendered
    assert "a-superseded" not in rendered


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
    contexts = {name: NegativeResultApplicabilityContext.from_dict(value) for name, value in fixture["contexts"].items()}
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
