# Validation receipt / 验证记录

Date / 日期: 2026-10-26 (Asia/Shanghai)

Runtime / 环境: Python 3.13.7, macOS. No third-party runtime dependency. Python 3.12 is the documented minimum target but was not separately exercised in this review.

## Verified / 已验证

- **43 existing unit tests passed**: contract units and sizing, ledger PnL, duplicate/conflicting events, order state transitions, SQLite atomicity and restart recovery, confirmed-candle causality, offline brokers and pre-execution safety gates.
- **Determinism**: repeated demo runs match the complete checked-in reference JSON, not only a headline metric.
- **Accounting identities**: ending equity minus initial equity equals net PnL; realized plus unrealized gross PnL minus fees equals net PnL for this no-funding demo.
- **Risk rejection**: a USD 100 order limit produces 46 rejected entry requests, zero fills and zero PnL. Rejections include explicit reasons.
- **Temporal behavior**: the first 48 equity points match a separately truncated run; execution uses a later candle; missing bars fail rather than being filled forward.
- **Replaceability**: a no-trade plugin leaves equity unchanged; a buy-once plugin retains an explicitly marked open position.
- **Costs**: removing fees and slippage improves this particular synthetic run, while the ordinary report charges positive fees.
- **Offline behavior**: the integration demo passes with socket connection functions patched to raise.
- **Data provenance**: the public generator recreates the synthetic CSV byte-for-byte.
- **Extraction boundary**: copied module/test hashes match the allowlist; selected files use only the standard library and public package; suspicious credential patterns, personal paths and symlinks are absent in the scanned export.
- **Original preservation**: all 29 selected original source/test files still match their pre-extraction hashes. No edits or mutating Git operations were performed in the original source repositories.
- **Standalone verification**: the public tree was copied to a temporary directory and verified with isolated Python (`-I -B`), without the original project on the import path.

43 项已有测试通过；演示确定性、账本恒等式、46 次拒单及零成交、前缀时序一致性、缺口拒绝、策略替换、期末持仓估值、成本比较、断网连接检查和数据再生成均通过。原始 29 个被提取文件哈希未变。独立临时副本采用隔离 Python 验证，无需原工程目录。

## Reference identity / 参考身份

```text
result_sha256:
a18d5b357b176f63a8cea659eeb400c26c92b39a5f195e31c7f3890868f94523
```

The sample contains 96 artificial hourly bars and 8 simulated fills. The reference is an engineering fixture, not a return forecast. This initial synthetic validation did not use real data. A separate public historical case is now documented below; no prospective trading or exchange execution was performed.

示例包含 96 根合成小时行情及 8 笔模拟成交。初始合成验证未使用真实数据；新增公开历史案例见下文。未运行前瞻交易或实盘验证，未得出盈利优势结论。

## Recheck / 复核

```bash
python3 scripts/verify.py
```

`RELEASE_FILES.sha256` fingerprints the public release, excluding itself and generated working output directories. The checker validates the copied source hashes; the release file list can additionally be checked using `shasum -a 256 -c RELEASE_FILES.sha256`.

`RELEASE_FILES.sha256` 标识公开版本中的文件，不包含清单自身和运行输出目录。复制源文件哈希由验证脚本检查，整个交付目录可额外使用上述 `shasum` 命令核对。

Pattern scans and synthetic tests are bounded evidence. They are not a security audit, a proof of no hidden defects, or an economic validation. The owner selected the MIT License for the public release.

## Public historical case / 公开历史案例

See [the case report](../research/eth_case/README.md). `scripts/verify_empirical.py` uses synthetic hand-calculable fixtures to check flat behavior, buy-and-hold accounting, per-side fees, hypothetical terminal exit costs, missing-bar nulls, next-open execution, prefix consistency and repeated-run equality. The original 43 regression tests remain unchanged.

The full historical replay checks each downloaded ZIP and decompressed CSV against pinned SHA-256 values, rejects incomplete evaluation ranges, reconciles incremental and recovered SQLite ledgers, and compares the complete result JSON, daily equity CSV and generated report against the published reference. Raw archives and fills remain in ignored local outputs.

合成可手算用例验证历史入口的会计、费用、期末退出成本、缺口留空和时序。完整历史复现另外核对源归档哈希、SQLite 重建账本、全量结果与每日权益。2023 因真实源缺口无效；2024 结果为负面教学证据，详见案例。

Full historical rerun matched `f7f0b710a293a13f628d0ae3804ba023070f1702cc8a66b2dd5e5c1756c30c5c`, all reported run metrics and fill hashes, daily equity bytes and generated report bytes. Six development combinations remain INVALID_DECLARED_GAP/null; six 2024 evaluation combinations are valid. This result does not alter the initial synthetic reference hash.
