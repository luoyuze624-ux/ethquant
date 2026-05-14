# Binance ETH/USDT 自动分析工具

基于 Binance 公开 API 的 ETH/USDT 价格分析工具，支持实时监控、技术分析、策略回测和数据采集。

## 功能

- **实时监控** - 轮询最新价格，触发涨跌幅/阈值告警
- **技术分析** - 计算 SMA/EMA/RSI/MACD/布林带，输出当前形态
- **策略回测** - MA 金叉策略向量化回测，输出收益率/胜率/最大回撤
- **数据采集** - K线数据落地到 SQLite + CSV
- **交易推荐** - 实时生成开多/开空/平仓信号（⚠️ 仅供参考，不构成投资建议）

## 安装

```bash
pip install -r requirements.txt
```

## 使用

### 1. 采集历史数据

```bash
python main.py collect -s ETHUSDT -i 1h -l 500
```

### 2. 实时价格监控

```bash
python main.py monitor -s ETHUSDT -p 10 --pct-alert 1.0
```

参数：
- `-p` / `--poll` - 轮询间隔（秒），默认 10
- `--pct-alert` - 涨跌幅告警阈值（%），默认 1.0
- `--upper` - 价格上限告警
- `--lower` - 价格下限告警

### 3. 技术分析

```bash
python main.py analyze -s ETHUSDT -i 1h -l 500
```

输出示例：
```
技术分析报告: ETHUSDT 1h
收盘价: 2850.50
移动平均线:
  SMA(20): 2845.30
  EMA(12): 2848.20 | EMA(26): 2842.10
RSI(14): 58.30 → 中性区域
MACD: 6.1000 | Signal: 4.2000 | Hist: 1.9000
布林带:
  上轨: 2920.50 | 中轨: 2850.00 | 下轨: 2779.50
  当前位置: 50.3%
```

### 4. 策略回测

```bash
python main.py backtest -s ETHUSDT -i 1h -l 1000 --fast 10 --slow 30
```

参数：
- `--fast` - 快线周期，默认 10
- `--slow` - 慢线周期，默认 30
- `--capital` - 初始资金，默认 10000
- `--fee` - 手续费率（%），默认 0.1

### 5. 实时交易策略推荐 ⚠️

**重要声明：此功能仅供学习参考，不构成投资建议。杠杆交易风险极高，可能导致全部本金损失。**

```bash
python main.py trade -s ETHUSDT -i 1h --capital 1000 --leverage 20
```

参数：
- `-c` / `--check` - 检查间隔（秒），默认 30
- `--capital` - 本金（USDT），默认 1000
- `--leverage` - 杠杆倍数，默认 20
- `--risk` - 单笔风险（%），默认 2.0
- `--stop-loss` - 止损（%），默认 2.0
- `--take-profit` - 止盈（%），默认 4.0

策略逻辑：
- **开多信号**：RSI < 30 + MACD 金叉 + 价格触及布林下轨（综合评分 ≥ 4）
- **开空信号**：RSI > 70 + MACD 死叉 + 价格触及布林上轨（综合评分 ≥ 4）
- **平仓信号**：触发止损/止盈，或反向技术信号

输出示例：
```
持仓中 [LONG] | 入场: 2250.00 | 当前: 2280.50 | 盈亏: +27.11% (+271.11 USDT) | 爆仓价: 2137.50
🟢 开多信号 | 价格: 2245.30 | 原因: RSI超卖(<30) | MACD金叉 | 价格触及布林下轨
   建议仓位: 4.4643 ETH (约 10000.00 USDT)
   止损: 2200.39 (2.0%) | 止盈: 2335.11 (4.0%)
⬆️  平多信号 | 价格: 2335.50 | 原因: 触发止盈 (入场:2245.30 止盈:2335.11)
   平仓盈亏: +80.32% (+803.20 USDT)
```

