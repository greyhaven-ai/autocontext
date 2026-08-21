from __future__ import annotations

import ast
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autocontext.kernel_evolution.authority_tensor import copy_tensor_to_device_preserving_abi

_ADAPTER = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "kernel_evolution"
    / "kernelbench_h100"
    / "adapter.py"
)


def _adapter_function(name: str, namespace: dict[str, Any] | None = None) -> Any:
    tree = ast.parse(_ADAPTER.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == name)
    module = ast.Module(body=[function], type_ignores=[])
    ast.fix_missing_locations(module)
    globals_: dict[str, Any] = {} if namespace is None else namespace
    exec(compile(module, str(_ADAPTER), "exec"), globals_)
    return globals_[name]


def test_protected_adapter_rejects_response_patching_candidate_before_dispatch() -> None:
    guard = _adapter_function("_require_protected_mutation_authority")
    fake_main = ModuleType("__main__")
    original_response = object()
    fake_main._response = original_response  # type: ignore[attr-defined]
    attack = compile(
        "import sys\n"
        "sys.modules['__main__']._response = lambda *args, **kwargs: ('forged', b'original-input')\n",
        "adversarial_mutating_candidate.py",
        "exec",
    )
    real_main = sys.modules.get("__main__")
    try:
        sys.modules["__main__"] = fake_main
        exec(attack, {})
    finally:
        if real_main is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = real_main
    assert fake_main._response is not original_response  # type: ignore[attr-defined]

    with pytest.raises(SystemExit, match="trusted out-of-process input-mutation observer"):
        guard(protected_mode=True)

    source = _ADAPTER.read_text(encoding="utf-8")
    assert source.index("_require_protected_mutation_authority(protected_mode=protected_mode)") < source.index(
        "candidate_endpoint.listen()"
    )


def test_mutating_candidate_changes_evaluator_owned_tensor_state() -> None:
    torch = pytest.importorskip("torch")
    equal = _adapter_function("_equal", {"torch": torch})
    candidate_input = torch.arange(6, dtype=torch.float32).reshape(2, 3)
    evaluator_before = candidate_input.clone()

    def mutating_candidate(value):
        value.add_(1)
        return value

    mutating_candidate(candidate_input)
    assert not equal(evaluator_before, candidate_input)


def test_tensor_state_comparison_includes_stride_and_storage_offset() -> None:
    torch = pytest.importorskip("torch")
    equal = _adapter_function("_equal", {"torch": torch})
    contiguous = torch.arange(6).reshape(2, 3)
    transposed_storage = contiguous.t().contiguous().t()

    assert torch.equal(contiguous, transposed_storage)
    assert contiguous.stride() != transposed_storage.stride()
    assert not equal(contiguous, transposed_storage)


def test_tensor_snapshot_preserves_nonzero_offset_without_false_mutation() -> None:
    torch = pytest.importorskip("torch")
    clone = _adapter_function(
        "_clone",
        {
            "copy_tensor_to_device_preserving_abi": copy_tensor_to_device_preserving_abi,
            "torch": torch,
        },
    )
    equal = _adapter_function("_equal", {"torch": torch})
    value = torch.arange(12, dtype=torch.float32)[3:9]

    snapshot = clone(value)

    assert snapshot.data_ptr() != value.data_ptr()
    assert snapshot.stride() == value.stride()
    assert snapshot.storage_offset() == value.storage_offset() == 3
    assert equal(snapshot, value)


def test_tensor_snapshot_detects_actual_mutation_of_offset_view() -> None:
    torch = pytest.importorskip("torch")
    clone = _adapter_function(
        "_clone",
        {
            "copy_tensor_to_device_preserving_abi": copy_tensor_to_device_preserving_abi,
            "torch": torch,
        },
    )
    equal = _adapter_function("_equal", {"torch": torch})
    value = torch.arange(12, dtype=torch.float32)[3:9]
    snapshot = clone(value)

    value.add_(1)

    assert not equal(snapshot, value)


def test_protected_timing_compares_only_shared_evaluator_owned_rpc_boundaries() -> None:
    source = _ADAPTER.read_text(encoding="utf-8")

    assert 'comparable_names = ("candidate_ms", "incumbent_ms") if remote_authority_timing else names' in source
    assert '"candidate_incumbent_comparable": True' in source
    assert '"reference_comparable": not remote_authority_timing' in source
    assert '"promotion_comparison": ["candidate_ms", "incumbent_ms"]' in source

    transport = (_ADAPTER.parent / "authority_transport.py").read_text(encoding="utf-8")
    assert "started = time.perf_counter_ns()" in transport
    assert "return self.last_measurement.elapsed_ns / 1_000_000.0" in transport
