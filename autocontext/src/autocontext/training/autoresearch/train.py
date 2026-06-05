"""Baseline MLX/CUDA GPT model and training loop for autoresearch distillation.

All MLX code is behind import guards so the module can be imported
(for type checking, tests, etc.) even when MLX is not installed. CUDA support
imports PyTorch only inside the CUDA execution path.
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
import time
from pathlib import Path
from typing import Any

from autocontext.training import HAS_MLX
from autocontext.training.autoresearch.data_selection import prepare_training_records

# Model architecture + checkpoint I/O were extracted to model.py; re-exported here so
# existing importers (cuda, MLXProvider, tests) keep their `from ...train import` paths.
from autocontext.training.autoresearch.model import (  # noqa: F401
    GPTModel,
    ModelConfig,
    compute_loss,
    load_checkpoint,
    save_checkpoint,
    save_inference_bundle,
)
from autocontext.training.autoresearch.prepare import BASE_VOCAB_SIZE
from autocontext.training.autoresearch.sequence_format import NUM_QUALITY_BUCKETS

logger = logging.getLogger(__name__)

# Model-shape hyperparameters. Deliberately kept in train.py (the model architecture
# itself lives in model.py) so the training agent's revision loop can tune model shape:
# the deterministic variant regex-edits these and the LLM agent sees + edits them. They
# override the ModelConfig defaults where the model is constructed.
MODEL_DEPTH = 4
MODEL_ASPECT_RATIO = 64
MODEL_HEAD_DIM = 64
MODEL_N_KV_HEADS = 4


# === Summary formatting (always available) ===


def format_summary(
    *,
    avg_score: float,
    valid_rate: float,
    training_seconds: float,
    peak_memory_mb: float,
    num_steps: int,
    num_params_m: float,
    depth: int,
    val_loss: float | None = None,
    num_records: float | None = None,
) -> str:
    """Format the training results summary block.

    This block is printed to stdout and parsed by the autoresearch agent and by
    TrainingRunner.parse_summary. ``val_loss`` and ``num_records`` are included only
    when available so existing callers stay unchanged.
    """
    val_loss_line = f"val_loss: {val_loss:.4f}\n" if val_loss is not None else ""
    num_records_line = f"num_records: {int(num_records)}\n" if num_records is not None else ""
    return (
        "=== TRAINING SUMMARY ===\n"
        f"avg_score: {avg_score:.4f}\n"
        f"valid_rate: {valid_rate:.4f}\n"
        f"{val_loss_line}"
        f"{num_records_line}"
        f"training_seconds: {training_seconds:.1f}\n"
        f"peak_memory_mb: {peak_memory_mb:.1f}\n"
        f"num_steps: {num_steps}\n"
        f"num_params_M: {num_params_m:.2f}\n"
        f"depth: {depth}\n"
        "========================"
    )


def _peak_memory_mb() -> float:
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        if usage > 1_000_000:
            return float(usage) / (1024.0 * 1024.0)
        return float(usage) / 1024.0
    except Exception:
        logger.debug("training.autoresearch.train: caught Exception", exc_info=True)
        return 0.0


def _count_params_million(params: Any) -> float:
    if HAS_MLX:
        import mlx.core as mx  # type: ignore[import-not-found]

        if isinstance(params, dict):
            return sum(_count_params_million(v) for v in params.values())
        if isinstance(params, list):
            return sum(_count_params_million(v) for v in params)
        return float(mx.array(params).size) / 1_000_000.0
    return 0.0


def _all_records(data_path: Path) -> list[dict[str, Any]]:
    try:
        from prepare import load_jsonl  # type: ignore[import-not-found]
    except ImportError:
        from autocontext.training.autoresearch.prepare import load_jsonl

    train_records, val_records = load_jsonl(data_path)
    records = list(train_records) or list(val_records)
    if not records:
        raise ValueError(f"no training records found in {data_path}")
    return records


def _build_corpus(records: list[dict[str, Any]], *, score_conditioned: bool = False) -> str:
    try:
        from prepare import TrainingExample  # type: ignore[import-not-found]
    except ImportError:
        from autocontext.training.autoresearch.prepare import TrainingExample

    return "\n".join(TrainingExample.from_record(record).to_sequence(score_conditioned=score_conditioned) for record in records)


def _run_mlx_training(
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
    val_select: bool = False,
    elite_fraction: float = 1.0,
    dedupe: bool = False,
    dedupe_near_threshold: float = 1.0,
    score_conditioned: bool = False,
    loss_weight_mode: str = "uniform",
    loss_weight_temperature: float = 1.0,
    augmenter_spec: str = "",
    vocab_size: int = BASE_VOCAB_SIZE,
    collect_samples_path: Path | None = None,
) -> dict[str, float]:
    _preflight_backend_deps("mlx")
    if not HAS_MLX:
        raise RuntimeError("MLX is required for local training. Install with: uv sync --group dev --extra mlx")

    import mlx.core as mx  # type: ignore[import-not-found]
    import mlx.nn as nn  # type: ignore[import-not-found]
    import mlx.optimizers as optim  # type: ignore[import-not-found]
    from mlx.utils import tree_map  # type: ignore[import-not-found]

    from autocontext.scenarios import SCENARIO_REGISTRY

    try:
        from prepare import (  # type: ignore[import-not-found]
            TrainingExample,
            assess_strategy_quality,
            build_special_tokens,
            iter_masked_batches,
            load_jsonl,
            score_loss_weights,
            train_tokenizer,
        )
    except ImportError:
        from autocontext.training.autoresearch.prepare import (
            TrainingExample,
            assess_strategy_quality,
            build_special_tokens,
            iter_masked_batches,
            load_jsonl,
            score_loss_weights,
            train_tokenizer,
        )

    if scenario_name not in SCENARIO_REGISTRY:
        raise ValueError(f"unknown scenario: {scenario_name}")

    # Hold out the validation split: train uses the train split; val drives val_loss + best-checkpoint selection.
    train_records, val_records = load_jsonl(data_path)
    if not train_records:
        train_records, val_records = list(val_records), []
    if not train_records:
        raise ValueError(f"no training records found in {data_path}")

    # Augment + curate the TRAIN split only (val stays held out): expand, then dedupe + elite-filter.
    train_records = prepare_training_records(
        train_records,
        augmenter_spec=augmenter_spec,
        elite_fraction=elite_fraction,
        dedupe=dedupe,
        near_threshold=dedupe_near_threshold,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    corpus_path = output_dir / "corpus.txt"
    corpus_path.write_text(_build_corpus(train_records, score_conditioned=score_conditioned), encoding="utf-8")
    tokenizer = train_tokenizer(corpus_path, vocab_size=vocab_size, score_conditioned=score_conditioned)

    base_vocab = int(getattr(tokenizer, "base_vocab_size", BASE_VOCAB_SIZE))
    strategy_token_id = build_special_tokens(base_vocab)["<|strategy|>"]
    pad_token_id = getattr(tokenizer, "end_token_id", 0) or 0

    # Per-example completion-masked batches; ``weights`` (RWR) scales TRAIN loss by score (val stays unweighted).
    def _masked_batches(recs: list[dict[str, Any]], *, weights: list[float] | None = None) -> list[Any]:
        seqs = [tokenizer.encode(TrainingExample.from_record(r).to_sequence(score_conditioned=score_conditioned)) for r in recs]
        kw = dict(seq_len=seq_len, batch_size=batch_size, pad_token_id=pad_token_id, strategy_token_id=strategy_token_id)
        return list(iter_masked_batches(seqs, weights=weights, **kw))

    scores = [float(r.get("score", 0.0)) for r in train_records]
    train_weights = score_loss_weights(scores, mode=loss_weight_mode, temperature=loss_weight_temperature)
    batches = _masked_batches(train_records, weights=train_weights)
    if not batches:
        raise ValueError("not enough tokenized training data for a single batch")
    val_batches = _masked_batches(val_records)

    # Size the model head/embedding to the tokenizer (grows one slot only when score-conditioned).
    # Model shape comes from the MODEL_* knobs above (kept in train.py so the agent loop can tune it).
    cfg = ModelConfig(
        depth=MODEL_DEPTH,
        aspect_ratio=MODEL_ASPECT_RATIO,
        head_dim=MODEL_HEAD_DIM,
        n_kv_heads=MODEL_N_KV_HEADS,
        seq_len=seq_len,
        vocab_size=int(tokenizer.vocab_size),
    )
    model = GPTModel(cfg)
    optimizer = optim.AdamW(learning_rate=learning_rate)
    loss_and_grad = nn.value_and_grad(model, compute_loss)

    def _mean_val_loss() -> float | None:
        if not val_batches:
            return None
        total = 0.0
        for vx, vy, vmask, _vw in val_batches:  # val stays unweighted (comparable val_loss)
            vloss = compute_loss(model, vx, vy, vmask)
            mx.eval(vloss)  # noqa: S307
            total += float(vloss.item())
        return total / len(val_batches)

    best_val = float("inf")
    best_params = None
    since_improve = 0
    patience = max(3, train_steps // 4)
    started = time.perf_counter()
    deadline = started + max(float(time_budget) - 1.0, 1.0)
    steps_completed = 0
    for step in range(train_steps):
        if time.perf_counter() >= deadline:
            break
        x, y, loss_mask, ex_weights = batches[step % len(batches)]
        loss, grads = loss_and_grad(model, x, y, loss_mask, ex_weights)
        optimizer.update(model, grads)
        mx.eval(model.parameters(), optimizer.state, loss)  # noqa: S307
        steps_completed += 1
        if val_select and val_batches:
            current = _mean_val_loss()
            if current is not None and current < best_val - 1e-6:
                best_val = current
                best_params = tree_map(lambda a: mx.array(a), model.parameters())
                since_improve = 0
            else:
                since_improve += 1
                if since_improve >= patience:
                    break  # early stop: validation loss plateaued

    val_loss: float | None
    if val_select and best_params is not None:
        model.update(best_params)  # restore the best-by-val-loss checkpoint
        mx.eval(model.parameters())  # noqa: S307
        val_loss = best_val
    else:
        val_loss = _mean_val_loss()

    scenario = SCENARIO_REGISTRY[scenario_name]()
    metrics = assess_strategy_quality(
        model=model,
        tokenizer=tokenizer,
        scenario=scenario,
        n_samples=assess_samples,
        temperature=assess_temperature,
        top_k=assess_top_k,
        # condition on the top quality bucket when trained score-conditioned
        target_quality=(NUM_QUALITY_BUCKETS - 1) if score_conditioned else None,
        collect_path=collect_samples_path,
    )
    save_inference_bundle(model, cfg, tokenizer, output_dir)

    return {
        "avg_score": metrics["avg_score"],
        "valid_rate": metrics["valid_rate"],
        "val_loss": float(val_loss) if val_loss is not None else float("nan"),
        "training_seconds": time.perf_counter() - started,
        "peak_memory_mb": min(_peak_memory_mb(), float(memory_limit_mb)),
        "num_steps": float(steps_completed),
        "num_records": float(len(train_records)),  # records used after curation
        "num_params_m": _count_params_million(model.parameters()),
        "depth": float(cfg.depth),
    }


def _preflight_backend_deps(backend: str) -> None:
    """Fail fast if a backend's runtime dependencies are missing.

    Training runs do work for tens of seconds before the checkpoint is written,
    so a missing ``numpy``/``safetensors`` used only at save time would otherwise
    crash *after* all that work. Check imports up front with an actionable message.
    """

    required = {
        "mlx": ["mlx", "rustbpe", "tiktoken", "numpy", "safetensors"],
        "cuda": ["torch", "numpy", "safetensors"],
        "mlxlm": ["mlx_lm"],
    }.get(backend, [])
    missing = [m for m in required if importlib.util.find_spec(m) is None]
    if missing:
        extra = {"mlx": "mlx", "cuda": "cuda", "mlxlm": "mlxlm"}.get(backend, backend)
        lead = {
            "mlx": "MLX is required for local training",
            "cuda": "CUDA (torch) is required for local training",
            "mlxlm": "mlx-lm is required for the pretrained-finetune backend",
        }.get(backend, f"missing dependencies for '{backend}' training backend")
        raise RuntimeError(f"{lead}; missing: {', '.join(missing)}. Install with: uv sync --group dev --extra {extra}")


def run_training(
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
    val_select: bool = False,
    elite_fraction: float = 1.0,
    dedupe: bool = False,
    dedupe_near_threshold: float = 1.0,
    score_conditioned: bool = False,
    loss_weight_mode: str = "uniform",
    loss_weight_temperature: float = 1.0,
    augmenter_spec: str = "",
    vocab_size: int = BASE_VOCAB_SIZE,
    base_model: str = "",
    fine_tune_type: str = "lora",
    num_layers: int = 8,
    collect_samples_path: Path | None = None,
    backend: str = "mlx",
) -> dict[str, float]:
    # Reject out-of-range curation before any work (select_top_fraction would clamp to one record).
    if not 0.0 < elite_fraction <= 1.0:
        raise ValueError(f"elite_fraction must be in (0, 1], got {elite_fraction}")
    if not 0.0 < dedupe_near_threshold <= 1.0:
        raise ValueError(f"dedupe_near_threshold must be in (0, 1], got {dedupe_near_threshold}")
    # Enforce the BPE vocab lower bound here (the public API + subprocess entry), not only in the
    # Typer wrapper, so direct callers and `python train.py --vocab-size N` can't flow a too-small
    # base vocab into the tokenizer (special-token ids would collide with / fall below the byte base).
    if vocab_size < 256:
        raise ValueError(f"vocab_size must be >= 256 (the byte-level BPE base), got {vocab_size}")

    normalized_backend = backend.strip().lower()
    # Shared args; backend extras added per dispatch (each entry runs its own dep preflight).
    common: dict[str, Any] = dict(
        scenario_name=scenario_name,
        data_path=data_path,
        output_dir=output_dir,
        time_budget=time_budget,
        memory_limit_mb=memory_limit_mb,
        train_steps=train_steps,
        batch_size=batch_size,
        learning_rate=learning_rate,
        assess_samples=assess_samples,
        assess_temperature=assess_temperature,
        elite_fraction=elite_fraction,
        dedupe=dedupe,
        dedupe_near_threshold=dedupe_near_threshold,
        score_conditioned=score_conditioned,
        loss_weight_mode=loss_weight_mode,  # reward-weighted regression (mlx/cuda; mlxlm rejects non-uniform)
        loss_weight_temperature=loss_weight_temperature,
        augmenter_spec=augmenter_spec,  # symmetry/transform augmentation seam (all backends)
        vocab_size=vocab_size,  # BPE tokenizer target vocab (mlx/cuda; mlxlm uses the pretrained tokenizer)
    )
    if normalized_backend == "mlx":
        return _run_mlx_training(
            **common, seq_len=seq_len, assess_top_k=assess_top_k, val_select=val_select, collect_samples_path=collect_samples_path
        )
    if collect_samples_path is not None:
        raise NotImplementedError("collect_samples_path (self-improving loop) is currently MLX-only")
    if normalized_backend == "cuda":
        if val_select:
            raise ValueError("val_select is currently MLX-only; omit it for the cuda backend")
        from autocontext.training.autoresearch.cuda import run_cuda_training

        return run_cuda_training(**common, seq_len=seq_len, assess_top_k=assess_top_k)
    if normalized_backend == "mlxlm":
        if loss_weight_mode != "uniform":
            raise NotImplementedError("loss-weighting (reward-weighted regression) is currently mlx/cuda-only")
        if vocab_size != BASE_VOCAB_SIZE:
            raise NotImplementedError(
                "--vocab-size applies to the from-scratch mlx/cuda backends; mlxlm uses the pretrained model's tokenizer"
            )
        from autocontext.training.autoresearch.mlxlm_backend import DEFAULT_BASE_MODEL, run_mlxlm_training

        mlxlm_kwargs = {k: v for k, v in common.items() if not (k.startswith("loss_weight") or k == "vocab_size")}
        return run_mlxlm_training(
            **mlxlm_kwargs,
            assess_top_k=assess_top_k,
            base_model=base_model or DEFAULT_BASE_MODEL,
            fine_tune_type=fine_tune_type,
            num_layers=num_layers,
        )
    raise ValueError("unsupported training backend: expected 'mlx', 'cuda', or 'mlxlm'")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local autoresearch MLX or CUDA training")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--data", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--backend", choices=("mlx", "cuda", "mlxlm"), default="mlx")
    parser.add_argument("--time-budget", type=int, default=300)
    parser.add_argument("--memory-limit", type=int, default=16384)
    parser.add_argument("--train-steps", type=int, default=8)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--seq-len", type=int, default=128)
    parser.add_argument("--assess-samples", type=int, default=8)
    parser.add_argument("--assess-temperature", type=float, default=0.0, help="assessment sampling temp (<=0 greedy)")
    parser.add_argument("--assess-top-k", type=int, default=0, help="optional top-k truncation when sampling")
    parser.add_argument("--val-select", action="store_true", help="keep best-by-val-loss checkpoint + early-stop (MLX)")
    parser.add_argument("--elite-fraction", type=float, default=1.0, help="train on only the top fraction by score")
    parser.add_argument("--dedupe", action="store_true", help="drop duplicate constructions (keep highest-scoring)")
    parser.add_argument("--dedupe-near-threshold", type=float, default=1.0, help="with --dedupe, drop near-dups")
    parser.add_argument("--score-conditioned", action="store_true", help="emit quality token; generate conditioned on top bucket")
    parser.add_argument("--loss-weight-by-score", choices=("uniform", "linear", "softmax"), default="uniform")
    parser.add_argument("--loss-weight-temperature", type=float, default=1.0)
    parser.add_argument("--augmenter", default="", help="record augmenter spec 'module:function' (empty = none)")
    parser.add_argument("--vocab-size", type=int, default=BASE_VOCAB_SIZE, help="BPE tokenizer target vocab (mlx/cuda)")
    parser.add_argument("--base-model", default="", help="mlxlm backend: pretrained base model (empty = default)")
    parser.add_argument("--fine-tune-type", choices=("lora", "dora", "full"), default="lora", help="mlxlm backend")
    parser.add_argument("--num-layers", type=int, default=8, help="mlxlm backend: layers to fine-tune")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        metrics = run_training(
            scenario_name=args.scenario,
            data_path=Path(args.data),
            output_dir=Path(args.output_dir),
            time_budget=args.time_budget,
            memory_limit_mb=args.memory_limit,
            train_steps=args.train_steps,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            seq_len=args.seq_len,
            assess_samples=args.assess_samples,
            assess_temperature=args.assess_temperature,
            assess_top_k=args.assess_top_k,
            val_select=args.val_select,
            elite_fraction=args.elite_fraction,
            dedupe=args.dedupe,
            dedupe_near_threshold=args.dedupe_near_threshold,
            score_conditioned=args.score_conditioned,
            loss_weight_mode=args.loss_weight_by_score,
            loss_weight_temperature=args.loss_weight_temperature,
            augmenter_spec=args.augmenter,
            vocab_size=args.vocab_size,
            base_model=args.base_model,
            fine_tune_type=args.fine_tune_type,
            num_layers=args.num_layers,
            backend=args.backend,
        )
    except Exception as exc:
        logger.debug("training.autoresearch.train: caught Exception", exc_info=True)
        print(f"Training failed: {exc}", file=sys.stderr)
        return 1

    print(
        format_summary(
            avg_score=metrics["avg_score"],
            valid_rate=metrics["valid_rate"],
            training_seconds=metrics["training_seconds"],
            peak_memory_mb=metrics["peak_memory_mb"],
            num_steps=int(metrics["num_steps"]),
            num_params_m=metrics["num_params_m"],
            depth=int(metrics["depth"]),
            val_loss=metrics.get("val_loss"),
            num_records=metrics.get("num_records"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
