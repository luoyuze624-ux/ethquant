"""手续费（按成交额比例）与 U 本位永续风格资金费（每 8h UTC 整点结算）。"""

from __future__ import annotations

import pandas as pd

_INTERVAL_TO_TIMEDELTA: dict[str, pd.Timedelta] = {
    "1m": pd.Timedelta(minutes=1),
    "3m": pd.Timedelta(minutes=3),
    "5m": pd.Timedelta(minutes=5),
    "15m": pd.Timedelta(minutes=15),
    "30m": pd.Timedelta(minutes=30),
    "1h": pd.Timedelta(hours=1),
    "2h": pd.Timedelta(hours=2),
    "4h": pd.Timedelta(hours=4),
    "6h": pd.Timedelta(hours=6),
    "8h": pd.Timedelta(hours=8),
    "12h": pd.Timedelta(hours=12),
    "1d": pd.Timedelta(days=1),
    "3d": pd.Timedelta(days=3),
    "1w": pd.Timedelta(weeks=1),
}


def kline_interval_to_timedelta(interval: str) -> pd.Timedelta:
    td = _INTERVAL_TO_TIMEDELTA.get(interval)
    if td is None:
        return pd.Timedelta(hours=1)
    return td


def to_utc_timestamp(value: object) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        ts = value
    elif isinstance(value, (int, float)):
        ts = pd.Timestamp(value, unit="ms", tz="UTC")
    else:
        ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def funding_settlement_times_utc(left_exclusive: pd.Timestamp, right_inclusive: pd.Timestamp) -> list[pd.Timestamp]:
    """Binance USDT-M 永续：UTC 每日 00:00 / 08:00 / 16:00 结算。返回 (left_exclusive, right_inclusive] 内的时刻。"""
    lo = to_utc_timestamp(left_exclusive)
    hi = to_utc_timestamp(right_inclusive)
    if hi <= lo:
        return []
    out: list[pd.Timestamp] = []
    day = lo.normalize()
    end_guard = hi.normalize() + pd.Timedelta(days=2)
    while day <= end_guard:
        for h in (0, 8, 16):
            ts = day + pd.Timedelta(hours=h)
            if ts > lo and ts <= hi:
                out.append(ts)
        day += pd.Timedelta(days=1)
    return sorted(out)


def taker_fee_usdt(quantity: float, price: float, fee_rate: float) -> float:
    """单边吃单手续费：名义 = quantity * price（USDT 线性合约近似）。"""
    if fee_rate <= 0 or quantity <= 0 or price <= 0:
        return 0.0
    return quantity * price * fee_rate


def funding_cashflow_usdt(*, side: str, quantity: float, mark_price: float, funding_rate_8h: float) -> float:
    """
    单次 8h 资金费对账户的 USDT 影响（正数表示权益增加）。
    funding_rate_8h > 0：多头付给空头（多头现金流出）。
    """
    if funding_rate_8h == 0 or quantity <= 0 or mark_price <= 0:
        return 0.0
    notional = quantity * mark_price
    if side == "LONG":
        return -notional * funding_rate_8h
    return notional * funding_rate_8h


def apply_funding_for_interval(
    position: object,
    interval_end_utc: pd.Timestamp,
    mark_usdt: float,
    funding_rate_8h: float,
) -> float:
    """
    对持仓结算 (left, interval_end] 内所有 8h 资金费；更新 position.last_funding_applied。
    返回本段累计 cashflow（USDT，正为收入）。
    """
    left = position.funding_anchor if position.last_funding_applied is None else position.last_funding_applied
    hi = to_utc_timestamp(interval_end_utc)
    events = funding_settlement_times_utc(left, hi)
    total = 0.0
    for _ in events:
        total += funding_cashflow_usdt(
            side=position.side,
            quantity=position.quantity,
            mark_price=mark_usdt,
            funding_rate_8h=funding_rate_8h,
        )
    if events:
        position.last_funding_applied = events[-1]
    return total
