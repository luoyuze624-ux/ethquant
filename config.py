import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_env_file() -> None:
    path = BASE_DIR / ".env"
    if not path.is_file():
        return
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if not key:
            continue
        val = val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        os.environ.setdefault(key, val)


_load_env_file()
DATA_DIR = BASE_DIR / "data"
LOG_DIR = BASE_DIR / "logs"
DB_PATH = DATA_DIR / "market.db"

# 可通过环境变量 BINANCE_BASE_URL 切换，例如 https://api1.binance.com（与 api.binance.com 同功能）
BINANCE_BASE_URL = os.environ.get("BINANCE_BASE_URL", "https://api.binance.com").rstrip("/")
HTTP_TIMEOUT = 10
HTTP_RETRY = 3
HTTP_RETRY_BACKOFF = 1.5

DEFAULT_SYMBOL = "ETHUSDT"
DEFAULT_INTERVAL = "1h"
DEFAULT_KLINES_LIMIT = 500

MONITOR_POLL_SECONDS = 10
PCT_CHANGE_ALERT = 1.0
PRICE_UPPER_ALERT: float | None = None
PRICE_LOWER_ALERT: float | None = None

BACKTEST_FAST_MA = 10
BACKTEST_SLOW_MA = 30
BACKTEST_INITIAL_CAPITAL = 10_000.0
BACKTEST_FEE_RATE = 0.001

TRADE_CAPITAL = 1000.0
TRADE_LEVERAGE = 20
TRADE_RISK_PER_TRADE = 2.0
TRADE_STOP_LOSS_PCT = 2.0
TRADE_TAKE_PROFIT_PCT = 4.0
TRADE_CHECK_INTERVAL = 30

# U 本位永续近似：每边吃单手续费（按名义成交额）；资金费为每 8h 常数（真实行情波动大，可用环境变量覆盖）
TRADE_TAKER_FEE_RATE = float(os.environ.get("TRADE_TAKER_FEE_RATE", "0.0004"))
TRADE_FUNDING_RATE_8H = float(os.environ.get("TRADE_FUNDING_RATE_8H", "0.0001"))

# 交易信号邮件：优先 EMAIL_*（见项目根目录 .env），兼容旧名 TRADE_SMTP_* / TRADE_EMAIL_TO。
EMAIL_SMTP_HOST = os.environ.get("EMAIL_SMTP_HOST") or os.environ.get("TRADE_SMTP_HOST", "smtp.qq.com")
EMAIL_SMTP_PORT = int(os.environ.get("EMAIL_SMTP_PORT") or os.environ.get("TRADE_SMTP_PORT", "465"))
EMAIL_SENDER = os.environ.get("EMAIL_SENDER") or os.environ.get("TRADE_SMTP_USER", "")
EMAIL_AUTH_CODE = os.environ.get("EMAIL_AUTH_CODE") or os.environ.get("TRADE_SMTP_PASSWORD", "")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER") or os.environ.get("TRADE_EMAIL_TO", "")
