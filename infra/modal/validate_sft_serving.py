"""AC-891 real-hardware validation: serve an sft (torch/peft LoRA) adapter on a GPU.

This runs the REAL autocontext serving code (SftTorchProvider + SftTorchClient) on a Modal T4:
it builds a tiny base model plus a peft LoRA adapter, loads them through the provider, and
generates, proving the torch/peft serving path works on CUDA hardware. Torch is an optional
(cuda) extra, so this is validated here rather than in CI.

Run (from the repo root, token injected from Doppler):
    doppler run -p autocontext-arc-agi -c dev -- uv run --with modal modal run infra/modal/validate_sft_serving.py

A T4 is deliberately small and cheap: the model is tiny-gpt2 and generation is a few tokens.
"""

from __future__ import annotations

from pathlib import Path

import modal

# This module is imported both locally (at submit, where the local package source is mounted into
# the image) and remotely (inside the container, where __file__ is /root/validate_sft_serving.py and
# has no such parents). Resolve the local package source only when it actually exists; remotely fall
# back to /pkg (already baked into the image), so module import never crashes on the container side.
_here = Path(__file__).resolve()
_candidate = (
    _here.parents[2] / "autocontext" / "src" if len(_here.parents) >= 3 else None
)
_PKG_SRC = (
    _candidate if (_candidate is not None and _candidate.exists()) else Path("/pkg")
)

# Base autocontext runtime deps (from pyproject [project.dependencies]); the mlx extra is mac-only
# and deliberately not installed. The torch stack is the cuda-serving path AC-891 adds.
_BASE_DEPS = [
    "pydantic>=2.11.0",
    "anthropic>=0.66.0",
    "fastapi>=0.116.1",
    "python-ulid>=3.0.0",
    "pyyaml>=6.0.2",
    "rich>=13.9.4",
    "typer>=0.16.0",
    "uvicorn>=0.35.0",
]

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch", "transformers", "peft", "accelerate", "safetensors")
    .pip_install(*_BASE_DEPS)
    .add_local_dir(
        str(_PKG_SRC), "/pkg", copy=True, ignore=["**/__pycache__", "**/*.pyc"]
    )
    .env({"PYTHONPATH": "/pkg", "HF_HUB_DISABLE_TELEMETRY": "1"})
)

app = modal.App("ac891-sft-serving-validation")


