from __future__ import annotations

import copy
import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from autocontext.weight_evolution.inference_models import (
    InferenceProblem,
    InferenceRequest,
    InferenceResult,
    load_inference_request,
)
from autocontext.weight_evolution.inference_worker import (
    BackendOutput,
    BackendSample,
    InferenceInfrastructureError,
    VllmBackend,
    run_inference,
    sampling_parameters,
)
from autocontext.weight_evolution.models import load_reproduction_lock

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"
LOCK = load_reproduction_lock(EXAMPLE / "reproduction.lock.json")


def request(**kwargs: Any) -> InferenceRequest:
    data = {
        "variant": "published_patch", "dataset": "math500", "dataset_sha256": "a" * 64,
        "problems": (InferenceProblem(problem_id="test/1", text="What is one plus one?"),),
    }
    data.update(kwargs)
    return InferenceRequest(**data)


class FakeBackend:
    def __init__(self) -> None:
        self.user_texts: list[str] = []
        self.sampling: dict[str, Any] = {}
        self.prompt_tokens = (1, 2, 3)
        self.outputs: list[BackendOutput] | None = None
        self.called = False

    def render_prompt(self, user_text: str) -> str:
        self.user_texts.append(user_text)
        return f"<user>{user_text}</user><assistant><think>"

    def tokenize_prompt(self, prompt: str) -> tuple[int, ...]:
        return self.prompt_tokens

    def generate(self, prompts: Any, sampling: dict[str, Any]) -> list[BackendOutput]:
        self.called = True
        self.sampling = sampling
        if self.outputs is not None:
            return self.outputs
        return [BackendOutput(prompt, self.prompt_tokens, tuple(
            BackendSample(index=index, text="done</think>\\boxed{2}", token_ids=(4, 5), finish_reason="stop")
            for index in reversed(range(sampling["n"]))
        )) for prompt in prompts]

    def hardware(self) -> dict[str, str]:
        return {"accelerator": "fixture"}


@pytest.fixture
def verified(monkeypatch: Any) -> SimpleNamespace:
    receipt = SimpleNamespace(
        variant="published_patch", digest="b" * 64, checkpoint_sha256="c" * 64, tensor_content_digest="d" * 64,
    )
    verifier = Mock(return_value=receipt)
    monkeypatch.setattr("autocontext.weight_evolution.patch_worker.verify_checkpoint", verifier)
    runtime = SimpleNamespace(digest="e" * 64, image="vllm/vllm-openai@sha256:" + "f" * 64)
    return SimpleNamespace(receipt=receipt, verifier=verifier, runtime=runtime)


def execute(verified: Any, backend: FakeBackend, payload: InferenceRequest | None = None) -> InferenceResult:
    return run_inference(LOCK, Path("assets"), Path("checkpoint"), payload or request(), verified.runtime,
                         backend_factory=lambda _path, _lock: backend)


def test_raw_samples_preserve_source_protocol_and_provenance(verified: Any) -> None:
    backend = FakeBackend()
    result = execute(verified, backend)
    assert result.execution_kind == "fixture"
    assert result.benchmark_score is None
    assert result.request_digest == request().digest
    assert result.lock_digest == LOCK.digest
    assert result.checkpoint_sha256 == verified.receipt.checkpoint_sha256
    assert result.checkpoint_receipt_digest == verified.receipt.digest
    assert result.runtime_digest == verified.runtime.digest
    assert result.runtime_image == verified.runtime.image
    assert result.elapsed_seconds >= 0
    assert backend.user_texts == ["What is one plus one?" + LOCK.evaluation.math_prompt_suffix]
    assert backend.sampling == {
        "n": 4, "temperature": 0.6, "top_p": 0.95, "top_k": 20, "presence_penalty": 1.5,
        "max_tokens": 32768, "seed": 0, "stop": None, "stop_token_ids": None, "ignore_eos": False,
    }
    assert [sample.sample_index for sample in result.samples] == [0, 1, 2, 3]
    assert all(sample.input_tokens == 3 and sample.output_tokens == 2 for sample in result.samples)
    assert all(sample.seed == 0 and sample.text == "done</think>\\boxed{2}" for sample in result.samples)
    assert result.digest == InferenceResult.model_validate_json(result.model_dump_json()).digest
    verified.verifier.assert_called_once_with(LOCK, Path("assets"), Path("checkpoint"), expected_variant="published_patch")


