from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_summary_module():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "escalation-sweep" / "summarize.py"
    spec = importlib.util.spec_from_file_location("escalation_sweep_summarize", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summarize_tallies_llm_classifier_fallback_from_structured_solve_output(tmp_path: Path, capsys) -> None:
    summary_mod = _load_summary_module()
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    (results_dir / "index.json").write_text(json.dumps(["ac580"]), encoding="utf-8")
    (results_dir / "ac580.meta.json").write_text(
        json.dumps(
            {
                "identifier": "ac580",
                "exit_code": 0,
                "elapsed_seconds": 12,
                "workspace_root": str(tmp_path / "workspaces" / "ac580"),
            }
        ),
        encoding="utf-8",
    )
    (results_dir / "ac580.out.json").write_text(
        json.dumps(
            {
                "job_id": "solve_ac580",
                "status": "completed",
                "description": "Fallback solve",
                "scenario_name": "fallback_case",
                "generations": 1,
                "progress": 1,
                "output_path": None,
                "llm_classifier_fallback_used": True,
                "result": {"scenario_name": "fallback_case"},
            }
        ),
        encoding="utf-8",
    )

    exit_code = summary_mod.main(["summarize.py", str(results_dir)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "llm_fallback_fired" in captured.out

    payload = json.loads((results_dir / "summary.json").read_text(encoding="utf-8"))
    assert payload["rows"][0]["bucket"] == "llm_fallback_fired"
    assert payload["buckets"]["llm_fallback_fired"] == 1
