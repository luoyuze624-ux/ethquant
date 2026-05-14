import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import pandas as pd

from config import (
    DEFAULT_INTERVAL,
    DEFAULT_SYMBOL,
    EMAIL_AUTH_CODE,
    EMAIL_RECEIVER,
    EMAIL_SENDER,
    EMAIL_SMTP_HOST,
    EMAIL_SMTP_PORT,
    TRADE_CAPITAL,
    TRADE_CHECK_INTERVAL,
    TRADE_FUNDING_RATE_8H,
    TRADE_LEVERAGE,
    TRADE_RISK_PER_TRADE,
    TRADE_STOP_LOSS_PCT,
    TRADE_TAKER_FEE_RATE,
    TRADE_TAKE_PROFIT_PCT,
)
from binance_eth.client import BinanceClient
from binance_eth.fees_funding import (
    apply_funding_for_interval,
    kline_interval_to_timedelta,
    taker_fee_usdt,
    to_utc_timestamp,
)
from binance_eth.email_notify import is_trade_email_configured, send_trade_email
from binance_eth.indicators import bollinger_bands, ema, macd, rsi, sma
from binance_eth.log import get_logger

log = get_logger(__name__)

# 全仓复投：名义价值低于此值则视为无法再开仓（模拟交易所最小下单）
DEFAULT_MIN_TRADE_NOTIONAL_USDT = 5.0


def _margin_usdt(quantity: float, entry_price: float, leverage: int) -> float:
    if leverage <= 0:
        return 0.0
    return quantity * entry_price / leverage


def _pnl_usdt_closed(side: str, entry_price: float, exit_price: float, quantity: float) -> float:
    if side == "LONG":
        return quantity * (exit_price - entry_price)
    return quantity * (entry_price - exit_price)


def _roi_pct_on_margin(pnl_usdt: float, margin: float) -> float:
    if margin <= 1e-12:
        return 0.0
    return (pnl_usdt / margin) * 100.0


