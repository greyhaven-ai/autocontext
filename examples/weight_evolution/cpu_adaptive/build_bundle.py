"""Build pinned finite CPU edit choices and cases, without inference responses."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import tempfile
from pathlib import Path

from autocontext.weight_evolution.artifacts import TensorInfo, write_tensor_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateSpec
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_search import search_implementation_digest
from autocontext.weight_evolution.cpu_search_models import (
    CpuSearchBudget,
    CpuSearchBundleLock,
    CpuSearchOption,
    CpuSearchSpec,
)
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.tensor_edits import (
    TensorEdit,
    TensorEditProgram,
    TensorReplacement,
    apply_tensor_edit,
    build_checkpoint_contract,
)


def build_bundle(output: Path, *, confirmation_outcome: str = "pass") -> None:
    if confirmation_outcome not in {"pass", "veto"}:
        raise ValueError("unknown confirmation outcome")
    if output.exists() or any(part.is_symlink() for part in (output, *output.parents)):
        raise ValueError("bundle builder requires a fresh directory without symlinks")
    vocabulary = ("anchor", "gap", "flipped", "near", "stochastic", "hint")
    model = CpuModelContract(vocabulary=vocabulary, output_tokens=("zero", "one"))
    payloads = {"projection.bias": struct.pack("<ff", 0, 0),
                "projection.weight": struct.pack("<12f", 8, 0, 1, 0, 0, 3, 1, 0, 0, 1, 0, 5)}
    infos = (TensorInfo("projection.bias", "F32", (2,), 0, 8),
             TensorInfo("projection.weight", "F32", (6, 2), 8, 56))
    with tempfile.TemporaryDirectory(prefix="cpu-adaptive-builder-") as temporary:
        scratch = Path(temporary).resolve()
        base = scratch / "base.safetensors"
        write_tensor_file(base, infos, payloads.__getitem__)
        contract = build_checkpoint_contract(base, editable_names=("projection.weight",), max_changed_entries=2)
        candidates = [TensorCandidateSpec(candidate_id="base", contract=contract, output_sha256=contract.sha256,
                                           output_size_bytes=contract.size_bytes)]
        tensor = next(t for t in contract.tensors if t.name == "projection.weight")
        for name, replacements in (
            ("row-two-1", ((4, 8.0), (5, 0.0))),
            ("row-two-2", ((4, 16.0), (5, 0.0))),
            ("row-zero-1", ((0, 0.0), (1, 8.0))),
            ("row-zero-2", ((0, 0.0), (1, 16.0))),
        ):
            program = TensorEditProgram(input_digest=contract.digest, edits=(TensorEdit(
                name=tensor.name, expected_sha256=tensor.sha256, dtype=tensor.dtype, shape=tensor.shape,
                replacements=tuple(TensorReplacement(index=index, value_hex=struct.pack("<f", value).hex())
                                   for index, value in replacements),
            ),))
            receipt = apply_tensor_edit(contract, program, input_path=base, output_dir=scratch / name)
            candidates.append(TensorCandidateSpec(candidate_id=name, contract=contract, program=program,
                                                   output_sha256=receipt.output_sha256,
                                                   output_size_bytes=receipt.output_size_bytes))
        options = (
            CpuSearchOption(option_id="c-hint-1", mechanism="C", direction="hint", step=1, context="hint"),
            CpuSearchOption(option_id="c-hint-2", mechanism="C", direction="hint", step=2, context="hint hint"),
            CpuSearchOption(option_id="c-anchor-1", mechanism="C", direction="anchor", step=1, context="anchor"),
            CpuSearchOption(option_id="c-anchor-2", mechanism="C", direction="anchor", step=2, context="anchor anchor"),
            CpuSearchOption(option_id="w-row-two-1", mechanism="W", direction="row-two", step=1,
                            candidate_id="row-two-1"),
            CpuSearchOption(option_id="w-row-two-2", mechanism="W", direction="row-two", step=2,
                            candidate_id="row-two-2"),
            CpuSearchOption(option_id="w-row-zero-1", mechanism="W", direction="row-zero", step=1,
                            candidate_id="row-zero-1"),
            CpuSearchOption(option_id="w-row-zero-2", mechanism="W", direction="row-zero", step=2,
                            candidate_id="row-zero-2"),
            CpuSearchOption(option_id="d-temperature-1", mechanism="D", direction="temperature", step=1,
                            decoding=CpuDecoding(mode="sample", temperature=1.0, top_k=2, seed=406)),
            CpuSearchOption(option_id="d-temperature-2", mechanism="D", direction="temperature", step=2,
                            decoding=CpuDecoding(mode="sample", temperature=10.0, top_k=2, seed=406)),
            CpuSearchOption(option_id="d-top-k-1", mechanism="D", direction="top-k", step=1,
                            decoding=CpuDecoding(mode="sample", temperature=1.0, top_k=1, seed=406)),
            CpuSearchOption(option_id="d-top-k-2", mechanism="D", direction="top-k", step=2,
                            decoding=CpuDecoding(mode="sample", temperature=2.0, top_k=1, seed=406)),
        )
        cases = []
        gold = ("zero", "one", "zero", "one", "zero")
        for role, repeat in (("practice", 1), ("confirmation", 2), ("audit", 3)):
            for index, (token, label) in enumerate(zip(vocabulary[:5], gold, strict=True)):
                count = 4 if role == "confirmation" and confirmation_outcome == "veto" and index in {1, 3, 4} else repeat
                cases.append(CpuCase(case_id=f"{role}-{index + 1}", role=role,  # type: ignore[arg-type]
                                     prompt=" ".join([token] * count), expected=label))
        spec = CpuSearchSpec(
            study_id=f"tiny-cpu-adaptive-{confirmation_outcome}-v1", model=model, candidates=tuple(candidates),
            options=options, rounds=3, baseline_decoding=CpuDecoding(seed=406), evaluation_seed=406,
            limits=CpuWorkerLimits(memory_mb=512, wall_seconds=5.0, max_input_tokens=16),
            budget=CpuSearchBudget(max_proposals=9, max_materializations=5, max_inference_calls=130,
                                   max_input_tokens=1024, max_output_tokens=130),
            cases=tuple(cases), minimum_confirmation_gain=0.1, implementation_digest=search_implementation_digest(),
        )
        encoded = (json.dumps(spec.to_dict(), sort_keys=True, indent=2) + "\n").encode()
        lock = CpuSearchBundleLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
                                   base_sha256=contract.sha256, base_size_bytes=contract.size_bytes)
        output.mkdir(parents=True)
        (output / "base.safetensors").write_bytes(base.read_bytes())
        (output / "study.json").write_bytes(encoded)
        (output / "bundle.lock.json").write_text(json.dumps(lock.to_dict(), sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirmation-outcome", choices=("pass", "veto"), default="pass")
    args = parser.parse_args()
    build_bundle(args.output, confirmation_outcome=args.confirmation_outcome)
