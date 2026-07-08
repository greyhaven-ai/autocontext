"""the serving resolver reads the ambient manifest to route (scenario, role) -> ambient target (AC-893).

``scenario_bound_mlx_client`` resolves a local model by the REAL scenario, but the ambient trainer
slots a promoted per-role model under ``scenario = target.name`` (AC-884). With the opt-in serving
manifest configured, the resolver first consults the manifest and resolves the record slotted under
the target name; with no manifest configured it must behave exactly as before (scenario-keyed). These
tests exercise both paths with fakes only, so no mlx / torch is ever imported.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import autocontext.agents.scenario_bound_clients as sbc
from autocontext.ambient.serving_manifest import write_serving_entry
from autocontext.config.settings import AppSettings
from autocontext.training.model_registry import DistilledModelRecord


class _FakeClient:
    def __init__(self, tag: str) -> None:
        self.tag = tag


class _FakeOrch:
    """Minimal stand-in exposing the three attributes ``scenario_bound_mlx_client`` touches."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        self._routed_clients: dict[Any, Any] = {}

    def _wrap_client(self, client: Any, *, provider_name: str) -> Any:
        client.provider_name = provider_name
        return client


def _record(scenario: str, artifact_id: str) -> DistilledModelRecord:
    return DistilledModelRecord(
        artifact_id=artifact_id,
        scenario=scenario,
        scenario_family="",
        backend="mlx",
        checkpoint_path=f"/ckpt/{artifact_id}",
        runtime_types=["provider"],
        activation_state="active",
        training_metrics={},
        provenance={},
    )


def test_manifest_hit_resolves_ambient_record_by_target_name(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autocontext.training.model_registry as mr

    manifest_path = tmp_path / "serving.json"
    write_serving_entry(
        manifest_path,
        scenario="grid_ctf",
        role="competitor",
        target_name="competitor-local",
        artifact_id="amb-1",
        backend="mlx",
    )

    queried: list[str] = []

    def fake_resolve(registry: Any, *, scenario: str, backend: str, runtime_type: str) -> Any:
        queried.append(scenario)
        # only the ambient target-name slot resolves; the real scenario would miss.
        return _record(scenario, "amb-1") if scenario == "competitor-local" else None

    monkeypatch.setattr(mr, "resolve_model", fake_resolve)
    monkeypatch.setattr(mr, "ModelRegistry", lambda root: object())
    monkeypatch.setattr(sbc, "build_planned_client", lambda plan, settings: _FakeClient("ambient"))

    settings = AppSettings(
        agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path, ambient_serving_manifest_path=manifest_path
    )
    orch = _FakeOrch(settings)

    client = sbc.scenario_bound_mlx_client(orch, "competitor", scenario_name="grid_ctf")

    assert isinstance(client, _FakeClient) and client.tag == "ambient"
    assert client.provider_name == "mlx:competitor"
    # the ambient bridge resolved by the target name, not the real scenario.
    assert queried == ["competitor-local"]
    # and the built client is cached.
    assert orch._routed_clients[("mlx", None, "grid_ctf", "competitor")] is client


def test_no_manifest_path_falls_back_to_scenario_keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autocontext.training.model_registry as mr

    queried: list[str] = []

    def fake_resolve(registry: Any, *, scenario: str, backend: str, runtime_type: str) -> Any:
        queried.append(scenario)
        # the existing resolver queries by the real scenario across backends; answer on the first.
        return _record(scenario, "local-1") if backend == "opd" else None

    monkeypatch.setattr(mr, "resolve_model", fake_resolve)
    monkeypatch.setattr(mr, "ModelRegistry", lambda root: object())
    monkeypatch.setattr(sbc, "build_planned_client", lambda plan, settings: _FakeClient("local"))

    # ambient_serving_manifest_path defaults to None: byte-unchanged behavior.
    settings = AppSettings(agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path)
    orch = _FakeOrch(settings)

    client = sbc.scenario_bound_mlx_client(orch, "competitor", scenario_name="grid_ctf")

    assert isinstance(client, _FakeClient) and client.tag == "local"
    # resolution used the real scenario only (no ambient target-name lookup happened).
    assert queried == ["grid_ctf"]


def test_manifest_miss_for_role_falls_back_to_scenario_keyed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import autocontext.training.model_registry as mr

    manifest_path = tmp_path / "serving.json"
    # an entry exists for a different role, so the requested role misses and we fall through.
    write_serving_entry(
        manifest_path,
        scenario="grid_ctf",
        role="analyst",
        target_name="analyst-local",
        artifact_id="amb-2",
        backend="mlx",
    )

    queried: list[str] = []

    def fake_resolve(registry: Any, *, scenario: str, backend: str, runtime_type: str) -> Any:
        queried.append(scenario)
        return _record(scenario, "local-1") if backend == "opd" else None

    monkeypatch.setattr(mr, "resolve_model", fake_resolve)
    monkeypatch.setattr(mr, "ModelRegistry", lambda root: object())
    monkeypatch.setattr(sbc, "build_planned_client", lambda plan, settings: _FakeClient("local"))

    settings = AppSettings(
        agent_provider="mlx", mlx_model_path="", knowledge_root=tmp_path, ambient_serving_manifest_path=manifest_path
    )
    orch = _FakeOrch(settings)

    client = sbc.scenario_bound_mlx_client(orch, "competitor", scenario_name="grid_ctf")

    assert isinstance(client, _FakeClient) and client.tag == "local"
    # no ambient target-name lookup (role missed); only the real scenario was queried.
    assert queried == ["grid_ctf"]
