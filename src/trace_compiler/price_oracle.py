"""Optional bulk price-oracle wrapper for the standalone graph compiler."""

from __future__ import annotations

from typing import Dict
from typing import List
from typing import Optional


class _NullBulkPriceOracle:
    """Fallback oracle that preserves graph behavior without fiat prices."""

    async def get_prices_bulk(
        self,
        asset_ids: List[str],
    ) -> Dict[str, Optional[float]]:
        return {asset_id: None for asset_id in asset_ids}


try:
    from src.intelligence.price_oracle import price_oracle as price_oracle
except ImportError:
    price_oracle = _NullBulkPriceOracle()
