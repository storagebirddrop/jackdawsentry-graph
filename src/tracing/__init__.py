"""Jackdaw Sentry — Tracing package.

Provides fund-tracing and exposure computation logic that operates on
pre-fetched transaction graphs. Import the public API from this package:

    from src.tracing.exposure import compute_exposure, TaintMethodology, ExposureResult
"""

from src.tracing.exposure import ExposureResult
from src.tracing.exposure import TaintHalt
from src.tracing.exposure import TaintMethodology
from src.tracing.exposure import compute_exposure

__all__ = [
    "compute_exposure",
    "ExposureResult",
    "TaintHalt",
    "TaintMethodology",
]
