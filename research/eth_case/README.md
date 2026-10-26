# Public ETH historical case / 公开 ETH 历史案例

[Results / 完整结果](RESULTS.md) · [Protocol / 固定方案](protocol.json) · [Source gap / 数据缺口](DATA_GAP.md) · [Data license / 数据许可](LICENSE.md)

**A negative result is part of the evidence.** The existing four-bar teaching rule was fixed before downloading the data. It loses money in the 2024 evaluation under all three cost assumptions. The 2023 development segment is invalid because of a verified provider gap, and all of its metrics remain null.

**负面结果也是证据。** 既有四根均价教学规则在下载数据前固定，2024 后置评估在三种成本假设下均亏损。2023 开发区间存在已核实的供应商缺口，所有指标保持 null。

## Reproduce / 复现

From the repository root, with Python 3.13 (standard library only):

```bash
# Synthetic semantic checks; no network or real-data download.
python3 scripts/verify_empirical.py

# Download the declared 25 monthly archives and verify provider + pinned hashes.
PYTHONPATH=src python3 -m quantlab.empirical --fetch --check-reference

# Subsequent replay is fully offline and checks the same published results.
PYTHONPATH=src python3 -m quantlab.empirical --check-reference
```

Downloads are cached under ignored `outputs/eth-case-cache/`. Generated results, daily equity and fill-level evidence are written to `outputs/eth-case/`. Do not use the synthetic demo's `--data` argument for this study; the historical entrypoint is separate. No account or API key is required.

下载缓存和逐笔结果保留在被 Git 忽略的 `outputs/`，原始归档不提交。历史案例使用独立入口，不放宽原合成演示的数据限制。无需账户及密钥。

`--check-reference` compares all run metrics and fill hashes, the daily equity CSV and generated report against the published reference. `source_manifest.json` pins both compressed archive and decompressed CSV SHA-256. Provider archive changes cause a mismatch rather than silently changing the sample. A 404 or missing hour invalidates affected segments; network/permission/checksum failures stop execution and are not treated as economic results.

## Data and design / 数据及设计

- **Source:** [Binance Vision](https://data.binance.vision/), `ETHUSDT` spot monthly `1h` archives; format/checksum rules follow the [official public-data repository](https://github.com/binance/binance-public-data). December 2022 is downloaded only to supply the development segment's four warmup bars.
- **Development:** 2023-01-01 through 2023-12-31 UTC, declared but invalid. No fitted parameters or valid development performance are reported.
- **Later evaluation:** 2024-01-01 through 2024-12-31 UTC, 8,784 hourly bars. It starts flat with 10,000 USDT and uses four preceding bars for warmup only. Positions and PnL do not cross segment boundaries. A decision on the final warmup bar may execute at the first evaluation open.
- **Rule:** the unchanged `TeachingMeanStrategy(window=4)`; latest close above the mean requests long exposure, otherwise flat. No parameter search or selection by returns.
- **Allocation:** 10% of equity per new entry, no leverage or continuous rebalance; the remainder earns no interest. The simulated ETH step (0.0001), price tick (0.01) and minimum notional (1 USDT) are fixed research assumptions, not historical venue-filter reconstruction.
- **Execution:** each decision trades only at the next hour's open, with adverse tick-rounded slippage and fees. Last-bar signals do not execute without a next bar. End positions are marked; hypothetical terminal exit cost is reported separately.
- **Risk:** new-entry notional/gross limits 1,500 USDT, day loss 500 USDT, absolute peak-equity drawdown 1,000 USDT, and four submitted orders per rolling 24 hours. Reduce-only exits retain the inherited exemptions from these entry limits. The complete strategy-plus-risk system is evaluated, not the raw moving-average rule in isolation.
- **Baseline:** buy 10% of initial equity at the segment's first open using identical cost and quantity assumptions, then hold; remaining cash stays idle. This controls starting allocation but not time-varying risk exposure. Cash-only return is zero.

2023 和 2024 按预先固定的日历切分，不因收益变化。2024 完整覆盖 8,784 小时，独立重置资金和仓位。四根历史 K 线只用于预热；教学规则不拟合、不寻优。比较对象为相同初始 10% 投入的买入持有，不能把相同初始仓位误称为全过程风险相同。

## Cost assumptions / 成本假设

| Scenario | Fee each side | Adverse slippage each side |
|---|---:|---:|
| zero | 0 bps | 0 bps |
| base | 10 bps (0.10%) | 5 bps (0.05%) |
| stress | 20 bps (0.20%) | 10 bps (0.10%) |

These are transparent modeling assumptions, not a claim about a particular account's historical fee tier. Fees apply on entry and exit notional; slippage changes the fill price. The gross/net scenarios are separate simulations: their position sizes, risk rejections and trade counts can differ. Do not subtract the fee columns from the zero-cost return to recreate the base run.

以上是明确的模型假设，不代表某账户当时实际费率。买卖两侧都收取费用；滑点进入成交价格。成本会改变仓位和风控路径，不能简单把零成本收益减去手续费列当成基础场景。

## Out-of-sample interpretation / 样本外含义

The protocol was committed as `6920da0` before downloads or performance results. The rule was already present in the public teaching example. The later interval is called `oos_2024` only in the chronological sense: this is a retrospective evaluation of publicly known history, not an untouched prospective holdout. The development interval's invalidation further prevents presenting the case as a completed train-and-validate study. The result does not establish alpha, causal superiority or statistical significance. Quarterly returns are dependent slices of the continuous year, not independent folds.

方案在下载及性能结果之前提交；“样本外”只表示时间后置，不代表从未见过的前瞻数据。2023 开发区间无效，进一步限制了训练—验证叙事。季度是连续年份的相关切片，不是独立验证折；不宣称 alpha、因果优越性或显著性。

## Files and rights / 文件及权利

`results.json` contains all twelve declared combinations, including six invalid development combinations. `daily_equity.csv` contains close-of-day account equity for valid runs. `RESULTS.md` is generated from the JSON, including the negative conclusions. Fill details are reproducible locally; their hashes are in the JSON.

Framework code remains MIT. Dataset-derived reports and series follow the [separate attribution and CC BY-NC-SA 4.0 notice](LICENSE.md), reflecting the provider's current [dataset terms](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md). Source archives are not redistributed. No private strategies, original project data or original repository files are used or modified by this case.