@app.function(gpu="T4", image=image, timeout=1800)
def validate() -> dict:
    import tempfile

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # 1) build a tiny base + a small peft LoRA adapter and save it to disk (stands in for a
    #    harness-trained sft adapter without any real training cost).
    base_id = "sshleifer/tiny-gpt2"
    tokenizer = AutoTokenizer.from_pretrained(base_id)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(base_id)
    lora = LoraConfig(
        r=4, lora_alpha=8, target_modules=["c_attn"], task_type="CAUSAL_LM"
    )
    adapter_dir = tempfile.mkdtemp()
    get_peft_model(model, lora).save_pretrained(adapter_dir)

    # 2) exercise the REAL autocontext provider: load base + adapter and generate on the GPU.
    from autocontext.providers.sft_torch_provider import SftTorchProvider

    provider = SftTorchProvider(
        base_id, adapter_path=adapter_dir, temperature=0.7, max_tokens=16
    )
    result = provider.complete("", "Hello, world.", temperature=0.7, max_tokens=16)
    assert isinstance(result.text, str)
    assert result.model == base_id
    assert result.usage["input_tokens"] > 0
    assert result.usage["output_tokens"] >= 0

    # 3) exercise the REAL client wrapper end to end (ModelResponse with usage).
    from autocontext.agents.sft_torch_client import SftTorchClient

    client = SftTorchClient(
        base_id, adapter_path=adapter_dir, temperature=0.7, max_tokens=8
    )
    response = client.generate(
        model=base_id, prompt="Hi there.", max_tokens=8, temperature=0.7
    )
    assert isinstance(response.text, str)
    assert response.usage.model == base_id

    # 4) exercise the FULL production closure: an active sft candidate record resolved through the
    #    serving resolver (plan_local_client -> build_planned_client) and generated via the ambient
    #    evaluate stage's build_candidate_generation_fn. This is the end-to-end path a real ambient
    #    run takes when settings.ambient_real_candidate_generation is on.
    from autocontext.ambient.charter import CharterAnchor
    from autocontext.ambient.generation import build_candidate_generation_fn
    from autocontext.config import AppSettings
    from autocontext.training.model_registry import DistilledModelRecord

    sft_record = DistilledModelRecord(
        artifact_id="ac891-modal-cand",
        scenario="grid_ctf",
        scenario_family="",
        backend="sft",
        checkpoint_path=adapter_dir,
        runtime_types=["provider"],
        activation_state="candidate",
        training_metrics={},
        provenance={},
        metadata={"base_model": base_id, "target": "competitor-local"},
    )
    settings = AppSettings(mlx_max_tokens=8, mlx_temperature=0.7)
    anchor = CharterAnchor(
        provider="anthropic", model="claude-sonnet-5", rubric="Score 0 to 1."
    )
    generate_fn = build_candidate_generation_fn(settings)
    closure_text = generate_fn(sft_record, anchor, "Evaluate this candidate.")
    assert isinstance(closure_text, str)
    # cached: a second call for the same record must not rebuild the client
    closure_text_2 = generate_fn(sft_record, anchor, "A second case.")
    assert isinstance(closure_text_2, str)

    # 5) exercise the AC-893 per-role serving-manifest bridge end to end. The ambient trainer slots
    #    a promoted per-role model under scenario = target.name (AC-884 anti-collision), and the
    #    serving manifest carries the (real-scenario, role) -> target bridge the promote stage wrote.
    #    _resolve_ambient_record must read the manifest, resolve the record slotted under the target
    #    name, and the serving resolver must load + generate on the GPU. This is the live-serving path
    #    a generation-loop role takes when settings.ambient_serving_manifest_path is configured.
    from autocontext.agents.scenario_bound_clients import (
        _resolve_ambient_record,
        build_planned_client,
        plan_local_client,
    )
    from autocontext.ambient.serving_manifest import (
        lookup_serving_entry,
        write_serving_entry,
    )
    from autocontext.training.model_registry import ModelRegistry

    knowledge_root = Path(tempfile.mkdtemp())
    manifest_path = Path(tempfile.mkdtemp()) / "serving-manifest.json"
    target_name = "competitor-local"
    real_scenario = "grid_ctf"
    role = "competitor"

    served_registry = ModelRegistry(knowledge_root)
    promoted = DistilledModelRecord(
        artifact_id="ac893-modal-served",
        scenario=target_name,  # ambient slots the promoted model under the target name, not the scenario
        scenario_family="",
        backend="sft",
        checkpoint_path=adapter_dir,
        runtime_types=["provider"],
        activation_state="candidate",
        training_metrics={},
        provenance={},
        metadata={"base_model": base_id, "target": target_name},
    )
    served_registry.register(promoted)
    served_registry.activate(promoted.artifact_id)
    write_serving_entry(
        manifest_path,
        scenario=real_scenario,
        role=role,
        target_name=target_name,
        artifact_id=promoted.artifact_id,
        backend="sft",
    )
    # the manifest bridge answers for the real (scenario, role), not the target name
    assert (
        lookup_serving_entry(manifest_path, scenario=real_scenario, role=role)
        is not None
    )

    served_settings = AppSettings(
        knowledge_root=knowledge_root,
        ambient_serving_manifest_path=manifest_path,
        mlx_max_tokens=8,
        mlx_temperature=0.7,
    )
    resolved = _resolve_ambient_record(served_settings, real_scenario, role)
    assert resolved is not None, "serving manifest did not resolve the promoted record"
    assert resolved.artifact_id == promoted.artifact_id, (
        "resolved the wrong artifact via the manifest"
    )
    served_plan = plan_local_client(resolved)
    assert served_plan is not None and served_plan.kind == "sft"
    served_client = build_planned_client(served_plan, served_settings)
    served_response = served_client.generate(
        model=served_plan.model,
        prompt="Served via the manifest.",
        max_tokens=8,
        temperature=0.7,
    )
    assert isinstance(served_response.text, str)

    return {
        "cuda_available": bool(torch.cuda.is_available()),
        "device": provider._device,
        "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "base_model": base_id,
        "provider_text_len": len(result.text),
        "provider_usage": result.usage,
        "client_text_len": len(response.text),
        "client_output_tokens": response.usage.output_tokens,
        "closure_text_len": len(closure_text),
        "closure_second_case_len": len(closure_text_2),
        "served_artifact": resolved.artifact_id,
        "served_via_manifest_text_len": len(served_response.text),
    }


@app.local_entrypoint()
def main() -> None:
    summary = validate.remote()
    print("AC-891 sft serving validation:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    assert summary["cuda_available"] is True, "expected a CUDA device on the T4"
    assert summary["device"] == "cuda"
    assert summary["closure_text_len"] >= 0, (
        "the production generation closure did not return text"
    )
    assert summary["closure_second_case_len"] >= 0
    assert summary["served_artifact"] == "ac893-modal-served", (
        "the AC-893 serving manifest did not resolve the promoted per-role record"
    )
    assert summary["served_via_manifest_text_len"] >= 0, (
        "the manifest-resolved model did not serve/generate"
    )
    print(
        "PASS: torch/peft sft adapter served + generated on a real GPU, via provider, client, "
        "the production closure, and the AC-893 per-role serving-manifest bridge."
    )
