import time
from typing import Any

from config import (
    DEFAULT_SYMBOL,
    MONITOR_POLL_SECONDS,
    PCT_CHANGE_ALERT,
    PRICE_LOWER_ALERT,
    PRICE_UPPER_ALERT,
)
from binance_eth.client import BinanceClient
from binance_eth.log import get_logger

log = get_logger(__name__)


def monitor_price(
    symbol: str = DEFAULT_SYMBOL,
    poll_seconds: int = MONITOR_POLL_SECONDS,
    pct_alert: float = PCT_CHANGE_ALERT,
    upper_alert: float | None = PRICE_UPPER_ALERT,
    lower_alert: float | None = PRICE_LOWER_ALERT,
) -> None:
    client = BinanceClient()
    log.info(
        "Starting price monitor for %s (poll=%ds, pct_alert=%.2f%%, upper=%s, lower=%s)",
        symbol, poll_seconds, pct_alert, upper_alert, lower_alert,
    )

    baseline_price: float | None = None
    last_alert_time = 0.0

    try:
        while True:
            ticker = client.get_24hr(symbol)
            current_price = float(ticker["lastPrice"])
            pct_change_24h = float(ticker["priceChangePercent"])
            high_24h = float(ticker["highPrice"])
            low_24h = float(ticker["lowPrice"])
            volume_24h = float(ticker["volume"])

            log.info(
                "%s | Price: %.2f | 24h: %+.2f%% | High: %.2f | Low: %.2f | Vol: %.2f",
                symbol, current_price, pct_change_24h, high_24h, low_24h, volume_24h,
            )

            if baseline_price is None:
                baseline_price = current_price

            now = time.time()
            if now - last_alert_time > 60:
                pct_from_baseline = ((current_price - baseline_price) / baseline_price) * 100

                if abs(pct_from_baseline) >= pct_alert:
                    direction = "上涨" if pct_from_baseline > 0 else "下跌"
                    log.warning(
                        "⚠️  价格%s告警: %s 从基准 %.2f 变动 %+.2f%% 至 %.2f",
                        direction, symbol, baseline_price, pct_from_baseline, current_price,
                    )
                    baseline_price = current_price
                    last_alert_time = now

                if upper_alert and current_price >= upper_alert:
                    log.warning(
                        "⚠️  价格突破上限: %s 当前 %.2f >= %.2f",
                        symbol, current_price, upper_alert,
                    )
                    last_alert_time = now

                if lower_alert and current_price <= lower_alert:
                    log.warning(
                        "⚠️  价格跌破下限: %s 当前 %.2f <= %.2f",
                        symbol, current_price, lower_alert,
                    )
                    last_alert_time = now

            time.sleep(poll_seconds)

    except KeyboardInterrupt:
        log.info("Monitor stopped by user")
