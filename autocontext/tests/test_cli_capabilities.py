"""AC-697 slice 5: `autoctx capabilities` (Python) loads the contract.

Both runtimes now serve the canonical command surface by loading
``docs/cli-contract.json``. This test pins the Python side's JSON
shape against the contract source so future contract edits propagate
to the CLI surface without code changes.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.cli_capabilities import build_capabilities_payload


def _contract_path() -> Path:
    return Path(__file__).resolve().parents[2] / "docs" / "cli-contract.json"


def test_build_capabilities_payload_shape() -> None:
    """The Python payload has `schema_version` and a `commands` list
    of dicts that mirror the slice-1 contract entries."""
    payload = build_capabilities_payload(_contract_path())
    assert payload["schema_version"] == 1
    assert isinstance(payload["commands"], list)
    assert len(payload["commands"]) > 0
    sample = payload["commands"][0]
    for key in ("id", "path", "summary", "audience", "maturity", "aliases", "runtime_support"):
        assert key in sample, f"missing key {key!r} in capabilities command payload"
    assert "python" in sample["runtime_support"]
    assert "typescript" in sample["runtime_support"]


def test_capabilities_payload_includes_paved_road_commands() -> None:
    """The paved-road surface from slice 1 must appear in the
    Python capabilities payload so operators can enumerate the
    canonical set from a single CLI invocation."""
    payload = build_capabilities_payload(_contract_path())
    ids = {cmd["id"] for cmd in payload["commands"]}
    expected_paved_road = {"solve", "run", "status", "watch", "show", "export"}
    missing = expected_paved_road - ids
    assert not missing, f"capabilities missing paved-road command ids: {missing}"


def test_capabilities_payload_records_intentional_gap_reasons() -> None:
    """Per slice-1 contract: every `intentional_gap` entry must carry
    a non-empty reason. Verifies the Python payload propagates the
    reason field so operators see why a runtime entry is gapped."""
    payload = build_capabilities_payload(_contract_path())
    for cmd in payload["commands"]:
        for runtime in ("python", "typescript"):
            entry = cmd["runtime_support"][runtime]
            if entry["status"] == "intentional_gap":
                assert entry.get("reason"), f"capabilities payload missing reason for {cmd['id']}.{runtime}"


def test_cli_autoctx_capabilities_json_emits_contract_payload(tmp_path, monkeypatch) -> None:
    """End-to-end: `autoctx capabilities --json` emits the payload."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["capabilities", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.strip())
    assert payload["schema_version"] == 1
    assert any(cmd["id"] == "capabilities" for cmd in payload["commands"])


def test_cli_autoctx_capabilities_no_json_emits_human_summary(tmp_path, monkeypatch) -> None:
    """Without --json, the CLI emits a human-readable per-command
    summary table."""
    monkeypatch.setenv("AUTOCONTEXT_DB_PATH", str(tmp_path / "autocontext.db"))
    monkeypatch.setenv("AUTOCONTEXT_CONFIG_DIR", str(tmp_path / "config"))
    result = CliRunner().invoke(app, ["capabilities"])
    assert result.exit_code == 0, result.output
    assert "autoctx CLI contract" in result.output
    assert "capabilities" in result.output


def test_default_contract_path_resolves_to_an_existing_file() -> None:
    """PR #1000 review (P2): the wheel ships docs/cli-contract.json
    as autocontext/cli_contract.json (via hatch force-include), and
    the loader resolves it via importlib.resources at runtime. The
    dev tree falls back to the repo-relative walk. Either branch
    must yield an existing file."""
    from autocontext.cli_capabilities import _default_contract_path

    path = _default_contract_path()
    assert path.exists(), f"resolved contract path does not exist: {path}"
    # Either dev-tree path (`cli-contract.json`) or packaged path
    # (`cli_contract.json`). Both spellings are valid file names.
    assert path.name in {"cli-contract.json", "cli_contract.json"}
