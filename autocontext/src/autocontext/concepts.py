"""Canonical concept model metadata for capability discovery surfaces."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

_CONCEPT_MODEL: dict[str, Any] = {
    "version": 1,
    "source_doc": "docs/concept-model.md",
    "user_facing": [
        {
            "name": "Scenario",
            "description": "A reusable environment, simulation, or evaluation context with stable rules and scoring.",
            "status": "implemented",
        },
        {
            "name": "Task",
            "description": (
                "A user-authored unit of work or prompt-centric objective that "
                "can be evaluated directly or embedded inside another surface."
            ),
            "status": "partial",
        },
        {
            "name": "Mission",
            "description": "A long-running goal advanced step by step until a verifier says it is complete.",
            "status": "partial",
        },
        {
            "name": "Campaign",
            "description": (
                "A planned grouping of missions, runs, and scenarios used to "
                "coordinate broader work over time. Partial support exists "
                "today through TypeScript API/MCP surfaces; the Python "
                "package does not expose campaign workflows yet."
            ),
            "status": "partial",
        },
    ],
    "runtime": [
        {
            "name": "Run",
            "description": "A concrete execution instance of a Scenario or Task.",
            "status": "implemented",
        },
        {
            "name": "Step",
            "description": "A bounded action taken while advancing a Mission or another long-running workflow.",
            "status": "partial",
        },
        {
            "name": "Verifier",
            "description": "The runtime check that decides whether a mission, step, or output is acceptable.",
            "status": "partial",
        },
        {
            "name": "Artifact",
            "description": "A persisted runtime output such as a replay, checkpoint, package, report, harness, or skill export.",
            "status": "implemented",
        },
        {
            "name": "Knowledge",
            "description": (
                "Persisted learned state that should carry forward across runs, such as playbooks, hints, lessons, and analysis."
            ),
            "status": "implemented",
        },
        {
            "name": "Budget",
            "description": "Constraints that bound runtime behavior, such as max steps, cost, time, or retries.",
            "status": "partial",
        },
        {
            "name": "Policy",
            "description": (
                "Structured rules that constrain or guide runtime behavior, such "
                "as escalation, hint volume, cost, conflict, or harness policies."
            ),
            "status": "partial",
        },
    ],
    "mappings": [
        {
            "surface": "run",
            "canonical_concept": "Run",
            "category": "operation",
            "notes": "CLI and MCP keep the verb, but the underlying runtime noun is Run.",
        },
        {
            "surface": "task queue / TaskRow",
            "canonical_concept": "Task",
            "category": "runtime_job",
            "notes": "Represents background evaluation jobs today, not the canonical user-facing Task concept.",
        },
        {
            "surface": "AgentTask / AgentTaskSpec",
            "canonical_concept": "Task",
            "category": "internal_type",
            "notes": "Current prompt-centric Task implementation.",
        },
        {
            "surface": "solve",
            "canonical_concept": "Run",
            "category": "operation",
            "notes": (
                "Solve is a workflow that creates or selects a scenario/task, launches a run, and exports resulting knowledge."
            ),
        },
        {
            "surface": "sandbox",
            "canonical_concept": "Policy",
            "category": "runtime_boundary",
            "notes": "Sandboxing is runtime isolation around execution, not a peer product noun.",
        },
        {
            "surface": "replay",
            "canonical_concept": "Artifact",
            "category": "artifact",
            "notes": "A replay is an artifact view over a run or generation.",
        },
        {
            "surface": "playbook",
            "canonical_concept": "Knowledge",
            "category": "artifact",
            "notes": "A playbook is one kind of knowledge artifact.",
        },
        {
            "surface": "artifacts",
            "canonical_concept": "Artifact",
            "category": "collection",
            "notes": "Collection term for runtime outputs.",
        },
    ],
}


def get_concept_model() -> dict[str, Any]:
    """Return a defensive copy of the canonical concept model metadata."""
    return deepcopy(_CONCEPT_MODEL)
