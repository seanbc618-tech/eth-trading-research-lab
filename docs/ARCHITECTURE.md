# Architecture / 架构

## Event time and execution / 事件时间与执行

A candle is visible to the strategy only when confirmed. `StrategyContext.history` ends at that candle. A proposal is queued until the next bar, whose open supplies the reference price. The example host requires a contiguous, hourly, single-instrument series. Missing or unordered bars fail with `INVALID_DECLARED_GAP`; it never fills gaps or silently drops missing periods.

策略只能看到当前已确认 K 线及更早历史，信号排队到下一根开盘定价。演示要求单品种、连续小时行情。缺口或乱序会报错，不补造行情、不静默缩短样本。底层回放器也支持忽略未确认行情和去重；演示入口采用更严格的输入要求。

This is a historical simulation convention: the next candle is supplied as a full record, but only its open is used for a pending fill and its close is not passed to the prior decision. It is not a live latency or order-book simulation.

这是历史回放的时序约定，不是实盘延迟或订单簿模拟。前一信号的成交只使用下一根开盘价；下一根收盘值不会传入前一决策。

## Ownership / 职责

- **Plugin / 插件**: emits LONG or EXIT intentions using confirmed history; repeated targets are allowed.
- **Host / 宿主**: skips redundant targets based on actual simulated holdings; derives size from 10% of equity at 1x leverage, rounded down to the contract step, using the adverse simulated fill price.
- **Risk / 风控**: approves or rejects before the broker; the demo tracks day-start equity, peak equity and rolling submitted-order count.
- **Broker / 执行**: applies fixed adverse slippage and fees, emits submit/ack/fill events. The replay broker fully fills orders; it does not enforce volume participation. The separate candle simulator's partial fills are tested but not used by this demo.
- **Store and ledger / 存储及账本**: persists facts and reconstructs average-cost positions; repeated equal events are idempotent, conflicting IDs fail.

The interface is dependency injection, not dynamic package loading or sandboxing. A supplied Python strategy is trusted code. The host supports long/flat only and rejects SHORT explicitly. No later private registration gate is included.

本版通过传入策略工厂替换策略，没有动态插件安装或沙箱。Python 插件属于可信代码；宿主只支持做多/空仓，明确拒绝 SHORT，未包含后期私有平台的注册闸门。

## Units and reconciliation / 单位与对账

`base_quantity = contracts × contract_size_base`

`price_pnl = signed_base_quantity × price_change`

`net_pnl = realized_gross + unrealized_gross − fees + funding`

Leverage is part of sizing/margin, not an additional PnL multiplier. All accounting is Decimal; floats are used only for drawing the report chart. The demo's reconciliation is an explicit assertion about its sole local simulated store, **not exchange-account reconciliation**. Its in-memory SQLite is discarded after reporting; file-backed recovery is exercised by existing tests.

杠杆影响定量与保证金，不额外乘入盈亏。账本使用 Decimal，只有绘图用浮点数。演示中的对账是针对唯一的本地模拟状态，不代表交易所账户核验。演示使用内存 SQLite，文件型 SQLite 的重启恢复由现有测试验证。

## Boundaries / 边界

No broker network adapter, credentials, live command, authenticated endpoints or private data is included. The demo's `order_submission_enabled=True` enables only its in-process simulation. Python itself is not network-sandboxed by the library; verification blocks socket connection paths during the demo as an additional check.

没有交易所网络适配器、凭据或实盘入口。演示中的订单开关只启用进程内模拟。库本身不会把 Python 变成网络沙箱；验证脚本额外阻止演示调用网络连接。
