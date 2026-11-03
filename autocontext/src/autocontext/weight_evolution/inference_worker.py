"""Trusted inference over independently verified Qwen checkpoint data.

Run this module's adapter inside the isolated pinned runtime. It never imports
downloaded source or candidate code, grades responses, or resumes a partial run.
Every result retains all samples, including valid length-limited responses.
"""

from __future__ import annotations

import importlib
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autocontext.weight_evolution.inference_models import (
    GenerationSample,
    InferenceRequest,
    InferenceResult,
    text_digest,
)
from autocontext.weight_evolution.models import ReproductionLock
from autocontext.weight_evolution.runtime import RuntimeLock, check_installed_runtime


class InferenceInfrastructureError(RuntimeError):
    """Incomplete generation or invalid execution evidence, never a zero score."""


@dataclass(frozen=True)
class BackendSample:
    index: int
    text: str
    token_ids: tuple[int, ...]
    finish_reason: str | None
    stop_reason: int | str | None = None


@dataclass(frozen=True)
class BackendOutput:
    prompt: str
    prompt_token_ids: tuple[int, ...]
    samples: tuple[BackendSample, ...]


class InferenceBackend(Protocol):
    def render_prompt(self, user_text: str) -> str: ...

    def tokenize_prompt(self, prompt: str) -> tuple[int, ...]: ...

    def generate(self, prompts: Sequence[str], sampling: dict[str, Any]) -> Sequence[BackendOutput]: ...

    def hardware(self) -> dict[str, str]: ...


class VllmBackend:
    """Lazy local-only loading with the released think35 generation settings."""

    def __init__(self, checkpoint_dir: Path, lock: ReproductionLock) -> None:
        self._checkpoint_dir = checkpoint_dir
        self._lock = lock
        transformers = importlib.import_module("transformers")
        self._tokenizer = transformers.AutoTokenizer.from_pretrained(
            str(checkpoint_dir), local_files_only=True, trust_remote_code=False,
        )
        self._llm: Any = None

    def render_prompt(self, user_text: str) -> str:
        value = self._tokenizer.apply_chat_template(
            [{"role": "user", "content": user_text}],
            tokenize=False, add_generation_prompt=True, enable_thinking=True,
        )
        if not isinstance(value, str):
            raise InferenceInfrastructureError("tokenizer returned a nontext prompt")
        return value

    def tokenize_prompt(self, prompt: str) -> tuple[int, ...]:
        # Match the source LLM.generate(text_prompts) tokenizer defaults. The
        # engine's returned prompt IDs are checked against these before saving.
        return tuple(self._tokenizer.encode(prompt))

    def generate(self, prompts: Sequence[str], sampling: dict[str, Any]) -> Sequence[BackendOutput]:
        vllm = importlib.import_module("vllm")
        self._llm = vllm.LLM(
            model=str(self._checkpoint_dir), tokenizer=str(self._checkpoint_dir),
            dtype="bfloat16", gpu_memory_utilization=0.85,
            max_model_len=self._lock.evaluation.max_model_len, seed=0,
            trust_remote_code=False, load_format="safetensors", tensor_parallel_size=1,
            # Keep the pinned local generation_config.json (including EOS), as
            # the source's default 'auto' behavior does. No extra stop strings.
            generation_config="auto",
        )
        outputs = self._llm.generate(list(prompts), vllm.SamplingParams(**sampling), use_tqdm=False)
        return tuple(
            BackendOutput(
                prompt=output.prompt,
                prompt_token_ids=tuple(output.prompt_token_ids),
                samples=tuple(BackendSample(
                    index=sample.index,
                    text=sample.text,
                    token_ids=tuple(sample.token_ids),
                    finish_reason=sample.finish_reason,
                    stop_reason=sample.stop_reason,
                ) for sample in output.outputs),
            )
            for output in outputs
        )

    def hardware(self) -> dict[str, str]:
        torch = importlib.import_module("torch")
        if not torch.cuda.is_available():
            raise InferenceInfrastructureError("the pinned inference profile requires CUDA")
        device = torch.cuda.current_device()
        properties = torch.cuda.get_device_properties(device)
        # PyTorch exposes cudaDeviceProp.uuid as _CUuuid with 16 uint8 bytes.
        # Record the CUDA-selected device, rather than trusting a host ordinal
        # or environment variable that can be remapped by the container runtime.
        raw_uuid = getattr(getattr(properties, "uuid", None), "bytes", None)
        if not isinstance(raw_uuid, (list, tuple, bytes)) or len(raw_uuid) != 16 or any(
            type(part) is not int or not 0 <= part <= 255 for part in raw_uuid
        ) or not any(raw_uuid):
            raise InferenceInfrastructureError("CUDA device lacks a valid observed UUID")
        device_uuid = f"GPU-{uuid.UUID(bytes=bytes(raw_uuid))}"
        return {
            "accelerator": "cuda",
            "device_uuid": device_uuid,
            "device_name": str(torch.cuda.get_device_name(device)),
            "device_capability": ".".join(str(part) for part in torch.cuda.get_device_capability(device)),
            "device_total_memory_bytes": str(properties.total_memory),
            "cuda_runtime": str(torch.version.cuda),
            "tensor_parallel_size": "1",
            "gpu_memory_utilization": "0.85",
        }


BackendFactory = Callable[[Path, ReproductionLock], InferenceBackend]