def test_ifeval_does_not_receive_math_suffix(verified: Any) -> None:
    backend = FakeBackend()
    result = execute(verified, backend, request(dataset="ifeval"))
    assert backend.user_texts == ["What is one plus one?"]
    assert result.samples_per_problem == 2


def test_valid_truncation_is_kept_with_smoke_identity(verified: Any) -> None:
    backend = FakeBackend()
    payload = request(dataset="gsm8k", mode="smoke", smoke_max_new_tokens=3)
    prompt = backend.render_prompt(payload.problems[0].text + LOCK.evaluation.math_prompt_suffix)
    backend.outputs = [BackendOutput(prompt, backend.prompt_tokens, tuple(
        BackendSample(index=index, text="unfinished thinking", token_ids=(4, 5, 6), finish_reason="length")
        for index in range(2)
    ))]
    result = execute(verified, backend, payload)
    assert result.max_new_tokens == 3
    assert result.max_model_len == 34816
    assert result.request.mode == "smoke"
    assert all(sample.finish_reason == "length" for sample in result.samples)
    assert result.request_digest != request(dataset="gsm8k").digest


@pytest.mark.parametrize("field,value", [
    ("prompt", "changed"), ("ids", (1, 2)), ("finish", "abort"), ("finish", None),
    ("count", 3), ("count", 5), ("duplicate", True), ("token", -1), ("token", True),
    ("length", 2), ("too_long", 5), ("missing_output", True), ("surplus_output", True),
])
def test_engine_failures_do_not_become_scored_samples(verified: Any, field: str, value: Any) -> None:
    backend = FakeBackend()
    payload = request(mode="smoke", smoke_max_new_tokens=4)
    prompt = backend.render_prompt(payload.problems[0].text + LOCK.evaluation.math_prompt_suffix)
    token_ids = (4, 5)
    if field == "token":
        token_ids = (value,)
    if field == "too_long":
        token_ids = tuple(range(value))
    finish: Any = value if field == "finish" else "length" if field == "length" else "stop"
    samples = tuple(BackendSample(
        index=0 if field == "duplicate" else index,
        text="answer", token_ids=token_ids, finish_reason=finish,
    ) for index in range(value if field == "count" else 4))
    output = BackendOutput(
        value if field == "prompt" else prompt, value if field == "ids" else backend.prompt_tokens, samples,
    )
    backend.outputs = [] if field == "missing_output" else [output, output] if field == "surplus_output" else [output]
    with pytest.raises(InferenceInfrastructureError):
        execute(verified, backend, payload)


def test_prompt_overflow_rejected_before_dispatch_even_for_smoke(verified: Any) -> None:
    backend = FakeBackend()
    backend.prompt_tokens = tuple(range(2049))
    with pytest.raises(InferenceInfrastructureError, match="input allowance"):
        execute(verified, backend, request(mode="smoke", smoke_max_new_tokens=4))
    assert not backend.called


def test_wrong_variant_rejected_before_backend(verified: Any) -> None:
    factory = Mock()
    with pytest.raises(InferenceInfrastructureError, match="variant"):
        run_inference(LOCK, Path("assets"), Path("checkpoint"), request(variant="base"), verified.runtime,
                      backend_factory=factory)
    factory.assert_not_called()


def test_checkpoint_verification_failure_precedes_any_model_import(verified: Any, monkeypatch: Any) -> None:
    verified.verifier.side_effect = ValueError("tensor mismatch")
    importer = Mock(side_effect=AssertionError("must not import"))
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.importlib.import_module", importer)
    with pytest.raises(InferenceInfrastructureError, match="tensor mismatch"):
        run_inference(LOCK, Path("assets"), Path("checkpoint"), request(), verified.runtime)
    importer.assert_not_called()


