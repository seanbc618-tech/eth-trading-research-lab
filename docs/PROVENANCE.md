# Provenance and publication boundary / 来源与公开边界

This is an allowlisted extraction of generic modules from an earlier local research core. Only the files enumerated in `EXTRACTION_MANIFEST.json` were copied. Their only transformations are package-name/import renames. Original and exported SHA-256 values are recorded. No original Git directory or commit history was copied.

本版只复制 `EXTRACTION_MANIFEST.json` 明列的通用模块与现有合成测试。复制文件仅做包名、测试导入路径替换，记录原件与副本哈希，没有复制原 Git 目录或历史。

New public-only work: `tutorial.py`, `demo.py`, the synthetic CSV and generator, integration verifier, bilingual documentation and sample reports. The private research strategy files were not used to construct the teaching plugin. Later plugin registration, candidate-binding gates, research families, private factors, protocols, experiment outputs and deployment materials are absent.

新增内容为教学策略、演示宿主、合成行情及生成器、验证脚本、双语文档和示例报告。未使用私有策略实现来构造教学插件。后期插件注册、候选参数检查、私有因子、策略家族、协议、研究结果及部署材料均未提取。

Existing tests include fixed fictional prices, fills and timestamps. They are test fixtures, not account records or a copy of held-out market data. `PaperBroker` is an offline candle simulator, not a deployed paper service.

已有测试中的固定价格、成交和日期是代码内的合成 fixture，不是账户记录或封存市场数据。保留的 `PaperBroker` 是离线 K 线模拟器，不是已部署的服务。

The verification script checks extraction hashes, Python import dependencies, suspicious credential patterns, personal absolute paths, file types, symlinks and directory boundaries. Pattern scanning reduces obvious exposure; it is not a proof that arbitrary code contains no secrets. The tight source allowlist and review of selected generic files are the primary publication controls.

验证脚本核对复制文件哈希、Python 导入依赖、凭据模式、个人绝对路径、文件类型、符号链接和目录边界。模式扫描不能证明任意代码绝无敏感信息；逐文件白名单及通用代码审阅才是主要边界。

No third-party source was downloaded or newly vendored for this release. At the owner's explicit direction, the public repository is distributed under the MIT License. See `LICENSE` and `NOTICE`.

本次未下载或引入第三方源码。按照项目所有者明确要求，本公共仓库采用 MIT 许可证，详见 `LICENSE` 和 `NOTICE`。
