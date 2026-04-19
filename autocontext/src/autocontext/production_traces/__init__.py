"""Production-traces contract + emit surface for customer-side integration.

Layer 1 exposes only the contract sub-package and a thin `validate` entry point.
Later layers will add `emit`, `hashing`, and CLI integrations.
"""

from autocontext.production_traces.validate import validate_production_trace

__all__ = ["validate_production_trace"]
