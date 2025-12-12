"""
Market Making Strategy for Polymarket
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Tuple
from enum import Enum

from config import Config
from polymarket_client import PolymarketClient, OrderBook, Market

logger = logging.getLogger(__name__)


class QuoteAction(Enum):
    """Actions the strategy can take"""
    PLACE_BOTH = "place_both"
    PLACE_BID = "place_bid"
    PLACE_ASK = "place_ask"
    CANCEL_ALL = "cancel_all"
    HOLD = "hold"


@dataclass
class Quote:
    """A quote to place in the market"""
    token_id: str
    side: str  # "BUY" or "SELL"
    price: float
    size: float


@dataclass
class StrategyState:
    """Track state for a single market"""
    market: Market
    yes_token_id: str
    no_token_id: str
    position_yes: float = 0.0
    position_no: float = 0.0
    active_bids: List[str] = field(default_factory=list)
    active_asks: List[str] = field(default_factory=list)
    last_mid_price: Optional[float] = None
    # P&L tracking
    realized_pnl: float = 0.0
    total_bought: float = 0.0  # Total $ spent buying
    total_sold: float = 0.0    # Total $ received selling
    num_trades: int = 0
    spread_captured: float = 0.0  # Estimated spread captured


class MarketMakingStrategy:
    """
    Simple market making strategy for Polymarket prediction markets.

    Key concepts:
    - In prediction markets, YES + NO probabilities should sum to ~1
    - We quote both sides with a spread to capture the bid-ask difference
    - Risk management: limit position size and skew quotes based on inventory
    """

    def __init__(self, config: Config, client: PolymarketClient):
        self.config = config
        self.client = client
        self.states: Dict[str, StrategyState] = {}

    def initialize_market(self, market: Market) -> StrategyState:
        """Initialize strategy state for a market"""
        state = StrategyState(
            market=market,
            yes_token_id=market.token_ids[0],
            no_token_id=market.token_ids[1] if len(market.token_ids) > 1 else ""
        )
        self.states[market.condition_id] = state
        logger.info(f"Initialized strategy for market: {market.question[:50]}...")
        return state

    def calculate_fair_value(self, order_book: OrderBook) -> Optional[float]:
        """
        Calculate fair value from order book.
        Uses microprice (volume-weighted midpoint) for better estimate.
        """
        if not order_book.best_bid or not order_book.best_ask:
            return order_book.mid_price

        # Calculate microprice - weighted by size at top of book
        bid_size = order_book.bids[0].size if order_book.bids else 0
        ask_size = order_book.asks[0].size if order_book.asks else 0
        total_size = bid_size + ask_size

        if total_size == 0:
            return order_book.mid_price

        # Microprice weights toward the side with more size
        microprice = (
            order_book.best_bid * ask_size +
            order_book.best_ask * bid_size
        ) / total_size

        return microprice

    def calculate_inventory_skew(self, state: StrategyState) -> float:
        """
        Calculate how much to skew quotes based on inventory.
        Positive = long YES, negative = short YES (long NO).
        Returns skew in price units.
        """
        net_position = state.position_yes - state.position_no
        max_position = self.config.max_position_size

        # Normalize position to [-1, 1] range
        position_ratio = net_position / max_position if max_position > 0 else 0
        position_ratio = max(-1, min(1, position_ratio))

        # Skew in basis points, scaled by position
        # If long, we want to lower asks (encourage selling) and raise bids less
        max_skew_bps = 50  # Maximum skew in basis points
        skew = position_ratio * max_skew_bps / 10000

        return skew

    def calculate_quotes(
        self,
        state: StrategyState,
        order_book: OrderBook
    ) -> Tuple[Optional[Quote], Optional[Quote]]:
        """
        Calculate bid and ask quotes for a market.
        Returns (bid_quote, ask_quote).
        """
        fair_value = self.calculate_fair_value(order_book)
        if fair_value is None:
            logger.warning(f"Cannot calculate fair value for {order_book.token_id}")
            return None, None

        # Update last mid price
        state.last_mid_price = fair_value

        # Calculate spread in price terms
        target_spread = self.config.target_spread_bps / 10000
        half_spread = target_spread / 2

        # Apply inventory skew
        skew = self.calculate_inventory_skew(state)

        # Calculate raw prices
        bid_price = fair_value - half_spread - skew
        ask_price = fair_value + half_spread - skew

        # Ensure prices are valid for prediction markets (0-1 range)
        bid_price = max(0.01, min(0.99, bid_price))
        ask_price = max(0.01, min(0.99, ask_price))

        # Ensure minimum spread
        min_spread = self.config.min_spread_bps / 10000
        if ask_price - bid_price < min_spread:
            # Widen spread symmetrically
            mid = (ask_price + bid_price) / 2
            bid_price = mid - min_spread / 2
            ask_price = mid + min_spread / 2

        # Calculate order sizes based on inventory
        base_size = self.config.max_bet_size
        max_pos = self.config.max_position_size

        # Reduce size if approaching position limits
        remaining_long = max_pos - state.position_yes
        remaining_short = max_pos - state.position_no

        bid_size = min(base_size, max(0.1, remaining_long))
        ask_size = min(base_size, max(0.1, remaining_short))

        bid_quote = Quote(
            token_id=order_book.token_id,
            side="BUY",
            price=round(bid_price, 4),
            size=round(bid_size, 2)
        ) if bid_size > 0 else None

        ask_quote = Quote(
            token_id=order_book.token_id,
            side="SELL",
            price=round(ask_price, 4),
            size=round(ask_size, 2)
        ) if ask_size > 0 else None

        return bid_quote, ask_quote

    def should_requote(
        self,
        state: StrategyState,
        current_book: OrderBook
    ) -> bool:
        """Determine if we should cancel and replace quotes"""
        if state.last_mid_price is None:
            return True

        current_mid = current_book.mid_price
        if current_mid is None:
            return False

        # Requote if price moved more than 0.5%
        price_change = abs(current_mid - state.last_mid_price) / state.last_mid_price
        if price_change > 0.005:
            return True

        return False

    def evaluate_market(
        self,
        state: StrategyState,
        yes_book: Optional[OrderBook],
        no_book: Optional[OrderBook]
    ) -> List[Quote]:
        """
        Evaluate a market and return quotes to place.
        For simplicity, we only quote the YES token (NO is the complement).
        """
        quotes = []

        if not yes_book:
            logger.warning(f"No order book for YES token in {state.market.question[:30]}")
            return quotes

        bid_quote, ask_quote = self.calculate_quotes(state, yes_book)

        if bid_quote:
            quotes.append(bid_quote)
        if ask_quote:
            quotes.append(ask_quote)

        return quotes

    def update_position(
        self,
        state: StrategyState,
        token_id: str,
        side: str,
        size: float,
        price: float
    ):
        """Update position and P&L after a fill"""
        trade_value = size * price
        state.num_trades += 1

        if token_id == state.yes_token_id:
            if side == "BUY":
                state.position_yes += size
                state.total_bought += trade_value
            else:
                state.position_yes -= size
                state.total_sold += trade_value
        elif token_id == state.no_token_id:
            if side == "BUY":
                state.position_no += size
                state.total_bought += trade_value
            else:
                state.position_no -= size
                state.total_sold += trade_value

        # Calculate realized P&L (simplified: sold - bought when flat)
        state.realized_pnl = state.total_sold - state.total_bought

        # Estimate spread captured (half spread per round trip)
        if state.num_trades >= 2:
            avg_spread = self.config.target_spread_bps / 10000
            round_trips = state.num_trades // 2
            avg_trade_size = (state.total_bought + state.total_sold) / state.num_trades
            state.spread_captured = round_trips * avg_spread * avg_trade_size

        logger.info(
            f"FILL: {side} {size:.2f} @ {price:.4f} = ${trade_value:.2f} | "
            f"P&L: ${state.realized_pnl:.2f} | Trades: {state.num_trades}"
        )

    def get_market_summary(self, state: StrategyState) -> Dict:
        """Get summary of market state"""
        # Calculate unrealized P&L
        unrealized = 0.0
        if state.last_mid_price and state.position_yes > 0:
            unrealized = state.position_yes * state.last_mid_price - state.total_bought

        return {
            "market": state.market.question[:50],
            "position_yes": state.position_yes,
            "position_no": state.position_no,
            "net_position": state.position_yes - state.position_no,
            "last_mid": state.last_mid_price,
            "active_orders": len(state.active_bids) + len(state.active_asks),
            "realized_pnl": state.realized_pnl,
            "unrealized_pnl": unrealized,
            "total_pnl": state.realized_pnl + unrealized,
            "num_trades": state.num_trades,
            "spread_captured": state.spread_captured
        }

    def get_total_pnl(self) -> Dict:
        """Get aggregate P&L across all markets"""
        total_realized = 0.0
        total_unrealized = 0.0
        total_trades = 0
        total_spread = 0.0

        for state in self.states.values():
            summary = self.get_market_summary(state)
            total_realized += summary["realized_pnl"]
            total_unrealized += summary["unrealized_pnl"]
            total_trades += summary["num_trades"]
            total_spread += summary["spread_captured"]

        return {
            "realized_pnl": total_realized,
            "unrealized_pnl": total_unrealized,
            "total_pnl": total_realized + total_unrealized,
            "num_trades": total_trades,
            "spread_captured": total_spread
        }
