"""Durable actual-fixture bindings for adaptive promotion campaigns."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Generic, TypeVar

from autocontext.context_bundles.models import TrialLane, stable_digest
from autocontext.util.json_io import read_json, write_json

_RiskReservationT = TypeVar("_RiskReservationT")


@dataclass(frozen=True, slots=True)
class CampaignFixtureUnit:
    """One predeclared scenario fixture in a campaign candidate's test plan."""

    lane: TrialLane
    fixture_digest: str
    seed: int

    def __post_init__(self) -> None:
        if not self.fixture_digest:
            raise ValueError("campaign fixture digest must be non-empty")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("campaign fixture seed must be an integer")

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane": self.lane.value,
            "fixture_digest": self.fixture_digest,
            "seed": self.seed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CampaignFixtureUnit:
        raw_seed = data.get("seed")
        if isinstance(raw_seed, bool) or not isinstance(raw_seed, int):
            raise ValueError("campaign fixture seed must be an integer")
        return cls(
            lane=TrialLane(str(data["lane"])),
            fixture_digest=str(data["fixture_digest"]),
            seed=raw_seed,
        )


@dataclass(frozen=True, slots=True)
class CandidateFixtureReservation:
    """Durable binding between one candidate and its unexposed fixture plan."""

    campaign_id: str
    candidate_digest: str
    evaluator_epoch: str
    cohort: str
    units: tuple[CampaignFixtureUnit, ...]

    def __post_init__(self) -> None:
        if not self.campaign_id or not self.candidate_digest or not self.evaluator_epoch or not self.cohort:
            raise ValueError("campaign fixture reservation identities must be non-empty")
        if not self.units:
            raise ValueError("campaign fixture reservation requires a non-empty plan")
        identities = {(unit.fixture_digest, unit.seed) for unit in self.units}
        if len(identities) != len(self.units):
            raise ValueError("campaign fixture plan contains duplicate fixture/seed identities")

    @property
    def plan_digest(self) -> str:
        return stable_digest(
            {
                "campaign_id": self.campaign_id,
                "candidate_digest": self.candidate_digest,
                "evaluator_epoch": self.evaluator_epoch,
                "cohort": self.cohort,
                "units": [unit.to_dict() for unit in self.units],
            }
        )

    @property
    def fixture_digests(self) -> frozenset[str]:
        return frozenset(unit.fixture_digest for unit in self.units)

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "candidate_digest": self.candidate_digest,
            "evaluator_epoch": self.evaluator_epoch,
            "cohort": self.cohort,
            "units": [unit.to_dict() for unit in self.units],
            "plan_digest": self.plan_digest,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CandidateFixtureReservation:
        raw_units = data.get("units")
        if not isinstance(raw_units, list) or not all(isinstance(item, dict) for item in raw_units):
            raise ValueError("campaign fixture reservation units must be a list of objects")
        reservation = cls(
            campaign_id=str(data["campaign_id"]),
            candidate_digest=str(data["candidate_digest"]),
            evaluator_epoch=str(data["evaluator_epoch"]),
            cohort=str(data["cohort"]),
            units=tuple(CampaignFixtureUnit.from_dict(item) for item in raw_units),
        )
        if data.get("plan_digest") != reservation.plan_digest:
            raise ValueError("campaign fixture plan digest mismatch")
        return reservation


@dataclass(slots=True)
class CampaignFalsePromotionState(Generic[_RiskReservationT]):
    reservations: list[_RiskReservationT]
    fixture_reservations: list[CandidateFixtureReservation]
    fixture_history_complete: bool = True


def campaign_path_segment(value: str) -> str:
    """Return a traversal-safe, deterministic storage segment for a campaign."""

    if not value:
        raise ValueError("campaign identity must be non-empty")
    if (
        value not in {".", ".."}
        and len(value.encode("utf-8")) <= 120
        and all(character.isascii() and (character.isalnum() or character in "-_.") for character in value)
    ):
        return value
    return f"%{stable_digest({'campaign_path_identity': value})}"


def persist_reservation_artifact(
    root: Path,
    campaign_id: str,
    risk_reservation: Any,
    fixture_reservation: CandidateFixtureReservation,
) -> tuple[Path, str]:
    """Write/read one immutable, reconstructible terminal reservation artifact."""

    risk_payload = risk_reservation.to_dict()
    candidate_digest = risk_payload.get("candidate_digest")
    if (
        not isinstance(candidate_digest, str)
        or len(candidate_digest) != 64
        or any(character not in "0123456789abcdef" for character in candidate_digest)
    ):
        raise ValueError("false-promotion reservation artifact has an invalid candidate digest")
    payload = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "risk_reservation": risk_payload,
        "fixture_reservation": fixture_reservation.to_dict(),
    }
    artifact_digest = stable_digest(payload)
    artifact = {**payload, "artifact_digest": artifact_digest}
    path = root / campaign_path_segment(campaign_id) / "reservation-artifacts" / f"{candidate_digest}.json"
    if path.exists():
        if read_json(path) != artifact:
            raise ValueError("false-promotion reservation artifact is immutable")
    else:
        write_json(path, artifact)
    return path.resolve(), artifact_digest


__all__ = [
    "CampaignFalsePromotionState",
    "CampaignFixtureUnit",
    "CandidateFixtureReservation",
    "campaign_path_segment",
    "persist_reservation_artifact",
]
