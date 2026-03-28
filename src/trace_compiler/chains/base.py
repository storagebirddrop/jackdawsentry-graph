"""
BaseChainCompiler — abstract interface for chain-specific trace compilers.

Each chain compiler receives a raw event store query result and an
attribution context, and emits a list of ``InvestigationNode`` objects and
``InvestigationEdge`` objects to be included in the ``ExpansionResponseV2``.

Concrete implementations live in sibling modules:
- bitcoin.py  (UTXOChainCompiler)
- evm.py      (EVMChainCompiler — handles all EVM variants)
- solana.py   (SolanaChainCompiler — most complex, instruction decomposition)
- tron.py     (TronChainCompiler)
- xrp.py      (XRPChainCompiler)

Phase 3 status: interface defined, no implementations yet.
"""

from abc import ABC
from abc import abstractmethod
from contextvars import ContextVar
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from src.trace_compiler.models import ExpandOptions
from src.trace_compiler.models import InvestigationEdge
from src.trace_compiler.models import InvestigationNode


class BaseChainCompiler(ABC):
    """Abstract base class for chain-specific trace compilers.

    Each subclass handles the chain-specific semantics for converting raw
    event store records into investigation-view nodes and edges.

    Args:
        postgres_pool: asyncpg pool for event store reads.
        neo4j_driver:  Neo4j driver for canonical graph reads.
        redis_client:  Redis client for ATA / ALT / service caches.
    """

    def __init__(self, postgres_pool=None, neo4j_driver=None, redis_client=None):
        self._pg = postgres_pool
        self._neo4j = neo4j_driver
        self._redis = redis_client
        self._expansion_data_sources: ContextVar[tuple[str, ...]] = ContextVar(
            f"{self.__class__.__name__}.expansion_data_sources",
            default=(),
        )
        from src.trace_compiler.bridges.hop_compiler import BridgeHopCompiler
        from src.trace_compiler.services.service_classifier import ServiceClassifier
        self._bridge = BridgeHopCompiler(postgres_pool=postgres_pool, redis_client=redis_client)
        self._service = ServiceClassifier(postgres_pool=postgres_pool, neo4j_driver=neo4j_driver)

    def _set_expansion_data_sources(self, *sources: str) -> None:
        """Record the backing stores used for the current expansion call."""
        ordered: list[str] = []
        seen: set[str] = set()
        for source in sources:
            if not source or source in seen:
                continue
            seen.add(source)
            ordered.append(source)
        self._expansion_data_sources.set(tuple(ordered))

    def _consume_expansion_data_sources(self) -> List[str]:
        """Return and clear the backing-store markers for the current call."""
        sources = list(self._expansion_data_sources.get(()))
        self._expansion_data_sources.set(())
        return sources

    @property
    @abstractmethod
    def supported_chains(self) -> List[str]:
        """Return the list of chain names this compiler handles."""

    @abstractmethod
    async def expand_next(
        self,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        seed_address: str,
        chain: str,
        options: ExpandOptions,
    ) -> tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Follow funds forward from seed_address.

        Args:
            session_id:   Investigation session UUID.
            branch_id:    Branch ID for lineage assignment.
            path_id:      Path ID for lineage assignment.
            depth:        Current hop depth from session root.
            seed_address: Address whose outbound transfers to return.
            chain:        Blockchain name.
            options:      Expansion options (filters, limits).

        Returns:
            Tuple of (nodes, edges) to add to the investigation view.
        """

    @abstractmethod
    async def expand_prev(
        self,
        session_id: str,
        branch_id: str,
        path_id: str,
        depth: int,
        seed_address: str,
        chain: str,
        options: ExpandOptions,
    ) -> tuple[List[InvestigationNode], List[InvestigationEdge]]:
        """Follow funds backward toward seed_address.

        Args: same as expand_next.

        Returns:
            Tuple of (nodes, edges) to add to the investigation view.
        """
