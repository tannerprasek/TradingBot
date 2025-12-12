"""
Test suite for the Polymarket Market Maker Bot
Tests components without requiring real API keys.
"""
import unittest
from unittest.mock import Mock, patch, MagicMock
import time

from config import Config
from polymarket_client import OrderBook, OrderBookLevel, Market
from strategy import MarketMakingStrategy, StrategyState, Quote


class TestConfig(unittest.TestCase):
    """Test configuration loading and validation"""

    def test_config_validation_positive_bet_size(self):
        """Test that max_bet_size must be positive"""
        config = Config(
            private_key="test_key",
            wallet_address="0x1234567890abcdef",
            signature_type=0,
            max_bet_size=-10,
            max_capital=100.0,
            market_ids=[],
            dry_run=True
        )
        with self.assertRaises(ValueError):
            config.validate()

    def test_config_validation_valid(self):
        """Test valid configuration passes validation"""
        config = Config(
            private_key="test_key",
            wallet_address="0x1234567890abcdef",
            signature_type=0,
            max_bet_size=10.0,
            max_capital=100.0,
            market_ids=[],
            dry_run=True
        )
        self.assertTrue(config.validate())

    def test_config_signature_type_validation(self):
        """Test signature type must be 0, 1, or 2"""
        config = Config(
            private_key="test_key",
            wallet_address="0x1234567890abcdef",
            signature_type=5,
            max_bet_size=10.0,
            max_capital=100.0,
            market_ids=[],
            dry_run=True
        )
        with self.assertRaises(ValueError):
            config.validate()


class TestOrderBook(unittest.TestCase):
    """Test order book calculations"""

    def setUp(self):
        """Create sample order book"""
        self.book = OrderBook(
            token_id="test_token",
            bids=[
                OrderBookLevel(price=0.50, size=100),
                OrderBookLevel(price=0.49, size=200),
            ],
            asks=[
                OrderBookLevel(price=0.52, size=150),
                OrderBookLevel(price=0.53, size=250),
            ],
            timestamp=time.time()
        )

    def test_best_bid(self):
        """Test best bid calculation"""
        self.assertEqual(self.book.best_bid, 0.50)

    def test_best_ask(self):
        """Test best ask calculation"""
        self.assertEqual(self.book.best_ask, 0.52)

    def test_mid_price(self):
        """Test mid price calculation"""
        self.assertEqual(self.book.mid_price, 0.51)

    def test_spread(self):
        """Test spread calculation"""
        self.assertAlmostEqual(self.book.spread, 0.02, places=4)

    def test_spread_bps(self):
        """Test spread in basis points"""
        # spread / mid_price * 10000 = 0.02 / 0.51 * 10000 ≈ 392
        self.assertAlmostEqual(self.book.spread_bps, 392.16, places=0)

    def test_empty_book(self):
        """Test empty order book"""
        empty_book = OrderBook(
            token_id="test",
            bids=[],
            asks=[],
            timestamp=time.time()
        )
        self.assertIsNone(empty_book.best_bid)
        self.assertIsNone(empty_book.best_ask)
        self.assertIsNone(empty_book.mid_price)
        self.assertIsNone(empty_book.spread)