def _notify_trade_email(subject: str, body: str) -> None:
    if not is_trade_email_configured(EMAIL_SENDER, EMAIL_AUTH_CODE, EMAIL_RECEIVER):
        return
    try:
        send_trade_email(
            smtp_host=EMAIL_SMTP_HOST,
            smtp_port=EMAIL_SMTP_PORT,
            smtp_user=EMAIL_SENDER,
            smtp_password=EMAIL_AUTH_CODE,
            email_to=EMAIL_RECEIVER,
            subject=subject,
            body=body,
        )
    except Exception:
        log.exception("交易信号邮件发送失败")


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
    funding_anchor: pd.Timestamp | None = None
    last_funding_applied: pd.Timestamp | None = None


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
        full_equity_sizing: bool = True,
        min_trade_notional_usdt: float = DEFAULT_MIN_TRADE_NOTIONAL_USDT,
    ):
        self.capital = capital
        self.leverage = leverage
        self.risk_per_trade = risk_per_trade
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.full_equity_sizing = full_equity_sizing
        self.min_trade_notional_usdt = min_trade_notional_usdt
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
            if position_size <= 0:
                return TradeSignal(SignalType.NONE, current_price, "资金不足或达不到最小开仓名义")
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
            if position_size <= 0:
                return TradeSignal(SignalType.NONE, current_price, "资金不足或达不到最小开仓名义")
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
        if price <= 0:
            return 0.0
        if self.full_equity_sizing:
            if self.capital <= 0:
                return 0.0
            notional = self.capital * self.leverage
            if notional < self.min_trade_notional_usdt:
                return 0.0
            return notional / price
        risk_amount = self.capital * (self.risk_per_trade / 100)
        stop_loss_distance = price * (self.stop_loss_pct / 100)
        position_value = (risk_amount / stop_loss_distance) * price
        max_position = self.capital * self.leverage
        return min(position_value, max_position) / price

    def open_position(self, signal: TradeSignal, funding_anchor: pd.Timestamp | None = None):
        side = "LONG" if signal.signal == SignalType.OPEN_LONG else "SHORT"
        anchor = funding_anchor if funding_anchor is not None else pd.Timestamp.now(tz="UTC")
        self.position = Position(
            side=side,
            entry_price=signal.price,
            quantity=signal.position_size,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            open_time=time.time(),
            funding_anchor=anchor,
            last_funding_applied=None,
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
    full_equity_sizing: bool = True,
    min_trade_notional_usdt: float = DEFAULT_MIN_TRADE_NOTIONAL_USDT,
    taker_fee_rate: float = TRADE_TAKER_FEE_RATE,
    funding_rate_8h: float = TRADE_FUNDING_RATE_8H,
) -> None:
    client = BinanceClient()
    strategy = TradingStrategy(
        capital,
        leverage,
        risk_per_trade,
        stop_loss_pct,
        take_profit_pct,
        full_equity_sizing=full_equity_sizing,
        min_trade_notional_usdt=min_trade_notional_usdt,
    )
    paper_equity = capital

    log.warning("=" * 80)
    log.warning("⚠️  交易策略推荐系统 - 仅供参考，不构成投资建议")
    log.warning("⚠️  杠杆交易风险极高，可能导致全部本金损失")
    log.warning("=" * 80)
    log.info("交易对: %s | K线周期: %s", symbol, interval)
    log.info("初始本金: %.2f USDT | 杠杆: %dx", capital, leverage)
    if full_equity_sizing:
        log.info("仓位模式: 全仓复投（模拟权益随平仓更新）| 最小开仓名义: %.1f USDT", min_trade_notional_usdt)
    else:
        log.info("仓位模式: 固定本金风险 sizing | 单笔风险: %.1f%%", risk_per_trade)
    log.info("止损: %.1f%% | 止盈: %.1f%%", stop_loss_pct, take_profit_pct)
    log.info(
        "成本模型: 吃单手续费 %.4f%%/边 | 资金费 %.5f/8h（正=多头付；UTC 0/8/16 点结算）",
        taker_fee_rate * 100,
        funding_rate_8h,
    )
    log.info("检查间隔: %d 秒", check_interval)
    log.info("=" * 80)

    if is_trade_email_configured(EMAIL_SENDER, EMAIL_AUTH_CODE, EMAIL_RECEIVER):
        log.info("交易信号邮件推送已启用，收件人: %s", EMAIL_RECEIVER)
    else:
        log.info(
            "交易信号邮件未启用：在项目根目录创建 .env 或导出环境变量 "
            "EMAIL_SENDER、EMAIL_AUTH_CODE、EMAIL_RECEIVER（及可选 EMAIL_SMTP_HOST / EMAIL_SMTP_PORT）"
        )

    try:
        while True:
            try:
                klines = client.get_klines(symbol, interval, 100)
            except RuntimeError as exc:
                log.error(
                    "无法从 Binance 获取 K 线（请检查本机/WSL 网络、代理、防火墙）。"
                    "%d 秒后重试。可尝试设置环境变量 BINANCE_BASE_URL=https://api1.binance.com 。详情: %s",
                    check_interval,
                    exc,
                )
                time.sleep(check_interval)
                continue

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

            strategy.capital = paper_equity

            signal = strategy.analyze_market(df)
            current_price = signal.price

            live_now = pd.Timestamp.now(tz="UTC")
            if strategy.position and funding_rate_8h != 0:
                fu = apply_funding_for_interval(strategy.position, live_now, current_price, funding_rate_8h)
                paper_equity += fu

            if strategy.position:
                qty = strategy.position.quantity
                entry = strategy.position.entry_price
                if strategy.position.side == "LONG":
                    pnl_usdt = qty * (current_price - entry)
                else:
                    pnl_usdt = qty * (entry - current_price)
                margin = _margin_usdt(qty, entry, leverage)
                pnl_pct = _roi_pct_on_margin(pnl_usdt, margin)
                liquidation_price = 0.0
                if strategy.position.side == "LONG":
                    liquidation_price = strategy.position.entry_price * (1 - 1 / leverage * 0.9)
                else:
                    liquidation_price = strategy.position.entry_price * (1 + 1 / leverage * 0.9)

                log.info(
                    "持仓中 [%s] | 入场: %.2f | 当前: %.2f | 浮动盈亏: %+.2f%% (%+.2f USDT) | 模拟权益: %.2f | 爆仓价: %.2f",
                    strategy.position.side,
                    strategy.position.entry_price,
                    current_price,
                    pnl_pct,
                    pnl_usdt,
                    paper_equity,
                    liquidation_price,
                )

            if signal.signal == SignalType.OPEN_LONG:
                if full_equity_sizing and (
                    paper_equity <= 0 or paper_equity * leverage < min_trade_notional_usdt or signal.position_size <= 0
                ):
                    log.warning(
                        "收到开多信号但模拟权益不足以全仓开仓（权益 %.6f USDT），停止运行",
                        paper_equity,
                    )
                    return
                log.warning("🟢 开多信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                log.warning(
                    "   建议仓位: %.4f %s (约 %.2f USDT)",
                    signal.position_size,
                    symbol.replace("USDT", ""),
                    signal.position_size * signal.price,
                )
                log.warning("   止损: %.2f (%.2f%%) | 止盈: %.2f (%.2f%%)", signal.stop_loss, stop_loss_pct, signal.take_profit, take_profit_pct)
                base_asset = symbol.replace("USDT", "")
                _notify_trade_email(
                    f"[{symbol}] 开多信号",
                    "\n".join(
                        [
                            f"交易对: {symbol} | K线: {interval}",
                            f"价格: {signal.price:.2f}",
                            f"原因: {signal.reason}",
                            f"建议仓位: {signal.position_size:.4f} {base_asset} (约 {signal.position_size * signal.price:.2f} USDT)",
                            f"止损: {signal.stop_loss:.2f} ({stop_loss_pct:.2f}%) | 止盈: {signal.take_profit:.2f} ({take_profit_pct:.2f}%)",
                            "",
                            "本邮件由交易策略推荐程序自动发送，仅供参考，不构成投资建议。",
                        ]
                    ),
                )
                anchor = to_utc_timestamp(df.iloc[-1]["open_time"])
                strategy.open_position(signal, funding_anchor=anchor)
                fee_o = taker_fee_usdt(signal.position_size, signal.price, taker_fee_rate)
                paper_equity -= fee_o
                if funding_rate_8h != 0:
                    bar_end = anchor + kline_interval_to_timedelta(interval)
                    fu0 = apply_funding_for_interval(strategy.position, bar_end, current_price, funding_rate_8h)
                    paper_equity += fu0

            elif signal.signal == SignalType.OPEN_SHORT:
                if full_equity_sizing and (
                    paper_equity <= 0 or paper_equity * leverage < min_trade_notional_usdt or signal.position_size <= 0
                ):
                    log.warning(
                        "收到开空信号但模拟权益不足以全仓开仓（权益 %.6f USDT），停止运行",
                        paper_equity,
                    )
                    return
                log.warning("🔴 开空信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                log.warning(
                    "   建议仓位: %.4f %s (约 %.2f USDT)",
                    signal.position_size,
                    symbol.replace("USDT", ""),
                    signal.position_size * signal.price,
                )
                log.warning("   止损: %.2f (%.2f%%) | 止盈: %.2f (%.2f%%)", signal.stop_loss, stop_loss_pct, signal.take_profit, take_profit_pct)
                base_asset = symbol.replace("USDT", "")
                _notify_trade_email(
                    f"[{symbol}] 开空信号",
                    "\n".join(
                        [
                            f"交易对: {symbol} | K线: {interval}",
                            f"价格: {signal.price:.2f}",
                            f"原因: {signal.reason}",
                            f"建议仓位: {signal.position_size:.4f} {base_asset} (约 {signal.position_size * signal.price:.2f} USDT)",
                            f"止损: {signal.stop_loss:.2f} ({stop_loss_pct:.2f}%) | 止盈: {signal.take_profit:.2f} ({take_profit_pct:.2f}%)",
                            "",
                            "本邮件由交易策略推荐程序自动发送，仅供参考，不构成投资建议。",
                        ]
                    ),
                )
                anchor = to_utc_timestamp(df.iloc[-1]["open_time"])
                strategy.open_position(signal, funding_anchor=anchor)
                fee_o = taker_fee_usdt(signal.position_size, signal.price, taker_fee_rate)
                paper_equity -= fee_o
                if funding_rate_8h != 0:
                    bar_end = anchor + kline_interval_to_timedelta(interval)
                    fu0 = apply_funding_for_interval(strategy.position, bar_end, current_price, funding_rate_8h)
                    paper_equity += fu0

            elif signal.signal == SignalType.CLOSE_LONG:
                log.warning("⬆️  平多信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                closed = strategy.close_position()
                if closed:
                    margin = _margin_usdt(closed["quantity"], closed["entry_price"], leverage)
                    gross = _pnl_usdt_closed("LONG", closed["entry_price"], signal.price, closed["quantity"])
                    fee_c = taker_fee_usdt(closed["quantity"], signal.price, taker_fee_rate)
                    net = gross - fee_c
                    pnl = _roi_pct_on_margin(net, margin)
                    log.warning("   平仓盈亏(含手续费): %+.2f%% (%+.2f USDT)", pnl, net)
                    paper_equity += net
                    if paper_equity <= 0:
                        log.warning("模拟权益已耗尽，停止运行")
                        _notify_trade_email(
                            f"[{symbol}] 平多信号",
                            "\n".join(
                                [
                                    f"交易对: {symbol} | K线: {interval}",
                                    f"平仓价: {signal.price:.2f}",
                                    f"入场价: {closed['entry_price']:.2f}",
                                    f"原因: {signal.reason}",
                                    f"盈亏(含手续费): {pnl:+.2f}% ({net:+.2f} USDT)",
                                    f"平仓后模拟权益: {paper_equity:.2f} USDT",
                                    "",
                                    "本邮件由交易策略推荐程序自动发送，仅供参考，不构成投资建议。",
                                ]
                            ),
                        )
                        return
                    _notify_trade_email(
                        f"[{symbol}] 平多信号",
                        "\n".join(
                            [
                                f"交易对: {symbol} | K线: {interval}",
                                f"平仓价: {signal.price:.2f}",
                                f"入场价: {closed['entry_price']:.2f}",
                                f"原因: {signal.reason}",
                                f"盈亏(含手续费): {pnl:+.2f}% ({net:+.2f} USDT)",
                                "",
                                "本邮件由交易策略推荐程序自动发送，仅供参考，不构成投资建议。",
                            ]
                        ),
                    )

            elif signal.signal == SignalType.CLOSE_SHORT:
                log.warning("⬇️  平空信号 | 价格: %.2f | 原因: %s", signal.price, signal.reason)
                closed = strategy.close_position()
                if closed:
                    margin = _margin_usdt(closed["quantity"], closed["entry_price"], leverage)
                    gross = _pnl_usdt_closed("SHORT", closed["entry_price"], signal.price, closed["quantity"])
                    fee_c = taker_fee_usdt(closed["quantity"], signal.price, taker_fee_rate)
                    net = gross - fee_c
                    pnl = _roi_pct_on_margin(net, margin)
                    log.warning("   平仓盈亏(含手续费): %+.2f%% (%+.2f USDT)", pnl, net)
                    paper_equity += net
                    if paper_equity <= 0:
                        log.warning("模拟权益已耗尽，停止运行")
                        _notify_trade_email(
                            f"[{symbol}] 平空信号",
                            "\n".join(
                                [
                                    f"交易对: {symbol} | K线: {interval}",
                                    f"平仓价: {signal.price:.2f}",
                                    f"入场价: {closed['entry_price']:.2f}",
                                    f"原因: {signal.reason}",
                                    f"盈亏(含手续费): {pnl:+.2f}% ({net:+.2f} USDT)",
                                    f"平仓后模拟权益: {paper_equity:.2f} USDT",
                                    "",
                                    "本邮件由交易策略推荐程序自动发送，仅供参考，不构成投资建议。",
                                ]
                            ),
                        )
                        return
                    _notify_trade_email(
                        f"[{symbol}] 平空信号",
                        "\n".join(
                            [
                                f"交易对: {symbol} | K线: {interval}",
                                f"平仓价: {signal.price:.2f}",
                                f"入场价: {closed['entry_price']:.2f}",
                                f"原因: {signal.reason}",
                                f"盈亏(含手续费): {pnl:+.2f}% ({net:+.2f} USDT)",
                                "",
                                "本邮件由交易策略推荐程序自动发送，仅供参考，不构成投资建议。",
                            ]
                        ),
                    )

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
    full_equity_sizing: bool = True,
    min_trade_notional_usdt: float = DEFAULT_MIN_TRADE_NOTIONAL_USDT,
    taker_fee_rate: float = TRADE_TAKER_FEE_RATE,
    funding_rate_8h: float = TRADE_FUNDING_RATE_8H,
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

    strategy = TradingStrategy(
        capital,
        leverage,
        risk_per_trade,
        stop_loss_pct,
        take_profit_pct,
        full_equity_sizing=full_equity_sizing,
        min_trade_notional_usdt=min_trade_notional_usdt,
    )
    trades = []
    equity_curve = [capital]
    current_capital = capital
    interval_td = kline_interval_to_timedelta(interval)
    total_fee_usdt = 0.0
    total_funding_usdt = 0.0

    log.info("=" * 80)
    log.info("交易策略回测报告")
    log.info("=" * 80)
    log.info("交易对: %s | K线周期: %s | 数据量: %d", symbol, interval, len(df))
    log.info("时间范围: %s 至 %s", df.iloc[0]["open_time"], df.iloc[-1]["open_time"])
    log.info("初始资金: %.2f USDT | 杠杆: %dx", capital, leverage)
    log.info("止损: %.1f%% | 止盈: %.1f%%", stop_loss_pct, take_profit_pct)
    if full_equity_sizing:
        log.info(
            "仓位: 全仓复投（每根K线按当前权益开仓）| 名义 < %.1f USDT 或权益 ≤ 0 时终止回测",
            min_trade_notional_usdt,
        )
    else:
        log.info("仓位: 固定本金风险 sizing | 单笔风险: %.1f%%", risk_per_trade)
    log.info(
        "成本: 吃单手续费 %.4f%%/边 | 资金费 %.5f/8h（正=多头付；UTC 0/8/16）",
        taker_fee_rate * 100,
        funding_rate_8h,
    )
    log.info("=" * 80)

    stopped = False
    for i in range(50, len(df)):
        if stopped:
            break

        current_time = df.iloc[i]["open_time"]
        bar_open = to_utc_timestamp(current_time)
        bar_end = bar_open + interval_td
        current_price = float(df.iloc[i]["close"])

        if strategy.position and funding_rate_8h != 0:
            fu = apply_funding_for_interval(strategy.position, bar_end, current_price, funding_rate_8h)
            current_capital += fu
            total_funding_usdt += fu

        strategy.capital = current_capital if full_equity_sizing else capital

        if full_equity_sizing and not strategy.position:
            if current_capital <= 0:
                log.warning("权益已耗尽，回测在此前终止（K线: %s）", current_time)
                stopped = True
                break
            if current_capital * leverage < min_trade_notional_usdt:
                log.warning(
                    "当前权益 %.4f USDT 无法满足最小开仓名义 %.1f USDT（%dx 杠杆），回测终止于 %s",
                    current_capital,
                    min_trade_notional_usdt,
                    leverage,
                    current_time,
                )
                stopped = True
                break

        window_df = df.iloc[: i + 1].copy()
        signal = strategy.analyze_market(window_df)

        if signal.signal == SignalType.OPEN_LONG and not strategy.position:
            if signal.position_size <= 0:
                log.warning("开仓信号下可开数量为 0，回测终止于 %s", current_time)
                stopped = True
                break
            strategy.open_position(signal, funding_anchor=bar_open)
            fee_o = taker_fee_usdt(signal.position_size, signal.price, taker_fee_rate)
            current_capital -= fee_o
            total_fee_usdt += fee_o
            if funding_rate_8h != 0 and strategy.position:
                fu0 = apply_funding_for_interval(strategy.position, bar_end, current_price, funding_rate_8h)
                current_capital += fu0
                total_funding_usdt += fu0
            log.info(
                "[%s] 🟢 开多 | 价格: %.2f | 仓位: %.4f | 止损: %.2f | 止盈: %.2f | 当前权益: %.2f | 原因: %s",
                current_time,
                signal.price,
                signal.position_size,
                signal.stop_loss,
                signal.take_profit,
                current_capital,
                signal.reason,
            )

        elif signal.signal == SignalType.OPEN_SHORT and not strategy.position:
            if signal.position_size <= 0:
                log.warning("开仓信号下可开数量为 0，回测终止于 %s", current_time)
                stopped = True
                break
            strategy.open_position(signal, funding_anchor=bar_open)
            fee_o = taker_fee_usdt(signal.position_size, signal.price, taker_fee_rate)
            current_capital -= fee_o
            total_fee_usdt += fee_o
            if funding_rate_8h != 0 and strategy.position:
                fu0 = apply_funding_for_interval(strategy.position, bar_end, current_price, funding_rate_8h)
                current_capital += fu0
                total_funding_usdt += fu0
            log.info(
                "[%s] 🔴 开空 | 价格: %.2f | 仓位: %.4f | 止损: %.2f | 止盈: %.2f | 当前权益: %.2f | 原因: %s",
                current_time,
                signal.price,
                signal.position_size,
                signal.stop_loss,
                signal.take_profit,
                current_capital,
                signal.reason,
            )

        elif signal.signal == SignalType.CLOSE_LONG and strategy.position:
            closed = strategy.close_position()
            if closed:
                margin = _margin_usdt(closed["quantity"], closed["entry_price"], leverage)
                gross = _pnl_usdt_closed("LONG", closed["entry_price"], current_price, closed["quantity"])
                fee_c = taker_fee_usdt(closed["quantity"], current_price, taker_fee_rate)
                net = gross - fee_c
                total_fee_usdt += fee_c
                pnl_pct = _roi_pct_on_margin(net, margin)
                current_capital += net
                trades.append(
                    {
                        "side": "LONG",
                        "entry_price": closed["entry_price"],
                        "exit_price": current_price,
                        "pnl_pct": pnl_pct,
                        "pnl_usdt": net,
                        "entry_time": current_time,
                        "exit_time": current_time,
                        "reason": signal.reason,
                    }
                )
                log.info(
                    "[%s] ⬆️  平多 | 价格: %.2f | 盈亏(含手续费): %+.2f%% (%+.2f USDT) | 累计权益: %.2f | 原因: %s",
                    current_time,
                    current_price,
                    pnl_pct,
                    net,
                    current_capital,
                    signal.reason,
                )
                if full_equity_sizing and current_capital <= 0:
                    log.warning("平仓后权益耗尽，回测终止")
                    stopped = True

        elif signal.signal == SignalType.CLOSE_SHORT and strategy.position:
            closed = strategy.close_position()
            if closed:
                margin = _margin_usdt(closed["quantity"], closed["entry_price"], leverage)
                gross = _pnl_usdt_closed("SHORT", closed["entry_price"], current_price, closed["quantity"])
                fee_c = taker_fee_usdt(closed["quantity"], current_price, taker_fee_rate)
                net = gross - fee_c
                total_fee_usdt += fee_c
                pnl_pct = _roi_pct_on_margin(net, margin)
                current_capital += net
                trades.append(
                    {
                        "side": "SHORT",
                        "entry_price": closed["entry_price"],
                        "exit_price": current_price,
                        "pnl_pct": pnl_pct,
                        "pnl_usdt": net,
                        "entry_time": current_time,
                        "exit_time": current_time,
                        "reason": signal.reason,
                    }
                )
                log.info(
                    "[%s] ⬇️  平空 | 价格: %.2f | 盈亏(含手续费): %+.2f%% (%+.2f USDT) | 累计权益: %.2f | 原因: %s",
                    current_time,
                    current_price,
                    pnl_pct,
                    net,
                    current_capital,
                    signal.reason,
                )
                if full_equity_sizing and current_capital <= 0:
                    log.warning("平仓后权益耗尽，回测终止")
                    stopped = True

        equity_curve.append(current_capital)
        if stopped:
            break

    if stopped:
        log.info("提示: 回测因权益耗尽或达不到最小开仓名义而提前结束，未使用全部历史K线")

    if strategy.position:
        log.warning("回测结束时仍有持仓，强制平仓")
        final_price = float(df.iloc[-1]["close"])
        qty = strategy.position.quantity
        entry = strategy.position.entry_price
        side = strategy.position.side
        gross = _pnl_usdt_closed(side, entry, final_price, qty)
        fee_c = taker_fee_usdt(qty, final_price, taker_fee_rate)
        net = gross - fee_c
        total_fee_usdt += fee_c
        margin = _margin_usdt(qty, entry, leverage)
        pnl_pct = _roi_pct_on_margin(net, margin)
        current_capital += net
        trades.append(
            {
                "side": side,
                "entry_price": entry,
                "exit_price": final_price,
                "pnl_pct": pnl_pct,
                "pnl_usdt": net,
                "entry_time": df.iloc[-1]["open_time"],
                "exit_time": df.iloc[-1]["open_time"],
                "reason": "回测结束强制平仓",
            }
        )
        strategy.close_position()
        equity_curve.append(current_capital)

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
    log.info("累计手续费(估算): %.2f USDT", total_fee_usdt)
    log.info("累计资金费(估算): %+.2f USDT", total_funding_usdt)
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
