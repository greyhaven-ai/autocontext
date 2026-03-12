"""Adaptation layer — types and logic for applying config recommendations."""

from __future__ import annotations

from autocontext.harness.adapt.applicator import ConfigApplicator
from autocontext.harness.adapt.types import AdaptationPolicy, AdaptationResult, AdaptationStatus

__all__ = ["AdaptationPolicy", "AdaptationResult", "AdaptationStatus", "ConfigApplicator"]
