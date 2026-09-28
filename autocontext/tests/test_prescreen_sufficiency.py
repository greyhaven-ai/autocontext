# autocontext/tests/test_prescreen_sufficiency.py
from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.prescreen.rounds import LedgerRound, eligible_rounds
from autocontext.prescreen.stats import cluster_mean_difference_interval, cluster_ratio_interval
from autocontext.prescreen.sufficiency import sufficiency_report
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"
_ids = iter(range(1, 100_000))


def rnd(
    loop: str,
    n: int,
    *,
    passed: bool = False,
    failed: bool = False,
    epoch: str | None = "e1",
    family: str = "fam",
    max_rounds: int = 5,
    name: str = "task",
) -> LedgerRound:
    return LedgerRound(
        row_id=next(_ids),
        loop_id=loop,
        run_id="run",
        scenario_name=name,
        scenario_family=family,
        round_number=n,
        max_rounds=max_rounds,
        quality_threshold=0.9,
        evaluator_epoch=epoch,
        task_prompt="p",
        output=f"out {loop} {n}",
        required_concepts=(),
        score=0.95 if passed else 0.4,
        passed=passed,
        judge_failed=failed,
        dimension_scores={},
        previous_round_number=None,
        previous_score=None,
        previous_dimension_scores={},
        created_at=f"2026-09-27T00:00:{n:02d}Z",
    )


def numbers(rounds: list[LedgerRound]) -> list[tuple[str, int]]:
    return [(e.round.loop_id, e.round.round_number) for e in eligible_rounds(rounds)]


def test_eligibility_follows_decision_3() -> None:
    rounds = [
        rnd("a", 1),
        rnd("a", 2),
        rnd("a", 3, passed=True),  # a2 eligible, a3 eligible (a2 failed)
        rnd("b", 1, passed=True),
        rnd("b", 2),  # b2 follows a pass: confirmation, not eligible
        rnd("c", 1, failed=True),
        rnd("c", 2),  # previous verdict did not parse
        rnd("d", 1),
        rnd("d", 2, failed=True),  # own verdict did not parse
        rnd("e", 1),
        rnd("e", 2, max_rounds=2),  # last allowed round
        rnd("f", 1),
        rnd("f", 3),  # gap: round 2 was never judged
        rnd("g", 1),
        rnd("g", 2, epoch=None),  # epoch unknown
    ]
    assert numbers(rounds) == [("a", 2), ("a", 3)]


def test_eligibility_spans_epoch_changes_within_a_loop() -> None:
    rounds = [rnd("a", 1, epoch="e0"), rnd("a", 2, epoch="e1")]
    assert numbers(rounds) == [("a", 2)]
    report = {(r.evaluator_epoch, r.scenario_family): r for r in sufficiency_report(rounds)}
    assert report[("e1", "fam")].eligible_rounds == 1 and report[("e0", "fam")].eligible_rounds == 0


def test_sufficiency_counts_and_phase1_bar() -> None:
    rounds = []
    for i in range(300):
        rounds += [rnd(f"L{i}", 1), rnd(f"L{i}", 2)]
    rows = sufficiency_report(rounds)
    assert len(rows) == 1
    row = rows[0]
    assert (row.loops, row.judged_rounds, row.eligible_rounds, row.eligible_failing_rounds) == (300, 600, 300, 300)
    assert row.value_ceiling == 0.5 and row.pass_rate == 0.0 and row.meets_phase1
    assert not sufficiency_report(rounds[:-2])[0].meets_phase1


def test_cluster_intervals() -> None:
    assert cluster_ratio_interval([1, 1, 0, 0], [1, 1, 1, 1], ["a", "a", "b", "b"], resamples=200) is not None
    assert cluster_ratio_interval([1.0], [0.0], ["a"]) is None
    assert cluster_ratio_interval([], [], []) is None
    same = cluster_ratio_interval([1, 0, 1, 0], [1, 1, 1, 1], ["a", "a", "b", "b"], resamples=200)
    assert same == (0.5, 0.5)
    assert cluster_mean_difference_interval([1.0, 1.0], [0.0, 0.0], ["a", "b"], resamples=50) == (1.0, 1.0)


def test_cli_sufficiency_writes_an_aggregate_report(tmp_path: Path) -> None:
    db = tmp_path / "ledger.db"
    store = SQLiteStore(db)
    store.migrate(MIGRATIONS)
    for n, score in ((1, 0.3), (2, 0.5)):
        store.insert_judge_ledger_row(
            {
                "loop_id": "L",
                "run_id": "r",
                "scenario_name": "s",
                "scenario_family": "fam",
                "round_number": n,
                "max_rounds": 5,
                "quality_threshold": 0.9,
                "evaluator_epoch": "e1",
                "rubric_hash": "h",
                "task_prompt_hash": "h",
                "task_prompt": "SECRET PROMPT",
                "output_hash": "h",
                "output": "SECRET OUTPUT",
                "score": score,
                "passed": 0,
            }
        )
    out = tmp_path / "reports"
    result = CliRunner().invoke(app, ["prescreen", "sufficiency", "--db-path", str(db), "--out", str(out)])
    assert result.exit_code == 0, result.output
    [path] = list(out.glob("sufficiency-*.json"))
    text = path.read_text()
    assert "SECRET" not in text
    rows = json.loads(text)["rows"]
    assert rows[0]["eligible_rounds"] == 1 and rows[0]["scenario_family"] == "fam"
