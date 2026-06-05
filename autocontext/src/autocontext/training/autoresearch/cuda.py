"""CUDA/PyTorch training path for autoresearch distillation."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from autocontext.training.autoresearch.prepare import save_tokenizer_json
from autocontext.training.autoresearch.sequence_format import (
    TrainingExample,
    build_generation_prompt,
    build_masked_example,
    build_special_tokens,
    extract_strategy,
    generation_logit_mask_values,
)

logger = logging.getLogger(__name__)


def require_torch_cuda() -> Any:
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "PyTorch with CUDA is required for --backend cuda. Install a CUDA-enabled torch build before running CUDA training."
        ) from exc

    cuda_module = getattr(torch, "cuda", None)
    if cuda_module is None or not bool(cuda_module.is_available()):
        raise RuntimeError("CUDA backend requires torch.cuda.is_available() to be true")
    return torch


def _create_torch_masked_dataloader(
    sequences: list[list[int]],
    *,
    torch_module: Any,
    device: Any,
    seq_len: int,
    batch_size: int,
    pad_token_id: int,
    strategy_token_id: int,
    weights: list[float] | None = None,
) -> list[tuple[Any, Any, Any, Any]]:
    """Per-example, completion-masked torch batches (mirrors prepare.iter_masked_batches).

    No cross-example packing (document boundaries respected), no tail dropped, and a
    completion-only loss mask (prompt + padding zeroed). Returns
    ``(x, y, mask, example_weights)`` tuples.

    ``weights`` (reward-weighted regression) is returned as a per-example weight
    vector ``example_weights`` (shape ``(batch,)``), NOT folded into the per-token
    mask, so the loss can weight each example's *mean* completion loss. It is ``None``
    when ``weights`` is ``None`` or all ``1.0`` (byte-identical, unweighted).
    """
    do_weight = weights is not None and any(w != 1.0 for w in weights)
    ws = weights if weights is not None else [1.0] * len(sequences)
    built: list[tuple[list[int], list[int], list[int], float]] = []
    for tokens, w in zip(sequences, ws, strict=True):
        example = build_masked_example(
            tokens,
            seq_len=seq_len,
            pad_token_id=pad_token_id,
            strategy_token_id=strategy_token_id,
        )
        if example is not None:
            inp, tgt, mask = example
            built.append((inp, tgt, mask, w))

    batches: list[tuple[Any, Any, Any, Any]] = []
    for start in range(0, len(built), batch_size):
        chunk = built[start : start + batch_size]
        x = torch_module.tensor([c[0] for c in chunk], dtype=torch_module.long, device=device)
        y = torch_module.tensor([c[1] for c in chunk], dtype=torch_module.long, device=device)
        mask = torch_module.tensor([c[2] for c in chunk], dtype=torch_module.float32, device=device)
        wv = torch_module.tensor([c[3] for c in chunk], dtype=torch_module.float32, device=device) if do_weight else None
        batches.append((x, y, mask, wv))
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


def _generate_torch_strategy_text(
    *,
    model: Any,
    tokenizer: Any,
    scenario: Any,
    torch_module: Any,
    device: Any,
    seed: int,
    max_new_tokens: int = 128,
    temperature: float = 0.0,
    top_k: int = 0,
    target_quality: int | None = None,
) -> str:
    prompt = build_generation_prompt(scenario, target_quality=target_quality)
    token_ids = list(tokenizer.encode(prompt))
    seq_len = int(model.cfg.seq_len)
    vocab_size = int(getattr(model.cfg, "vocab_size", 0))
    end_token_id = getattr(tokenizer, "end_token_id", None)
    torch_module.manual_seed(seed)

    # Mask phantom ids + structural specials (mirrors the MLX path) so neither
    # greedy nor sampled generation emits an undecodable id or restarts the header.
    mask = None
    if vocab_size > 0:
        mask = torch_module.tensor(
            generation_logit_mask_values(tokenizer, vocab_size, block_structural_specials=True),
            dtype=torch_module.float32,
            device=device,
        )
    sampling = temperature is not None and temperature > 0.0

    model.eval()
    with torch_module.no_grad():
        for _ in range(max_new_tokens):
            window = token_ids[-seq_len:]
            x = torch_module.tensor([window], dtype=torch_module.long, device=device)
            next_logits = model(x)[:, -1, :]
            if mask is not None:
                next_logits = next_logits + mask
            if sampling:
                scaled = next_logits / float(temperature)
                if top_k and top_k > 0:
                    k = min(int(top_k), int(scaled.shape[-1]))
                    kth = torch_module.topk(scaled, k, dim=-1).values[..., -1, None]
                    scaled = torch_module.where(scaled < kth, torch_module.full_like(scaled, float("-inf")), scaled)
                probs = torch_module.softmax(scaled, dim=-1)
                next_token = int(torch_module.multinomial(probs, num_samples=1).item())
            else:
                next_token = int(torch_module.argmax(next_logits, dim=-1).item())
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
    temperature: float = 0.0,
    top_k: int = 0,
    target_quality: int | None = None,
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
                temperature=temperature,
                top_k=top_k,
                target_quality=target_quality,
            )
            strategy = extract_strategy(raw_output)
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
    assess_temperature: float = 0.0,
    assess_top_k: int = 0,
    elite_fraction: float = 1.0,
    dedupe: bool = False,
    dedupe_near_threshold: float = 1.0,
    score_conditioned: bool = False,
    loss_weight_mode: str = "uniform",
    loss_weight_temperature: float = 1.0,
    augmenter_spec: str = "",
) -> dict[str, float]:
    from autocontext.training.autoresearch.data_selection import prepare_training_records
    from autocontext.training.autoresearch.sequence_format import NUM_QUALITY_BUCKETS, score_loss_weights
    from autocontext.training.autoresearch.train import _preflight_backend_deps

    _preflight_backend_deps("cuda")
    torch_module = require_torch_cuda()
    device = torch_module.device("cuda")

    from autocontext.scenarios import SCENARIO_REGISTRY
    from autocontext.training.autoresearch.train import ModelConfig, _all_records, _build_corpus, _peak_memory_mb

    try:
        from prepare import train_tokenizer  # type: ignore[import-not-found]
    except ImportError:
        from autocontext.training.autoresearch.prepare import train_tokenizer

    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")

    records = prepare_training_records(
        _all_records(data_path),
        augmenter_spec=augmenter_spec,
        elite_fraction=elite_fraction,
        dedupe=dedupe,
        near_threshold=dedupe_near_threshold,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = output_dir / "corpus.txt"
    corpus_path.write_text(_build_corpus(records, score_conditioned=score_conditioned), encoding="utf-8")
    tokenizer = train_tokenizer(corpus_path, score_conditioned=score_conditioned)

    sequences = [
        tokenizer.encode(TrainingExample.from_record(record).to_sequence(score_conditioned=score_conditioned))
        for record in records
    ]
    base_vocab = int(getattr(tokenizer, "base_vocab_size", 8192))
    strategy_token_id = build_special_tokens(base_vocab)["<|strategy|>"]
    pad_token_id = getattr(tokenizer, "end_token_id", 0) or 0
    # Reward-weighted regression: scale each example's loss by its score-derived weight.
    loss_weights = score_loss_weights(
        [float(record.get("score", 0.0)) for record in records],
        mode=loss_weight_mode,
        temperature=loss_weight_temperature,
    )
    batches = _create_torch_masked_dataloader(
        sequences,
        torch_module=torch_module,
        device=device,
        seq_len=seq_len,
        batch_size=batch_size,
        pad_token_id=pad_token_id,
        strategy_token_id=strategy_token_id,
        weights=loss_weights,
    )
    if not batches:
        raise ValueError("not enough tokenized training data for a single batch")

    cfg = ModelConfig(seq_len=seq_len, vocab_size=int(tokenizer.vocab_size))
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
        x, y, loss_mask, ex_weights = batches[step % len(batches)]
        optimizer.zero_grad(set_to_none=True)
        logits = model(x)
        batch_n, seq_n = y.shape
        per_token = torch_module.nn.functional.cross_entropy(
            logits.reshape(-1, cfg.vocab_size),
            y.reshape(-1),
            reduction="none",
        )
        if ex_weights is None:
            mask_flat = loss_mask.reshape(-1)
            loss = (per_token * mask_flat).sum() / mask_flat.sum().clamp(min=1.0)
        else:
            # Per-example (RWR): weight each example's mean completion loss, then average.
            pt = per_token.reshape(batch_n, seq_n)
            per_example_loss = (pt * loss_mask).sum(dim=1) / loss_mask.sum(dim=1).clamp(min=1.0)
            loss = (per_example_loss * ex_weights).sum() / ex_weights.sum().clamp(min=1.0)
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
        temperature=assess_temperature,
        top_k=assess_top_k,
        target_quality=(NUM_QUALITY_BUCKETS - 1) if score_conditioned else None,
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
        "num_records": float(len(records)),  # records used after curation
        "num_params_m": _count_torch_params_million(model),
        "depth": float(cfg.depth),
    }
