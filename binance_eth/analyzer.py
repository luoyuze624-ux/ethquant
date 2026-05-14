import pandas as pd

from config import DEFAULT_INTERVAL, DEFAULT_KLINES_LIMIT, DEFAULT_SYMBOL
from binance_eth.client import BinanceClient
from binance_eth.indicators import bollinger_bands, ema, macd, rsi, sma
from binance_eth.log import get_logger
from binance_eth.storage import load_klines_from_db, save_klines_to_csv, save_klines_to_db

log = get_logger(__name__)


def analyze_klines(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    limit: int = DEFAULT_KLINES_LIMIT,
    save_data: bool = True,
) -> None:
    client = BinanceClient()
    log.info("Fetching %d klines for %s %s", limit, symbol, interval)
    klines = client.get_klines(symbol, interval, limit)

    if not klines:
        log.error("No klines returned")
        return

    if save_data:
        save_klines_to_db(klines, symbol, interval)
        save_klines_to_csv(klines, symbol, interval)

    df = pd.DataFrame(
        klines,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
            "ignore",
        ],
    )
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["close"] = df["close"].astype(float)
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)
    df["volume"] = df["volume"].astype(float)

    df["sma_20"] = sma(df["close"], 20)
    df["ema_12"] = ema(df["close"], 12)
    df["ema_26"] = ema(df["close"], 26)
    df["rsi_14"] = rsi(df["close"], 14)
    df["macd"], df["macd_signal"], df["macd_hist"] = macd(df["close"])
    df["bb_upper"], df["bb_middle"], df["bb_lower"] = bollinger_bands(df["close"])

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else None

    log.info("=" * 60)
    log.info("技术分析报告: %s %s", symbol, interval)
    log.info("=" * 60)
    log.info("时间: %s", latest["open_time"])
    log.info("收盘价: %.2f", latest["close"])
    log.info("24K 最高: %.2f | 最低: %.2f", df["high"].tail(24).max(), df["low"].tail(24).min())
    log.info("")
    log.info("移动平均线:")
    log.info("  SMA(20): %.2f", latest["sma_20"])
    log.info("  EMA(12): %.2f | EMA(26): %.2f", latest["ema_12"], latest["ema_26"])
    log.info("")
    log.info("RSI(14): %.2f", latest["rsi_14"])
    if latest["rsi_14"] > 70:
        log.info("  → 超买区域")
    elif latest["rsi_14"] < 30:
        log.info("  → 超卖区域")
    else:
        log.info("  → 中性区域")
    log.info("")
    log.info("MACD:")
    log.info("  MACD: %.4f | Signal: %.4f | Hist: %.4f", latest["macd"], latest["macd_signal"], latest["macd_hist"])
    if prev is not None:
        if prev["macd_hist"] < 0 and latest["macd_hist"] > 0:
            log.info("  → 金叉信号 (看涨)")
        elif prev["macd_hist"] > 0 and latest["macd_hist"] < 0:
            log.info("  → 死叉信号 (看跌)")
    log.info("")
    log.info("布林带:")
    log.info("  上轨: %.2f | 中轨: %.2f | 下轨: %.2f", latest["bb_upper"], latest["bb_middle"], latest["bb_lower"])
    bb_position = (latest["close"] - latest["bb_lower"]) / (latest["bb_upper"] - latest["bb_lower"]) * 100
    log.info("  当前位置: %.1f%% (0%%=下轨, 100%%=上轨)", bb_position)
    log.info("=" * 60)
