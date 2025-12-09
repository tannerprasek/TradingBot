"""
Polymarket CLOB Client Wrapper
"""
import logging
import time
from typing import Optional, Dict, List, Any
from dataclasses import dataclass

import httpx
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import OrderArgs, OrderType, OpenOrderParams
from py_clob_client.order_builder.constants import BUY, SELL

from config import Config

logger = logging.getLogger(__name__)


@dataclass
class OrderBookLevel:
    """Single level in order book"""
    price: float
    size: float


@dataclass
class OrderBook:
    """Order book for a token"""
    token_id: str
    bids: List[OrderBookLevel]
    asks: List[OrderBookLevel]
    timestamp: float

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return self.best_ask - self.best_bid
        return None

    @property
    def spread_bps(self) -> Optional[float]:
        """Spread in basis points"""
        if self.spread and self.mid_price:
            return (self.spread / self.mid_price) * 10000
        return None


@dataclass
class Market:
    """Market information"""
    condition_id: str
    question: str
    slug: str
    outcomes: List[str]
    token_ids: List[str]  # YES and NO token IDs
    liquidity: float
    volume: float
    active: bool
    end_date: str


class PolymarketClient:
    """Wrapper around Polymarket CLOB client"""

    def __init__(self, config: Config):
        self.config = config
        self._clob_client: Optional[ClobClient] = None
        self._http_client = httpx.Client(timeout=30.0)

    def connect(self) -> bool:
        """Initialize connection to Polymarket"""
        try:
            self._clob_client = ClobClient(
                self.config.clob_url,
                key=self.config.private_key,
                chain_id=self.config.chain_id,
                signature_type=self.config.signature_type,
                funder=self.config.wallet_address
            )

            # Create or derive API credentials
            api_creds = self._clob_client.create_or_derive_api_creds()
            self._clob_client.set_api_creds(api_creds)

            # Test connection
            ok = self._clob_client.get_ok()
            if ok:
                logger.info("Successfully connected to Polymarket CLOB")
                return True
            else:
                logger.error("Failed to connect to Polymarket CLOB")
                return False

        except Exception as e:
            logger.error(f"Error connecting to Polymarket: {e}")
            return False

    def get_markets(
        self,
        active_only: bool = True,
        min_liquidity: float = 0,
        limit: int = 100
    ) -> List[Market]:
        """Fetch available markets from Gamma API"""
        try:
            params = {
                "limit": limit,
                "active": str(active_only).lower(),
                "closed": "false"
            }

            response = self._http_client.get(
                f"{self.config.gamma_url}/markets",
                params=params
            )
            response.raise_for_status()
            data = response.json()

            markets = []
            for m in data:
                # Skip markets without CLOB token IDs
                clob_token_ids = m.get("clobTokenIds", [])
                if not clob_token_ids or len(clob_token_ids) < 2:
                    continue

                liquidity = float(m.get("liquidity", 0) or 0)
                if liquidity < min_liquidity:
                    continue

                markets.append(Market(
                    condition_id=m.get("conditionId", ""),
                    question=m.get("question", ""),
                    slug=m.get("slug", ""),
                    outcomes=m.get("outcomes", ["Yes", "No"]),
                    token_ids=clob_token_ids,
                    liquidity=liquidity,
                    volume=float(m.get("volume", 0) or 0),
                    active=m.get("active", False),
                    end_date=m.get("endDate", "")
                ))

            return markets

        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []

    def get_order_book(self, token_id: str) -> Optional[OrderBook]:
        """Get order book for a token"""
        try:
            book = self._clob_client.get_order_book(token_id)

            bids = [
                OrderBookLevel(float(b["price"]), float(b["size"]))
                for b in book.get("bids", [])
            ]
            asks = [
                OrderBookLevel(float(a["price"]), float(a["size"]))
                for a in book.get("asks", [])
            ]

            # Sort bids descending, asks ascending
            bids.sort(key=lambda x: x.price, reverse=True)
            asks.sort(key=lambda x: x.price)

            return OrderBook(
                token_id=token_id,
                bids=bids,
                asks=asks,
                timestamp=time.time()
            )

        except Exception as e:
            logger.error(f"Error fetching order book for {token_id}: {e}")
            return None

    def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get midpoint price for a token"""
        try:
            mid = self._clob_client.get_midpoint(token_id)
            return float(mid) if mid else None
        except Exception as e:
            logger.error(f"Error getting midpoint for {token_id}: {e}")
            return None

    def place_limit_order(
        self,
        token_id: str,
        side: str,  # "BUY" or "SELL"
        price: float,
        size: float,
        dry_run: bool = False
    ) -> Optional[Dict[str, Any]]:
        """Place a limit order"""
        try:
            side_const = BUY if side == "BUY" else SELL

            order_args = OrderArgs(
                token_id=token_id,
                price=price,
                size=size,
                side=side_const
            )

            if dry_run:
                logger.info(f"[DRY RUN] Would place {side} order: {size} @ {price} for {token_id}")
                return {"dry_run": True, "order": str(order_args)}

            signed_order = self._clob_client.create_order(order_args)
            response = self._clob_client.post_order(signed_order, OrderType.GTC)

            logger.info(f"Placed {side} order: {size} @ {price}, response: {response}")
            return response

        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return None

    def get_open_orders(self, market: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get all open orders"""
        try:
            params = OpenOrderParams(market=market) if market else OpenOrderParams()
            orders = self._clob_client.get_orders(params)
            return orders if orders else []
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []

    def cancel_order(self, order_id: str, dry_run: bool = False) -> bool:
        """Cancel a specific order"""
        try:
            if dry_run:
                logger.info(f"[DRY RUN] Would cancel order: {order_id}")
                return True

            response = self._clob_client.cancel(order_id)
            logger.info(f"Cancelled order {order_id}: {response}")
            return True
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False

    def cancel_all_orders(self, dry_run: bool = False) -> bool:
        """Cancel all open orders"""
        try:
            if dry_run:
                logger.info("[DRY RUN] Would cancel all orders")
                return True

            response = self._clob_client.cancel_all()
            logger.info(f"Cancelled all orders: {response}")
            return True
        except Exception as e:
            logger.error(f"Error cancelling all orders: {e}")
            return False

    def get_positions(self) -> Dict[str, float]:
        """Get current positions (placeholder - needs implementation based on actual API)"""
        # The actual position tracking would need to query on-chain balances
        # or track fills from trade history
        try:
            trades = self._clob_client.get_trades()
            # Aggregate positions from trade history
            positions: Dict[str, float] = {}
            for trade in (trades or []):
                token_id = trade.get("asset_id", "")
                side = trade.get("side", "")
                size = float(trade.get("size", 0))

                if token_id not in positions:
                    positions[token_id] = 0.0

                if side == "BUY":
                    positions[token_id] += size
                else:
                    positions[token_id] -= size

            return positions
        except Exception as e:
            logger.error(f"Error getting positions: {e}")
            return {}

    def close(self):
        """Close connections"""
        if self._http_client:
            self._http_client.close()