def test_production_checks_runtime_before_loading_backend(verified: Any, monkeypatch: Any) -> None:
    checker = Mock(side_effect=ValueError("wrong runtime"))
    factory = Mock()
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.check_installed_runtime", checker)
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.VllmBackend", factory)
    with pytest.raises(InferenceInfrastructureError, match="wrong runtime"):
        run_inference(LOCK, Path("assets"), Path("checkpoint"), request(), verified.runtime)
    checker.assert_called_once_with(verified.runtime)
    factory.assert_not_called()


def test_real_adapter_is_local_only_and_preserves_template_and_eos_defaults(monkeypatch: Any) -> None:
    tokenizer = Mock()
    tokenizer.apply_chat_template.return_value = "rendered"
    tokenizer.encode.return_value = [1, 2]
    transformers = SimpleNamespace(AutoTokenizer=Mock())
    transformers.AutoTokenizer.from_pretrained.return_value = tokenizer
    llm = Mock()
    llm.generate.return_value = [SimpleNamespace(
        prompt="rendered", prompt_token_ids=[1, 2], outputs=[SimpleNamespace(
            index=0, text="answer", token_ids=[3], finish_reason="stop", stop_reason=248044,
        )],
    )]
    vllm = SimpleNamespace(LLM=Mock(return_value=llm), SamplingParams=Mock())
    modules = {"transformers": transformers, "vllm": vllm}
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.importlib.import_module", modules.__getitem__)
    backend = VllmBackend(Path("checkpoint"), LOCK)
    transformers.AutoTokenizer.from_pretrained.assert_called_once_with(
        "checkpoint", local_files_only=True, trust_remote_code=False,
    )
    assert backend.render_prompt("question") == "rendered"
    tokenizer.apply_chat_template.assert_called_once_with(
        [{"role": "user", "content": "question"}], tokenize=False, add_generation_prompt=True, enable_thinking=True,
    )
    assert backend.tokenize_prompt("rendered") == (1, 2)
    outputs = backend.generate(["rendered"], sampling_parameters(LOCK, request()))
    assert outputs[0].samples[0].stop_reason == 248044
    assert vllm.LLM.call_args.kwargs == {
        "model": "checkpoint", "tokenizer": "checkpoint", "dtype": "bfloat16", "gpu_memory_utilization": 0.85,
        "max_model_len": 34816, "seed": 0, "trust_remote_code": False, "load_format": "safetensors",
        "tensor_parallel_size": 1, "generation_config": "auto",
    }
    llm.generate.assert_called_once_with(["rendered"], vllm.SamplingParams.return_value, use_tqdm=False)


def test_hardware_records_cuda_selected_uuid_not_host_ordinal(monkeypatch: Any) -> None:
    identity = uuid.UUID("01234567-89ab-cdef-0123-456789abcdef")
    properties = SimpleNamespace(uuid=SimpleNamespace(bytes=list(identity.bytes)), total_memory=24 * 1024**3)
    cuda = SimpleNamespace(
        is_available=Mock(return_value=True), current_device=Mock(return_value=0),
        get_device_properties=Mock(return_value=properties), get_device_name=Mock(return_value="GPU name"),
        get_device_capability=Mock(return_value=(8, 9)),
    )
    torch = SimpleNamespace(cuda=cuda, version=SimpleNamespace(cuda="13.0"))
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.importlib.import_module", lambda _: torch)
    # Device observation needs no tokenizer or inference construction.
    observed = object.__new__(VllmBackend).hardware()
    assert observed == {
        "accelerator": "cuda", "device_uuid": f"GPU-{identity}", "device_name": "GPU name",
        "device_capability": "8.9", "device_total_memory_bytes": str(24 * 1024**3),
        "cuda_runtime": "13.0", "tensor_parallel_size": "1", "gpu_memory_utilization": "0.85",
    }
    cuda.get_device_properties.assert_called_once_with(0)


