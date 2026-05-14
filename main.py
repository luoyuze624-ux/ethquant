#!/usr/bin/env python3
import argparse
import sys

from binance_eth.analyzer import analyze_klines
from binance_eth.backtest import backtest_ma_cross
from binance_eth.client import BinanceClient
from binance_eth.log import get_logger
from binance_eth.monitor import monitor_price
from binance_eth.storage import save_klines_to_csv, save_klines_to_db
from binance_eth.trader import backtest_trading_strategy, run_trading_bot
from config import (
    DEFAULT_INTERVAL,
    DEFAULT_KLINES_LIMIT,
    DEFAULT_SYMBOL,
    MONITOR_POLL_SECONDS,
    PCT_CHANGE_ALERT,
    PRICE_LOWER_ALERT,
    PRICE_UPPER_ALERT,
    TRADE_CAPITAL,
    TRADE_CHECK_INTERVAL,
    TRADE_LEVERAGE,
    TRADE_RISK_PER_TRADE,
    TRADE_STOP_LOSS_PCT,
    TRADE_TAKE_PROFIT_PCT,
)

log = get_logger(__name__)


def calculate_limit_from_days(interval: str, days: int) -> int:
    """根据 K 线周期和天数计算需要的 K 线数量"""
    interval_minutes = {
        "1m": 1,
        "3m": 3,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "2h": 120,
        "4h": 240,
        "6h": 360,
        "8h": 480,
        "12h": 720,
        "1d": 1440,
        "3d": 4320,
        "1w": 10080,
    }

    minutes_per_interval = interval_minutes.get(interval)
    if not minutes_per_interval:
        log.warning("未知的 K 线周期 %s，使用默认值", interval)
        return 1000

    total_minutes = days * 24 * 60
    limit = int(total_minutes / minutes_per_interval)
    return limit


def cmd_collect(args):
    client = BinanceClient()
    log.info("采集 %s %s K线数据 (limit=%d)", args.symbol, args.interval, args.limit)
    klines = client.get_klines(args.symbol, args.interval, args.limit)
    if not klines:
        log.error("未获取到数据")
        return
    save_klines_to_db(klines, args.symbol, args.interval)
    csv_path = save_klines_to_csv(klines, args.symbol, args.interval)
    log.info("数据采集完成，共 %d 条", len(klines))


def cmd_monitor(args):
    monitor_price(
        symbol=args.symbol,
        poll_seconds=args.poll,
        pct_alert=args.pct_alert,
        upper_alert=args.upper,
        lower_alert=args.lower,
    )


def cmd_analyze(args):
    analyze_klines(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        save_data=args.save,
    )


def cmd_backtest(args):
    backtest_ma_cross(
        symbol=args.symbol,
        interval=args.interval,
        limit=args.limit,
        fast_ma=args.fast,
        slow_ma=args.slow,
        initial_capital=args.capital,
        fee_rate=args.fee / 100,
        use_db=args.use_db,
    )


def cmd_trade(args):
    run_trading_bot(
        symbol=args.symbol,
        interval=args.interval,
        check_interval=args.check,
        capital=args.capital,
        leverage=args.leverage,
        risk_per_trade=args.risk,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
    )


def cmd_trade_backtest(args):
    limit = args.limit
    if args.days:
        limit = calculate_limit_from_days(args.interval, args.days)
        log.info("回测周期: %d 天 (%s) = %d 根 K 线", args.days, args.interval, limit)

    backtest_trading_strategy(
        symbol=args.symbol,
        interval=args.interval,
        limit=limit,
        capital=args.capital,
        leverage=args.leverage,
        risk_per_trade=args.risk,
        stop_loss_pct=args.stop_loss,
        take_profit_pct=args.take_profit,
        use_db=args.use_db,
    )

