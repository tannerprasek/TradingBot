#!/usr/bin/env python3
"""
Polymarket Market Maker Bot - Entry Point

Usage:
    python run.py                    # Run with .env configuration
    python run.py --dry-run          # Force dry run mode
    python run.py --max-bet 25       # Override max bet size
    python run.py --help             # Show help
"""
import argparse
import logging
import sys
import os

# Ensure we can import from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import Config
from market_maker import MarketMakerBot


def setup_logging(verbose: bool = False):
    """Configure logging"""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)]
    )
    # Reduce noise from third-party libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Polymarket Market Maker Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                     Run with .env settings
  python run.py --dry-run           Simulate without placing orders
  python run.py --max-bet 50        Set maximum bet size to $50
  python run.py -v                  Enable verbose logging

Configuration:
  Copy .env.example to .env and fill in your API credentials.
  Required: PRIVATE_KEY, WALLET_ADDRESS
  Optional: SIGNATURE_TYPE, MAX_BET_SIZE, DRY_RUN, MARKET_IDS
        """
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in simulation mode without placing real orders"
    )

    parser.add_argument(
        "--max-bet",
        type=float,
        help="Maximum bet size in USDC per order"
    )

    parser.add_argument(
        "--max-capital",
        type=float,
        help="Maximum total capital at risk in USDC (default: 100)"
    )

    parser.add_argument(
        "--env",
        type=str,
        default=".env",
        help="Path to environment file (default: .env)"
    )

    parser.add_argument(
        "--markets",
        type=str,
        help="Comma-separated list of market condition IDs to trade"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose/debug logging"
    )

    parser.add_argument(
        "--min-spread",
        type=int,
        help="Minimum spread in basis points (default: 50)"
    )

    parser.add_argument(
        "--target-spread",
        type=int,
        help="Target spread in basis points (default: 100)"
    )

    parser.add_argument(
        "--refresh-interval",
        type=int,
        help="Order refresh interval in seconds (default: 30)"
    )

    return parser.parse_args()


def main():
    """Main entry point"""
    args = parse_args()

    # Setup logging
    setup_logging(verbose=args.verbose)
    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("  POLYMARKET MARKET MAKER BOT")
    logger.info("=" * 60)

    try:
        # Load base configuration from env file
        env_path = args.env if os.path.exists(args.env) else None

        if not env_path and not os.getenv("PRIVATE_KEY"):
            logger.error(
                "No .env file found and PRIVATE_KEY not set.\n"
                "Please copy .env.example to .env and fill in your credentials."
            )
            sys.exit(1)

        config = Config.from_env(env_path)

        # Apply command line overrides
        if args.dry_run:
            config.dry_run = True
            logger.info("Dry run mode enabled via command line")

        if args.max_bet:
            config.max_bet_size = args.max_bet
            logger.info(f"Max bet size set to ${args.max_bet}")

        if args.max_capital:
            config.max_capital = args.max_capital
            logger.info(f"Max capital set to ${args.max_capital}")

        if args.markets:
            config.market_ids = [m.strip() for m in args.markets.split(",")]
            logger.info(f"Trading specific markets: {config.market_ids}")

        if args.min_spread:
            config.min_spread_bps = args.min_spread

        if args.target_spread:
            config.target_spread_bps = args.target_spread

        if args.refresh_interval:
            config.order_refresh_seconds = args.refresh_interval

        # Validate configuration
        config.validate()

        # Print configuration summary
        logger.info(f"Configuration:")
        logger.info(f"  Wallet: {config.wallet_address[:10]}...{config.wallet_address[-6:]}")
        logger.info(f"  Max Bet Size: ${config.max_bet_size}")
        logger.info(f"  Max Capital: ${config.max_capital}")
        logger.info(f"  Dry Run: {config.dry_run}")
        logger.info(f"  Target Spread: {config.target_spread_bps} bps")
        logger.info(f"  Refresh Interval: {config.order_refresh_seconds}s")

        if config.dry_run:
            logger.warning("=" * 60)
            logger.warning("  RUNNING IN DRY RUN MODE - NO REAL ORDERS")
            logger.warning("=" * 60)

        # Create and start bot
        bot = MarketMakerBot(config)
        success = bot.start()

        sys.exit(0 if success else 1)

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
