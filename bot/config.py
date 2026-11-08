"""Settings, read from environment variables (.env on the server).

Safe by default: testnet + dry-run. Live trading needs TWO explicit settings.
"""
import os
from dataclasses import dataclass

BASE_URLS = {
    "testnet": "https://cdn-ind.testnet.deltaex.org",
    "live": "https://api.india.delta.exchange",
}
LIVE_CONFIRM_PHRASE = "I_ACCEPT_REAL_MONEY_RISK"


def _f(name, default):
    return float(os.getenv(name, default))


def _i(name, default):
    return int(os.getenv(name, default))


def _b(name, default):
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    env: str = "testnet"
    api_key: str = ""
    api_secret: str = ""
    dry_run: bool = True              # log orders, never send them
    underlying: str = "BTC"

    # --- risk limits (contracts / USD) ---
    max_contracts_per_leg: int = 5
    max_open_baskets: int = 2
    max_daily_loss_usd: float = 50.0
    max_loss_per_basket_usd: float = 25.0
    max_loss_pct_of_premium: float = 50.0   # basket stop at 50% of premium paid/received
    max_spread_pct: float = 15.0     # skip legs whose bid/ask spread is wider than this (% of mid)
    max_premium_per_basket_usd: float = 100.0

    # --- futures (perpetuals) ---
    risk_per_trade_usd: float = 5.0     # max loss if the stop is hit
    max_futures_contracts: int = 50
    max_leverage: float = 3.0
    max_open_futures: int = 1
    max_funding_pct: float = 0.05       # skip trades paying more funding than this per interval
    dry_run_equity_usd: float = 500.0   # pretend balance used for sizing in dry-run

    # --- trend portfolio (STRATEGY=trend_portfolio) ---
    trend_coins: str = "BTCUSD:trend,ETHUSD:breakout"
    trend_leverage: float = 1.0          # total exposure = equity x this, split equally between coins
    max_daily_loss_pct: float = 10.0     # flatten + kill switch if today's loss exceeds this % of equity
    trading_capital_usd: float = 0.0     # >0: bot treats the account as this big (uses min(wallet, this))

    # --- ops ---
    poll_seconds: int = 30
    kill_file: str = "KILL"           # create this file to stop + flatten everything
    journal_path: str = "data/journal.sqlite"
    telegram_token: str = ""
    telegram_chat_id: str = ""

    @property
    def base_url(self) -> str:
        return BASE_URLS[self.env]


def load() -> Settings:
    env = os.getenv("DELTA_ENV", "testnet").strip().lower()
    if env not in BASE_URLS:
        raise SystemExit(f"DELTA_ENV must be 'testnet' or 'live', got {env!r}")
    if env == "live" and os.getenv("LIVE_TRADING_CONFIRM") != LIVE_CONFIRM_PHRASE:
        raise SystemExit(
            "Refusing to start in LIVE mode: set LIVE_TRADING_CONFIRM="
            f"{LIVE_CONFIRM_PHRASE} only after testnet results are approved."
        )
    s = Settings(
        env=env,
        api_key=os.getenv("DELTA_API_KEY", ""),
        api_secret=os.getenv("DELTA_API_SECRET", ""),
        dry_run=_b("DRY_RUN", True),
        underlying=os.getenv("UNDERLYING", "BTC").upper(),
        max_contracts_per_leg=_i("MAX_CONTRACTS_PER_LEG", 5),
        max_open_baskets=_i("MAX_OPEN_BASKETS", 2),
        max_daily_loss_usd=_f("MAX_DAILY_LOSS_USD", 50),
        max_loss_per_basket_usd=_f("MAX_LOSS_PER_BASKET_USD", 25),
        max_loss_pct_of_premium=_f("MAX_LOSS_PCT_OF_PREMIUM", 50),
        max_spread_pct=_f("MAX_SPREAD_PCT", 15),
        max_premium_per_basket_usd=_f("MAX_PREMIUM_PER_BASKET_USD", 100),
        risk_per_trade_usd=_f("RISK_PER_TRADE_USD", 5),
        max_futures_contracts=_i("MAX_FUTURES_CONTRACTS", 50),
        max_leverage=_f("MAX_LEVERAGE", 3),
        max_open_futures=_i("MAX_OPEN_FUTURES", 1),
        max_funding_pct=_f("MAX_FUNDING_PCT", 0.05),
        dry_run_equity_usd=_f("DRY_RUN_EQUITY_USD", 500),
        trend_coins=os.getenv("TREND_COINS", "BTCUSD:trend,ETHUSD:breakout"),
        trend_leverage=_f("TREND_LEVERAGE", 1.0),
        max_daily_loss_pct=_f("MAX_DAILY_LOSS_PCT", 10),
        trading_capital_usd=_f("TRADING_CAPITAL_USD", 0),
        poll_seconds=_i("POLL_SECONDS", 30),
        kill_file=os.getenv("KILL_FILE", "KILL"),
        journal_path=os.getenv("JOURNAL_PATH", "data/journal.sqlite"),
        telegram_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
    if not s.dry_run and not (s.api_key and s.api_secret):
        raise SystemExit("DELTA_API_KEY / DELTA_API_SECRET missing (or set DRY_RUN=true).")
    return s
