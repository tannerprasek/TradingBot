"""
Configuration management for Polymarket Market Maker Bot
"""
import os
from dataclasses import dataclass
from typing import Optional, List
from dotenv import load_dotenv


@dataclass
class Config:
    """Bot configuration loaded from environment variables"""

    # API Configuration
    private_key: str
    wallet_address: str
    signature_type: int  # 0=EOA, 1=Email/Magic

    # Trading Configuration
    max_bet_size: float  # Max size per order in USDC

    # Market Selection
    market_ids: List[str]  # Specific markets to trade, empty = auto-select

    # Bot Settings
    dry_run: bool  # If true, simulate without real orders

    # API Endpoints
    clob_url: str = "https://clob.polymarket.com"
    gamma_url: str = "https://gamma-api.polymarket.com"
    chain_id: int = 137  # Polygon mainnet

    # Strategy Parameters
    min_spread_bps: int = 50  # Minimum spread in basis points (0.5%)
    target_spread_bps: int = 100  # Target spread in basis points (1%)
    order_refresh_seconds: int = 30  # How often to refresh orders
    max_position_size: float = 100.0  # Max position per market in USDC
    min_liquidity: float = 1000.0  # Minimum market liquidity to trade

    @classmethod
    def from_env(cls, env_path: Optional[str] = None) -> "Config":
        """Load configuration from environment variables"""
        if env_path:
            load_dotenv(env_path)
        else:
            load_dotenv()

        private_key = os.getenv("PRIVATE_KEY", "")
        if not private_key:
            raise ValueError("PRIVATE_KEY environment variable is required")

        wallet_address = os.getenv("WALLET_ADDRESS", "")
        if not wallet_address:
            raise ValueError("WALLET_ADDRESS environment variable is required")

        market_ids_str = os.getenv("MARKET_IDS", "")
        market_ids = [m.strip() for m in market_ids_str.split(",") if m.strip()]

        return cls(
            private_key=private_key,
            wallet_address=wallet_address,
            signature_type=int(os.getenv("SIGNATURE_TYPE", "0")),
            max_bet_size=float(os.getenv("MAX_BET_SIZE", "10.0")),
            market_ids=market_ids,
            dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        )

    def validate(self) -> bool:
        """Validate configuration"""
        if self.max_bet_size <= 0:
            raise ValueError("MAX_BET_SIZE must be positive")
        if self.signature_type not in [0, 1, 2]:
            raise ValueError("SIGNATURE_TYPE must be 0, 1, or 2")
        if not self.wallet_address.startswith("0x"):
            raise ValueError("WALLET_ADDRESS must start with 0x")
        return True
