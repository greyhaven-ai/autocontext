"""Tests for AC-265: scenario world-state abstraction for stateful task families.

Covers: WorldEntity, WorldResource, DependencyEdge, HiddenVariable,
StateDelta, StateTransition, WorldState, WorldStateManager, WorldStateStore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _entity(entity_id: str = "agent-1", **overrides: Any) -> Any:
    from autocontext.scenarios.world_state import WorldEntity

    defaults: dict[str, Any] = {
        "entity_id": entity_id,
        "entity_type": "agent",
        "name": "Agent Alpha",
        "properties": {"skill": "high", "health": 100},
        "status": "active",
    }
    defaults.update(overrides)
    return WorldEntity(**defaults)


def _resource(resource_id: str = "gold-1", **overrides: Any) -> Any:
    from autocontext.scenarios.world_state import WorldResource

    defaults: dict[str, Any] = {
        "resource_id": resource_id,
        "resource_type": "currency",
        "name": "Gold",
        "quantity": 100.0,
        "capacity": 500.0,
        "owner_entity_id": "agent-1",
    }
    defaults.update(overrides)
    return WorldResource(**defaults)


def _dependency(src: str = "task-1", tgt: str = "task-2") -> Any:
    from autocontext.scenarios.world_state import DependencyEdge

    return DependencyEdge(
        source_entity_id=src,
        target_entity_id=tgt,
        dependency_type="requires",
    )


def _hidden_var(variable_id: str = "trap-1") -> Any:
    from autocontext.scenarios.world_state import HiddenVariable

    return HiddenVariable(
        variable_id=variable_id,
        name="Hidden trap",
        value={"location": [3, 4], "damage": 50},
        revealed=False,
        reveal_condition="Agent enters cell [3,4]",
    )


def _make_world_state() -> Any:
    from autocontext.scenarios.world_state import WorldState

    return WorldState(
        state_id="ws-1",
        scenario_name="orchestration",
        step_index=0,
        entities=[_entity("agent-1"), _entity("agent-2", name="Agent Beta")],
        resources=[_resource("gold-1"), _resource("wood-1", resource_type="material", name="Wood", quantity=50.0)],
        dependencies=[_dependency("agent-1", "agent-2")],
        hidden_variables=[_hidden_var("trap-1")],
    )


# ===========================================================================
# WorldEntity
# ===========================================================================


class TestWorldEntity:
    def test_construction(self) -> None:
        e = _entity()
        assert e.entity_id == "agent-1"
        assert e.status == "active"
        assert e.properties["health"] == 100

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import WorldEntity

        e = _entity("svc-1", entity_type="service", name="API Gateway")
        d = e.to_dict()
        restored = WorldEntity.from_dict(d)
        assert restored.entity_id == "svc-1"
        assert restored.entity_type == "service"


# ===========================================================================
# WorldResource
# ===========================================================================


class TestWorldResource:
    def test_construction(self) -> None:
        r = _resource()
        assert r.resource_id == "gold-1"
        assert r.quantity == 100.0
        assert r.capacity == 500.0

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import WorldResource

        r = _resource("energy-1", resource_type="energy", name="Power", quantity=75.0, capacity=None)
        d = r.to_dict()
        restored = WorldResource.from_dict(d)
        assert restored.resource_id == "energy-1"
        assert restored.capacity is None


# ===========================================================================
# DependencyEdge
# ===========================================================================


class TestDependencyEdge:
    def test_construction(self) -> None:
        d = _dependency()
        assert d.source_entity_id == "task-1"
        assert d.dependency_type == "requires"

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import DependencyEdge

        d = DependencyEdge(
            source_entity_id="a", target_entity_id="b",
            dependency_type="blocks",
        )
        data = d.to_dict()
        restored = DependencyEdge.from_dict(data)
        assert restored.dependency_type == "blocks"


# ===========================================================================
# HiddenVariable
# ===========================================================================


class TestHiddenVariable:
    def test_construction(self) -> None:
        h = _hidden_var()
        assert h.variable_id == "trap-1"
        assert not h.revealed
        assert h.value["damage"] == 50

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import HiddenVariable

        h = _hidden_var("secret-1")
        d = h.to_dict()
        restored = HiddenVariable.from_dict(d)
        assert restored.variable_id == "secret-1"
        assert not restored.revealed


# ===========================================================================
# StateDelta
# ===========================================================================


class TestStateDelta:
    def test_construction(self) -> None:
        from autocontext.scenarios.world_state import StateDelta

        d = StateDelta(
            delta_type="entity_updated",
            target_id="agent-1",
            field="health",
            old_value=100,
            new_value=75,
        )
        assert d.delta_type == "entity_updated"
        assert d.old_value == 100

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import StateDelta

        d = StateDelta(
            delta_type="resource_changed",
            target_id="gold-1",
            field="quantity",
            old_value=100.0,
            new_value=80.0,
        )
        data = d.to_dict()
        restored = StateDelta.from_dict(data)
        assert restored.delta_type == "resource_changed"
        assert restored.new_value == 80.0


# ===========================================================================
# StateTransition
# ===========================================================================


class TestStateTransition:
    def test_construction(self) -> None:
        from autocontext.scenarios.world_state import StateDelta, StateTransition

        t = StateTransition(
            transition_id="tx-1",
            timestamp="2026-03-14T12:00:00Z",
            action="attack",
            actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="entity_updated", target_id="agent-2",
                    field="health", old_value=100, new_value=70,
                ),
            ],
        )
        assert t.transition_id == "tx-1"
        assert len(t.changes) == 1

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import StateDelta, StateTransition

        t = StateTransition(
            transition_id="tx-2",
            timestamp="2026-03-14T12:01:00Z",
            action="gather",
            actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="resource_changed", target_id="gold-1",
                    field="quantity", old_value=100.0, new_value=120.0,
                ),
            ],
        )
        data = t.to_dict()
        restored = StateTransition.from_dict(data)
        assert restored.action == "gather"
        assert len(restored.changes) == 1


# ===========================================================================
# WorldState
# ===========================================================================


class TestWorldState:
    def test_construction(self) -> None:
        ws = _make_world_state()
        assert ws.state_id == "ws-1"
        assert len(ws.entities) == 2
        assert len(ws.resources) == 2
        assert len(ws.dependencies) == 1
        assert len(ws.hidden_variables) == 1

    def test_roundtrip(self) -> None:
        from autocontext.scenarios.world_state import WorldState

        ws = _make_world_state()
        d = ws.to_dict()
        restored = WorldState.from_dict(d)
        assert restored.state_id == "ws-1"
        assert len(restored.entities) == 2
        assert len(restored.resources) == 2
        assert restored.hidden_variables[0].variable_id == "trap-1"

    def test_empty_state(self) -> None:
        from autocontext.scenarios.world_state import WorldState

        ws = WorldState(
            state_id="empty", scenario_name="test",
            step_index=0, entities=[], resources=[],
            dependencies=[], hidden_variables=[],
        )
        assert ws.step_index == 0
        assert len(ws.entities) == 0


# ===========================================================================
# WorldStateManager
# ===========================================================================


class TestWorldStateManager:
    def test_init_and_snapshot(self) -> None:
        from autocontext.scenarios.world_state import WorldStateManager

        ws = _make_world_state()
        mgr = WorldStateManager(ws)
        snap = mgr.snapshot()
        assert snap.state_id != ws.state_id  # new snapshot gets new ID
        assert len(snap.entities) == 2

    def test_get_entity(self) -> None:
        from autocontext.scenarios.world_state import WorldStateManager

        mgr = WorldStateManager(_make_world_state())
        e = mgr.get_entity("agent-1")
        assert e is not None
        assert e.name == "Agent Alpha"
        assert mgr.get_entity("nonexistent") is None

    def test_get_resource(self) -> None:
        from autocontext.scenarios.world_state import WorldStateManager

        mgr = WorldStateManager(_make_world_state())
        r = mgr.get_resource("gold-1")
        assert r is not None
        assert r.quantity == 100.0
        assert mgr.get_resource("nonexistent") is None

    def test_apply_entity_update(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        tx = StateTransition(
            transition_id="tx-1", timestamp="2026-03-14T12:01:00Z",
            action="damage", actor_entity_id="agent-2",
            changes=[
                StateDelta(
                    delta_type="entity_updated", target_id="agent-1",
                    field="health", old_value=100, new_value=70,
                ),
            ],
        )
        new_state = mgr.apply_transition(tx)
        assert new_state.step_index == 1
        e = mgr.get_entity("agent-1")
        assert e is not None
        assert e.properties["health"] == 70

    def test_apply_resource_change(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        tx = StateTransition(
            transition_id="tx-2", timestamp="2026-03-14T12:02:00Z",
            action="spend", actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="resource_changed", target_id="gold-1",
                    field="quantity", old_value=100.0, new_value=60.0,
                ),
            ],
        )
        mgr.apply_transition(tx)
        r = mgr.get_resource("gold-1")
        assert r is not None
        assert r.quantity == 60.0

    def test_apply_entity_create(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        tx = StateTransition(
            transition_id="tx-3", timestamp="2026-03-14T12:03:00Z",
            action="spawn", actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="entity_created", target_id="agent-3",
                    field=None, old_value=None,
                    new_value={
                        "entity_id": "agent-3", "entity_type": "agent",
                        "name": "Agent Gamma", "properties": {"health": 100},
                        "status": "active",
                    },
                ),
            ],
        )
        mgr.apply_transition(tx)
        e = mgr.get_entity("agent-3")
        assert e is not None
        assert e.name == "Agent Gamma"

    def test_apply_entity_remove(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        assert mgr.get_entity("agent-2") is not None

        tx = StateTransition(
            transition_id="tx-4", timestamp="2026-03-14T12:04:00Z",
            action="eliminate", actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="entity_removed", target_id="agent-2",
                    field=None, old_value=None, new_value=None,
                ),
            ],
        )
        mgr.apply_transition(tx)
        assert mgr.get_entity("agent-2") is None

    def test_apply_variable_reveal(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        tx = StateTransition(
            transition_id="tx-5", timestamp="2026-03-14T12:05:00Z",
            action="explore", actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="variable_revealed", target_id="trap-1",
                    field="revealed", old_value=False, new_value=True,
                ),
            ],
        )
        mgr.apply_transition(tx)

        snap = mgr.snapshot()
        trap = next(v for v in snap.hidden_variables if v.variable_id == "trap-1")
        assert trap.revealed is True

    def test_apply_dependency_add(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        initial_deps = len(mgr.snapshot().dependencies)

        tx = StateTransition(
            transition_id="tx-6", timestamp="2026-03-14T12:06:00Z",
            action="link", actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="dependency_added", target_id="agent-2",
                    field=None, old_value=None,
                    new_value={
                        "source_entity_id": "agent-2", "target_entity_id": "agent-1",
                        "dependency_type": "blocks",
                    },
                ),
            ],
        )
        mgr.apply_transition(tx)
        assert len(mgr.snapshot().dependencies) == initial_deps + 1

    def test_apply_dependency_remove(self) -> None:
        from autocontext.scenarios.world_state import (
            StateDelta,
            StateTransition,
            WorldStateManager,
        )

        mgr = WorldStateManager(_make_world_state())
        initial_deps = len(mgr.snapshot().dependencies)

        tx = StateTransition(
            transition_id="tx-7", timestamp="2026-03-14T12:07:00Z",
            action="unlink", actor_entity_id="agent-1",
            changes=[
                StateDelta(
                    delta_type="dependency_removed", target_id="agent-2",
                    field=None,
                    old_value={"source_entity_id": "agent-1", "target_entity_id": "agent-2"},
                    new_value=None,
                ),
            ],
        )
        mgr.apply_transition(tx)
        assert len(mgr.snapshot().dependencies) == initial_deps - 1

    def test_diff_detects_entity_property_change(self) -> None:
        import copy

        from autocontext.scenarios.world_state import WorldState, WorldStateManager

        state_a = _make_world_state()
        mgr = WorldStateManager(state_a)

        # Deep copy to avoid mutating state_a through shared references
        state_b_dict = copy.deepcopy(state_a.to_dict())
        state_b_dict["state_id"] = "ws-2"
        state_b_dict["step_index"] = 1
        state_b_dict["entities"][0]["properties"]["health"] = 70
        state_b = WorldState.from_dict(state_b_dict)

        deltas = mgr.diff(state_a, state_b)
        assert len(deltas) > 0
        health_delta = next((d for d in deltas if d.field == "health"), None)
        assert health_delta is not None
        assert health_delta.old_value == 100
        assert health_delta.new_value == 70

    def test_diff_detects_resource_change(self) -> None:
        import copy

        from autocontext.scenarios.world_state import WorldState, WorldStateManager

        state_a = _make_world_state()
        mgr = WorldStateManager(state_a)

        state_b_dict = copy.deepcopy(state_a.to_dict())
        state_b_dict["state_id"] = "ws-3"
        state_b_dict["resources"][0]["quantity"] = 50.0
        state_b = WorldState.from_dict(state_b_dict)

        deltas = mgr.diff(state_a, state_b)
        qty_delta = next((d for d in deltas if d.field == "quantity"), None)
        assert qty_delta is not None
        assert qty_delta.old_value == 100.0
        assert qty_delta.new_value == 50.0

    def test_diff_detects_dependency_and_hidden_variable_changes(self) -> None:
        import copy

        from autocontext.scenarios.world_state import WorldState, WorldStateManager

        state_a = _make_world_state()
        mgr = WorldStateManager(state_a)

        state_b_dict = copy.deepcopy(state_a.to_dict())
        state_b_dict["state_id"] = "ws-4"
        state_b_dict["dependencies"].append(
            {
                "source_entity_id": "agent-2",
                "target_entity_id": "agent-1",
                "dependency_type": "blocks",
                "metadata": {},
            }
        )
        state_b_dict["hidden_variables"][0]["revealed"] = True
        state_b_dict["hidden_variables"][0]["value"] = {"location": [4, 4], "damage": 75}
        state_b = WorldState.from_dict(state_b_dict)

        deltas = mgr.diff(state_a, state_b)
        assert any(delta.delta_type == "dependency_added" for delta in deltas)
        assert any(
            delta.delta_type == "variable_revealed" and delta.target_id == "trap-1"
            for delta in deltas
        )
        assert any(
            delta.delta_type == "variable_updated" and delta.field == "value"
            for delta in deltas
        )

    def test_diff_detects_resource_lifecycle_and_metadata_changes(self) -> None:
        import copy

        from autocontext.scenarios.world_state import WorldState, WorldStateManager

        state_a = _make_world_state()
        mgr = WorldStateManager(state_a)

        state_b_dict = copy.deepcopy(state_a.to_dict())
        state_b_dict["state_id"] = "ws-5"
        state_b_dict["resources"][0]["capacity"] = 750.0
        state_b_dict["resources"][0]["owner_entity_id"] = "agent-2"
        state_b_dict["resources"] = [
            resource for resource in state_b_dict["resources"]
            if resource["resource_id"] != "wood-1"
        ]
        state_b_dict["resources"].append(
            {
                "resource_id": "energy-1",
                "resource_type": "energy",
                "name": "Power",
                "quantity": 20.0,
                "capacity": 100.0,
                "owner_entity_id": "agent-1",
            }
        )
        state_b = WorldState.from_dict(state_b_dict)

        deltas = mgr.diff(state_a, state_b)
        assert any(
            delta.delta_type == "resource_changed"
            and delta.target_id == "gold-1"
            and delta.field == "capacity"
            for delta in deltas
        )
        assert any(
            delta.delta_type == "resource_changed"
            and delta.target_id == "gold-1"
            and delta.field == "owner_entity_id"
            for delta in deltas
        )
        assert any(delta.delta_type == "resource_removed" and delta.target_id == "wood-1" for delta in deltas)
        assert any(delta.delta_type == "resource_created" and delta.target_id == "energy-1" for delta in deltas)

    def test_to_event_payload(self) -> None:
        from autocontext.scenarios.world_state import WorldStateManager

        world_state = _make_world_state()
        world_state.metadata = {
            "run_id": "run-123",
            "generation_index": 2,
            "sequence_number": 7,
            "actor_entity_id": "agent-1",
            "actor_name": "Agent Alpha",
            "stage": "match",
        }
        mgr = WorldStateManager(world_state)
        payload = mgr.to_event_payload()

        assert payload["event_id"] == "world-state-ws-1"
        assert payload["run_id"] == "run-123"
        assert payload["generation_index"] == 2
        assert payload["category"] == "checkpoint"
        assert payload["event_type"] == "world_state_snapshot"
        assert payload["actor"]["actor_id"] == "agent-1"
        assert payload["stage"] == "match"
        assert payload["detail"]["state_id"] == "ws-1"
        assert isinstance(payload["resources"], list)


# ===========================================================================
# WorldStateStore
# ===========================================================================


class TestWorldStateStore:
    def test_persist_and_load(self, tmp_path: Path) -> None:
        from autocontext.scenarios.world_state import WorldStateStore

        store = WorldStateStore(tmp_path)
        ws = _make_world_state()
        path = store.persist(ws)
        assert path.exists()

        loaded = store.load("ws-1")
        assert loaded is not None
        assert loaded.state_id == "ws-1"
        assert len(loaded.entities) == 2

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.scenarios.world_state import WorldStateStore

        store = WorldStateStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_list_states(self, tmp_path: Path) -> None:
        from autocontext.scenarios.world_state import WorldState, WorldStateStore

        store = WorldStateStore(tmp_path)
        for i in range(3):
            store.persist(WorldState(
                state_id=f"ws-{i}", scenario_name="test",
                step_index=i, entities=[], resources=[],
                dependencies=[], hidden_variables=[],
            ))
        assert len(store.list_states()) == 3
