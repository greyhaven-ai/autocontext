"""Canonical sequence-format contract for autoresearch training (pure domain).

This is the single source of truth for how a training record becomes a token
sequence, how special tokens are laid out, how a generation prompt is built, how
a generated strategy is parsed back out, and the decodable/structural logit mask.

It is pure: no MLX, no torch, no import of ``prepare``/``train``/``cuda`` (so it can
be reused by every backend and by ``MLXProvider`` without import cycles). ``prepare``
re-exports these names for backward compatibility; ``cuda`` and the providers consume
them directly instead of duplicating the format.

Frontier note: keeping the format in one value object (``TrainingExample``) is what
lets later work move a quality/return control token into the sequence (Decision
Transformer / Quark style) by editing exactly one place.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Vocab + special-token layout
# ---------------------------------------------------------------------------

BASE_VOCAB_SIZE = 8192

SPECIAL_TOKEN_STRINGS = (
    "<|scenario|>",
    "<|context|>",
    "<|strategy|>",
    "<|score|>",
    "<|end|>",
)

# Quality/return control token (Decision-Transformer / Quark style). It is GATED
# behind score-conditioned training: appended last (id = base + 5) only when
# ``include_quality`` is set, so default training keeps the original 5-token vocab
# (no change to the default model head/embedding size).
QUALITY_TOKEN = "<|quality|>"

# Number of discrete quality buckets the score is quantized into for conditioning.
NUM_QUALITY_BUCKETS = 5

# rustbpe / tiktoken split pattern (GPT-style). Part of the tokenizer contract.
_BPE_PAT = (
    r"(?i:'s|'t|'re|'ve|'m|'ll|'d)"
    r"|[^\r\n\p{L}\p{N}]?\p{L}+"
    r"|\p{N}{1,3}"
    r"| ?[^\s\p{L}\p{N}]+[\r\n]*"
    r"|\s*[\r\n]+"
    r"|\s+"
)

_STRATEGY_RE = re.compile(r"<\|strategy\|>(.*?)(?:<\||$)", re.DOTALL)


def build_special_tokens(base_vocab_size: int, *, include_quality: bool = False) -> dict[str, int]:
    """Map the autoresearch special tokens above the base tokenizer range.

    The five base tokens always map to ``base..base+4``. When ``include_quality`` is
    set (score-conditioned training), ``<|quality|>`` is appended at ``base+5``.
    """
    tokens = list(SPECIAL_TOKEN_STRINGS) + ([QUALITY_TOKEN] if include_quality else [])
    return {token: base_vocab_size + offset for offset, token in enumerate(tokens)}


def total_vocab_size(base_vocab_size: int, *, include_quality: bool = False) -> int:
    """Return the embedding/output vocab size including special tokens.

    Defaults to the original five-token vocab; ``include_quality`` reserves one more
    slot for the gated quality control token.
    """
    return base_vocab_size + len(SPECIAL_TOKEN_STRINGS) + (1 if include_quality else 0)


def score_to_quality_bucket(score: float, *, num_buckets: int = NUM_QUALITY_BUCKETS, lo: float = 0.0, hi: float = 1.0) -> int:
    """Quantize a score into a discrete quality bucket ``0..num_buckets-1``.

    Buckets are uniform over ``[lo, hi]`` (scores are normalized to ``[0, 1]`` for
    construction scenarios). The top bucket (``num_buckets-1``) is the conditioning
    target used at generation time: "produce a construction as good as the best".
    Scores outside the range are clamped.
    """
    if num_buckets <= 1 or hi <= lo:
        return 0
    frac = (score - lo) / (hi - lo)
    bucket = int(frac * num_buckets)
    return max(0, min(num_buckets - 1, bucket))


# Loss-weighting modes for reward-weighted regression (PR3b). ``uniform`` is the
# default and is byte-identical to unweighted training.
LOSS_WEIGHT_MODES = ("uniform", "linear", "softmax")
# Lowest relative weight the worst-scoring example gets in ``linear`` mode (before
# mean-normalization), so no example is fully dropped (that is what curation is for).
LOSS_WEIGHT_FLOOR = 0.1


def _mean_normalize(weights: list[float]) -> list[float]:
    """Scale weights so their mean is 1.0 (keeps the effective learning rate stable)."""
    n = len(weights)
    total = sum(weights)
    if n == 0 or total <= 0.0:
        return [1.0] * n
    mean = total / n
    return [w / mean for w in weights]


def score_loss_weights(scores: list[float], *, mode: str = "uniform", temperature: float = 1.0) -> list[float]:
    """Per-example loss weights from scores (reward-weighted regression / RWR).

    Soft counterpart to elite filtering: instead of dropping low-scoring records
    (curation) or tagging them (score-conditioning), every record is kept but its
    per-example loss is scaled by a weight derived from its score, so the gradient
    leans toward high-reward constructions. The weight is applied per training
    example (each example's mean completion loss is weighted, then averaged), so a
    long completion cannot drown out a short high-reward one.

    Modes (all but ``uniform`` are mean-normalized to 1.0, so they change the
    *relative* emphasis across examples, not the overall step size):

    - ``uniform``: every weight is ``1.0`` (byte-identical to unweighted training).
    - ``linear``: min-max maps scores onto ``[LOSS_WEIGHT_FLOOR, 1]`` then normalizes.
      A mild, interpretable tilt toward better examples.
    - ``softmax``: ``softmax(score / temperature)`` then normalized. ``temperature``
      is a continuous knob: large flattens toward ``uniform``, small concentrates on
      the top example (soft elite selection). It must be ``> 0``.

    No score spread (all scores equal) falls back to ``uniform`` for every mode.
    """
    n = len(scores)
    if n == 0:
        return []
    if mode == "uniform":
        return [1.0] * n
    if mode not in LOSS_WEIGHT_MODES:
        raise ValueError(f"unknown loss-weight mode: {mode!r} (expected one of {LOSS_WEIGHT_MODES})")
    if mode == "softmax" and temperature <= 0:
        raise ValueError(f"softmax loss-weight temperature must be > 0, got {temperature}")

    lo, hi = min(scores), max(scores)
    if hi <= lo:  # no spread to weight by
        return [1.0] * n

    if mode == "linear":
        raw = [LOSS_WEIGHT_FLOOR + (1.0 - LOSS_WEIGHT_FLOOR) * (s - lo) / (hi - lo) for s in scores]
        return _mean_normalize(raw)

    # softmax (subtract max for numerical stability; shift-invariant after normalization)
    exps = [math.exp((s - hi) / temperature) for s in scores]
    return _mean_normalize(exps)


def decodable_vocab_size(tokenizer: Any) -> int:
    """Number of real, decodable BPE base tokens the tokenizer learned.

    On small corpora the BPE trainer learns fewer than ``base_vocab_size`` merges,
    leaving a gap of ids in ``[n_learned, base_vocab_size)`` that the underlying
    tiktoken encoding cannot decode (``decode`` raises ``KeyError``). Sampling must
    not emit ids in that gap. Returns ``max(rank) + 1`` over the mergeable ranks,
    falling back to ``base_vocab_size`` when the ranks are unavailable.
    """
    enc = getattr(tokenizer, "_encoding", None)
    ranks = getattr(enc, "_mergeable_ranks", None)
    if isinstance(ranks, dict) and ranks:
        return int(max(ranks.values())) + 1
    base = getattr(tokenizer, "base_vocab_size", None)
    return int(base) if base else BASE_VOCAB_SIZE


def generation_logit_mask_values(
    tokenizer: Any,
    vocab_size: int,
    *,
    block_structural_specials: bool = True,
) -> list[float]:
    """Additive logit-mask values (0.0 = allowed, -1e9 = blocked).

    Always blocks the phantom-id gap ``[decodable_vocab_size, base_vocab_size)``
    that the tokenizer cannot decode, so neither training-time assessment nor
    ``MLXProvider`` inference can sample an id that later crashes ``decode``.

    When ``block_structural_specials`` is true (training assessment) it also blocks
    the ``<|scenario|>`` / ``<|context|>`` / ``<|strategy|>`` tokens so a generated
    body cannot restart the header. Providers doing general completion pass
    ``False`` to leave all (decodable) special tokens available.
    """
    base = int(getattr(tokenizer, "base_vocab_size", BASE_VOCAB_SIZE))
    n_base = decodable_vocab_size(tokenizer)
    mask = [0.0] * vocab_size
    for i in range(max(0, n_base), min(base, vocab_size)):
        mask[i] = -1e9
    if block_structural_specials:
        # Use the tokenizer's own special-token map when available so the quality
        # token is blocked only for score-conditioned tokenizers that actually have it.
        specials = getattr(tokenizer, "special_tokens", None) or build_special_tokens(base)
        for name in ("<|scenario|>", "<|context|>", "<|strategy|>", QUALITY_TOKEN):
            sid = specials.get(name)
            if sid is not None and 0 <= sid < vocab_size:
                mask[sid] = -1e9
    return mask


# ---------------------------------------------------------------------------
# Scenario resolution (pure; previously duplicated in prepare + cuda)
# ---------------------------------------------------------------------------


def resolve_scenario_name(scenario: Any) -> str:
    """Resolve a scenario's stable name (its ``name`` attr, else lowercased class)."""
    value = getattr(scenario, "name", None)
    if isinstance(value, str) and value.strip():
        return value
    return str(scenario.__class__.__name__).lower()


def resolve_scenario_context(scenario: Any) -> str:
    """Resolve the context string for a scenario (task prompt, else description)."""
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


# Backward-compatible private aliases (prepare/cuda historically used underscored names).
_resolve_scenario_name = resolve_scenario_name
_resolve_scenario_context = resolve_scenario_context


# ---------------------------------------------------------------------------
# Example formatting + parsing (the format itself)
# ---------------------------------------------------------------------------


def format_example(
    *,
    scenario: str,
    context: str,
    strategy_json: str,
    score: float,
    quality: int | None = None,
) -> str:
    """Format a single training example in the standard input format.

    Format (``quality`` omitted unless score-conditioned):
        <|scenario|>{scenario}<|context|>{context}[<|quality|>{quality}]<|strategy|>{strategy_json}<|score|>{score}<|end|>
    """
    quality_segment = f"<|quality|>{quality}" if quality is not None else ""
    return f"<|scenario|>{scenario}<|context|>{context}{quality_segment}<|strategy|>{strategy_json}<|score|>{score}<|end|>"


def build_generation_prompt(scenario: Any, *, target_quality: int | None = None) -> str:
    """Build the generation prompt for a scenario (header up to ``<|strategy|>``).

    Single source for both the MLX and CUDA generators (and any provider) so the
    prompt prefix stays in lockstep with :func:`format_example`. When
    ``target_quality`` is given (score-conditioned generation) the quality control
    token is emitted before ``<|strategy|>`` to steer toward that quality bucket.
    """
    quality_segment = f"<|quality|>{target_quality}" if target_quality is not None else ""
    return (
        f"<|scenario|>{resolve_scenario_name(scenario)}<|context|>{resolve_scenario_context(scenario)}"
        f"{quality_segment}<|strategy|>"
    )


def extract_strategy(text: str) -> dict[str, Any] | None:
    """Extract the JSON strategy object from model output text.

    Prefers the span after ``<|strategy|>`` up to the next special token; falls back
    to parsing the whole text as JSON. Returns ``None`` if nothing parses.
    """
    match = _STRATEGY_RE.search(text)
    if match:
        try:
            result = json.loads(match.group(1))
        except json.JSONDecodeError:
            return None
        return result if isinstance(result, dict) else None
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        return None
    return result if isinstance(result, dict) else None


def completion_loss_mask(token_ids: list[int], *, strategy_token_id: int) -> list[int]:
    """Per-target loss mask for completion-only training (1 = train, 0 = ignore).

    For next-token prediction the target sequence is ``token_ids[1:]`` (aligned to
    inputs ``token_ids[:-1]``), so the mask has length ``len(token_ids) - 1``. Loss
    is enabled only for targets that fall *after* the ``<|strategy|>`` token (the
    completion the model must learn to generate); the scenario/context prompt that
    precedes it is masked out. This is the standard completion-only / "loss over
    completions" objective used in instruction tuning.

    If the strategy token is absent (unexpected/legacy), all positions are trained
    (all-ones) so no example is silently dropped.
    """
    n = len(token_ids)
    if n <= 1:
        return []
    try:
        strategy_index = token_ids.index(strategy_token_id)
    except ValueError:
        return [1] * (n - 1)
    # input j predicts token_ids[j+1]; it is a completion target iff j+1 > strategy_index
    return [1 if (j + 1) > strategy_index else 0 for j in range(n - 1)]


def build_masked_example(
    tokens: list[int],
    *,
    seq_len: int,
    pad_token_id: int,
    strategy_token_id: int,
) -> tuple[list[int], list[int], list[int]] | None:
    """Build a padded ``(input_ids, target_ids, loss_mask)`` triple for one example.

    Pure and backend-agnostic (the MLX and torch dataloaders both wrap this). The
    example is right-truncated to ``seq_len + 1`` tokens when too long (keeping the
    tail so the completion survives), then split into next-token ``input``/``target``
    of length ``seq_len`` and padded with ``pad_token_id``. The loss mask is the
    completion-only mask with padding positions zeroed. Returns ``None`` for
    sequences too short to form a single (input, target) pair.
    """
    if len(tokens) < 2:
        return None
    if len(tokens) > seq_len + 1:
        tokens = tokens[-(seq_len + 1) :]
    mask = completion_loss_mask(tokens, strategy_token_id=strategy_token_id)
    input_ids = tokens[:-1]
    target_ids = tokens[1:]
    pad = seq_len - len(input_ids)
    if pad > 0:
        input_ids = input_ids + [pad_token_id] * pad
        target_ids = target_ids + [pad_token_id] * pad
        mask = mask + [0] * pad
    return input_ids, target_ids, mask


@dataclass(frozen=True, slots=True)
class TrainingExample:
    """A training record reduced to the fields that define its token sequence.

    ``from_record`` is the single place that maps a raw JSONL/record dict onto the
    sequence fields (canonical JSON serialization of context/strategy, float score),
    so producers (``_build_corpus``, the MLX/CUDA tokenize loops) no longer repeat
    that mapping. ``to_sequence`` is the single place that lays the fields out.
    """

    scenario: str
    context: str
    strategy_json: str
    score: float

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> TrainingExample:
        return cls(
            scenario=str(record["scenario"]),
            context=json.dumps(record.get("context", {}), sort_keys=True),
            strategy_json=json.dumps(record["strategy"], sort_keys=True),
            score=float(record["score"]),
        )

    def to_sequence(self, *, score_conditioned: bool = False, num_buckets: int = NUM_QUALITY_BUCKETS) -> str:
        """Lay the fields out as a token sequence.

        When ``score_conditioned`` is set, a quality control token derived from the
        record's score is emitted before the strategy so the model learns to map a
        target quality onto a construction (Decision-Transformer / Quark style).
        """
        quality = score_to_quality_bucket(self.score, num_buckets=num_buckets) if score_conditioned else None
        return format_example(
            scenario=self.scenario,
            context=self.context,
            strategy_json=self.strategy_json,
            score=self.score,
            quality=quality,
        )
