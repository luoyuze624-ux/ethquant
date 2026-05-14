import pandas as pd

from config import (
    BACKTEST_FAST_MA,
    BACKTEST_FEE_RATE,
    BACKTEST_INITIAL_CAPITAL,
    BACKTEST_SLOW_MA,
    DEFAULT_INTERVAL,
    DEFAULT_KLINES_LIMIT,
    DEFAULT_SYMBOL,
)
from binance_eth.client import BinanceClient
from binance_eth.indicators import sma
from binance_eth.log import get_logger
from binance_eth.storage import load_klines_from_db

log = get_logger(__name__)


def backtest_ma_cross(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    limit: int = DEFAULT_KLINES_LIMIT,
    fast_ma: int = BACKTEST_FAST_MA,
    slow_ma: int = BACKTEST_SLOW_MA,
    initial_capital: float = BACKTEST_INITIAL_CAPITAL,
    fee_rate: float = BACKTEST_FEE_RATE,
    use_db: bool = True,
) -> None:
    if use_db:
        df = load_klines_from_db(symbol, interval, limit)
        if df.empty:
            log.warning("DB 无数据，从 API 拉取")
            use_db = False

    if not use_db:
        client = BinanceClient()
        log.info("Fetching %d klines for backtest: %s %s", limit, symbol, interval)
        klines = client.get_klines(symbol, interval, limit)
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
    df["fast_ma"] = sma(df["close"], fast_ma)
    df["slow_ma"] = sma(df["close"], slow_ma)
    df = df.dropna(subset=["fast_ma", "slow_ma"]).reset_index(drop=True)

    if len(df) < 2:
        log.error("数据不足，无法回测")
        return

    df["signal"] = 0
    df.loc[df["fast_ma"] > df["slow_ma"], "signal"] = 1
    df["position"] = df["signal"].diff()

    capital = initial_capital
    position = 0.0
    trades = []
    equity_curve = []

    for idx, row in df.iterrows():
        if row["position"] == 1:
            cost = capital * (1 - fee_rate)
            position = cost / row["close"]
            capital = 0
            trades.append({"type": "BUY", "time": row["open_time"], "price": row["close"], "qty": position})
        elif row["position"] == -1 and position > 0:
            capital = position * row["close"] * (1 - fee_rate)
            trades.append({"type": "SELL", "time": row["open_time"], "price": row["close"], "qty": position})
            position = 0.0

        current_value = capital + (position * row["close"] if position > 0 else 0)
        equity_curve.append(current_value)

    final_value = capital + (position * df.iloc[-1]["close"] if position > 0 else 0)
    total_return = ((final_value - initial_capital) / initial_capital) * 100

    df["equity"] = equity_curve
    peak = df["equity"].expanding().max()
    drawdown = (df["equity"] - peak) / peak * 100
    max_drawdown = drawdown.min()

    buy_trades = [t for t in trades if t["type"] == "BUY"]
    sell_trades = [t for t in trades if t["type"] == "SELL"]
    num_trades = min(len(buy_trades), len(sell_trades))

    wins = 0
    if num_trades > 0:
        for i in range(num_trades):
            if sell_trades[i]["price"] > buy_trades[i]["price"]:
                wins += 1
    win_rate = (wins / num_trades * 100) if num_trades > 0 else 0

    log.info("=" * 60)
    log.info("回测报告: %s %s", symbol, interval)
    log.info("=" * 60)
    log.info("策略: MA(%d) × MA(%d) 金叉", fast_ma, slow_ma)
    log.info("数据范围: %s 至 %s", df.iloc[0]["open_time"], df.iloc[-1]["open_time"])
    log.info("K线数量: %d", len(df))
    log.info("")
    log.info("初始资金: $%.2f", initial_capital)
    log.info("最终资金: $%.2f", final_value)
    log.info("总收益率: %+.2f%%", total_return)
    log.info("最大回撤: %.2f%%", max_drawdown)
    log.info("")
    log.info("交易次数: %d", num_trades)
    log.info("胜率: %.1f%% (%d/%d)", win_rate, wins, num_trades)
    log.info("手续费率: %.2f%%", fee_rate * 100)
    log.info("=" * 60)