class TestStrategy(unittest.TestCase):
    """Test market making strategy"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = Config(
            private_key="test_key",
            wallet_address="0x1234567890abcdef",
            signature_type=0,
            max_bet_size=10.0,
            max_capital=100.0,
            market_ids=[],
            dry_run=True,
            min_spread_bps=50,
            target_spread_bps=100,
            max_position_size=100.0
        )

        self.mock_client = Mock()
        self.strategy = MarketMakingStrategy(self.config, self.mock_client)

        self.market = Market(
            condition_id="test_condition",
            question="Will it rain tomorrow?",
            slug="rain-tomorrow",
            outcomes=["Yes", "No"],
            token_ids=["yes_token_123", "no_token_456"],
            liquidity=10000.0,
            volume=5000.0,
            active=True,
            end_date="2025-01-01"
        )

    def test_initialize_market(self):
        """Test market initialization"""
        state = self.strategy.initialize_market(self.market)

        self.assertEqual(state.market, self.market)
        self.assertEqual(state.yes_token_id, "yes_token_123")
        self.assertEqual(state.no_token_id, "no_token_456")
        self.assertEqual(state.position_yes, 0.0)
        self.assertEqual(state.position_no, 0.0)

    def test_calculate_fair_value(self):
        """Test fair value calculation"""
        book = OrderBook(
            token_id="test",
            bids=[OrderBookLevel(0.48, 100)],
            asks=[OrderBookLevel(0.52, 100)],
            timestamp=time.time()
        )

        # With equal sizes, microprice = midpoint
        fair_value = self.strategy.calculate_fair_value(book)
        self.assertAlmostEqual(fair_value, 0.50, places=2)

    def test_calculate_fair_value_weighted(self):
        """Test microprice weighting"""
        book = OrderBook(
            token_id="test",
            bids=[OrderBookLevel(0.48, 50)],   # Small bid
            asks=[OrderBookLevel(0.52, 150)],  # Large ask
            timestamp=time.time()
        )

        # Microprice should be closer to bid (more size on ask)
        fair_value = self.strategy.calculate_fair_value(book)
        # microprice = (0.48 * 150 + 0.52 * 50) / 200 = (72 + 26) / 200 = 0.49
        self.assertAlmostEqual(fair_value, 0.49, places=2)

    def test_inventory_skew_neutral(self):
        """Test no skew with neutral position"""
        state = self.strategy.initialize_market(self.market)
        state.position_yes = 0
        state.position_no = 0

        skew = self.strategy.calculate_inventory_skew(state)
        self.assertEqual(skew, 0)

    def test_inventory_skew_long(self):
        """Test negative skew when long YES"""
        state = self.strategy.initialize_market(self.market)
        state.position_yes = 50  # Half of max position
        state.position_no = 0

        skew = self.strategy.calculate_inventory_skew(state)
        # Should be positive to lower asks
        self.assertGreater(skew, 0)

    def test_inventory_skew_short(self):
        """Test positive skew when short YES (long NO)"""
        state = self.strategy.initialize_market(self.market)
        state.position_yes = 0
        state.position_no = 50

        skew = self.strategy.calculate_inventory_skew(state)
        # Should be negative to lower bids
        self.assertLess(skew, 0)

    def test_calculate_quotes(self):
        """Test quote calculation"""
        state = self.strategy.initialize_market(self.market)
        book = OrderBook(
            token_id="yes_token_123",
            bids=[OrderBookLevel(0.48, 100)],
            asks=[OrderBookLevel(0.52, 100)],
            timestamp=time.time()
        )

        bid_quote, ask_quote = self.strategy.calculate_quotes(state, book)

        self.assertIsNotNone(bid_quote)
        self.assertIsNotNone(ask_quote)
        self.assertEqual(bid_quote.side, "BUY")
        self.assertEqual(ask_quote.side, "SELL")
        self.assertLess(bid_quote.price, ask_quote.price)

        # Check spread is at least minimum
        spread = ask_quote.price - bid_quote.price
        min_spread = self.config.min_spread_bps / 10000
        self.assertGreaterEqual(spread, min_spread)

    def test_quotes_stay_in_bounds(self):
        """Test quotes stay within 0-1 range"""
        state = self.strategy.initialize_market(self.market)

        # Test edge case near 0
        book_low = OrderBook(
            token_id="yes_token_123",
            bids=[OrderBookLevel(0.02, 100)],
            asks=[OrderBookLevel(0.04, 100)],
            timestamp=time.time()
        )
        bid_quote, ask_quote = self.strategy.calculate_quotes(state, book_low)
        self.assertGreaterEqual(bid_quote.price, 0.01)
        self.assertLessEqual(ask_quote.price, 0.99)

        # Test edge case near 1
        book_high = OrderBook(
            token_id="yes_token_123",
            bids=[OrderBookLevel(0.96, 100)],
            asks=[OrderBookLevel(0.98, 100)],
            timestamp=time.time()
        )
        bid_quote, ask_quote = self.strategy.calculate_quotes(state, book_high)
        self.assertGreaterEqual(bid_quote.price, 0.01)
        self.assertLessEqual(ask_quote.price, 0.99)

    def test_position_limits(self):
        """Test quotes respect position limits"""
        state = self.strategy.initialize_market(self.market)
        state.position_yes = 95  # Near max

        book = OrderBook(
            token_id="yes_token_123",
            bids=[OrderBookLevel(0.48, 100)],
            asks=[OrderBookLevel(0.52, 100)],
            timestamp=time.time()
        )

        bid_quote, ask_quote = self.strategy.calculate_quotes(state, book)

        # Bid size should be limited due to position
        self.assertLessEqual(bid_quote.size, 5.0)  # max - current = 100 - 95

    def test_update_position(self):
        """Test position tracking"""
        state = self.strategy.initialize_market(self.market)

        self.strategy.update_position(
            state,
            token_id="yes_token_123",
            side="BUY",
            size=10.0,
            price=0.50
        )

        self.assertEqual(state.position_yes, 10.0)
        self.assertEqual(state.position_no, 0.0)

        self.strategy.update_position(
            state,
            token_id="yes_token_123",
            side="SELL",
            size=5.0,
            price=0.55
        )

        self.assertEqual(state.position_yes, 5.0)

    def test_should_requote(self):
        """Test requote logic"""
        state = self.strategy.initialize_market(self.market)
        state.last_mid_price = 0.50

        # Small price change - no requote
        book_small = OrderBook(
            token_id="test",
            bids=[OrderBookLevel(0.495, 100)],
            asks=[OrderBookLevel(0.505, 100)],
            timestamp=time.time()
        )
        self.assertFalse(self.strategy.should_requote(state, book_small))

        # Large price change - requote
        book_large = OrderBook(
            token_id="test",
            bids=[OrderBookLevel(0.54, 100)],
            asks=[OrderBookLevel(0.56, 100)],
            timestamp=time.time()
        )
        self.assertTrue(self.strategy.should_requote(state, book_large))


class TestMarketMakerIntegration(unittest.TestCase):
    """Integration tests with mocked API"""

    def setUp(self):
        """Set up test fixtures"""
        self.config = Config(
            private_key="test_key",
            wallet_address="0x1234567890abcdef",
            signature_type=0,
            max_bet_size=10.0,
            max_capital=100.0,
            market_ids=[],
            dry_run=True
        )

    @patch('polymarket_client.ClobClient')
    @patch('polymarket_client.httpx.Client')
    def test_market_selection(self, mock_http, mock_clob):
        """Test market auto-selection"""
        from polymarket_client import PolymarketClient

        # Setup mock HTTP response
        mock_response = Mock()
        mock_response.json.return_value = [
            {
                "conditionId": "cond1",
                "question": "Test Market 1",
                "slug": "test-1",
                "outcomes": ["Yes", "No"],
                "clobTokenIds": ["token1", "token2"],
                "liquidity": 5000,
                "volume": 1000,
                "active": True,
                "endDate": "2025-01-01"
            },
            {
                "conditionId": "cond2",
                "question": "Test Market 2",
                "slug": "test-2",
                "outcomes": ["Yes", "No"],
                "clobTokenIds": ["token3", "token4"],
                "liquidity": 10000,
                "volume": 2000,
                "active": True,
                "endDate": "2025-01-01"
            }
        ]
        mock_response.raise_for_status = Mock()
        mock_http.return_value.get.return_value = mock_response

        client = PolymarketClient(self.config)
        markets = client.get_markets(min_liquidity=1000)

        self.assertEqual(len(markets), 2)
        self.assertEqual(markets[0].condition_id, "cond1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
