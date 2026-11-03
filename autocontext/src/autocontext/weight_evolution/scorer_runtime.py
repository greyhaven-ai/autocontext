"""Pinned CPU scorers with verified official IFEval source and offline resources.

Fetching only stores data. Loading is a separate trusted operation: after byte
and dependency checks it imports the three explicitly pinned Google modules.
No arbitrary module search path or downloaded checkpoint code is accepted.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import os
import platform
import random
import re
import sys
import tempfile
import threading
import types
import urllib.request
import zipfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import Field, field_validator, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest, load_small_json, regular_file
from autocontext.weight_evolution.assets import _HTTPSRedirectHandler
from autocontext.weight_evolution.models import Digest, FrozenModel
from autocontext.weight_evolution.scorers import IFEvalInstruction, ScorerInfrastructureError, score_ifeval, score_math

if TYPE_CHECKING:
    from autocontext.weight_evolution.datasets import EvaluationProblem
    from autocontext.weight_evolution.inference_models import GenerationSample

GOOGLE_REVISION = "e6890f85757dd84e27ca6df2dd30651dafad28e0"
NLTK_REVISION = "550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"
_SOURCE_FILES = ("instructions_util.py", "instructions.py", "instructions_registry.py")
_RESOURCE_FILES = ("abbrev_types.txt", "collocations.tab", "ortho_context.tab", "sent_starters.txt")
_SCORING_LOCK = threading.RLock()


class ScorerPackagePin(FrozenModel):
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    version: str = Field(pattern=r"^[0-9][A-Za-z0-9.+_-]*$")


class ScorerAssetPin(FrozenModel):
    filename: str
    url: str
    sha256: Digest
    size_bytes: int = Field(gt=0, le=8 * 1024 * 1024)

    @model_validator(mode="after")
    def fixed_source(self) -> Self:
        prefix = f"https://raw.githubusercontent.com/google-research/google-research/{GOOGLE_REVISION}/"
        sources = {name: prefix + "instruction_following_eval/" + name for name in _SOURCE_FILES}
        sources["LICENSE"] = prefix + "LICENSE"
        sources["punkt_tab.zip"] = (
            f"https://raw.githubusercontent.com/nltk/nltk_data/{NLTK_REVISION}/packages/tokenizers/punkt_tab.zip"
        )
        if sources.get(self.filename) != self.url:
            raise ValueError("scorer assets must identify the fixed official source/resource revisions")
        return self


class ResourceMemberPin(FrozenModel):
    filename: str
    sha256: Digest
    size_bytes: int = Field(gt=0, le=1024 * 1024)

    @field_validator("filename")
    @classmethod
    def fixed_name(cls, value: str) -> str:
        if value not in _RESOURCE_FILES:
            raise ValueError("only the four English Punkt tables are supported")
        return value


class ScorerLock(FrozenModel):
    schema_version: Literal[1]
    profile: Literal["qwen-p50-math-verify-official-ifeval-v1"]
    python_version: Literal["3.13"]
    requirements_sha256: Digest
    packages: tuple[ScorerPackagePin, ...]
    assets: tuple[ScorerAssetPin, ...]
    punkt_members: tuple[ResourceMemberPin, ...]
    random_seed: Literal[0]
    parse_timeout_seconds: Literal[5]
    source_revision: Literal["e6890f85757dd84e27ca6df2dd30651dafad28e0"]
    nltk_revision: Literal["550b6625bcef1f2abff2ff770a5a0d272c9c6b2a"]
    fidelity_notes: tuple[str, ...]

    @model_validator(mode="after")
    def complete_pins(self) -> Self:
        names = [pin.name for pin in self.packages]
        if len(names) != len(set(names)):
            raise ValueError("duplicate scorer package pins")
        required = {
            "math-verify", "latex2sympy2-extended", "antlr4-python3-runtime", "sympy", "mpmath", "langdetect", "six",
            "nltk", "immutabledict", "absl-py", "click", "joblib", "cloudpickle", "regex", "tqdm", "defusedxml",
            "pydantic", "pydantic-core", "annotated-types", "typing-extensions", "typing-inspection",
        }
        if set(names) != required:
            raise ValueError("scorer dependency closure is incomplete or unexpected")
        source_math = {"math-verify": "0.9.0", "latex2sympy2-extended": "1.11.0", "antlr4-python3-runtime": "4.13.2"}
        if any(source_math.get(pin.name, pin.version) != pin.version for pin in self.packages):
            raise ValueError("math verifier versions differ from the released source")
        assets = [pin.filename for pin in self.assets]
        if len(assets) != 5 or set(assets) != {*_SOURCE_FILES, "LICENSE", "punkt_tab.zip"}:
            raise ValueError("scorer source/license/resource assets are incomplete or duplicated")
        members = [pin.filename for pin in self.punkt_members]
        if len(members) != 4 or set(members) != set(_RESOURCE_FILES):
            raise ValueError("Punkt member pins are incomplete or duplicated")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def load_scorer_lock(path: Path) -> ScorerLock:
    data = load_small_json(path, max_bytes=128 * 1024)
    lock = ScorerLock.model_validate_json(json.dumps(data, allow_nan=False))
    requirements = path.with_name("scorer-requirements.txt")
    with regular_file(requirements) as stream:
        payload = stream.read(256 * 1024 + 1)
    if len(payload) > 256 * 1024 or hashlib.sha256(payload).hexdigest() != lock.requirements_sha256:
        raise ValueError("scorer installation requirements differ from their lock")
    installed_pins = dict(re.findall(r"^([a-z0-9-]+)==([^\s]+)", payload.decode("utf-8"), re.MULTILINE))
    if installed_pins != {pin.name: pin.version for pin in lock.packages}:
        raise ValueError("scorer package versions differ from their installation manifest")
    return lock


def _asset_bytes(root: Path, pin: ScorerAssetPin) -> bytes:
    with regular_file(root / pin.filename) as stream:
        payload = stream.read(pin.size_bytes + 1)
    if len(payload) != pin.size_bytes or hashlib.sha256(payload).hexdigest() != pin.sha256:
        raise ValueError(f"scorer asset differs from its pin: {pin.filename}")
    return payload


def verify_scorer_assets(lock: ScorerLock, root: Path) -> dict[str, Any]:
    for pin in lock.assets:
        _asset_bytes(root, pin)
    return {"ok": True, "scorer_lock_digest": lock.digest, "assets": len(lock.assets), "code_executed": False}


def fetch_scorer_assets(lock: ScorerLock, root: Path, *, timeout_seconds: float = 30.0) -> dict[str, Any]:
    """Explicit bounded acquisition; never import the downloaded modules."""
    if type(timeout_seconds) not in (int, float) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("download timeout must be positive and finite")
    if any(p.is_symlink() for p in (root.absolute(), *root.absolute().parents)):
        raise ValueError("scorer asset paths must not traverse symlinks")
    root.mkdir(parents=True, exist_ok=True)
    for pin in lock.assets:
        target = root / pin.filename
        if target.exists() or target.is_symlink():
            _asset_bytes(root, pin)
            continue
        with urllib.request.build_opener(_HTTPSRedirectHandler()).open(pin.url, timeout=timeout_seconds) as response:
            if urlsplit(response.geturl()).scheme != "https":
                raise ValueError("scorer asset redirected away from HTTPS")
            payload = response.read(pin.size_bytes + 1)
        if len(payload) != pin.size_bytes or hashlib.sha256(payload).hexdigest() != pin.sha256:
            raise ValueError(f"downloaded scorer asset differs: {pin.filename}")
        with tempfile.NamedTemporaryFile(dir=root, prefix=".scorer-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
        try:
            try:
                os.link(temporary, target)
            except FileExistsError:
                _asset_bytes(root, pin)
        finally:
            temporary.unlink(missing_ok=True)
    return verify_scorer_assets(lock, root)


def check_scorer_environment(lock: ScorerLock) -> dict[str, Any]:
    actual_python = platform.python_version()
    if ".".join(actual_python.split(".")[:2]) != lock.python_version:
        raise ScorerInfrastructureError(f"scorer requires Python {lock.python_version}, found {actual_python}")
    versions = {}
    for pin in lock.packages:
        try:
            version = importlib.metadata.version(pin.name)
        except importlib.metadata.PackageNotFoundError as exc:
            raise ScorerInfrastructureError(f"missing scorer package: {pin.name}") from exc
        if version != pin.version:
            raise ScorerInfrastructureError(f"scorer version mismatch: {pin.name} requires {pin.version}, found {version}")
        versions[pin.name] = version
    return {"python_version": actual_python, "packages": versions, "platform": platform.platform()}


def _official_registry(sources: dict[str, bytes]) -> Any:
    """Load unchanged trusted source without adding an asset root to sys.path."""
    names = ["instruction_following_eval", *("instruction_following_eval." + f[:-3] for f in _SOURCE_FILES)]
    if any(name in sys.modules for name in names):
        raise ScorerInfrastructureError("an ambient IFEval module is already imported")
    package = types.ModuleType(names[0])
    package.__path__ = []
    sys.modules[names[0]] = package
    try:
        for filename in _SOURCE_FILES:
            name = "instruction_following_eval." + filename[:-3]
            module = types.ModuleType(name)
            module.__package__ = names[0]
            sys.modules[name] = module
            setattr(package, filename[:-3], module)
            exec(compile(sources[filename], f"pinned-ifeval/{GOOGLE_REVISION}/{filename}", "exec"), module.__dict__)
        return sys.modules["instruction_following_eval.instructions_registry"].INSTRUCTION_DICT
    finally:
        for name in names:
            sys.modules.pop(name, None)


class PinnedScorer:
    execution_kind = "official"

    def __init__(self, lock: ScorerLock, root: Path) -> None:
        self.lock = lock
        self.lock_digest = lock.digest
        self.environment = check_scorer_environment(lock)
        source = {pin.filename: _asset_bytes(root, pin) for pin in lock.assets}
        self._resources = tempfile.TemporaryDirectory(prefix="autocontext-punkt-")
        self.resource_root = Path(self._resources.name)
        english = self.resource_root / "tokenizers" / "punkt_tab" / "english"
        english.mkdir(parents=True)
        import io

        with zipfile.ZipFile(io.BytesIO(source["punkt_tab.zip"])) as archive:
            for pin in lock.punkt_members:
                name = "punkt_tab/english/" + pin.filename
                matches = [item for item in archive.infolist() if item.filename == name]
                if len(matches) != 1 or matches[0].file_size != pin.size_bytes:
                    raise ScorerInfrastructureError("Punkt archive member differs from its pin")
                payload = archive.read(matches[0])
                if hashlib.sha256(payload).hexdigest() != pin.sha256:
                    raise ScorerInfrastructureError("Punkt table hash mismatch")
                (english / pin.filename).write_bytes(payload)
        with _SCORING_LOCK:
            self.registry = _official_registry(source)
        self._math = importlib.import_module("math_verify")
        self._nltk = importlib.import_module("nltk")
        self._langdetect = importlib.import_module("langdetect")
        factory_module = importlib.import_module("langdetect.detector_factory")
        self._language_factory_module: Any = factory_module
        self._language_factory = self._langdetect.DetectorFactory()
        self._language_factory.load_profile(factory_module.PROFILES_DIRECTORY)
        self._language_factory.set_seed(self.lock.random_seed)
        self._gold: dict[str, Any] = {}
        with self._context():
            # Both official sentence counting and capital-word counting must work
            # offline before any cohort can be scored. No pickle is deserialized.
            self._nltk.data.load("nltk:tokenizers/punkt/english.pickle").tokenize("One. Two.")
            self._nltk.word_tokenize("ONE example.")

    def provenance(self) -> dict[str, Any]:
        return {"scorer_lock_digest": self.lock_digest, "execution_kind": self.execution_kind,
                "environment": self.environment, "google_source_revision": self.lock.source_revision,
                "nltk_resource_revision": self.lock.nltk_revision, "random_seed": self.lock.random_seed,
                "source_assets": {pin.filename: pin.sha256 for pin in self.lock.assets},
                "fidelity_notes": list(self.lock.fidelity_notes)}

    @contextmanager
    def _context(self) -> Iterator[None]:
        if threading.current_thread() is not threading.main_thread():
            raise ScorerInfrastructureError("pinned scoring requires a main-thread evaluator process")
        with _SCORING_LOCK:
            old_path, old_random = self._nltk.data.path[:], random.getstate()
            old_seed = self._langdetect.DetectorFactory.seed
            old_factory = self._language_factory_module._factory
            self._nltk.data.path[:] = [str(self.resource_root)]
            self._nltk.data.clear_cache()
            self._nltk.tokenize._get_punkt_tokenizer.cache_clear()
            self._langdetect.DetectorFactory.seed = self.lock.random_seed
            self._language_factory_module._factory = self._language_factory
            random.seed(self.lock.random_seed)
            try:
                yield
            finally:
                self._nltk.data.path[:] = old_path
                self._nltk.data.clear_cache()
                self._nltk.tokenize._get_punkt_tokenizer.cache_clear()
                self._langdetect.DetectorFactory.seed = old_seed
                self._language_factory_module._factory = old_factory
                random.setstate(old_random)

    def _parse_gold(self, gold: str | None) -> Any:
        if gold is None or not gold.strip():
            raise ScorerInfrastructureError("math cohort has missing gold answer")
        if gold not in self._gold:
            try:
                parsed = self._math.parse("\\boxed{" + gold + "}", parsing_timeout=self.lock.parse_timeout_seconds)
            except Exception as exc:
                raise ScorerInfrastructureError("math gold parsing failed") from exc
            if not parsed:
                raise ScorerInfrastructureError("math cohort has unparseable gold answer")
            self._gold[gold] = parsed
        return self._gold[gold]

    def validate_cohort(self, dataset: str, problems: Sequence[EvaluationProblem]) -> None:
        if dataset not in {"math500", "gsm8k", "ifeval"} or not problems:
            raise ScorerInfrastructureError("unknown or empty scoring cohort")
        with self._context():
            for problem in problems:
                if dataset != "ifeval":
                    self._parse_gold(problem.answer)
                else:
                    if not problem.instructions:
                        raise ScorerInfrastructureError("IFEval cohort has no instructions")
                    for item in problem.instructions:
                        try:
                            checker = self.registry[item.instruction_id](item.instruction_id)
                            checker.build_description(**{k: v for k, v in item.kwargs.items() if v is not None})
                            args = checker.get_instruction_args()
                            if args and "prompt" in args:
                                checker.build_description(prompt=problem.text)
                        except Exception as exc:
                            raise ScorerInfrastructureError(f"invalid IFEval cohort instruction: {item.instruction_id}") from exc

    def score_sample(self, dataset: str, problem: EvaluationProblem, sample: GenerationSample) -> int:
        if sample.problem_id != problem.problem_id:
            raise ScorerInfrastructureError("sample identity differs from scoring problem")
        self.validate_cohort(dataset, (problem,))
        with self._context():
            if dataset == "ifeval":
                instructions = [IFEvalInstruction(item.instruction_id, item.kwargs) for item in problem.instructions]
                return score_ifeval(sample.text, instructions, prompt=problem.text, registry=self.registry,
                                    thinking=True, finish_reason=sample.finish_reason).score
            assert problem.answer is not None

            def equivalent(prediction: str, gold: str) -> bool:
                predicted = self._math.parse("\\boxed{" + prediction + "}", parsing_timeout=self.lock.parse_timeout_seconds)
                return bool(predicted and self._math.verify(self._parse_gold(gold), predicted,
                                                           timeout_seconds=self.lock.parse_timeout_seconds))

            return score_math(sample.text, problem.answer, verifier=equivalent, finish_reason=sample.finish_reason).score


def load_pinned_scorer(lock: ScorerLock, root: Path) -> PinnedScorer:
    try:
        return PinnedScorer(lock, root)
    except ScorerInfrastructureError:
        raise
    except Exception as exc:
        raise ScorerInfrastructureError(f"pinned scorer initialization failed: {type(exc).__name__}: {exc}") from exc
