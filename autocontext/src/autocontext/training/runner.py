"""Training loop runner with git experiment state machine (AC-179).

Orchestrates the autoresearch-style experiment loop:
1. Set up workspace (copy templates, create branch, init results.tsv)
2. Render program.md with scenario-specific context
3. Run a baseline experiment from the copied training template
4. Optionally iterate with agent-proposed train.py revisions under keep/discard git control
5. Return the best kept inference bundle path
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from autocontext.agents.llm_client import LanguageModelClient, build_client_from_settings
from autocontext.config.settings import load_settings
from autocontext.training.backends import TrainingBackend, default_backend_registry
from autocontext.training.model_registry import (
    ModelRegistry,
    TrainingCompletionOutput,
    publish_training_output,
)
from autocontext.training.scale import training_scale_metadata_from_config

logger = logging.getLogger(__name__)

CONVERGENCE_NUDGE_THRESHOLD = 10
_TEMPLATE_DIR = Path(__file__).parent / "autoresearch"
_REPO_ROOT = Path(__file__).resolve().parents[3]

_TSV_HEADER = "experiment\tavg_score\tvalid_rate\tpeak_memory_mb\ttraining_seconds\toutcome\terror\n"
_PYTHON_BLOCK_RE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)


class ExperimentOutcome(StrEnum):
    KEPT = "kept"
    DISCARDED = "discarded"
    ERROR = "error"


@dataclass(slots=True)
class TrainingConfig:
    """Configuration for the autoresearch training loop."""

    scenario: str
    data_path: Path
    time_budget: int = 300
    max_experiments: int = 0
    memory_limit_mb: int = 16384
    device_count: int = 1
    sharding_strategy: str = "none"
    per_device_memory_limit_mb: int = 0
    base_model_parameter_count: int = 0
    base_model_quantization: str = ""
    deployment_target_vram_mb: int = 0
    backend: str = "mlx"
    train_steps: int = 0  # 0 = backend default (8 from-scratch mlx/cuda, 100 pretrained-adapter backends)
    learning_rate: float = 0.0  # 0 = backend default (1e-3 from-scratch, 1e-4 mlxlm, 1e-5 opd/grpo/trl)
    agent_provider: str = "anthropic"
    agent_model: str = ""
    val_select: bool = False  # keep best-by-validation-loss checkpoint + early stop (MLX only)
    elite_fraction: float = 1.0  # train on only the top fraction of records by score (1.0 = all)
    dedupe: bool = False  # drop duplicate constructions, keeping the highest-scoring representative
    dedupe_near_threshold: float = 1.0  # with dedupe, also drop near-dups at/above this similarity
    score_conditioned: bool = False  # emit a quality control token + generate conditioned on the top bucket
    loss_weight_mode: str = "uniform"  # reward-weighted regression: uniform | linear | softmax (mlx/cuda)
    loss_weight_temperature: float = 1.0  # softmax loss-weight temperature (lower = sharper toward top scores)
    augmenter_spec: str = ""  # record augmenter 'package.module:function' for symmetry/transform expansion
    vocab_size: int = 8192  # BPE tokenizer target vocab (mlx/cuda from-scratch backends); BASE_VOCAB_SIZE
    base_model: str = ""  # mlxlm backend: pretrained base model (empty = backend default)
    teacher_model: str = ""  # opd backend: distillation teacher (empty = backend default)
    trl_mode: str = "gkd"  # trl backend: gkd (on-policy distillation) | grpo (RLVR)
    seed: int = 0  # trl backend: training seed (for seeded repeats)
    max_completion_length: int = 512  # trl grpo: generation cap (256 truncates reasoning -> 0 reward / no gradient)
    grpo_beta: float = 0.04  # trl grpo: KL penalty toward base policy (0.0 = KL-free; nonzero prevents overfitting)
    opd_diagnostics: bool = False  # OPD/GKD token-pressure report; no training-update changes
    opd_diagnostics_debug_tokens: bool = False  # include sampled token text in diagnostics (off by default)
    opd_pressure_mode: str = "full_kl"  # opd backend: full_kl | sample_positive | sample_positive_reverse_negative
    fine_tune_type: str = "lora"  # mlxlm backend: lora | dora | full
    num_layers: int = 8  # mlxlm backend: number of layers to fine-tune


@dataclass(slots=True)
class ExperimentResult:
    """Result of a single training experiment."""

    experiment_index: int
    avg_score: float
    valid_rate: float
    peak_memory_mb: float
    training_seconds: float
    outcome: ExperimentOutcome
    error_message: str = ""
    checkpoint_path: Path | None = None
    summary_metrics: dict[str, float] = field(default_factory=dict)


@dataclass(slots=True)
class TrainingResult:
    """Final result of a training session."""

    scenario: str
    total_experiments: int
    kept_count: int
    discarded_count: int
    best_score: float
    best_experiment_index: int
    checkpoint_path: Path | None
    results: list[ExperimentResult] = field(default_factory=list)
    published_model_id: str | None = None

    @property
    def kept_ratio(self) -> float:
        if self.total_experiments == 0:
            return 0.0
        return self.kept_count / self.total_experiments


class TrainingRunner:
    """Manages the autoresearch experiment loop with git state machine."""

    def __init__(self, config: TrainingConfig, *, work_dir: Path) -> None:
        self.config = config
        self.work_dir = work_dir
        # Capture the invocation cwd before any subprocess runs from the workspace, so a
        # consumer-repo augmenter module (importable from where the user ran the command)
        # stays importable in the subprocess (which runs from runs/train_<scenario>).
        self._invocation_cwd = Path.cwd()
        self._best_score = float("-inf")
        self._best_experiment_index = -1
        self._backend = self._resolve_backend()

    def _resolve_backend(self) -> TrainingBackend:
        backend = default_backend_registry().get(self.config.backend)
        if backend is None:
            raise ValueError(f"Unknown training backend: {self.config.backend}")
        return backend

    @property
    def subprocess_timeout(self) -> int:
        """Wall-clock timeout for experiment subprocesses (2x time budget)."""
        return self.config.time_budget * 2

    def setup_workspace(self) -> None:
        """Copy template files, create git branch, render program.md, init results.tsv."""
        self.work_dir.mkdir(parents=True, exist_ok=True)

        # model.py is copied as read-only architecture context (train.py imports it and the
        # LLM agent prompt includes it); train.py remains the single file the agent rewrites.
        for filename in ("train.py", "prepare.py", "model.py"):
            src = _TEMPLATE_DIR / filename
            if src.exists():
                shutil.copy2(src, self.work_dir / filename)

        # Render program.md with scenario context
        from autocontext.training.autoresearch.program import render_program

        rendered = render_program(
            scenario=self.config.scenario,
            strategy_schema="(see scenario definition)",
            playbook_summary="(no playbook loaded)",
            dead_ends_summary="(none known)",
            time_budget=str(self.config.time_budget),
            memory_limit=str(self.config.memory_limit_mb),
        )
        (self.work_dir / "program.md").write_text(rendered, encoding="utf-8")
        (self.work_dir / "results.tsv").write_text(_TSV_HEADER, encoding="utf-8")
        self._try_create_branch()

    def _try_create_branch(self) -> None:
        """Initialize a git repo in the workspace and create a training branch."""
        try:
            self._init_git_repo()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return

    def _init_git_repo(self) -> None:
        """Initialize git repo, commit workspace files, and create a training branch."""
        git_dir = self.work_dir / ".git"
        if not git_dir.exists():
            subprocess.run(["git", "init"], cwd=self.work_dir, capture_output=True, check=True)
            subprocess.run(
                ["git", "config", "user.email", "autocontext-train@local"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "autocontext Training"],
                cwd=self.work_dir,
                capture_output=True,
                check=True,
            )

        subprocess.run(["git", "add", "-A"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", f"autocontext-train: setup workspace for {self.config.scenario}"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

        timestamp = time.strftime("%Y%m%d-%H%M%S")
        branch_name = f"autocontext-train/{self.config.scenario}/{timestamp}"
        subprocess.run(
            ["git", "checkout", "-b", branch_name],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def _git_commit(self, message: str) -> None:
        """Stage all changes and create a commit."""
        subprocess.run(["git", "add", "-A"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def _git_head_sha(self) -> str:
        """Return current HEAD commit SHA."""
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()

    def keep_experiment(self) -> None:
        """Keep the current experiment (HEAD stays as-is)."""

    def discard_experiment(self) -> None:
        """Discard the current experiment by resetting HEAD~1."""
        subprocess.run(
            ["git", "reset", "--hard", "HEAD~1"],
            cwd=self.work_dir,
            capture_output=True,
            check=True,
        )

    def record_result(self, result: ExperimentResult) -> None:
        """Append an experiment result to results.tsv."""
        line = (
            f"{result.experiment_index}\t"
            f"{result.avg_score}\t"
            f"{result.valid_rate}\t"
            f"{result.peak_memory_mb}\t"
            f"{result.training_seconds}\t"
            f"{result.outcome.value}\t"
            f"{result.error_message}\n"
        )
        with open(self.work_dir / "results.tsv", "a", encoding="utf-8") as f:
            f.write(line)

    def should_stop(self, *, experiment_count: int, started_at: float | None = None) -> bool:
        """Check if the training loop should stop."""
        if self.config.max_experiments > 0 and experiment_count >= self.config.max_experiments:
            return True
        if started_at is not None and (time.monotonic() - started_at) >= self.config.time_budget:
            return True
        return False

    def needs_convergence_nudge(self, *, consecutive_discards: int) -> bool:
        """Check if the agent needs a convergence nudge."""
        return consecutive_discards >= CONVERGENCE_NUDGE_THRESHOLD

    def parse_summary(self, stdout: str) -> dict[str, float] | None:
        """Parse the training summary block from subprocess stdout."""
        match = re.search(
            r"=== TRAINING SUMMARY ===\n(.*?)\n========================",
            stdout,
            re.DOTALL,
        )
        if not match:
            return None

        block = match.group(1)
        result: dict[str, float] = {}
        for line in block.strip().split("\n"):
            line = line.strip()
            if ":" not in line:
                continue
            key, val = line.split(":", 1)
            try:
                result[key.strip()] = float(val.strip())
            except ValueError:
                continue

        required = {"avg_score", "valid_rate", "peak_memory_mb", "training_seconds"}
        if not required.issubset(result.keys()):
            return None
        return result

    def _experiment_env(self) -> dict[str, str]:
        env = os.environ.copy()
        # autocontext repo root first (so its own modules win), then the invocation cwd
        # (so a consumer-repo augmenter importable from where the user ran the command
        # resolves once the subprocess runs from the workspace), then any pre-set path.
        python_path_parts = [str(_REPO_ROOT), str(self._invocation_cwd)]
        existing = env.get("PYTHONPATH")
        if existing:
            python_path_parts.append(existing)
        env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
        return env

    def _build_agent_client(self) -> LanguageModelClient:
        settings = load_settings().model_copy(update={"agent_provider": self.config.agent_provider})
        return build_client_from_settings(settings, scenario_name=self.config.scenario)

    def _resolve_agent_model(self) -> str:
        """Resolve the effective model for the training-agent prompt revision loop.

        The training loop uses the lower-level LanguageModelClient interface, so
        unlike provider-backed complete() calls it cannot rely on an empty string
        to trigger provider-default fallback.
        """
        if self.config.agent_model:
            return self.config.agent_model

        settings = load_settings().model_copy(update={"agent_provider": self.config.agent_provider})
        if self.config.agent_provider in {"openai", "openai-compatible", "ollama", "vllm"}:
            return settings.agent_default_model
        return settings.model_competitor

    def _recent_results_tail(self, limit: int = 5) -> str:
        tsv_path = self.work_dir / "results.tsv"
        if not tsv_path.exists():
            return "(no prior results)"
        lines = tsv_path.read_text(encoding="utf-8").strip().splitlines()
        if len(lines) <= 1:
            return "(no prior results)"
        return "\n".join(lines[-limit:])

    def _extract_python_source(self, response_text: str) -> str:
        match = _PYTHON_BLOCK_RE.search(response_text)
        if match:
            return match.group(1).strip()
        return response_text.strip()

    def _deterministic_train_py_variant(self, current_source: str, experiment_index: int) -> str:
        # Tune the model-shape knobs in train.py (the architecture moved to model.py, but
        # these MODEL_* overrides stay in train.py precisely so this loop can edit shape).
        variants = [
            (r"MODEL_DEPTH = \d+", "MODEL_DEPTH = 5"),
            (r"MODEL_ASPECT_RATIO = \d+", "MODEL_ASPECT_RATIO = 48"),
            (r"MODEL_HEAD_DIM = \d+", "MODEL_HEAD_DIM = 32"),
        ]
        pattern, replacement = variants[(experiment_index - 1) % len(variants)]
        updated, count = re.subn(pattern, replacement, current_source, count=1)
        if count == 0 or updated == current_source:
            return f"{current_source.rstrip()}\n\n# experiment-{experiment_index}\n"
        return updated

    def _propose_train_py(self, client: LanguageModelClient, *, experiment_index: int, consecutive_discards: int) -> str:
        current_source = (self.work_dir / "train.py").read_text(encoding="utf-8")
        if self.config.agent_provider == "deterministic":
            return self._deterministic_train_py_variant(current_source, experiment_index)

        # The GPT architecture lives in the read-only model.py; expose it so the agent can
        # see what it is tuning (model shape is controlled by the MODEL_* knobs in train.py).
        model_src = ""
        model_path = self.work_dir / "model.py"
        if model_path.exists():
            model_src = (
                "Read-only model architecture (model.py; edit shape via the MODEL_* constants in train.py):\n"
                f"```python\n{model_path.read_text(encoding='utf-8')}\n```\n\n"
            )
        prompt = (
            "You are revising train.py for an autoresearch training loop.\n"
            "Return the complete updated contents of train.py only, wrapped in one ```python block.\n\n"
            f"Program instructions:\n{(self.work_dir / 'program.md').read_text(encoding='utf-8')}\n\n"
            f"Recent experiment log:\n{self._recent_results_tail()}\n\n"
            f"Consecutive discards: {consecutive_discards}\n\n"
            f"{model_src}"
            "Current train.py:\n"
            "```python\n"
            f"{current_source}\n"
            "```\n"
        )
        response = client.generate(
            model=self._resolve_agent_model(),
            prompt=prompt,
            max_tokens=8000,
            temperature=0.2,
            role="training_agent",
        )
        proposed = self._extract_python_source(response.text)
        compile(proposed, str(self.work_dir / "train.py"), "exec")
        return proposed

    def _run_experiment_subprocess(self, experiment_index: int) -> subprocess.CompletedProcess[str]:
        checkpoint_dir = self._checkpoint_dir(experiment_index)
        command = [
            sys.executable,
            "train.py",
            "--scenario",
            self.config.scenario,
            "--data",
            str(self.config.data_path.resolve()),
            "--output-dir",
            str(checkpoint_dir),
            "--time-budget",
            str(self.config.time_budget),
            "--memory-limit",
            str(self.config.memory_limit_mb),
            "--backend",
            self.config.backend,
        ]
        if self.config.train_steps > 0:
            command += ["--train-steps", str(self.config.train_steps)]
        if self.config.learning_rate > 0:
            command += ["--learning-rate", str(self.config.learning_rate)]
        if self.config.device_count != 1:
            command += ["--device-count", str(self.config.device_count)]
        if self.config.sharding_strategy != "none":
            command += ["--sharding-strategy", self.config.sharding_strategy]
        if self.config.per_device_memory_limit_mb != 0:
            command += ["--per-device-memory-limit", str(self.config.per_device_memory_limit_mb)]
        if self.config.deployment_target_vram_mb != 0:
            command += ["--deployment-target-vram", str(self.config.deployment_target_vram_mb)]
        if self.config.val_select:
            command.append("--val-select")
        if self.config.elite_fraction != 1.0:
            command += ["--elite-fraction", str(self.config.elite_fraction)]
        if self.config.dedupe:
            command.append("--dedupe")
        if self.config.dedupe_near_threshold != 1.0:
            command += ["--dedupe-near-threshold", str(self.config.dedupe_near_threshold)]
        if self.config.score_conditioned:
            command.append("--score-conditioned")
        if self.config.loss_weight_mode != "uniform":
            command += ["--loss-weight-by-score", self.config.loss_weight_mode]
        if self.config.loss_weight_temperature != 1.0:
            command += ["--loss-weight-temperature", str(self.config.loss_weight_temperature)]
        if self.config.augmenter_spec:
            command += ["--augmenter", self.config.augmenter_spec]
        if self.config.vocab_size != 8192:
            command += ["--vocab-size", str(self.config.vocab_size)]
        if self.config.base_model:
            command += ["--base-model", self.config.base_model]
        if self.config.base_model_quantization:
            command += ["--base-model-quantization", self.config.base_model_quantization]
        if self.config.teacher_model:
            command += ["--teacher-model", self.config.teacher_model]
        if self.config.trl_mode != "gkd":
            command += ["--trl-mode", self.config.trl_mode]
        if self.config.seed != 0:
            command += ["--seed", str(self.config.seed)]
        if self.config.max_completion_length != 512:
            command += ["--max-completion-length", str(self.config.max_completion_length)]
        if self.config.grpo_beta != 0.04:
            command += ["--grpo-beta", str(self.config.grpo_beta)]
        if self.config.opd_diagnostics:
            command.append("--opd-diagnostics")
        if self.config.opd_diagnostics_debug_tokens:
            command.append("--opd-diagnostics-debug-tokens")
        if self.config.opd_pressure_mode != "full_kl":
            command += ["--opd-pressure-mode", self.config.opd_pressure_mode]
        if self.config.fine_tune_type != "lora":
            command += ["--fine-tune-type", self.config.fine_tune_type]
        if self.config.num_layers != 8:
            command += ["--num-layers", str(self.config.num_layers)]
        return subprocess.run(
            command,
            cwd=self.work_dir,
            capture_output=True,
            text=True,
            timeout=self.subprocess_timeout,
            env=self._experiment_env(),
            check=False,
        )

    def _execute_experiment(self, experiment_index: int) -> ExperimentResult:
        checkpoint_dir = self._checkpoint_dir(experiment_index)
        try:
            completed = self._run_experiment_subprocess(experiment_index)
        except subprocess.TimeoutExpired:
            return ExperimentResult(
                experiment_index=experiment_index,
                avg_score=0.0,
                valid_rate=0.0,
                peak_memory_mb=0.0,
                training_seconds=0.0,
                outcome=ExperimentOutcome.ERROR,
                error_message="timeout",
            )

        combined = f"{completed.stdout}\n{completed.stderr}".strip()
        if completed.returncode != 0:
            return ExperimentResult(
                experiment_index=experiment_index,
                avg_score=0.0,
                valid_rate=0.0,
                peak_memory_mb=0.0,
                training_seconds=0.0,
                outcome=ExperimentOutcome.ERROR,
                error_message=combined or f"exit_code={completed.returncode}",
            )

        summary = self.parse_summary(combined)
        if summary is None:
            return ExperimentResult(
                experiment_index=experiment_index,
                avg_score=0.0,
                valid_rate=0.0,
                peak_memory_mb=0.0,
                training_seconds=0.0,
                outcome=ExperimentOutcome.ERROR,
                error_message="missing training summary",
            )

        improved = summary["avg_score"] > self._best_score
        outcome = ExperimentOutcome.KEPT if improved else ExperimentOutcome.DISCARDED
        checkpoint_path = checkpoint_dir if outcome == ExperimentOutcome.KEPT else None
        return ExperimentResult(
            experiment_index=experiment_index,
            avg_score=summary["avg_score"],
            valid_rate=summary["valid_rate"],
            peak_memory_mb=summary["peak_memory_mb"],
            training_seconds=summary["training_seconds"],
            outcome=outcome,
            checkpoint_path=checkpoint_path,
            summary_metrics=dict(summary),
        )

    def _update_best(self, result: ExperimentResult) -> None:
        if result.outcome != ExperimentOutcome.KEPT:
            return
        if result.avg_score > self._best_score:
            self._best_score = result.avg_score
            self._best_experiment_index = result.experiment_index

    def run(self) -> TrainingResult:
        """Run the full training loop and return the best kept result."""
        self.setup_workspace()
        started_at = time.monotonic()
        results: list[ExperimentResult] = []

        baseline = self._execute_experiment(0)
        if baseline.outcome == ExperimentOutcome.ERROR:
            raise RuntimeError(baseline.error_message or "baseline training experiment failed")
        self.record_result(baseline)
        self._update_best(baseline)
        results.append(baseline)

        if self.should_stop(experiment_count=1, started_at=started_at):
            return self.build_training_result(results)

        try:
            client = self._build_agent_client()
        except Exception:
            logger.debug("training.runner: caught Exception", exc_info=True)
            return self.build_training_result(results)

        experiment_index = 1
        consecutive_discards = 0

        while not self.should_stop(experiment_count=experiment_index, started_at=started_at):
            proposed_source = self._propose_train_py(
                client,
                experiment_index=experiment_index,
                consecutive_discards=consecutive_discards,
            )
            (self.work_dir / "train.py").write_text(proposed_source, encoding="utf-8")
            self._git_commit(f"experiment {experiment_index}")

            result = self._execute_experiment(experiment_index)
            if result.outcome == ExperimentOutcome.KEPT:
                self.keep_experiment()
                self._update_best(result)
                consecutive_discards = 0
            else:
                self.discard_experiment()
                consecutive_discards += 1

            self.record_result(result)
            results.append(result)
            experiment_index += 1

        return self.build_training_result(results)

    def build_training_result(self, results: list[ExperimentResult]) -> TrainingResult:
        """Build the final TrainingResult from accumulated experiment results."""
        kept = [r for r in results if r.outcome == ExperimentOutcome.KEPT]
        discarded = [r for r in results if r.outcome == ExperimentOutcome.DISCARDED]

        best_result = max(kept, key=lambda r: r.avg_score, default=None)
        published_model_id = self._publish_best_model(best_result)
        return TrainingResult(
            scenario=self.config.scenario,
            total_experiments=len(results),
            kept_count=len(kept),
            discarded_count=len(discarded),
            best_score=best_result.avg_score if best_result is not None else 0.0,
            best_experiment_index=best_result.experiment_index if best_result is not None else -1,
            checkpoint_path=best_result.checkpoint_path if best_result is not None else None,
            results=results,
            published_model_id=published_model_id,
        )

    def _training_run_id(self) -> str:
        try:
            return self._git_head_sha()
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            return self.work_dir.name

    def _scenario_family_name(self) -> str:
        try:
            from autocontext.scenarios import SCENARIO_REGISTRY
            from autocontext.scenarios.families import detect_family

            scenario_cls = SCENARIO_REGISTRY.get(self.config.scenario)
            if scenario_cls is None:
                return ""
            family = detect_family(scenario_cls())
            return family.name if family is not None else ""
        except Exception:
            logger.debug("training.runner: caught Exception", exc_info=True)
            return ""

    def _data_stats(self, best_result: ExperimentResult | None = None) -> dict[str, float | str]:
        # Record the curation settings (with the raw + curated record counts) so a
        # published model carries enough provenance to reproduce the training split.
        stats: dict[str, float | str] = {
            "data_path": str(self.config.data_path),
            "elite_fraction": float(self.config.elite_fraction),
            "dedupe": 1.0 if self.config.dedupe else 0.0,
            "dedupe_near_threshold": float(self.config.dedupe_near_threshold),
            "score_conditioned": 1.0 if self.config.score_conditioned else 0.0,
            "loss_weight_mode": self.config.loss_weight_mode,
            "loss_weight_temperature": float(self.config.loss_weight_temperature),
            "augmenter_spec": self.config.augmenter_spec,
            "vocab_size": float(self.config.vocab_size),
        }
        try:
            line_count = sum(1 for _ in self.config.data_path.open(encoding="utf-8"))
            stats["records"] = float(line_count)
        except OSError:
            logger.debug("training.runner: suppressed OSError", exc_info=True)
        if best_result is not None and "num_records" in best_result.summary_metrics:
            stats["curated_records"] = float(best_result.summary_metrics["num_records"])
        return stats

    def _checkpoint_dir(self, experiment_index: int) -> Path:
        return self.work_dir / self._backend.default_checkpoint_dir(self.config.scenario) / f"exp_{experiment_index}"

    def _publish_best_model(self, best_result: ExperimentResult | None) -> str | None:
        if best_result is None or best_result.checkpoint_path is None:
            return None

        num_params_m = best_result.summary_metrics.get("num_params_M", 0.0)

        settings = load_settings()
        registry = ModelRegistry(settings.knowledge_root)
        training_metrics = {
            "avg_score": best_result.avg_score,
            "valid_rate": best_result.valid_rate,
            "val_loss": best_result.summary_metrics.get("val_loss", float("nan")),
            "peak_memory_mb": best_result.peak_memory_mb,
            "training_seconds": best_result.training_seconds,
            "num_steps": best_result.summary_metrics.get("num_steps", 0.0),
            "depth": best_result.summary_metrics.get("depth", 0.0),
        }
        for key in (
            "token_pressure_positive_ratio",
            "token_pressure_negative_ratio",
            "token_pressure_shock_spike_count",
            "opd_positive_token_fraction",
            "opd_negative_token_fraction",
            "opd_mean_masked_loss",
        ):
            if key in best_result.summary_metrics:
                training_metrics[key] = best_result.summary_metrics[key]

        diagnostics_written = "token_pressure_positive_ratio" in best_result.summary_metrics
        completion = TrainingCompletionOutput(
            run_id=self._training_run_id(),
            checkpoint_path=str(best_result.checkpoint_path),
            backend=self._backend.name,
            scenario=self.config.scenario,
            scenario_family=self._scenario_family_name(),
            parameter_count=max(int(num_params_m * 1_000_000), 1),
            architecture="autoresearch_gpt",
            training_metrics=training_metrics,
            data_stats=self._data_stats(best_result),
            runtime_types=self._backend.supported_runtime_types(),
            metadata={
                "backend_metadata": self._backend.metadata(),
                "experiment_index": best_result.experiment_index,
                "work_dir": str(self.work_dir),
                # Serving bridge: an adapter checkpoint (mlxlm/opd) is useless without
                # the base model it was trained against, and a score-conditioned model
                # must be prompted with the quality prefix at inference. Record both so
                # the scenario-bound resolver can rebuild the right client (see
                # scenario_bound_clients.plan_local_client). Fall back to the backend's
                # effective default when no explicit --base-model was given, since the
                # subprocess applies that same default (an empty value is unservable).
                "base_model": self.config.base_model or self._backend.default_base_model(),
                "training_scale": training_scale_metadata_from_config(self.config),
                "score_conditioned": self.config.score_conditioned,
                "opd_diagnostics": diagnostics_written,
                "opd_pressure_mode": self.config.opd_pressure_mode,
                "opd_diagnostics_path": (
                    str(best_result.checkpoint_path / "token_pressure_diagnostics.json")
                    if diagnostics_written and best_result.checkpoint_path is not None
                    else ""
                ),
            },
        )
        record = publish_training_output(
            completion,
            registry,
            artifacts_root=settings.knowledge_root,
            auto_activate=True,
        )
        return record.artifact_id
