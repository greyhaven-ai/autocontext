import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.harness_optimization.contract import models as m

REPO = Path(__file__).resolve().parents[3]
FIX = REPO / "fixtures" / "harness-optimization"
MANIFEST = json.loads((FIX / "parity-manifest.json").read_text())
SCHEMA_DIR = REPO / "ts" / "src" / "harness-optimization" / "contract" / "json-schemas"

MODELS = {
    "candidate-evidence": m.CandidateEvidence,
    "promotion-score": m.PromotionScore,
    "repair-result": m.RepairResult,
    "integrity-metadata": m.IntegrityMetadata,
    "frontier-mechanism": m.FrontierMechanism,
    "orphan-mechanism": m.OrphanMechanism,
    "calibration-report": m.CalibrationReport,
}


def _load(rel: str) -> dict:
    return json.loads((REPO / "fixtures" / rel).read_text())


def test_manifest_covers_every_schema() -> None:
    schema_files = {p.name for p in SCHEMA_DIR.glob("*.schema.json") if p.name != "_aggregate.schema.json"}
    manifest_file_list = [a["schema_file"] for a in MANIFEST["artifacts"]]
    manifest_files = set(manifest_file_list)
    assert len(manifest_file_list) == len(manifest_files), (
        f"parity manifest has a duplicate schema_file entry: {manifest_file_list}"
    )
    assert manifest_files == schema_files, (
        f"parity manifest out of sync with schemas: missing {schema_files - manifest_files}, "
        f"extra {manifest_files - schema_files}"
    )


def test_manifest_schema_id_matches_schema_file() -> None:
    for a in MANIFEST["artifacts"]:
        schema = json.loads((SCHEMA_DIR / a["schema_file"]).read_text())
        assert a["schema_id"] == schema["$id"], (
            f"{a['name']} manifest schema_id {a['schema_id']} does not match {a['schema_file']} $id {schema['$id']}"
        )


def test_every_artifact_has_clean_and_invalid() -> None:
    for a in MANIFEST["artifacts"]:
        assert a["valid"] and a["invalid"], f"{a['name']} needs >=1 valid and >=1 invalid fixture"


@pytest.mark.parametrize("artifact", MANIFEST["artifacts"], ids=lambda a: a["name"])
def test_clean_fixtures_validate(artifact: dict) -> None:
    model = MODELS[artifact["name"]]
    for rel in artifact["valid"]:
        model.model_validate(_load(rel))


@pytest.mark.parametrize("artifact", MANIFEST["artifacts"], ids=lambda a: a["name"])
def test_invalid_fixtures_rejected(artifact: dict) -> None:
    model = MODELS[artifact["name"]]
    for rel in artifact["invalid"]:
        with pytest.raises(ValidationError):
            model.model_validate(_load(rel))
