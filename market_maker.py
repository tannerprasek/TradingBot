"""
Polymarket Market Maker Bot
Main execution engine that orchestrates the market making strategy.
"""
import logging
import signal
import time
import sys
from typing import List, Dict, Optional, Set
from dataclasses import dataclass

from config import Config
from polymarket_client import PolymarketClient, Market, OrderBook
from strategy import MarketMakingStrategy, Quote, StrategyState

logger = logging.getLogger(__name__)


@dataclass
class OrderTracker:
    """Track an active order"""
    order_id: str
    token_id: str
    side: str
    price: float
    size: float
    placed_at: float


class MarketMakerBot:
    """
    Main market maker bot that:
    1. Selects markets to trade
    2. Monitors order books
    3. Places and manages quotes
    4. Handles risk management
    """

    def __init__(self, config: Config):
        self.config = config
        self.client = PolymarketClient(config)
        self.strategy = MarketMakingStrategy(config, self.client)
        self.active_orders: Dict[str, OrderTracker] = {}
        self.running = False
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        logger.info(f"Received signal {signum}, initiating graceful shutdown...")
        self.running = False

    def select_markets(self) -> List[Market]:
        """Select markets to trade"""
        if self.config.market_ids:
            # User specified specific markets
            all_markets = self.client.get_markets(active_only=True)
            return [m for m in all_markets if m.condition_id in self.config.market_ids]
        else:
            # Auto-select liquid markets
            markets = self.client.get_markets(
                active_only=True,
                min_liquidity=self.config.min_liquidity,
                limit=50
            )

            # Sort by liquidity and take top markets
            markets.sort(key=lambda m: m.liquidity, reverse=True)

            # Filter for markets with reasonable activity
            selected = []
            for market in markets[:10]:  # Top 10 by liquidity
                if market.volume > 100:  # Some minimum volume
                    selected.append(market)

            logger.info(f"Auto-selected {len(selected)} markets for trading")
            return selected

    def cancel_stale_orders(self, state: StrategyState, max_age_seconds: float = 60):
        """Cancel orders older than threshold"""
        now = time.time()
        to_cancel = []

        for order_id, tracker in list(self.active_orders.items()):
            if tracker.token_id in [state.yes_token_id, state.no_token_id]:
                if now - tracker.placed_at > max_age_seconds:
                    to_cancel.append(order_id)

        for order_id in to_cancel:
            if self.client.cancel_order(order_id, dry_run=self.config.dry_run):
                del self.active_orders[order_id]
                logger.info(f"Cancelled stale order {order_id}")

    def place_quotes(self, quotes: List[Quote]) -> List[str]:
        """Place quotes and track them"""
        order_ids = []

        for quote in quotes:
            result = self.client.place_limit_order(
                token_id=quote.token_id,
                side=quote.side,
                price=quote.price,
                size=quote.size,
                dry_run=self.config.dry_run
            )

            if result and not self.config.dry_run:
                order_id = result.get("orderID", result.get("id", ""))
                if order_id:
                    self.active_orders[order_id] = OrderTracker(
                        order_id=order_id,
                        token_id=quote.token_id,
                        side=quote.side,
                        price=quote.price,
                        size=quote.size,
                        placed_at=time.time()
                    )
                    order_ids.append(order_id)

        return order_ids

    def refresh_orders_for_market(self, state: StrategyState):
        """Cancel existing orders and place new quotes for a market"""
        # Get current order books
        yes_book = self.client.get_order_book(state.yes_token_id)

        if not yes_book:
            logger.warning(f"Failed to get order book for {state.market.question[:30]}")
            return

        # Check if we should requote
        if not self.strategy.should_requote(state, yes_book):
            return

        # Cancel existing orders for this market
        self.cancel_stale_orders(state, max_age_seconds=0)  # Cancel all

        # Get new quotes
        no_book = None
        if state.no_token_id:
            no_book = self.client.get_order_book(state.no_token_id)

        quotes = self.strategy.evaluate_market(state, yes_book, no_book)

        if quotes:
            logger.info(
                f"Placing {len(quotes)} quotes for {state.market.question[:40]}... "
                f"(mid={yes_book.mid_price:.4f})"
            )
            self.place_quotes(quotes)

    def run_once(self):
        """Run one iteration of the market making loop"""
        for condition_id, state in self.strategy.states.items():
            try:
                self.refresh_orders_for_market(state)
            except Exception as e:
                logger.error(f"Error processing market {condition_id}: {e}")

    def print_status(self):
        """Print current status"""
        logger.info("=" * 60)
        logger.info("MARKET MAKER STATUS")
        logger.info("=" * 60)
        logger.info(f"Active orders: {len(self.active_orders)}")
        logger.info(f"Dry run mode: {self.config.dry_run}")

        for state in self.strategy.states.values():
            summary = self.strategy.get_market_summary(state)
            logger.info(
                f"  {summary['market']}: "
                f"pos_yes={summary['position_yes']:.2f}, "
                f"pos_no={summary['position_no']:.2f}, "
                f"mid={summary['last_mid']}"
            )
        logger.info("=" * 60)

    def start(self):
        """Start the market maker bot"""
        logger.info("Starting Polymarket Market Maker Bot")
        logger.info(f"Max bet size: ${self.config.max_bet_size}")
        logger.info(f"Dry run mode: {self.config.dry_run}")

        # Connect to Polymarket
        if not self.client.connect():
            logger.error("Failed to connect to Polymarket. Exiting.")
            return False

        # Select markets
        markets = self.select_markets()
        if not markets:
            logger.error("No markets selected for trading. Exiting.")
            return False

        logger.info(f"Selected {len(markets)} markets for trading:")
        for market in markets:
            logger.info(f"  - {market.question[:60]}... (liquidity: ${market.liquidity:,.0f})")
            self.strategy.initialize_market(market)

        # Main loop
        self.running = True
        iteration = 0

        try:
            while self.running:
                iteration += 1

                try:
                    self.run_once()
                except Exception as e:
                    logger.error(f"Error in main loop: {e}")

                # Print status every 10 iterations
                if iteration % 10 == 0:
                    self.print_status()

                # Sleep before next iteration
                time.sleep(self.config.order_refresh_seconds)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")

        finally:
            self.shutdown()

        return True

    def shutdown(self):
        """Graceful shutdown"""
        logger.info("Shutting down market maker...")

        # Cancel all orders
        if not self.config.dry_run:
            logger.info("Cancelling all open orders...")
            self.client.cancel_all_orders()

        # Close connections
        self.client.close()

        logger.info("Market maker shutdown complete")


def main():
    """Main entry point"""
    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ]
    )

    try:
        # Load configuration
        config = Config.from_env()
        config.validate()

        # Create and start bot
        bot = MarketMakerBot(config)
        bot.start()

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
