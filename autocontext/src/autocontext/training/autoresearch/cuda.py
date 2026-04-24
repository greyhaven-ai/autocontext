"""CUDA/PyTorch training path for autoresearch distillation."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.prepare import save_tokenizer_json

logger = logging.getLogger(__name__)


def require_torch_cuda() -> Any:
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch with CUDA is required for --backend cuda. "
            "Install a CUDA-enabled torch build before running CUDA training."
        ) from exc

    cuda_module = getattr(torch, "cuda", None)
    if cuda_module is None or not bool(cuda_module.is_available()):
        raise RuntimeError("CUDA backend requires torch.cuda.is_available() to be true")
    return torch


def _create_torch_dataloader(
    token_ids: list[int],
    *,
    torch_module: Any,
    device: Any,
    seq_len: int,
    batch_size: int,
) -> list[tuple[Any, Any]]:
    stride = seq_len + 1
    total_seqs = len(token_ids) // stride
    usable_seqs = (total_seqs // batch_size) * batch_size
    total_tokens = usable_seqs * stride
    if total_tokens == 0:
        return []

    data = torch_module.tensor(token_ids[:total_tokens], dtype=torch_module.long, device=device)
    data = data.reshape(usable_seqs, stride)
    batches: list[tuple[Any, Any]] = []
    for batch_start in range(0, usable_seqs, batch_size):
        batch = data[batch_start : batch_start + batch_size]
        batches.append((batch[:, :seq_len], batch[:, 1 : seq_len + 1]))
    return batches


def _build_torch_model(cfg: Any, torch_module: Any) -> Any:
    nn_module = torch_module.nn

    class TorchGPTModel(nn_module.Module):  # type: ignore[misc, valid-type, name-defined]
        def __init__(self, model_cfg: Any) -> None:
            super().__init__()
            self.cfg = model_cfg
            self.embed = nn_module.Embedding(model_cfg.vocab_size, model_cfg.d_model)
            self.layers = nn_module.ModuleList(
                [
                    nn_module.TransformerEncoderLayer(
                        d_model=model_cfg.d_model,
                        nhead=model_cfg.n_heads,
                        dim_feedforward=model_cfg.d_model * 4,
                        activation="gelu",
                        batch_first=True,
                        norm_first=True,
                    )
                    for _ in range(model_cfg.depth)
                ]
            )
            self.norm = nn_module.LayerNorm(model_cfg.d_model)
            self.head = nn_module.Linear(model_cfg.d_model, model_cfg.vocab_size, bias=False)

        def forward(self, x: Any) -> Any:
            h = self.embed(x)
            seq_len = int(x.shape[1])
            mask = torch_module.triu(
                torch_module.full((seq_len, seq_len), float("-inf"), device=x.device),
                diagonal=1,
            )
            for layer in self.layers:
                h = layer(h, src_mask=mask)
            return self.head(self.norm(h))

    return TorchGPTModel(cfg)


def _count_torch_params_million(model: Any) -> float:
    return sum(float(param.numel()) for param in model.parameters()) / 1_000_000.0


def _torch_peak_memory_mb(torch_module: Any, device: Any) -> float:
    try:
        return float(torch_module.cuda.max_memory_allocated(device)) / (1024.0 * 1024.0)
    except Exception:
        logger.debug("training.autoresearch.cuda: suppressed torch memory read", exc_info=True)
        return 0.0


def _save_torch_checkpoint_bundle(
    *,
    model: Any,
    cfg: Any,
    tokenizer: Any,
    output_dir: Path,
    torch_module: Any,
) -> None:
    config_payload = {
        key: getattr(cfg, key)
        for key in ("depth", "aspect_ratio", "head_dim", "n_kv_heads", "vocab_size", "seq_len")
        if hasattr(cfg, key)
    }
    config_payload["backend"] = "cuda"
    config_payload["format"] = "torch_state_dict"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "config.json").write_text(
        json.dumps(config_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    save_tokenizer_json(tokenizer, output_dir / "tokenizer.json")
    torch_module.save(
        {"config": config_payload, "state_dict": model.state_dict()},
        output_dir / "model.pt",
    )


def _resolve_scenario_name(scenario: Any) -> str:
    value = getattr(scenario, "name", None)
    if isinstance(value, str) and value.strip():
        return value
    scenario_name = str(scenario.__class__.__name__)
    return scenario_name.lower()


def _resolve_scenario_context(scenario: Any) -> str:
    task_prompt = getattr(scenario, "get_task_prompt", None)
    if callable(task_prompt):
        try:
            prompt = task_prompt()
        except TypeError:
            prompt = None
        if isinstance(prompt, str):
            return prompt

    description = getattr(scenario, "description", None)
    if isinstance(description, str):
        return description
    return ""


def _extract_strategy_json(text: str) -> dict[str, Any] | None:
    match = re.search(r"<\|strategy\|>(.*?)(?:<\||$)", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            return None
    try:
        return json.loads(text)  # type: ignore[no-any-return]
    except json.JSONDecodeError:
        return None


def _generate_torch_strategy_text(
    *,
    model: Any,
    tokenizer: Any,
    scenario: Any,
    torch_module: Any,
    device: Any,
    seed: int,
    max_new_tokens: int = 128,
) -> str:
    prompt = (
        f"<|scenario|>{_resolve_scenario_name(scenario)}"
        f"<|context|>{_resolve_scenario_context(scenario)}"
        "<|strategy|>"
    )
    token_ids = list(tokenizer.encode(prompt))
    seq_len = int(model.cfg.seq_len)
    end_token_id = getattr(tokenizer, "end_token_id", None)
    torch_module.manual_seed(seed)

    model.eval()
    with torch_module.no_grad():
        for _ in range(max_new_tokens):
            window = token_ids[-seq_len:]
            x = torch_module.tensor([window], dtype=torch_module.long, device=device)
            logits = model(x)
            next_token = int(torch_module.argmax(logits[:, -1, :], dim=-1).item())
            token_ids.append(next_token)
            if end_token_id is not None and next_token == end_token_id:
                break
    return str(tokenizer.decode(token_ids))


def _assess_torch_strategy_quality(
    *,
    model: Any,
    tokenizer: Any,
    scenario: Any,
    torch_module: Any,
    device: Any,
    n_samples: int,
) -> dict[str, float]:
    scores: list[float] = []
    valid_count = 0
    is_game = hasattr(scenario, "execute_match")

    for i in range(n_samples):
        try:
            raw_output = _generate_torch_strategy_text(
                model=model,
                tokenizer=tokenizer,
                scenario=scenario,
                torch_module=torch_module,
                device=device,
                seed=i,
            )
            strategy = _extract_strategy_json(raw_output)
            if strategy is None:
                continue
            valid_count += 1
            if is_game:
                result = scenario.execute_match(strategy, seed=i)
                scores.append(result.score)
            else:
                result = scenario.evaluate_output(output=json.dumps(strategy))
                scores.append(result.score)
        except Exception:
            logger.debug("training.autoresearch.cuda: suppressed assessment error", exc_info=True)

    return {
        "avg_score": sum(scores) / len(scores) if scores else 0.0,
        "valid_rate": valid_count / n_samples if n_samples > 0 else 0.0,
    }


def run_cuda_training(
    *,
    scenario_name: str,
    data_path: Path,
    output_dir: Path,
    time_budget: int,
    memory_limit_mb: int,
    train_steps: int = 8,
    batch_size: int = 4,
    learning_rate: float = 1e-3,
    seq_len: int = 128,
    assess_samples: int = 8,
) -> dict[str, float]:
    torch_module = require_torch_cuda()
    device = torch_module.device("cuda")

    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.training.autoresearch.train import ModelConfig, _all_records, _build_corpus, _peak_memory_mb
    try:
        from prepare import format_example, train_tokenizer  # type: ignore[import-not-found]
    except ImportError:
        from autocontext.training.autoresearch.prepare import format_example, train_tokenizer

    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")

    records = _all_records(data_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = output_dir / "corpus.txt"
    corpus_path.write_text(_build_corpus(records), encoding="utf-8")
    tokenizer = train_tokenizer(corpus_path)

    token_ids: list[int] = []
    for record in records:
        token_ids.extend(
            tokenizer.encode(
                format_example(
                    scenario=str(record["scenario"]),
                    context=json.dumps(record.get("context", {}), sort_keys=True),
                    strategy_json=json.dumps(record["strategy"], sort_keys=True),
                    score=float(record["score"]),
                )
            )
        )

    batches = _create_torch_dataloader(
        token_ids,
        torch_module=torch_module,
        device=device,
        seq_len=seq_len,
        batch_size=batch_size,
    )
    if not batches:
        raise ValueError("not enough tokenized training data for a single batch")

    cfg = ModelConfig(seq_len=seq_len)
    model = _build_torch_model(cfg, torch_module).to(device)
    optimizer = torch_module.optim.AdamW(model.parameters(), lr=learning_rate)
    try:
        torch_module.cuda.reset_peak_memory_stats(device)
    except Exception:
        logger.debug("training.autoresearch.cuda: suppressed torch memory reset", exc_info=True)

    started = time.perf_counter()
    deadline = started + max(float(time_budget) - 1.0, 1.0)
    steps_completed = 0
    model.train()
    for step in range(train_steps):
        if time.perf_counter() >= deadline:
            break
        x, y = batches[step % len(batches)]
        optimizer.zero_grad(set_to_none=True)
        logits = model(x)
        loss = torch_module.nn.functional.cross_entropy(
            logits.reshape(-1, cfg.vocab_size),
            y.reshape(-1),
        )
        loss.backward()
        optimizer.step()
        steps_completed += 1

    scenario = SCENARIO_REGISTRY[scenario_name]()
    metrics = _assess_torch_strategy_quality(
        model=model,
        tokenizer=tokenizer,
        scenario=scenario,
        torch_module=torch_module,
        device=device,
        n_samples=assess_samples,
    )
    _save_torch_checkpoint_bundle(
        model=model,
        cfg=cfg,
        tokenizer=tokenizer,
        output_dir=output_dir,
        torch_module=torch_module,
    )

    peak_memory_mb = _torch_peak_memory_mb(torch_module, device) or _peak_memory_mb()
    return {
        "avg_score": metrics["avg_score"],
        "valid_rate": metrics["valid_rate"],
        "training_seconds": time.perf_counter() - started,
        "peak_memory_mb": min(peak_memory_mb, float(memory_limit_mb)),
        "num_steps": float(steps_completed),
        "num_params_m": _count_torch_params_million(model),
        "depth": float(cfg.depth),
    }
