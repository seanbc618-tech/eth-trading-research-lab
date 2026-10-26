# Source gap declaration / 源数据缺口声明

Declared during source validation, before any historical performance results were produced. The protocol and calendar split are unchanged.

The provider's `ETHUSDT-1h-2023-03.zip` contains 743 rows rather than the expected 744. The row opening at 2023-03-24 12:00 UTC ends at millisecond timestamp `1679661583061` rather than the full hour's expected end. The 13:00 UTC hourly row is missing. A shortened row is not treated as a complete hour.

The official [spot maintenance completion announcement](https://www.binance.com/en/support/announcement/detail/813a31506e9f478ea8c1058b425df87a) states that trading resumed at 14:00 UTC on March 24, 2023. This supports interpreting the observed archive gap as a source availability issue, not a local downloader failure. Provider ZIP and CSV hashes are retained in the source manifest.

The entire `development_2023` segment is therefore `INVALID_DECLARED_GAP`, with null metrics for all strategy/benchmark cost scenarios. No backfill, alternate source, shortened evaluation range or replacement development year is used. The separately declared `oos_2024` interval may run only if its complete bars and four preceding warmup bars pass validation.

2023 年 3 月源归档只有 743 行，12:00 UTC K 线提前结束，13:00 UTC K 线缺失。官方停机公告提供了外部背景。整个 2023 开发区间标记为 `INVALID_DECLARED_GAP`、指标为 null；不补造、不换源、不缩区间、不换年份。预先声明的 2024 后置区间单独验证后运行。

Consequently, the case is a fixed-rule retrospective chronological evaluation with an invalid development segment, not a completed fit-and-validate experiment. No parameters were fitted on 2023 or selected from 2024 results.