# python main.py trade -s ETHUSDT -i 5m --capital 1000 --leverage 20  --stop-loss 1.5 --take-profit 5.0
def main():
    parser = argparse.ArgumentParser(
        description="Binance ETH/USDT 自动分析工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    collect_parser = subparsers.add_parser("collect", help="采集K线数据到本地")
    collect_parser.add_argument("-s", "--symbol", default=DEFAULT_SYMBOL, help="交易对")
    collect_parser.add_argument("-i", "--interval", default=DEFAULT_INTERVAL, help="K线间隔")
    collect_parser.add_argument("-l", "--limit", type=int, default=DEFAULT_KLINES_LIMIT, help="K线数量")
    collect_parser.set_defaults(func=cmd_collect)

    monitor_parser = subparsers.add_parser("monitor", help="实时价格监控与告警")
    monitor_parser.add_argument("-s", "--symbol", default=DEFAULT_SYMBOL, help="交易对")
    monitor_parser.add_argument("-p", "--poll", type=int, default=MONITOR_POLL_SECONDS, help="轮询间隔(秒)")
    monitor_parser.add_argument("--pct-alert", type=float, default=PCT_CHANGE_ALERT, help="涨跌幅告警阈值(%)")
    monitor_parser.add_argument("--upper", type=float, default=PRICE_UPPER_ALERT, help="价格上限告警")
    monitor_parser.add_argument("--lower", type=float, default=PRICE_LOWER_ALERT, help="价格下限告警")
    monitor_parser.set_defaults(func=cmd_monitor)

    analyze_parser = subparsers.add_parser("analyze", help="历史K线技术分析")
    analyze_parser.add_argument("-s", "--symbol", default=DEFAULT_SYMBOL, help="交易对")
    analyze_parser.add_argument("-i", "--interval", default=DEFAULT_INTERVAL, help="K线间隔")
    analyze_parser.add_argument("-l", "--limit", type=int, default=DEFAULT_KLINES_LIMIT, help="K线数量")
    analyze_parser.add_argument("--no-save", dest="save", action="store_false", help="不保存数据")
    analyze_parser.set_defaults(func=cmd_analyze)

    backtest_parser = subparsers.add_parser("backtest", help="策略回测")
    backtest_parser.add_argument("-s", "--symbol", default=DEFAULT_SYMBOL, help="交易对")
    backtest_parser.add_argument("-i", "--interval", default=DEFAULT_INTERVAL, help="K线间隔")
    backtest_parser.add_argument("-l", "--limit", type=int, default=DEFAULT_KLINES_LIMIT, help="K线数量")
    backtest_parser.add_argument("--fast", type=int, default=10, help="快线周期")
    backtest_parser.add_argument("--slow", type=int, default=30, help="慢线周期")
    backtest_parser.add_argument("--capital", type=float, default=10000, help="初始资金")
    backtest_parser.add_argument("--fee", type=float, default=0.1, help="手续费率(%)")
    backtest_parser.add_argument("--no-db", dest="use_db", action="store_false", help="不使用本地DB")
    backtest_parser.set_defaults(func=cmd_backtest)

    trade_parser = subparsers.add_parser("trade", help="实时交易策略推荐 (仅供参考)")
    trade_parser.add_argument("-s", "--symbol", default=DEFAULT_SYMBOL, help="交易对")
    trade_parser.add_argument("-i", "--interval", default=DEFAULT_INTERVAL, help="K线间隔")
    trade_parser.add_argument("-c", "--check", type=int, default=TRADE_CHECK_INTERVAL, help="检查间隔(秒)")
    trade_parser.add_argument("--capital", type=float, default=TRADE_CAPITAL, help="本金(USDT)")
    trade_parser.add_argument("--leverage", type=int, default=TRADE_LEVERAGE, help="杠杆倍数")
    trade_parser.add_argument("--risk", type=float, default=TRADE_RISK_PER_TRADE, help="单笔风险(%)")
    trade_parser.add_argument("--stop-loss", type=float, default=TRADE_STOP_LOSS_PCT, help="止损(%)")
    trade_parser.add_argument("--take-profit", type=float, default=TRADE_TAKE_PROFIT_PCT, help="止盈(%)")
    trade_parser.set_defaults(func=cmd_trade)

    trade_backtest_parser = subparsers.add_parser("trade-backtest", help="交易策略历史回测")
    trade_backtest_parser.add_argument("-s", "--symbol", default=DEFAULT_SYMBOL, help="交易对")
    trade_backtest_parser.add_argument("-i", "--interval", default=DEFAULT_INTERVAL, help="K线间隔")
    trade_backtest_parser.add_argument("-l", "--limit", type=int, default=1000, help="K线数量（与--days互斥）")
    trade_backtest_parser.add_argument("-d", "--days", type=int, help="回测天数（优先于--limit）")
    trade_backtest_parser.add_argument("--capital", type=float, default=TRADE_CAPITAL, help="本金(USDT)")
    trade_backtest_parser.add_argument("--leverage", type=int, default=TRADE_LEVERAGE, help="杠杆倍数")
    trade_backtest_parser.add_argument("--risk", type=float, default=TRADE_RISK_PER_TRADE, help="单笔风险(%)")
    trade_backtest_parser.add_argument("--stop-loss", type=float, default=TRADE_STOP_LOSS_PCT, help="止损(%)")
    trade_backtest_parser.add_argument("--take-profit", type=float, default=TRADE_TAKE_PROFIT_PCT, help="止盈(%)")
    trade_backtest_parser.add_argument("--no-db", dest="use_db", action="store_false", help="不使用本地DB")
    trade_backtest_parser.set_defaults(func=cmd_trade_backtest)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
