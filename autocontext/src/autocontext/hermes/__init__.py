"""Hermes Agent integration helpers.

Scope: integration with NousResearch's Hermes agent and its Curator
subsystem (inspect, ingest, export-skill, train-advisor; see
docs/internal/hermes-positioning.md). Read-only against Hermes/Curator state,
though export-skill writes a local skill file when an output path is
supplied. Currently Python-only: no ts/ mirror exists for this package.

Naming note: unrelated to the "hermes" OpenAI-compatible provider gateway
in ts/src/providers/provider-factory.ts, which just points at a
Hermes-3-Llama model endpoint.
"""

from autocontext.hermes.inspection import CuratorInventory, CuratorRunSummary, HermesInventory, HermesSkill, inspect_hermes_home
from autocontext.hermes.skill import AUTOCONTEXT_HERMES_SKILL_NAME, render_autocontext_skill

__all__ = [
    "AUTOCONTEXT_HERMES_SKILL_NAME",
    "CuratorInventory",
    "CuratorRunSummary",
    "HermesInventory",
    "HermesSkill",
    "inspect_hermes_home",
    "render_autocontext_skill",
]