@pytest.mark.parametrize("raw_uuid", [None, "01234567-89ab-cdef-0123-456789abcdef", [], [1] * 15,
                                      [1] * 17, [True] * 16, [-1] * 16, [256] * 16, [0] * 16])
def test_hardware_rejects_missing_or_malformed_cuda_uuid(monkeypatch: Any, raw_uuid: Any) -> None:
    properties = SimpleNamespace(uuid=SimpleNamespace(bytes=raw_uuid), total_memory=1024)
    cuda = SimpleNamespace(is_available=lambda: True, current_device=lambda: 0,
                           get_device_properties=lambda _: properties)
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.importlib.import_module",
                        lambda _: SimpleNamespace(cuda=cuda))
    with pytest.raises(InferenceInfrastructureError, match="valid observed UUID"):
        object.__new__(VllmBackend).hardware()


def test_hardware_rejects_cuda_unavailable_before_device_observation(monkeypatch: Any) -> None:
    cuda = SimpleNamespace(is_available=lambda: False)
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.importlib.import_module",
                        lambda _: SimpleNamespace(cuda=cuda))
    with pytest.raises(InferenceInfrastructureError, match="requires CUDA"):
        object.__new__(VllmBackend).hardware()


@pytest.mark.parametrize("changes", [
    {"variant": "custom"}, {"dataset": "unregistered"}, {"dataset_sha256": "main"},
    {"mode": "reproduction", "smoke_max_new_tokens": 64}, {"mode": "smoke"},
    {"mode": "smoke", "smoke_max_new_tokens": 32768}, {"mode": "smoke", "smoke_max_new_tokens": True},
    {"problems": ()}, {"problems": (InferenceProblem(problem_id="dup", text="A"), InferenceProblem(problem_id="dup", text="B"))},
    {"seed": 1}, {"temperature": 0.1}, {"samples": 1},
])
def test_request_rejects_ambiguous_or_unpinned_conditions(changes: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        request(**changes)


@pytest.mark.parametrize("problem_id,text", [(" ", "question"), ("\0", "question"), ("a", " "), ("a", "a\0b")])
def test_problem_requires_canonical_identity_and_nonblank_text(problem_id: str, text: str) -> None:
    with pytest.raises(ValidationError):
        InferenceProblem(problem_id=problem_id, text=text)


def test_request_loader_rejects_duplicates_unknown_fields_and_symlinks(tmp_path: Path) -> None:
    path = tmp_path / "request.json"
    path.write_text(request().model_dump_json())
    assert load_inference_request(path).digest == request().digest
    path.write_text(request().model_dump_json()[:-1] + ',"dataset":"ifeval"}')
    with pytest.raises(ValueError, match="duplicate"):
        load_inference_request(path)
    data = request().to_dict()
    data["python"] = "raise RuntimeError()"
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="Extra inputs"):
        load_inference_request(path)
    alias = tmp_path / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="symlink"):
        load_inference_request(alias)
    with pytest.raises(ValueError, match="regular file"):
        load_inference_request(tmp_path)


@pytest.mark.parametrize("change", ["missing", "duplicate", "text", "hash", "count", "request", "tokens"])
def test_result_rejects_corrupted_or_incomplete_evidence(verified: Any, change: str) -> None:
    data = copy.deepcopy(execute(verified, FakeBackend()).to_dict())
    if change == "missing":
        data["samples"].pop()
    elif change == "duplicate":
        data["samples"].append(data["samples"][0])
    elif change == "text":
        data["samples"][0]["text"] = "tampered"
    elif change == "hash":
        data["samples"][0]["prompt_sha256"] = "0" * 64
    elif change == "count":
        data["samples"][0]["input_tokens"] = 100
    elif change == "request":
        data["request"]["problems"][0]["text"] = "changed"
    else:
        data["samples"][0]["token_ids"][0] = -1
    with pytest.raises(ValidationError):
        InferenceResult.model_validate_json(json.dumps(data))
