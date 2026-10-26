# ETH historical case — results / 历史案例结果

Source: Binance Vision. Data-derived analysis: CC BY-NC-SA 4.0; see [LICENSE.md](LICENSE.md).

Fixed teaching rule; 2024 is a retrospective chronological evaluation, not prospective evidence. Development 2023 is invalid because of a source gap. No parameter optimization was performed.

固定教学规则；2024 是回顾性的时间后置评估，不是前瞻证据。2023 开发区间因源缺口无效，未做参数寻优。

Returns below are percentages of the full 10,000 USDT account, not returns on the 10% allocation. Fees/slippage are per executed side. Cost scenarios rerun the risk gates and can take different trade paths.

下列收益以全部 10,000 USDT 账户为分母；不是 10% 投入部分的收益。费用按每次成交单边计入，各成本场景独立运行风控，路径可能不同。

| Segment / 区间 | Cost | Strategy | Marked return | Exit-adjusted return | Max close DD | Fees (USDT) | Slippage (USDT) | Fills |
|---|---|---|---:|---:|---:|---:|---:|---:|
| development_2023 | zero | teaching_mean_4 | INVALID_DECLARED_GAP | null | null | null | null | null |
| development_2023 | zero | buy_hold_10pct | INVALID_DECLARED_GAP | null | null | null | null | null |
| development_2023 | base | teaching_mean_4 | INVALID_DECLARED_GAP | null | null | null | null | null |
| development_2023 | base | buy_hold_10pct | INVALID_DECLARED_GAP | null | null | null | null | null |
| development_2023 | stress | teaching_mean_4 | INVALID_DECLARED_GAP | null | null | null | null | null |
| development_2023 | stress | buy_hold_10pct | INVALID_DECLARED_GAP | null | null | null | null | null |
| oos_2024 | zero | teaching_mean_4 | -1.7644% | -1.7644% | 3.8202% | 0.0000 | 0.0000 | 1362 |
| oos_2024 | zero | buy_hold_10pct | 4.6270% | 4.6270% | 7.7127% | 0.0000 | 0.0000 | 1 |
| oos_2024 | base | teaching_mean_4 | -9.7548% | -9.7548% | 10.0236% | 662.4528 | 332.3811 | 694 |
| oos_2024 | base | buy_hold_10pct | 4.6098% | 4.5879% | 7.7105% | 1.0000 | 0.5037 | 1 |
| oos_2024 | stress | teaching_mean_4 | -9.8348% | -9.8348% | 10.0086% | 677.6757 | 339.4304 | 356 |
| oos_2024 | stress | buy_hold_10pct | 4.5917% | 4.5479% | 7.7067% | 1.9996 | 1.0023 | 1 |

## Quarterly returns / 季度收益

Quarters are slices of each continuous 2024 run, not separately reset portfolios or independent validation folds. / 各季度来自同一次连续运行，不重置资金，不属于独立验证折。

| Cost / Strategy | Q1 | Q2 | Q3 | Q4 |
|---|---:|---:|---:|---:|
| oos_2024/zero/teaching_mean_4 | 0.9949% | -0.5923% | 0.1301% | -2.2797% |
| oos_2024/zero/buy_hold_10pct | 5.9745% | -0.8565% | -3.4864% | 3.1786% |
| oos_2024/base/teaching_mean_4 | -4.0604% | -5.5368% | -0.4220% | 0.0000% |
| oos_2024/base/buy_hold_10pct | 5.9567% | -0.8562% | -3.4854% | 3.1776% |
| oos_2024/stress/teaching_mean_4 | -8.8540% | -1.0761% | 0.0000% | 0.0000% |
| oos_2024/stress/buy_hold_10pct | 5.9377% | -0.8558% | -3.4836% | 3.1759% |

## Interpretation / 解读

The teaching strategy loses money even in the zero-cost scenario and underperforms the 10% buy-and-hold benchmark in every modeled cost scenario. Cash has a zero return under the stated no-interest assumption. This case provides no support for an investment edge.

教学策略在零成本下仍亏损，所有成本场景均落后于 10% 买入持有。按现金不计息假设，持有现金收益为零。本案例不支持策略具有投资优势。

The 1,000 USDT drawdown gate restricts new entries; it does not guarantee a maximum realized loss or liquidate existing holdings. Flat late-year returns in costed strategy runs reflect stopped entries, not sustained strategy stability. Read the rejection counts in results.json.

1,000 USDT 回撤限额限制新增入场，不保证最大实际亏损，也不主动强平已有持仓。含成本策略后期曲线走平源于入场被阻止，不代表策略稳定。拒单原因计数见 results.json。

Fee and slippage columns include executed fills only. Exit-adjusted return additionally estimates fees and adverse slippage to sell any final holding at the final close; that hypothetical exit is not recorded as a fill. Slippage is already embedded in fill prices and is not subtracted twice.

费用列仅统计已模拟成交；期末退出成本调整收益另估算剩余持仓在最后收盘退出的费用及滑点，假设退出不记为成交。滑点已包含在成交价格中，不重复扣减。

No funding/borrowing is charged because this is an unlevered spot long/cash simulation. Full-fill assumptions omit order-book impact, latency and historical exchange-filter changes. No statistical significance or real execution claim is made.

这是无杠杆现货多头/现金模拟，不计资金费或借贷。全量成交假设未建模订单簿冲击、延迟及历史交易规则变化，不宣称统计显著性或真实成交。

Result SHA-256: `f7f0b710a293a13f628d0ae3804ba023070f1702cc8a66b2dd5e5c1756c30c5c`
