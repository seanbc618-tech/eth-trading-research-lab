# Publication audit / 发布检查

Release: initial MIT public framework, 2026-10-26.

- The 29 extracted files match their source hashes and the documented namespace-only transformations. Their source paths are limited to generic domain, execution, persistence, risk, candle/replay modules and synthetic regression tests.
- The teaching strategy and its deterministic synthetic CSV are public-only additions. No original strategy directory, private factor implementation, experiment protocol, real dataset, account state, deployment material or original Git history is part of the release.
- Every staged path must match the release checksum inventory plus the inventory file itself. Only that set is committed into a new, independent Git repository.
- The public verifier scans the exported text and imports for sensitive patterns, local personal paths, unexpected dependencies and file types. An additional Python literal review found no embedded endpoint or credential assignment strings.
- Existing tests and integration checks are run on the staged Git tree in an isolated temporary directory before uploading it.
- Commits use the owner's GitHub account name and GitHub noreply address; no local personal email is published.
- The original private repository is neither reconfigured nor pushed. Its main-branch identity and private visibility are checked separately before and after publication.

29 个复制文件均与源文件及既定包名替换规则一致。新增教学策略和合成数据不来源于私有研究策略。提交路径限定为发布清单，以全新 Git 历史发布；验证针对暂存区导出的独立副本运行。提交使用 GitHub noreply 邮箱。原私有仓库不修改远端、不推送、不调整可见性，并在发布前后核对。

These checks provide a bounded review of this release, not a universal guarantee against hidden defects or every possible form of confidential information. See `PROVENANCE.md` for the selection boundary and `VALIDATION.md` for the actual engineering evidence.

本检查针对当前发布文件集合，不能替代无限范围的保密或安全保证。文件选择边界见 `PROVENANCE.md`，工程证据见 `VALIDATION.md`。
