"""Reusable world-state abstraction for stateful scenario families (AC-265).

Provides a shared state contract for richer task families such as
orchestration, negotiation, and debugging scenarios. Supports entities,
resources, hidden variables, dependency graphs, state transitions and
diffs, with utilities for evaluation, inspection, and replay.

Compatible with the canonical event model (AC-262) via to_event_payload().
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autocontext.util.json_io import read_json, write_json


class WorldEntity(BaseModel):
    """An entity in the world state (agent, service, task, etc.)."""

    entity_id: str
    entity_type: str
    name: str
    properties: dict[str, Any]
    status: str  # active, inactive, blocked, completed, failed

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldEntity:
        return cls.model_validate(data)


class WorldResource(BaseModel):
    """A quantifiable resource in the world."""

    resource_id: str
    resource_type: str
    name: str
    quantity: float
    capacity: float | None
    owner_entity_id: str | None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldResource:
        return cls.model_validate(data)


class DependencyEdge(BaseModel):
    """A dependency between entities.

    Types: requires, blocks, produces, consumes.
    """

    source_entity_id: str
    target_entity_id: str
    dependency_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DependencyEdge:
        return cls.model_validate(data)


class HiddenVariable(BaseModel):
    """A hidden variable that may be revealed during play."""

    variable_id: str
    name: str
    value: Any
    revealed: bool
    reveal_condition: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HiddenVariable:
        return cls.model_validate(data)


class StateDelta(BaseModel):
    """A single change within a state transition.

    Delta types: entity_created, entity_updated, entity_removed,
    resource_created, resource_changed, resource_removed,
    variable_added, variable_revealed, variable_updated, variable_removed,
    dependency_added, dependency_removed.
    """

    delta_type: str
    target_id: str
    field: str | None
    old_value: Any
    new_value: Any

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateDelta:
        return cls.model_validate(data)


class StateTransition(BaseModel):
    """A transition that changes world state."""

    transition_id: str
    timestamp: str
    action: str
    actor_entity_id: str
    changes: list[StateDelta]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StateTransition:
        return cls.model_validate(data)


class WorldState(BaseModel):
    """A snapshot of the entire world at a point in time."""

    state_id: str
    scenario_name: str
    step_index: int
    entities: list[WorldEntity]
    resources: list[WorldResource]
    dependencies: list[DependencyEdge]
    hidden_variables: list[HiddenVariable]
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorldState:
        return cls.model_validate(data)


class WorldStateManager:
    """Manages world state, applies transitions, and produces diffs."""

    def __init__(self, initial_state: WorldState) -> None:
        self._state = copy.deepcopy(initial_state)
        self._entity_map: dict[str, WorldEntity] = {
            e.entity_id: e for e in self._state.entities
        }
        self._resource_map: dict[str, WorldResource] = {
            r.resource_id: r for r in self._state.resources
        }

    def snapshot(self) -> WorldState:
        """Return a deep copy of the current state with a new ID."""
        snap = copy.deepcopy(self._state)
        snap.state_id = f"ws-{uuid.uuid4().hex[:8]}"
        return snap

    def get_entity(self, entity_id: str) -> WorldEntity | None:
        return self._entity_map.get(entity_id)

    def get_resource(self, resource_id: str) -> WorldResource | None:
        return self._resource_map.get(resource_id)

    def apply_transition(self, transition: StateTransition) -> WorldState:
        """Apply a state transition, returning the new state."""
        for delta in transition.changes:
            self._apply_delta(delta)

        self._state.step_index += 1
        self._sync_collections()
        return self.snapshot()

    def diff(self, state_a: WorldState, state_b: WorldState) -> list[StateDelta]:
        """Compute deltas between two world states."""
        deltas: list[StateDelta] = []

        # Entity property diffs
        a_entities = {e.entity_id: e for e in state_a.entities}
        b_entities = {e.entity_id: e for e in state_b.entities}

        for eid, b_ent in b_entities.items():
            a_ent = a_entities.get(eid)
            if a_ent is None:
                deltas.append(StateDelta(
                    delta_type="entity_created", target_id=eid,
                    field=None, old_value=None, new_value=b_ent.to_dict(),
                ))
                continue
            # Check property changes
            for key in set(a_ent.properties) | set(b_ent.properties):
                old_val = a_ent.properties.get(key)
                new_val = b_ent.properties.get(key)
                if old_val != new_val:
                    deltas.append(StateDelta(
                        delta_type="entity_updated", target_id=eid,
                        field=key, old_value=old_val, new_value=new_val,
                    ))
            if a_ent.name != b_ent.name:
                deltas.append(StateDelta(
                    delta_type="entity_updated", target_id=eid,
                    field="name", old_value=a_ent.name, new_value=b_ent.name,
                ))
            if a_ent.entity_type != b_ent.entity_type:
                deltas.append(StateDelta(
                    delta_type="entity_updated", target_id=eid,
                    field="entity_type", old_value=a_ent.entity_type, new_value=b_ent.entity_type,
                ))
            # Check status change
            if a_ent.status != b_ent.status:
                deltas.append(StateDelta(
                    delta_type="entity_updated", target_id=eid,
                    field="status", old_value=a_ent.status, new_value=b_ent.status,
                ))

        for eid in set(a_entities) - set(b_entities):
            deltas.append(StateDelta(
                delta_type="entity_removed", target_id=eid,
                field=None, old_value=a_entities[eid].to_dict(), new_value=None,
            ))

        # Resource diffs
        a_resources = {r.resource_id: r for r in state_a.resources}
        b_resources = {r.resource_id: r for r in state_b.resources}

        for rid, b_res in b_resources.items():
            a_res = a_resources.get(rid)
            if a_res is None:
                deltas.append(StateDelta(
                    delta_type="resource_created", target_id=rid,
                    field=None, old_value=None, new_value=b_res.to_dict(),
                ))
                continue
            for field_name in ("quantity", "capacity", "owner_entity_id", "name", "resource_type"):
                old_val = getattr(a_res, field_name)
                new_val = getattr(b_res, field_name)
                if old_val != new_val:
                    deltas.append(StateDelta(
                        delta_type="resource_changed", target_id=rid,
                        field=field_name, old_value=old_val, new_value=new_val,
                    ))

        for rid in set(a_resources) - set(b_resources):
            deltas.append(StateDelta(
                delta_type="resource_removed", target_id=rid,
                field=None, old_value=a_resources[rid].to_dict(), new_value=None,
            ))

        # Dependency diffs
        a_dependencies = {self._dependency_key(dep): dep for dep in state_a.dependencies}
        b_dependencies = {self._dependency_key(dep): dep for dep in state_b.dependencies}

        for dep_key in set(b_dependencies) - set(a_dependencies):
            dep = b_dependencies[dep_key]
            deltas.append(StateDelta(
                delta_type="dependency_added", target_id=dep.target_entity_id,
                field=None, old_value=None, new_value=dep.to_dict(),
            ))

        for dep_key in set(a_dependencies) - set(b_dependencies):
            dep = a_dependencies[dep_key]
            deltas.append(StateDelta(
                delta_type="dependency_removed", target_id=dep.target_entity_id,
                field=None, old_value=dep.to_dict(), new_value=None,
            ))

        # Hidden-variable diffs
        a_variables = {var.variable_id: var for var in state_a.hidden_variables}
        b_variables = {var.variable_id: var for var in state_b.hidden_variables}

        for variable_id, b_var in b_variables.items():
            a_var = a_variables.get(variable_id)
            if a_var is None:
                deltas.append(StateDelta(
                    delta_type="variable_added", target_id=variable_id,
                    field=None, old_value=None, new_value=b_var.to_dict(),
                ))
                continue
            if not a_var.revealed and b_var.revealed:
                deltas.append(StateDelta(
                    delta_type="variable_revealed", target_id=variable_id,
                    field="revealed", old_value=False, new_value=True,
                ))
            elif a_var.revealed != b_var.revealed:
                deltas.append(StateDelta(
                    delta_type="variable_updated", target_id=variable_id,
                    field="revealed", old_value=a_var.revealed, new_value=b_var.revealed,
                ))
            for field_name in ("name", "value", "reveal_condition"):
                old_val = getattr(a_var, field_name)
                new_val = getattr(b_var, field_name)
                if old_val != new_val:
                    deltas.append(StateDelta(
                        delta_type="variable_updated", target_id=variable_id,
                        field=field_name, old_value=old_val, new_value=new_val,
                    ))

        for variable_id in set(a_variables) - set(b_variables):
            deltas.append(StateDelta(
                delta_type="variable_removed", target_id=variable_id,
                field=None, old_value=a_variables[variable_id].to_dict(), new_value=None,
            ))

        return deltas

    def to_event_payload(self) -> dict[str, Any]:
        """Convert current state to a payload compatible with the canonical event model."""
        metadata = self._state.metadata
        actor_id = str(metadata.get("actor_entity_id") or metadata.get("actor_id") or "world_state_manager")
        actor_name = str(metadata.get("actor_name") or actor_id)
        actor_type = str(metadata.get("actor_type") or "system")
        return {
            "event_id": str(metadata.get("event_id") or f"world-state-{self._state.state_id}"),
            "run_id": str(metadata.get("run_id", "")),
            "generation_index": int(metadata.get("generation_index", 0) or 0),
            "sequence_number": int(metadata.get("sequence_number", self._state.step_index)),
            "timestamp": str(metadata.get("timestamp") or datetime.now(UTC).isoformat()),
            "category": str(metadata.get("category", "checkpoint")),
            "event_type": str(metadata.get("event_type", "world_state_snapshot")),
            "actor": {
                "actor_type": actor_type,
                "actor_id": actor_id,
                "actor_name": actor_name,
            },
            "resources": self._resource_refs(),
            "summary": str(
                metadata.get("summary")
                or f"World-state snapshot for {self._state.scenario_name} at step {self._state.step_index}"
            ),
            "detail": self._state.to_dict(),
            "parent_event_id": metadata.get("parent_event_id"),
            "cause_event_ids": self._coerce_list(metadata.get("cause_event_ids")),
            "evidence_ids": self._coerce_list(metadata.get("evidence_ids")),
            "severity": str(metadata.get("severity", "info")),
            "stage": str(metadata.get("stage", "match")),
            "outcome": metadata.get("outcome"),
            "duration_ms": metadata.get("duration_ms"),
            "metadata": {
                **metadata,
                "scenario_name": self._state.scenario_name,
                "world_state_id": self._state.state_id,
            },
        }

    # --- private ---

    def _apply_delta(self, delta: StateDelta) -> None:
        dt = delta.delta_type

        if dt == "entity_updated":
            entity = self._entity_map.get(delta.target_id)
            if entity is not None and delta.field is not None:
                if delta.field == "status":
                    entity.status = delta.new_value
                elif delta.field == "name":
                    entity.name = delta.new_value
                elif delta.field == "entity_type":
                    entity.entity_type = delta.new_value
                else:
                    entity.properties[delta.field] = delta.new_value

        elif dt == "entity_created":
            if isinstance(delta.new_value, dict):
                new_entity = WorldEntity.from_dict(delta.new_value)
                self._entity_map[new_entity.entity_id] = new_entity

        elif dt == "entity_removed":
            self._entity_map.pop(delta.target_id, None)

        elif dt == "resource_created":
            if isinstance(delta.new_value, dict):
                new_resource = WorldResource.from_dict(delta.new_value)
                self._resource_map[new_resource.resource_id] = new_resource

        elif dt == "resource_changed":
            resource = self._resource_map.get(delta.target_id)
            if resource is not None and delta.field is not None:
                if delta.field == "quantity":
                    resource.quantity = delta.new_value
                elif delta.field == "capacity":
                    resource.capacity = delta.new_value
                elif delta.field == "owner_entity_id":
                    resource.owner_entity_id = delta.new_value
                elif delta.field == "name":
                    resource.name = delta.new_value
                elif delta.field == "resource_type":
                    resource.resource_type = delta.new_value

        elif dt == "resource_removed":
            self._resource_map.pop(delta.target_id, None)

        elif dt == "variable_revealed":
            for var in self._state.hidden_variables:
                if var.variable_id == delta.target_id:
                    var.revealed = True
                    break

        elif dt == "variable_added":
            if isinstance(delta.new_value, dict):
                self._state.hidden_variables.append(HiddenVariable.from_dict(delta.new_value))

        elif dt == "variable_updated":
            for var in self._state.hidden_variables:
                if var.variable_id != delta.target_id or delta.field is None:
                    continue
                if delta.field == "revealed":
                    var.revealed = delta.new_value
                elif delta.field == "name":
                    var.name = delta.new_value
                elif delta.field == "value":
                    var.value = delta.new_value
                elif delta.field == "reveal_condition":
                    var.reveal_condition = delta.new_value
                break

        elif dt == "variable_removed":
            self._state.hidden_variables = [
                var for var in self._state.hidden_variables
                if var.variable_id != delta.target_id
            ]

        elif dt == "dependency_added":
            if isinstance(delta.new_value, dict):
                self._state.dependencies.append(DependencyEdge.from_dict(delta.new_value))

        elif dt == "dependency_removed":
            if isinstance(delta.old_value, dict):
                src = delta.old_value.get("source_entity_id")
                tgt = delta.old_value.get("target_entity_id")
                self._state.dependencies = [
                    d for d in self._state.dependencies
                    if not (d.source_entity_id == src and d.target_entity_id == tgt)
                ]

    def _sync_collections(self) -> None:
        """Sync internal maps back to state lists."""
        self._state.entities = list(self._entity_map.values())
        self._state.resources = list(self._resource_map.values())

    @staticmethod
    def _coerce_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return []

    @staticmethod
    def _dependency_key(edge: DependencyEdge) -> tuple[str, str, str, str]:
        return (
            edge.source_entity_id,
            edge.target_entity_id,
            edge.dependency_type,
            json.dumps(edge.metadata, sort_keys=True, default=str),
        )

    def _resource_refs(self) -> list[dict[str, Any]]:
        entity_refs = [
            {
                "resource_type": "scenario_entity",
                "resource_id": entity.entity_id,
                "resource_name": entity.name,
                "resource_path": f"{self._state.scenario_name}/entities/{entity.entity_id}",
            }
            for entity in self._state.entities
        ]
        resource_refs = [
            {
                "resource_type": resource.resource_type,
                "resource_id": resource.resource_id,
                "resource_name": resource.name,
                "resource_path": f"{self._state.scenario_name}/resources/{resource.resource_id}",
            }
            for resource in self._state.resources
        ]
        return [*entity_refs, *resource_refs]


class WorldStateStore:
    """Persists world state snapshots as JSON files."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "world_states"
        self._dir.mkdir(parents=True, exist_ok=True)

    def persist(self, state: WorldState) -> Path:
        path = self._dir / f"{state.state_id}.json"
        write_json(path, state.to_dict())
        return path

    def load(self, state_id: str) -> WorldState | None:
        path = self._dir / f"{state_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return WorldState.from_dict(data)

    def list_states(self) -> list[WorldState]:
        results: list[WorldState] = []
        for path in sorted(self._dir.glob("*.json")):
            data = read_json(path)
            results.append(WorldState.from_dict(data))
        return results
