from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = ROOT / "docs" / "negative-result-ledger-parity-fixture.json"


def _cases() -> list[dict[str, Any]]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]


def test_build_negative_result_ledger_matches_shared_fixture() -> None:
    from autocontext.analytics.negative_result_ledger import build_negative_result_ledger

    for case in _cases():
        ledger = build_negative_result_ledger(
            run_id=case["run_id"],
            events=case["events"],
            generated_at=case["generated_at"],
        )

        assert ledger.to_dict() == case["expected_ledger"]


def test_negative_result_ledger_round_trips_shared_json() -> None:
    from autocontext.analytics.negative_result_ledger import NegativeResultLedger

    for case in _cases():
        expected = case["expected_ledger"]
        assert NegativeResultLedger.from_dict(expected).to_dict() == expected


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
    assert "Hard ban:" in hard_ban_text
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
    assert restored.to_dict() == expected
    assert "Hard ban:" in read_latest_negative_result_ledgers_markdown(tmp_path / "knowledge", "grid_ctf")


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
