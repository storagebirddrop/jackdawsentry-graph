"""Jackdaw Sentry tracing package.

The heavy exposure helpers are imported lazily so submodules such as
``src.tracing.bridge_registry`` can be used without pulling the entire tracing
stack into lightweight environments like the standalone graph repo.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "compute_exposure",
    "ExposureResult",
    "TaintHalt",
    "TaintMethodology",
]


def __getattr__(name: str) -> Any:
    if name in __all__:
        from src.tracing.exposure import ExposureResult
        from src.tracing.exposure import TaintHalt
        from src.tracing.exposure import TaintMethodology
        from src.tracing.exposure import compute_exposure

        exports = {
            "compute_exposure": compute_exposure,
            "ExposureResult": ExposureResult,
            "TaintHalt": TaintHalt,
            "TaintMethodology": TaintMethodology,
        }
        return exports[name]
    raise AttributeError(f"module 'src.tracing' has no attribute {name!r}")
