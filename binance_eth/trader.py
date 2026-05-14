import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from config import (
    DEFAULT_INTERVAL,
    DEFAULT_SYMBOL,
    TRADE_CAPITAL,
    TRADE_CHECK_INTERVAL,
    TRADE_LEVERAGE,
    TRADE_RISK_PER_TRADE,
    TRADE_STOP_LOSS_PCT,
    TRADE_TAKE_PROFIT_PCT,
)
from binance_eth.client import BinanceClient
from binance_eth.indicators import bollinger_bands, ema, macd, rsi, sma
from binance_eth.log import get_logger

log = get_logger(__name__)


class SignalType(Enum):
    NONE = "无信号"
    OPEN_LONG = "开多"
    OPEN_SHORT = "开空"
    CLOSE_LONG = "平多"
    CLOSE_SHORT = "平空"


@dataclass
class Position:
    side: str
    entry_price: float
    quantity: float
    stop_loss: float
    take_profit: float
    open_time: float


@dataclass
class TradeSignal:
    signal: SignalType
    price: float
    reason: str
    position_size: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0


class TradingStrategy:
    def __init__(
        self,
        capital: float = TRADE_CAPITAL,
        leverage: int = TRADE_LEVERAGE,
        risk_per_trade: float = TRADE_RISK_PER_TRADE,
        stop_loss_pct: float = TRADE_STOP_LOSS_PCT,
        take_profit_pct: float = TRADE_TAKE_PROFIT_PCT,
    ):
        self.capital = capital
        self.leverage = leverage
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.position: Optional[Position] = None

    def analyze_market(self, df: pd.DataFrame) -> TradeSignal:
        if len(df) < 50:
            return TradeSignal(SignalType.NONE, df.iloc[-1]["close"], "数据不足")

        latest = df.iloc[-1]
        prev = df.iloc[-2]
        current_price = float(latest["close"])

        df["sma_20"] = sma(df["close"], 20)
        df["ema_12"] = ema(df["close"], 12)
        df["ema_26"] = ema(df["close"], 26)
        df["rsi_14"] = rsi(df["close"], 14)
        df["macd"], df["macd_signal"], df["macd_hist"] = macd(df["close"])
        df["bb_upper"], df["bb_middle"], df["bb_lower"] = bollinger_bands(df["close"])

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        if self.position:
            return self._check_exit_signal(latest, prev, current_price)
        else:
            return self._check_entry_signal(latest, prev, current_price)

    def _check_entry_signal(self, latest, prev, current_price: float) -> TradeSignal:
        reasons = []
        long_score = 0
        short_score = 0

        if latest["rsi_14"] < 30:
            long_score += 2
            reasons.append("RSI超卖(<30)")
        elif latest["rsi_14"] > 70:
            short_score += 2
            reasons.append("RSI超买(>70)")

        if prev["macd_hist"] < 0 and latest["macd_hist"] > 0:
            long_score += 3
            reasons.append("MACD金叉")
        elif prev["macd_hist"] > 0 and latest["macd_hist"] < 0:
            short_score += 3
            reasons.append("MACD死叉")

        if current_price < latest["bb_lower"]:
            long_score += 2
            reasons.append("价格触及布林下轨")
        elif current_price > latest["bb_upper"]:
            short_score += 2
            reasons.append("价格触及布林上轨")

        if latest["ema_12"] > latest["ema_26"]:
            long_score += 1
        else:
            short_score += 1

        if long_score >= 4:
            position_size = self._calculate_position_size(current_price)
            stop_loss = current_price * (1 - self.stop_loss_pct / 100)
            take_profit = current_price * (1 + self.take_profit_pct / 100)
            return TradeSignal(
                SignalType.OPEN_LONG,
                current_price,
                " | ".join(reasons),
                position_size,
                stop_loss,
                take_profit,
            )
        elif short_score >= 4:
            position_size = self._calculate_position_size(current_price)
            stop_loss = current_price * (1 + self.stop_loss_pct / 100)
            take_profit = current_price * (1 - self.take_profit_pct / 100)
            return TradeSignal(
                SignalType.OPEN_SHORT,
                current_price,
                " | ".join(reasons),
                position_size,
                stop_loss,
                take_profit,
            )

        return TradeSignal(SignalType.NONE, current_price, "信号不足")

    def _check_exit_signal(self, latest, prev, current_price: float) -> TradeSignal:
        if not self.position:
            return TradeSignal(SignalType.NONE, current_price, "无持仓")

        if self.position.side == "LONG":
            if current_price <= self.position.stop_loss:
                return TradeSignal(
                    SignalType.CLOSE_LONG,
                    current_price,
                    f"触发止损 (入场:{self.position.entry_price:.2f} 止损:{self.position.stop_loss:.2f})",
                )
            if current_price >= self.position.take_profit:
                return TradeSignal(
                    SignalType.CLOSE_LONG,
                    current_price,
                    f"触发止盈 (入场:{self.position.entry_price:.2f} 止盈:{self.position.take_profit:.2f})",
                )
            if prev["macd_hist"] > 0 and latest["macd_hist"] < 0:
                return TradeSignal(SignalType.CLOSE_LONG, current_price, "MACD死叉信号")
            if latest["rsi_14"] > 70:
                return TradeSignal(SignalType.CLOSE_LONG, current_price, "RSI超买，建议止盈")

        elif self.position.side == "SHORT":
            if current_price >= self.position.stop_loss:
                return TradeSignal(
                    SignalType.CLOSE_SHORT,
                    current_price,
                    f"触发止损 (入场:{self.position.entry_price:.2f} 止损:{self.position.stop_loss:.2f})",
                )
            if current_price <= self.position.take_profit:
                return TradeSignal(
                    SignalType.CLOSE_SHORT,
                    current_price,
                    f"触发止盈 (入场:{self.position.entry_price:.2f} 止盈:{self.position.take_profit:.2f})",
                )
            if prev["macd_hist"] < 0 and latest["macd_hist"] > 0:
                return TradeSignal(SignalType.CLOSE_SHORT, current_price, "MACD金叉信号")
            if latest["rsi_14"] < 30:
                return TradeSignal(SignalType.CLOSE_SHORT, current_price, "RSI超卖，建议止盈")

        return TradeSignal(SignalType.NONE, current_price, "持仓中，无平仓信号")

    def _calculate_position_size(self, price: float) -> float:
        risk_amount = self.capital * (self.risk_per_trade / 100)
        stop_loss_distance = price * (self.stop_loss_pct / 100)
        position_value = (risk_amount / stop_loss_distance) * price
        max_position = self.capital * self.leverage
        return min(position_value, max_position) / price

    def open_position(self, signal: TradeSignal):
        side = "LONG" if signal.signal == SignalType.OPEN_LONG else "SHORT"
        self.position = Position(
            side=side,
            entry_price=signal.price,
            quantity=signal.position_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            open_time=time.time(),
        )

    def close_position(self) -> Optional[dict]:
        if not self.position:
            return None
        result = {
            "side": self.position.side,
            "entry_price": self.position.entry_price,
            "quantity": self.position.quantity,
            "duration": time.time() - self.position.open_time,
        }
        self.position = None
        return result


