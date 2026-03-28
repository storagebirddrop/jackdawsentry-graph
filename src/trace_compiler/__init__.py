"""
Trace compiler — converts raw blockchain facts + attribution data into
stable, lineage-tagged ``ExpansionResponse v2`` payloads.

This package is the semantic boundary between the raw event store
(PostgreSQL) and the investigation-view graph served to the frontend.
The Graph API (src/api/routers/graph.py) delegates ALL chain-specific
logic here; it never issues Cypher or raw SQL directly.
"""

from src.trace_compiler.compiler import TraceCompiler

__all__ = ["TraceCompiler"]
