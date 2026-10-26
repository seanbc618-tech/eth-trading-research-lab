# Quant Replay Lab

**一个小型、可审计的事件驱动金融工程实验框架。**

[English](README.md) · [架构说明](docs/ARCHITECTURE.md) · [教学演示](docs/WALKTHROUGH.md) · [验证记录](docs/VALIDATION.md)

将已确认的行情转成策略决策、通过风控的订单、模拟成交及可恢复账本。项目首先回答“实验是否可靠执行”，再讨论策略的经济价值。

## 快速运行

使用 Python 3.12 或 3.13。仅依赖标准库，不需要安装依赖、密钥、联网下载或启动服务。

```bash
git clone https://github.com/seanbc618-tech/quant-replay-lab.git
cd quant-replay-lab
PYTHONPATH=src python3 -m quantlab.demo
# 主动降低单笔限额，观察拒单。
PYTHONPATH=src python3 -m quantlab.demo --risk-limit 100 --output outputs/rejected
# 执行已有回归测试及公共演示的集成验证。
python3 scripts/verify.py
```

在浏览器打开 `outputs/demo/report.html`。报告为中英双语、自包含 HTML；同目录还有 `report.json` 和 `equity.csv`。仓库自带 [参考结果](sample_output/)，可以直接阅读。

Windows PowerShell 先执行 `$env:PYTHONPATH="src"`，再运行上述命令中的 `python -m ...` 部分。

## 能展示什么能力

| 问题 | 实现 |
|---|---|
| 策略是否使用未来信息？ | 回调只接收已确认历史，信号在下一根开盘模拟成交。 |
| 杠杆是否被重复计入盈亏？ | 区分合约张数、基础币数量、名义价值和保证金；使用 Decimal 计算。 |
| 同一成交是否被重复入账？ | 事件幂等处理，重复 ID 内容冲突时拒绝。 |
| 未经风控的订单会不会成交？ | 风控决策位于订单创建和模拟执行之前。 |
| 重启后能否恢复？ | SQLite 保存订单、事件和成交事实，可重建账本。 |
| 结果是否可重复？ | 固定合成数据、确定性事件 ID、数据和结果 SHA-256。 |

## 模块与策略的关系

```text
已确认行情 → 策略回调 → 下一根开盘机会 → 框架定量与风控
                                       ↓
                         模拟执行 → SQLite 与账本 → 报告
```

教学策略新写，规则是收盘价高于四根均价时请求做多，否则请求空仓。策略不计算订单数量，也不维护独立账本。替换方式见[教学说明](docs/WALKTHROUGH.md)。

本版使用原有的轻量 Python 协议接口。它不是不可信代码的安全沙箱，也没有包含后期私有研究平台的完整插件注册、维度检查和实现锁系统。

## 公开边界

本版从较早的本地研究内核逐文件提取通用模块。私有策略、参数、实验记录、实际行情、账户配置、部署代码和原 Git 历史均未带入。来源及文件哈希见[提取说明](docs/PROVENANCE.md)。

示例数据是确定生成的 **96 根合成小时行情**。报告中的收益不代表历史市场表现、样本外检验或策略优势。演示支持单品种、做多/空仓、下一根开盘全量模拟成交、固定不利滑点及手续费；不模拟市场冲击、强平和资金费。期末如有持仓只做估值，不强制平仓。通用资金费和部分成交模块另有回归测试。

## 文件导航

- `src/quantlab/domain/`：金融单位、定量、订单状态、仓位和账本。
- `src/quantlab/market_data/`：K 线、事件时钟与策略接口。
- `src/quantlab/execution/`：离线模拟执行和风控前置的审计流程。
- `src/quantlab/persistence/`：SQLite 持久化、恢复和安全状态。
- `src/quantlab/risk/`：单笔、组合、亏损、回撤及频率限制。
- `tutorial.py`、`demo.py`：教学插件及独立演示入口。
- `examples/`、`sample_output/`：合成数据、生成器和参考输出。
- `tests/`、`scripts/verify.py`：提取的现有测试及集成验证。

通用内核来自项目所有者的既有工程。此次公开版集成、教学材料和文档使用 AI 辅助完成；不声称每行代码均由个人独立编写。用于申请时，应准确说明本人参与的设计、审查、实现和实验工作。

本公共仓库采用 [MIT 许可证](LICENSE)，来源说明见 [NOTICE](NOTICE)。

GitHub：[seanbc618-tech/quant-replay-lab](https://github.com/seanbc618-tech/quant-replay-lab)。
