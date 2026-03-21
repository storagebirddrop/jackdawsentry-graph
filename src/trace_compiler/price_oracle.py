"""Optional bulk price-oracle wrapper for the standalone graph compiler.

Resolution order:
1. ``src.services.price_oracle`` — standalone CoinGecko oracle (graph repo)
2. ``src.intelligence.price_oracle`` — private-platform oracle (fallback)
3. ``_NullBulkPriceOracle`` — returns None for all assets; graph still works
   but fiat value filtering is disabled
"""

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
        """Return None for every asset — no fiat data available."""
        return {asset_id: None for asset_id in asset_ids}


try:
    from src.services.price_oracle import get_price_oracle as _get
    price_oracle = _get()
except Exception:
    try:
        from src.intelligence.price_oracle import price_oracle as price_oracle
    except ImportError:
        price_oracle = _NullBulkPriceOracle()