风险提示：
- 20 倍杠杆下，价格波动 5% 即可能爆仓
- 系统不会自动下单，仅提供信号参考
- 建议先用小资金测试，熟悉策略逻辑
- 务必设置止损，严格控制仓位

### 6. 交易策略历史回测 ✨

**使用与实时推荐系统完全相同的信号逻辑，在历史数据上验证策略表现。**

```bash
# 使用天数参数（推荐）
python main.py trade-backtest -s ETHUSDT -i 1h --days 30 --leverage 20

# 使用 K 线数量参数
python main.py trade-backtest -s ETHUSDT -i 1h -l 1000 --leverage 20

# 测试不同止损止盈参数
python main.py trade-backtest --days 30 --stop-loss 1.5 --take-profit 3.0
```

参数：
- `-d` / `--days` - 回测天数（推荐使用，自动计算 K 线数量）
- `-l` / `--limit` - K 线数量（与 --days 互斥，--days 优先）
- `--capital` - 本金（USDT），默认 1000
- `--leverage` - 杠杆倍数，默认 20
- `--risk` - 单笔风险（%），默认 2.0
- `--stop-loss` - 止损（%），默认 2.0
- `--take-profit` - 止盈（%），默认 4.0
- `--no-db` - 不使用本地数据库，从 API 拉取

回测周期说明：
- `--days 7` + `-i 1h` = 168 根 K 线（7 天 × 24 小时）
- `--days 7` + `-i 15m` = 672 根 K 线（7 天 × 96 个 15 分钟）
- `--days 30` + `-i 1h` = 720 根 K 线（30 天 × 24 小时）
- 注意：Binance API 单次最多返回 1000 根 K 线

输出示例：
```
交易策略回测报告
交易对: ETHUSDT | K线周期: 1h | 数据量: 720
时间范围: 2026-04-14 03:00:00 至 2026-05-14 02:00:00
初始资金: 1000.00 USDT | 杠杆: 20x

回测结果汇总
初始资金: 1000.00 USDT
最终资金: 2864.54 USDT
总收益率: +186.45%
最大回撤: -27.42%

交易统计:
  总交易次数: 20
  盈利次数: 14 | 亏损次数: 6
  胜率: 70.0%
  平均盈利: 237.70 USDT | 平均亏损: -243.89 USDT
  盈亏比: 2.27

交易明细:
  #1 [LONG] 入场: 2308.09 | 出场: 2327.84 | 盈亏: +17.11% (+171.14 USDT) | RSI超买，建议止盈
  #2 [SHORT] 入场: 2329.99 | 出场: 2367.40 | 盈亏: -32.11% (-321.12 USDT) | 触发止损
  ...
```

## 配置

编辑 `config.py` 修改默认参数：

```python
# 基础配置
DEFAULT_SYMBOL = "ETHUSDT"
DEFAULT_INTERVAL = "1h"
MONITOR_POLL_SECONDS = 10
PCT_CHANGE_ALERT = 1.0

# 回测配置
BACKTEST_FAST_MA = 10
BACKTEST_SLOW_MA = 30

# 交易推荐配置
TRADE_CAPITAL = 1000.0          # 本金 (USDT)
TRADE_LEVERAGE = 20             # 杠杆倍数
TRADE_RISK_PER_TRADE = 2.0      # 单笔风险 (%)
TRADE_STOP_LOSS_PCT = 2.0       # 止损 (%)
TRADE_TAKE_PROFIT_PCT = 4.0     # 止盈 (%)
TRADE_CHECK_INTERVAL = 30       # 检查间隔 (秒)
```

## 数据存储

- SQLite: `data/market.db`
- CSV: `data/ETHUSDT_1h.csv`
- 日志: `logs/app.log`

## 依赖

- requests - HTTP 客户端
- pandas - 数据处理
- numpy - 数值计算

## 注意

- 使用 Binance 公开接口，无需 API 密钥
- 默认超时 10 秒，失败自动重试 3 次
- 实时监控使用 REST 轮询，非 WebSocket
