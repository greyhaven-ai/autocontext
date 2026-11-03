"""The pinned Qwen p50 reproduction contract, independent of model runtimes."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import ConfigDict, Field, field_validator, model_validator

from autocontext.util.models import StrictModel

SOURCE_REVISION = "4d09ba6d6a7e891dd8063974dfe81b3d78e3494f"
MODEL_REVISION = "2fc06364715b967f1860aea9cf38778875588b17"
_TEXT_CONFIG_ROOT = "exp8_math/base_model/Qwen3.5-0.8B-text"
REQUIRED_ASSET_PATHS = {
    "source_readme": "exp8_math/README.md",
    "apply_edit_source": "exp8_math/code/apply_edit.py",
    "evaluate_source": "exp8_math/code/evaluate.py",
    "mathqa_source": "exp8_math/code/mathqa.py",
    "profiles_source": "exp8_math/code/profiles.py",
    "build_checkpoint_source": "exp8_math/code/build_ckpt.py",
    "practice_filter_source": "exp8_math/code/practice35.py",
    "patch_manifest": "exp8_math/runs/p50c/edit/manifest.json",
    "patch_delta": "exp8_math/runs/p50c/edit/edit.npz",
    "text_config": f"{_TEXT_CONFIG_ROOT}/config.json",
    "generation_config": f"{_TEXT_CONFIG_ROOT}/generation_config.json",
    "tokenizer_config": f"{_TEXT_CONFIG_ROOT}/tokenizer_config.json",
    "chat_template": f"{_TEXT_CONFIG_ROOT}/chat_template.jinja",
    "source_license": "LICENSE",
}
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Identifier = Annotated[str, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")]


class ContractError(ValueError):
    """The manifest is not a valid pinned reproduction contract."""


class FrozenModel(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class SourcePin(FrozenModel):
    repository: Literal["RikiyaT/lm_understand-logs"]
    revision: Literal["4d09ba6d6a7e891dd8063974dfe81b3d78e3494f"]


class ModelPin(FrozenModel):
    repository: Literal["Qwen/Qwen3.5-0.8B"]
    revision: Literal["2fc06364715b967f1860aea9cf38778875588b17"]
    architecture: Literal["Qwen3_5ForCausalLM"]
    dtype: Literal["bfloat16"]
    tensor_namespace: Literal["model.language_model."]
    num_hidden_layers: Literal[24]


class SampleCounts(FrozenModel):
    math500: Literal[4]
    gsm8k: Literal[2]
    ifeval: Literal[2]

    @field_validator("math500", "gsm8k", "ifeval", mode="before")
    @classmethod
    def integer_counts(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("sample counts must be integers")
        return value


class EvaluationProtocol(FrozenModel):
    engine: Literal["vllm"]
    thinking: Literal[True]
    temperature: float = Field(allow_inf_nan=False)
    top_p: float = Field(allow_inf_nan=False)
    top_k: Literal[20]
    presence_penalty: float = Field(allow_inf_nan=False)
    max_new_tokens: Literal[32768]
    max_model_len: Literal[34816]
    seed: Literal[0]
    math_prompt_suffix: str
    samples_per_problem: SampleCounts
    math_metric: Literal["mean_sample_accuracy"]
    ifeval_metric: Literal["strict_prompt_accuracy"]
    math_grader: Literal["math-verify"]
    math_extraction: Literal["last_boxed_after_last_think_if_present"]
    ifeval_extraction: Literal["require_think_close"]

    @model_validator(mode="after")
    def pinned_sampling(self) -> Self:
        if (self.temperature, self.top_p, self.presence_penalty) != (0.6, 0.95, 1.5):
            raise ValueError("sampling differs from the pinned think35 protocol")
        return self

    @field_validator("math_prompt_suffix")
    @classmethod
    def pinned_prompt(cls, value: str) -> str:
        expected = "\nPlease reason step by step, and put your final answer within \\boxed{}."
        if value != expected:
            raise ValueError("math prompt differs from the pinned think35 protocol")
        return value

class AssetPin(FrozenModel):
    asset_id: Identifier
    relative_path: str
    url: str
    sha256: Digest
    size_bytes: int = Field(ge=0)
    role: Identifier
    required: bool = True

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            not value
            or path.is_absolute()
            or "\\" in value
            or ":" in value
            or any(ord(char) < 32 for char in value)
            or any(part in {"", ".", ".."} for part in value.split("/"))
            or str(path) != value
        ):
            raise ValueError("asset path must be a canonical relative POSIX path")
        return value

    @field_validator("url")
    @classmethod
    def pinned_url(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme != "https" or url.username or url.password or url.port or url.query or url.fragment:
            raise ValueError("asset URL must be plain HTTPS with an immutable revision")
        prefixes = {
            "raw.githubusercontent.com": f"/RikiyaT/lm_understand-logs/{SOURCE_REVISION}/",
            "huggingface.co": f"/Qwen/Qwen3.5-0.8B/resolve/{MODEL_REVISION}/",
        }
        prefix = prefixes.get(url.netloc)
        if prefix is None or not url.path.startswith(prefix):
            raise ValueError("asset URL is not bound to the pinned source or model revision")
        cls.safe_relative_path(url.path[len(prefix):])
        if "%" in url.path:
            raise ValueError("encoded asset paths are not supported")
        return value


class TensorPin(FrozenModel):
    name: str
    shape: tuple[int, int]
    dtype: Literal["bfloat16"]
    changed_entries: int = Field(gt=0)
    changed_columns: Literal[64]
    changed_rows: int = Field(gt=0)
    relative_change: float = Field(gt=0, allow_inf_nan=False)
    base_sha256: Digest
    edited_sha256: Digest
    submitted_file: str
    form: Literal["sparse"]

    @model_validator(mode="after")
    def tensor_contract(self) -> Self:
        if self.shape != (1024, 3584):
            raise ValueError("unexpected Qwen down-projection shape")
        if not re.fullmatch(r"model\.language_model\.layers\.(15|19)\.mlp\.down_proj\.weight", self.name):
            raise ValueError("unexpected p50 tensor")
        if self.submitted_file != f"{self.name}.npy":
            raise ValueError("submitted tensor filename does not match its name")
        if self.changed_rows > self.shape[0] or self.changed_entries > self.shape[0] * self.changed_columns:
            raise ValueError("changed entries/rows exceed selected tensor bounds")
        return self


class PatchPin(FrozenModel):
    manifest_asset_id: Literal["patch_manifest"]
    delta_asset_id: Literal["patch_delta"]
    touched_entries: Literal[131072]
    changed_entries: Literal[130188]
    tensors: tuple[TensorPin, ...] = Field(min_length=2, max_length=2)

    @model_validator(mode="after")
    def counts_match(self) -> Self:
        if len({tensor.name for tensor in self.tensors}) != 2:
            raise ValueError("patch tensors must be distinct")
        if sum(tensor.changed_entries for tensor in self.tensors) != self.changed_entries:
            raise ValueError("changed-entry total does not match tensor records")
        if sum(tensor.shape[0] * tensor.changed_columns for tensor in self.tensors) != self.touched_entries:
            raise ValueError("touched-entry total does not match tensor records")
        return self


class ReproductionLock(FrozenModel):
    schema_version: Literal[1]
    study_id: Literal["qwen35-08b-weight-space-p50-replay-v1"]
    source: SourcePin
    model: ModelPin
    evaluation: EvaluationProtocol
    assets: tuple[AssetPin, ...] = Field(min_length=1)
    patch: PatchPin
    fidelity_gaps: tuple[str, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def asset_contract(self) -> Self:
        ids = [asset.asset_id for asset in self.assets]
        paths = [asset.relative_path.casefold() for asset in self.assets]
        if len(set(ids)) != len(ids) or len(set(paths)) != len(paths):
            raise ValueError("duplicate asset identity or destination")
        for path in paths:
            if any(parent in paths for parent in (str(p) for p in PurePosixPath(path).parents) if parent != "."):
                raise ValueError("asset destination is also another asset's parent directory")
        assets = {asset.asset_id: asset for asset in self.assets}
        for key, path in REQUIRED_ASSET_PATHS.items():
            asset = assets.get(key)
            source_url = f"https://raw.githubusercontent.com/{self.source.repository}/{self.source.revision}/{path}"
            if asset is None or not asset.required or asset.relative_path != path or asset.url != source_url:
                raise ValueError(f"missing or changed required reproduction asset: {key}")
        for key in (self.patch.manifest_asset_id, self.patch.delta_asset_id):
            if key not in assets or not assets[key].required:
                raise ValueError("patch manifest and delta must be required pinned assets")
        if self.patch.manifest_asset_id == self.patch.delta_asset_id:
            raise ValueError("patch manifest and delta must be separate assets")
        if any(not gap.strip() for gap in self.fidelity_gaps):
            raise ValueError("fidelity gaps must be explicit nonempty descriptions")
        return self

    @property
    def digest(self) -> str:
        payload = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(payload.encode()).hexdigest()


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_reproduction_lock(path: Path) -> ReproductionLock:
    """Read a small data-only lock, rejecting ambiguous JSON and changed protocols."""
    try:
        with path.open("rb") as stream:
            data = stream.read(1024 * 1024 + 1)
        if len(data) > 1024 * 1024:
            raise ContractError("reproduction lock exceeds 1 MiB")
        # Validate duplicate keys before Pydantic's strict JSON tuple conversion.
        json.loads(data, object_pairs_hook=_unique_object)
        return ReproductionLock.model_validate_json(data)
    except (OSError, ValueError) as exc:
        raise ContractError(f"invalid reproduction lock: {exc}") from exc