def run_trading_bot(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    check_interval: int = TRADE_CHECK_INTERVAL,
    capital: float = TRADE_CAPITAL,
    leverage: int = TRADE_LEVERAGE,
    risk_per_trade: float = TRADE_RISK_PER_TRADE,
    stop_loss_pct: float = TRADE_STOP_LOSS_PCT,
    take_profit_pct: float = TRADE_TAKE_PROFIT_PCT,
) -> None:
    client = BinanceClient()
    strategy = TradingStrategy(capital, leverage, risk_per_trade, stop_loss_pct, take_profit_pct)

    log.warning("=" * 80)
    log.warning("⚠️  交易策略推荐系统 - 仅供参考，不构成投资建议")
    log.warning("⚠️  杠杆交易风险极高，可能导致全部本金损失")
    log.warning("=" * 80)
    log.info("交易对: %s | K线周期: %s", symbol, interval)
    log.info("本金: %.2f USDT | 杠杆: %dx | 单笔风险: %.1f%%", capital, leverage, risk_per_trade)
    log.info("止损: %.1f%% | 止盈: %.1f%%", stop_loss_pct, take_profit_pct)
    log.info("检查间隔: %d 秒", check_interval)
    log.info("=" * 80)

    try:
        while True:
            klines = client.get_klines(symbol, interval, 100)
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
            df["close"] = df["close"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)

            signal = strategy.analyze_market(df)
            current_price = signal.price

            if strategy.position:
                pnl_pct = 0.0
                if strategy.position.side == "LONG":
                    pnl_pct = ((current_price - strategy.position.entry_price) / strategy.position.entry_price) * 100 * leverage
                else:
                    pnl_pct = ((strategy.position.entry_price - current_price) / strategy.position.entry_price) * 100 * leverage

                pnl_usdt = (pnl_pct / 100) * capital
                liquidation_price = 0.0
                if strategy.position.side == "LONG":
                    liquidation_price = strategy.position.entry_price * (1 - 1 / leverage * 0.9)
                else:
                    liquidation_price = strategy.position.entry_price * (1 + 1 / leverage * 0.9)

                log.info(
                    "持仓中 [%s] | 入场: %.2f | 当前: %.2f | 盈亏: %+.2f%% (%+.2f USDT) | 爆仓价: %.2f",
                    strategy.position.side,
                    strategy.position.entry_price,
                    current_price,
                    pnl_pct,
                    pnl_usdt,
                    liquidation_price,
                )

            if signal.signal == SignalType.OPEN_LONG:
                log.warning("🟢 开多信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                log.warning(
                    "   建议仓位: %.4f %s (约 %.2f USDT)",
                    signal.position_size,
                    symbol.replace("USDT", ""),
                    signal.position_size * signal.price,
                )
                log.warning("   止损: %.2f (%.2f%%) | 止盈: %.2f (%.2f%%)", signal.stop_loss, stop_loss_pct, signal.take_profit, take_profit_pct)
                strategy.open_position(signal)

            elif signal.signal == SignalType.OPEN_SHORT:
                log.warning("🔴 开空信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                log.warning(
                    "   建议仓位: %.4f %s (约 %.2f USDT)",
                    signal.position_size,
                    symbol.replace("USDT", ""),
                    signal.position_size * signal.price,
                )
                log.warning("   止损: %.2f (%.2f%%) | 止盈: %.2f (%.2f%%)", signal.stop_loss, stop_loss_pct, signal.take_profit, take_profit_pct)
                strategy.open_position(signal)

            elif signal.signal == SignalType.CLOSE_LONG:
                log.warning("⬆️  平多信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                closed = strategy.close_position()
                if closed:
                    pnl = ((signal.price - closed["entry_price"]) / closed["entry_price"]) * 100 * leverage
                    pnl_usdt = (pnl / 100) * capital
                    log.warning("   平仓盈亏: %+.2f%% (%+.2f USDT)", pnl, pnl_usdt)

            elif signal.signal == SignalType.CLOSE_SHORT:
                log.warning("⬇️  平空信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                closed = strategy.close_position()
                if closed:
                    pnl = ((closed["entry_price"] - signal.price) / closed["entry_price"]) * 100 * leverage
                    pnl_usdt = (pnl / 100) * capital
                    log.warning("   平仓盈亏: %+.2f%% (%+.2f USDT)", pnl, pnl_usdt)

            elif signal.signal == SignalType.NONE:
                log.info("当前价格: %.2f | %s", current_price, signal.reason)

            time.sleep(check_interval)

    except KeyboardInterrupt:
        log.info("交易推荐系统已停止")
        if strategy.position:
            log.warning("注意: 当前仍有持仓 [%s] 入场价: %.2f", strategy.position.side, strategy.position.entry_price)


def backtest_trading_strategy(
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    limit: int = 1000,
    capital: float = TRADE_CAPITAL,
    leverage: int = TRADE_LEVERAGE,
    risk_per_trade: float = TRADE_RISK_PER_TRADE,
    stop_loss_pct: float = TRADE_STOP_LOSS_PCT,
    take_profit_pct: float = TRADE_TAKE_PROFIT_PCT,
    use_db: bool = True,
) -> None:
    from binance_eth.storage import load_klines_from_db

    if use_db:
        df = load_klines_from_db(symbol, interval, limit)
        if df.empty:
            log.warning("DB 无数据，从 API 拉取")
            use_db = False

    if not use_db:
        client = BinanceClient()
        log.info("从 API 拉取 %d 根 K 线用于回测", limit)
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
    df["high"] = df["high"].astype(float)
    df["low"] = df["low"].astype(float)

    if len(df) < 100:
        log.error("数据不足，至少需要 100 根 K 线")
        return

    strategy = TradingStrategy(capital, leverage, risk_per_trade, stop_loss_pct, take_profit_pct)
    trades = []
    equity_curve = [capital]
    current_capital = capital

    log.info("=" * 80)
    log.info("交易策略回测报告")
    log.info("=" * 80)
    log.info("交易对: %s | K线周期: %s | 数据量: %d", symbol, interval, len(df))
    log.info("时间范围: %s 至 %s", df.iloc[0]["open_time"], df.iloc[-1]["open_time"])
    log.info("初始资金: %.2f USDT | 杠杆: %dx", capital, leverage)
    log.info("止损: %.1f%% | 止盈: %.1f%% | 单笔风险: %.1f%%", stop_loss_pct, take_profit_pct, risk_per_trade)
    log.info("=" * 80)

    for i in range(50, len(df)):
        window_df = df.iloc[: i + 1].copy()
        signal = strategy.analyze_market(window_df)
        current_price = float(df.iloc[i]["close"])
        current_time = df.iloc[i]["open_time"]

        if signal.signal == SignalType.OPEN_LONG and not strategy.position:
            strategy.open_position(signal)
            log.info(
                "[%s] 🟢 开多 | 价格: %.2f | 仓位: %.4f | 止损: %.2f | 止盈: %.2f | 原因: %s",
                current_time,
                signal.price,
                signal.position_size,
                signal.stop_loss,
                signal.take_profit,
                signal.reason,
            )

        elif signal.signal == SignalType.OPEN_SHORT and not strategy.position:
            strategy.open_position(signal)
            log.info(
                "[%s] 🔴 开空 | 价格: %.2f | 仓位: %.4f | 止损: %.2f | 止盈: %.2f | 原因: %s",
                current_time,
                signal.price,
                signal.position_size,
                signal.stop_loss,
                signal.take_profit,
                signal.reason,
            )

        elif signal.signal == SignalType.CLOSE_LONG and strategy.position:
            closed = strategy.close_position()
            if closed:
                pnl_pct = ((current_price - closed["entry_price"]) / closed["entry_price"]) * 100 * leverage
                pnl_usdt = (pnl_pct / 100) * capital
                current_capital += pnl_usdt
                trades.append(
                    {
                        "side": "LONG",
                        "entry_price": closed["entry_price"],
                        "exit_price": current_price,
                        "pnl_pct": pnl_pct,
                        "pnl_usdt": pnl_usdt,
                        "entry_time": current_time,
                        "exit_time": current_time,
                        "reason": signal.reason,
                    }
                )
                log.info(
                    "[%s] ⬆️  平多 | 价格: %.2f | 盈亏: %+.2f%% (%+.2f USDT) | 原因: %s",
                    current_time,
                    current_price,
                    pnl_pct,
                    pnl_usdt,
                    signal.reason,
                )

        elif signal.signal == SignalType.CLOSE_SHORT and strategy.position:
            closed = strategy.close_position()
            if closed:
                pnl_pct = ((closed["entry_price"] - current_price) / closed["entry_price"]) * 100 * leverage
                pnl_usdt = (pnl_pct / 100) * capital
                current_capital += pnl_usdt
                trades.append(
                    {
                        "side": "SHORT",
                        "entry_price": closed["entry_price"],
                        "exit_price": current_price,
                        "pnl_pct": pnl_pct,
                        "pnl_usdt": pnl_usdt,
                        "entry_time": current_time,
                        "exit_time": current_time,
                        "reason": signal.reason,
                    }
                )
                log.info(
                    "[%s] ⬇️  平空 | 价格: %.2f | 盈亏: %+.2f%% (%+.2f USDT) | 原因: %s",
                    current_time,
                    current_price,
                    pnl_pct,
                    pnl_usdt,
                    signal.reason,
                )

        equity_curve.append(current_capital)

    if strategy.position:
        log.warning("回测结束时仍有持仓，强制平仓")
        final_price = float(df.iloc[-1]["close"])
        if strategy.position.side == "LONG":
            pnl_pct = ((final_price - strategy.position.entry_price) / strategy.position.entry_price) * 100 * leverage
        else:
            pnl_pct = ((strategy.position.entry_price - final_price) / strategy.position.entry_price) * 100 * leverage
        pnl_usdt = (pnl_pct / 100) * capital
        current_capital += pnl_usdt
        trades.append(
            {
                "side": strategy.position.side,
                "entry_price": strategy.position.entry_price,
                "exit_price": final_price,
                "pnl_pct": pnl_pct,
                "pnl_usdt": pnl_usdt,
                "entry_time": df.iloc[-1]["open_time"],
                "exit_time": df.iloc[-1]["open_time"],
                "reason": "回测结束强制平仓",
            }
        )

    total_return = ((current_capital - capital) / capital) * 100
    wins = sum(1 for t in trades if t["pnl_usdt"] > 0)
    losses = sum(1 for t in trades if t["pnl_usdt"] <= 0)
    win_rate = (wins / len(trades) * 100) if trades else 0

    equity_series = pd.Series(equity_curve)
    peak = equity_series.expanding().max()
    drawdown = (equity_series - peak) / peak * 100
    max_drawdown = drawdown.min()

    avg_win = sum(t["pnl_usdt"] for t in trades if t["pnl_usdt"] > 0) / wins if wins > 0 else 0
    avg_loss = sum(t["pnl_usdt"] for t in trades if t["pnl_usdt"] <= 0) / losses if losses > 0 else 0
    profit_factor = abs(sum(t["pnl_usdt"] for t in trades if t["pnl_usdt"] > 0) / sum(t["pnl_usdt"] for t in trades if t["pnl_usdt"] <= 0)) if losses > 0 else float("inf")

    log.info("=" * 80)
    log.info("回测结果汇总")
    log.info("=" * 80)
    log.info("初始资金: %.2f USDT", capital)
    log.info("最终资金: %.2f USDT", current_capital)
    log.info("总收益率: %+.2f%%", total_return)
    log.info("最大回撤: %.2f%%", max_drawdown)
    log.info("")
    log.info("交易统计:")
    log.info("  总交易次数: %d", len(trades))
    log.info("  盈利次数: %d | 亏损次数: %d", wins, losses)
    log.info("  胜率: %.1f%%", win_rate)
    log.info("  平均盈利: %.2f USDT | 平均亏损: %.2f USDT", avg_win, avg_loss)
    log.info("  盈亏比: %.2f", profit_factor if profit_factor != float("inf") else 0)
    log.info("")
    log.info("交易明细:")
    for idx, trade in enumerate(trades, 1):
        log.info(
            "  #%d [%s] 入场: %.2f | 出场: %.2f | 盈亏: %+.2f%% (%+.2f USDT) | %s",
            idx,
            trade["side"],
            trade["entry_price"],
            trade["exit_price"],
            trade["pnl_pct"],
            trade["pnl_usdt"],
            trade["reason"],
        )
    log.info("=" * 80)
