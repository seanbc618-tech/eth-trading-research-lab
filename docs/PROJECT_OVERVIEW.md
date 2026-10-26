# Project overview / 项目介绍

## ETH Trading Research Lab

An offline Python framework, extracted from a personal ETH trading project, for examining the path from a strategy decision to a recorded financial outcome. Its public demonstration combines confirmed-candle replay, host-controlled position sizing, risk checks, simulated fills and recoverable accounting using only the standard library.

一个从个人 ETH 交易项目提取的离线 Python 框架，用于检查策略决策如何形成可记录、可复核的金融结果。公共演示将已确认行情回放、宿主定量、风险检查、模拟成交与可恢复账本连接起来，仅依赖标准库。

## Why these choices / 为什么这样设计

**Separate strategy intent from execution.** The teaching strategy asks for long or flat exposure. A shared host determines size, applies limits and routes execution through the same ledger. This makes a strategy replacement less likely to silently change the accounting model. The interface is for trusted Python code; it does not prevent arbitrary code execution.

**把策略意图与执行分开。** 教学策略提出做多或空仓请求，共用宿主决定数量、检查限额并使用同一账本。这样可以减少替换策略时会计口径被顺带改变的问题，但 Python 接口不构成安全沙箱。

**Represent financial units explicitly.** Contracts, base quantity, notional and margin have different meanings. Decimal arithmetic and rounding tests make those distinctions visible. Leverage affects sizing; it must not multiply already computed price PnL a second time.

**明确金融单位。** 合约张数、基础币数量、名义价值和保证金不能混用。Decimal 计算及取整测试使这些区别可检查；杠杆影响定量，不能再次乘入已经计算出的价格盈亏。

**Use persisted events for recovery.** SQLite records order transitions and fills so the ledger can be reconstructed. Duplicate IDs are accepted only when their contents agree. This is local simulation-state recovery, not reconciliation against an exchange account.

**用持久化事件恢复状态。** SQLite 记录订单变化与成交，从事实重建账本；重复 ID 只在内容一致时接受。这是本地模拟状态恢复，不是交易所账户对账。

**Keep the public experiment small.** A generated fixture avoids private data and external service dependencies. It makes repeatability easy to check, at the cost of providing no evidence about real-market returns or liquidity. The public host is intentionally single-instrument and long/flat.

**控制公开实验范围。** 生成数据避免携带私有数据或依赖外部服务，方便检查可重复性；代价是无法证明真实市场收益或流动性行为。公共宿主限定为单品种、做多/空仓。

## What the evidence supports / 证据支持什么

The selected regression suite has 43 passing tests. Integration checks demonstrate reproducible results, accounting identities, risk rejection before fills, temporal prefix consistency, missing-bar rejection and strategy replacement. Source provenance and verification conditions are linked from the README.

所选回归套件有 43 项通过。集成检查覆盖结果复现、会计恒等式、成交前风险拒绝、时序前缀一致性、缺口拒绝与策略替换。README 提供源文件来源及验证条件的链接。

This establishes bounded engineering behavior under the tested assumptions. It does not establish alpha, actual executed ETH returns, statistical significance, production readiness or a complete implementation of the original private project. A separate public ETH case now reports a negative retrospective result with explicit fees, slippage and a chronological split; its invalid development interval and modeling limits are disclosed.

这些证据只支持已测试假设下的有限工程行为，不证明 alpha、真实成交收益、统计显著性、生产可用性，也不代表原私有项目的完整实现。新增公开 ETH 案例报告负面的回顾性实证结果，明确手续费、滑点、时间切分、无效开发区间和模型限制。

## Development and attribution / 开发与署名

The owner selected the public release scope: preserve the private project, exclude private strategies and sensitive material, provide a runnable teaching example and bilingual documentation, and release the public framework under MIT. The extraction, teaching integration and documentation were prepared with AI assistance. Generic source modules and their hashes are listed in the extraction manifest.

项目所有者确定了公开范围：保留原私有工程，排除私有策略及敏感材料，提供可运行教学示例和双语文档，并以 MIT 许可发布公共框架。提取、教学集成及文档采用 AI 辅助完成；通用源模块及哈希列于提取清单。

The available repository evidence does not reconstruct individual authorship of the earlier private project's components. Any accompanying account of personal contributions should identify specific decisions, implementations or reviews the author can explain and substantiate, rather than attributing every line or test to one person.

现有仓库证据不能还原更早私有工程各模块的个人作者分工。配套的个人贡献说明应具体到本人能够解释、举证的决策、实现或审查，不把全部代码和测试笼统归为个人独立完成。