def sampling_parameters(lock: ReproductionLock, request: InferenceRequest) -> dict[str, Any]:
    protocol = lock.evaluation
    return {
        "n": getattr(protocol.samples_per_problem, request.dataset),
        "temperature": protocol.temperature,
        "top_p": protocol.top_p,
        "top_k": protocol.top_k,
        "presence_penalty": protocol.presence_penalty,
        "max_tokens": request.smoke_max_new_tokens or protocol.max_new_tokens,
        "seed": protocol.seed,
        "stop": None,
        "stop_token_ids": None,
        "ignore_eos": False,
    }


def run_inference(
    lock: ReproductionLock,
    asset_root: Path,
    checkpoint_dir: Path,
    request: InferenceRequest,
    runtime: RuntimeLock,
    *,
    backend_factory: BackendFactory | None = None,
) -> InferenceResult:
    """Verify checkpoint and runtime before importing a model or issuing work.

    An injected backend is for offline fixtures and permanently labels the
    result ``execution_kind=fixture``. Production has no dependency fallback.
    The caller owns process/resource isolation and durable success/failure
    publication; this function returns only complete, independently bound raw
    evidence and does not convert infrastructure failures into model failures.
    """
    from autocontext.weight_evolution.patch_worker import verify_checkpoint

    started = time.monotonic()
    try:
        receipt = verify_checkpoint(lock, asset_root, checkpoint_dir, expected_variant=request.variant)
        if receipt.variant != request.variant:
            raise InferenceInfrastructureError("request variant differs from verified checkpoint")
        environment = check_installed_runtime(runtime) if backend_factory is None else {"execution_kind": "fixture"}
        backend = (backend_factory or VllmBackend)(checkpoint_dir, lock)
        sampling = sampling_parameters(lock, request)
        prompts: list[str] = []
        prompt_ids: list[tuple[int, ...]] = []
        for problem in request.problems:
            user_text = problem.text
            if request.dataset in ("math500", "gsm8k"):
                user_text += lock.evaluation.math_prompt_suffix
            prompt = backend.render_prompt(user_text)
            ids = backend.tokenize_prompt(prompt)
            if not isinstance(prompt, str) or not prompt or not ids:
                raise InferenceInfrastructureError("tokenizer produced an empty or invalid prompt")
            if any(type(token) is not int or token < 0 for token in ids):
                raise InferenceInfrastructureError("tokenizer produced invalid token IDs")
            # Keep the source's 2,048-token input allowance even for reduced
            # smoke runs, rather than silently expanding/truncating the cohort.
            if len(ids) > lock.evaluation.max_model_len - lock.evaluation.max_new_tokens:
                raise InferenceInfrastructureError(f"prompt exceeds pinned input allowance: {problem.problem_id}")
            prompts.append(prompt)
            prompt_ids.append(ids)

        outputs = backend.generate(prompts, sampling)
        if len(outputs) != len(prompts):
            raise InferenceInfrastructureError("engine returned an incomplete or surplus problem set")
        samples: list[GenerationSample] = []
        for problem, prompt, ids, output in zip(request.problems, prompts, prompt_ids, outputs, strict=True):
            if output.prompt != prompt or output.prompt_token_ids != ids:
                raise InferenceInfrastructureError("engine prompt evidence differs from rendered input")
            if {sample.index for sample in output.samples} != set(range(sampling["n"])) or len(output.samples) != sampling["n"]:
                raise InferenceInfrastructureError("engine returned missing, duplicate, or unexpected sample indices")
            for sample in sorted(output.samples, key=lambda value: value.index):
                if sample.finish_reason not in ("stop", "length"):
                    raise InferenceInfrastructureError(f"generation did not finish normally: {sample.finish_reason!r}")
                if len(sample.token_ids) > sampling["max_tokens"]:
                    raise InferenceInfrastructureError("engine exceeded the output token cap")
                if sample.finish_reason == "length" and len(sample.token_ids) != sampling["max_tokens"]:
                    raise InferenceInfrastructureError("length termination did not reach the pinned output token cap")
                samples.append(GenerationSample(
                    problem_id=problem.problem_id, sample_index=sample.index,
                    rendered_prompt=prompt, prompt_sha256=text_digest(prompt),
                    prompt_token_ids=ids, input_tokens=len(ids), text=sample.text,
                    response_sha256=text_digest(sample.text), token_ids=sample.token_ids,
                    output_tokens=len(sample.token_ids), finish_reason=sample.finish_reason,  # type: ignore[arg-type]
                    stop_reason=sample.stop_reason,
                ))
        return InferenceResult(
            execution_kind="fixture" if backend_factory is not None else "vllm",
            lock_digest=lock.digest, runtime_digest=runtime.digest, runtime_image=runtime.image,
            runtime_environment=environment,
            checkpoint_receipt_digest=receipt.digest, checkpoint_sha256=receipt.checkpoint_sha256,
            tensor_content_digest=receipt.tensor_content_digest,
            request_digest=request.digest, request=request,
            max_new_tokens=sampling["max_tokens"], samples_per_problem=sampling["n"],
            sampling=sampling, elapsed_seconds=time.monotonic() - started,
            hardware=backend.hardware(), samples=tuple(samples),
        )
    except InferenceInfrastructureError:
        raise
    except Exception as exc:
        raise InferenceInfrastructureError(f"inference failed: {type(exc).__name__}: {exc}") from exc
