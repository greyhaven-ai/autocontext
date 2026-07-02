"""autocontext control-plane Python parity surface (AC-728).

Intentionally thin: the full control plane lives in ts/src/control-plane/
(runtime, contract, promotion, registry, and CLI layers). This package
holds only the Python-side contract probes and change-proposal harness
needed to keep the two runtimes aligned.
"""
