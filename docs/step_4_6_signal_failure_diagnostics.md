# Step 4.6 — Signal Failure Attribution & Research Protocol Audit

**状态：** `root_cause_not_identified`  
**指标角色：** `POST_HOC_DIAGNOSTIC`；单因子结果为 `EXPLORATORY_UNIVARIATE_DIAGNOSTIC`  
**分析范围：** BaoStock qfq、2018-01-01 至 2023-12-31 的已保存开发期材料。

本阶段只复核 Step 4.5C 已保存预测、验证输入、模型和重要性文件以及冻结 Universe/数据集。没有重新训练、下载行情、挑选证券、调整特征/目标/参数或修改原实验产物。没有读取或评价 2024–2025 `LEGACY_OBSERVED_TEST`，也没有访问 2026+ sealed holdout。

## Git、运行环境与输入完整性

Step 4.5C 已在开始诊断前建立本地检查点：`a307a55 chore: checkpoint step 4.5C universe breadth robustness`。没有设置 Git remote，也没有 push。Step 4.6 源码、测试和本报告目前留在工作区，未提交。

运行环境为 Python 3.14.6；Step 4.5C 基线为 123 项测试通过、107 个依赖兼容。诊断阶段新增 9 项离线测试，随后完整运行全套测试及编译、依赖和 diff 检查；最终结果记于本报告的验证段。`.env`、数据库、行情、模型与 `artifacts/` 均由 `.gitignore` 排除；诊断 JSON/CSV 写入被忽略的 artifacts 目录。

诊断 ID：`signal_failure_20261005T021422Z_83390a3990_final_v2`。输入 Step 4.5C breadth report SHA-256：`2ae45fa5b86ea30e347d11d7ce4975b3fee75de740911be75502207bbfaf6131`。冻结 Universe 哈希：U11 `8b2fae187a7540c2801f1905e13a18543a9a031b8a5cd42d5508a94304d38d62`；U50 `d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218`；U100 `05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9`。数据集 SHA-256：U11 `dfd043f5da9b7b0dc78fd8ebc92291fc2ceb91e465dd37dca03d4e4a0fe45bab`；U50 `cf747f4503e8c11560422177ab11ac326e75cdaf6af83d8378831c82661334c2`；U100 `1814347050b9a35d23148f72a52355f40523ab699fdb1f5d9a2a4c76f1d1cf02`。9 个保存模型及 9 份 validation input 均按文件哈希核验，并在冻结输入上重载复现。

## Daily Rank IC 与负尾部

下表的 negative tail 是每折 Rank IC 最差的约 5% 有效日期（12 日），其 mean contribution 是尾部 IC 总和除以该折全部有效日期数；它是算术分解，不是剔除尾部后的替代绩效。

| U100 fold | 日期数 | Mean | Median | Std | 正 IC 日占比 | 负 IC 日占比 | 负 IC 日平均绝对幅度 | 最差 5% 对全折均值的贡献 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 2021 | 233 | -0.00291 | 0.01402 | 0.14531 | 53.65% | 46.35% | 0.13084 | -0.01529 |
| 2022 | 232 | 0.04668 | 0.05354 | 0.13046 | 64.66% | 35.34% | 0.09299 | -0.01174 |
| 2023 | 232 | -0.00857 | -0.00170 | 0.13101 | 48.71% | 51.29% | 0.11199 | -0.01538 |

2021 正 IC 日期略多于一半，但其负 IC 日期的平均绝对幅度更大（正向幅度均值 0.10763，负向绝对幅度均值 0.13084），折均值仍为负。2023 则负 IC 日期略多。最差尾部日期保留在计算中；U11/U50/U100 每池每折的极端日期均列于 `daily_ic_extreme_dates.csv`，不能据此删除日期或重述实验结论。所有池完整分布（含 P01/P05/P10/P90/P95/P99、正负幅度分布和尾部贡献）见机器报告。

## 2022 正向结果的诊断

U11、U50、U100 在 2022 的每日 Rank IC 均值分别约为 0.04252、0.04748、0.04668。U100 各月均值从 1 月 -0.00318 到 12 月 +0.05345，8 月为 -0.04789，5 月最高 +0.15972；季度均值 Q1 +0.04684、Q2 +0.09747、Q3 +0.01320、Q4 +0.03007。正向表现跨越多个季度和月份，并非仅由单一时期构成，但季度幅度不同，不能据此认定市场状态因果效应。

按 2022 冻结 Universe 中当前交易所分组，SSE 与 SZSE 各 50 只，均有 232 个有效 IC 日期；组内每日 Rank IC 均值分别为 +0.02921 与 +0.06043。按冻结 U11 锚点对齐三模型分数排序，U11/U50、U11/U100、U50/U100 的日 Spearman 均值分别为 0.5213、0.4882、0.6974。排序一致性并非完全相同，且这是 2022 年的观察关联。

现行业分类快照将 U100 分成 36 组，多数组别证券数少于预设的 8 只，因此没有可用的逐日组内 IC，报告将其标为样本宽度不足。相关结果全部带有 `CURRENT_INDUSTRY_CLASSIFICATION_NOT_HISTORICAL_PIT`；它不能解释 2018–2023 的历史行业归属。逐股及逐年分组结果见 `symbol_diagnostics*.csv`、`exchange_diagnostics*.csv`、`industry_diagnostics*.csv`。

## 股票贡献、分数尺度与重要性

逐股诊断覆盖预测参与日期、实际收益/预测分数分布、MAE/RMSE 和截面绝对排名误差。U100 每股结果均保留在 `symbol_diagnostics.csv`，2022 子集在 `symbol_diagnostics_2022.csv`。它们用于定位异质性，没有据此剔除股票或行业。

U100 保存分数与股票层级中位尺度代理的 Spearman 关系在三折间不稳定：股票中位分数对 MA_60 分别为 +0.245、-0.107、-0.258；对 Volume_MA20 为 +0.125、+0.079、-0.036；对 Volatility_20d 为 +0.378、+0.272、+0.154。跨折股票中位预测分数排序相关为 0.502、0.259、0.548。此结果仅支持继续记录 `stock-scale proxy hypothesis`，不能证明尺度导致预测失败。

U100 已保存模型的 top-5 gain 占比约为 43.19%、43.90%、43.99%，折间重要性排序相关为 0.877、0.831、0.946。MA_60 与 Volatility_20d 在三折都进入 top-5；重要性是既有模型解释材料，不代表因果贡献，也没有据此改动特征。

## 固定 17 个特征的单因子诊断

对三池、三折的已保存 validation inputs 逐日计算每个现有特征与 raw forward return 的 Rank IC。共 17 × 3 × 3 = 153 行，均标记 `EXPLORATORY_UNIVARIATE_DIAGNOSTIC`，不拟合单因子模型、不翻转方向、不选择赢家。下表列出 U100 三折每日 Rank IC 均值的等权平均；完整逐折结果与所有 17 个特征位于 `univariate_feature_ic.csv`。

| Feature | U100 平均 daily Rank IC |
|---|---:|
| Return_1d | -0.0089 |
| Return_5d | -0.0265 |
| Return_10d | -0.0396 |
| Return_20d | -0.0450 |
| MA_5 | -0.0614 |
| MA_10 | -0.0595 |
| MA_20 | -0.0574 |
| MA_60 | -0.0542 |
| Price_to_MA20 | -0.0428 |
| RSI_14 | -0.0404 |
| MACD | -0.0430 |
| MACD_Signal | -0.0405 |
| Volatility_5d | -0.0809 |
| Volatility_20d | -0.0783 |
| Volume_MA5 | -0.0828 |
| Volume_MA20 | -0.0716 |
| Volume_Ratio | -0.0440 |

这些事后相关性不构成新 Alpha 证据；未通过观察结果翻转任何特征符号。

## 交易状态与目标期限

按 Step 4.5B/C 已有质量与一致性材料复核：1,684 个 BaoStock `provider_reported_nontrading` sessions 未进入当前 U100 研究样本；当前抽样研究观察 143,911 行均为 `tradestatus=1`，非交易状态和未知状态计数为 0。数据库保留的 961 条旧 canonical bars 与当前 no-trade 元数据重叠，但它们不作为当前成交观察，且 Step 4.5C consistency gate 确认当前研究样本键不含这些记录；没有修改数据库行情。

`target_5d_h5_v1` 的定义仍是逐股后续五个有效成交观察，不等于固定五个交易所交易日。对每个样本检查 `label_end_date` 与该股第 5 个后续有效观察一致，三池 mismatch 均为 0。存在交易停顿时，结束日可跨越更长日历时间或更多交易所 session：

| Universe | 有标签行 | 多个 label end 的预测日 | 超过 5 个交易所 session 的行 | 占比 | 最大日历跨度 | 最大 session 距离 |
|---|---:|---:|---:|---:|---:|---:|
| U11 | 15,821 | 25 | 25 (0.1580%) | 0.1580% | 56 日 | 34 |
| U50 | 71,372 | 112 | 122 (0.1709%) | 0.1709% | 146 日 | 95 |
| U100 | 143,411 | 291 | 377 (0.2629%) | 0.2629% | 150 日 | 100 |

同一预测日不同股票可以有不同 `label_end_date`。因此标签语义正确实现了五个有效观察，但不保证统一持有期；后续任何可交易性研究都必须单独处理这一期限差异。本阶段没有修改目标定义。

## 基线对比

只复算 Step 4.5C 保存的 LightGBM、训练均值和 Return_5d Momentum 预测，不重新拟合。U100 每折 MAE/RMSE 如下：

LightGBM 三折 Rank IC 与平均误差的跨池汇总：

| Universe | 2021 / 2022 / 2023 Rank IC | 三折等权 Rank IC | 平均 MAE | 平均 RMSE |
|---|---|---:|---:|---:|
| U11 | +0.01104 / +0.04252 / +0.04056 | +0.03137 | 0.03954 | 0.05098 |
| U50 | +0.00008 / +0.04748 / +0.00113 | +0.01623 | 0.04131 | 0.06067 |
| U100 | -0.00291 / +0.04668 / -0.00857 | +0.01173 | 0.04132 | 0.06120 |

三池训练均值预测的截面分数恒定，Rank IC 不定义；其三折平均 MAE/RMSE 分别为 U11 0.03816/0.04925、U50 0.04123/0.06027、U100 0.04143/0.06108。Return_5d Momentum 三折 Rank IC 分别为 U11 `-0.02130/-0.04937/+0.00333`、U50 `-0.02615/-0.02811/-0.01865`、U100 `-0.03090/-0.02988/-0.01878`。逐折 Pearson IC、误差和预测分布见 CSV。

| Fold | 基线 | MAE | RMSE | Daily Rank IC | 正 IC 日占比 |
|---|---|---:|---:|---:|---:|
| 2021 | LightGBM | 0.04440 | 0.06483 | -0.00291 | 53.65% |
| 2021 | Train Mean | 0.04417 | 0.06427 | NA（常量分数） | NA |
| 2021 | Return_5d Momentum | 0.06508 | 0.09256 | -0.03090 | 46.78% |
| 2022 | LightGBM | 0.04595 | 0.06750 | +0.04668 | 64.66% |
| 2022 | Train Mean | 0.04641 | 0.06795 | NA（常量分数） | NA |
| 2022 | Return_5d Momentum | 0.06871 | 0.09945 | -0.02988 | 39.22% |
| 2023 | LightGBM | 0.03361 | 0.05128 | -0.00857 | 48.71% |
| 2023 | Train Mean | 0.03371 | 0.05102 | NA（常量分数） | NA |
| 2023 | Return_5d Momentum | 0.04850 | 0.07189 | -0.01878 | 46.55% |

U100 LightGBM 的 Pearson IC 均值三折为 +0.02309、+0.07234、+0.00556；对应 Rank IC 为 -0.00291、+0.04668、-0.00857。保存分数均值/标准差分别为 2021 `0.00083/0.01077`、2022 `0.00146/0.00885`、2023 `-0.00022/0.00709`，折间分布确有变化。该现象与 Pearson 和 Rank IC 的不同符号共同说明线性相关与排序相关不是同一诊断量，不足以确定分布漂移的成因。U11/U50 的 Pearson IC、完整预测分布及全部基线结果在 `baseline_comparison.csv`。这些比较不构成策略收益或投资结论。

## 研究治理核对

- Baseline v1 manifest SHA-256 与 registry 一致：`b997e2960aa3871306ab708cfbccf15dbb86718c4dcc94b17a29c54a007688e9`；其 `legacy_observed_test` 角色与不可变状态保持不变。
- Step 4.3 v2 仍为 development-only；Step 4.4 状态为 `no_target_upgrade`，保留 `target_5d_h5_v1`；Step 4.5C 保留预注册结论 `breadth_not_supported`。
- 当前 U11 与 Step 4.3 历史快照的 46 条旧 no-trade 行和 55 条当前无标签期末行差异已有记录；不混用两版 U11 的 IC。
- 2024–2025 继续登记为 `LEGACY_OBSERVED_TEST` 且本阶段未访问；2026+ sealed holdout 未访问。
- U100 qfq 为 `NOT_POINT_IN_TIME`，其候选名单是当前交易所快照；历史幸存者偏差 unresolved。
- 处理版本并非纯模型差异；没有把跨版本结果解释为纯模型性能变化。

## 结论、限制与后续研究建议

当前证据说明：U100 排序质量在 2021/2023 弱或为负、2022 为正；2021 的正向日期比例超过一半但负向日幅度抵消均值；2022 的正向结果跨多个时段但强弱不同；SSE/SZSE 分组和股票尺度代理存在描述性差异；单因子 Rank IC 在 U100 三折平均值均为负；标签有效观察期限在缺失/非交易期间会拉长。没有一个诊断切片唯一解释失效。结论为 **`root_cause_not_identified`**。

主要限制仍是非 PIT qfq、历史退市/历史成分股缺失、当前行业标签非历史 PIT、重叠标签与非固定日历持有期。建议后续先解决 PIT 价格和历史 Universe 的可验证性；然后在冻结开发日期上预注册 feature/benchmark 分析；独立定义并研究五有效观察期与可执行持有期之间的映射。将 2021/2022/2023 差异视为待检验的稳定性假设，不能按已观察的负年份事后规避。任何后续状态条件需预先登记并留出未触碰的评价数据。本报告完成后停止，不继续实验。

## 产物与测试

机器可读产物目录：`artifacts/reports/signal_failure_diagnostics_20261005T021422Z_83390a3990_final_v2/`。其中 `report.json` 保存全部分布、治理状态、输入哈希、九折模型及 validation input 哈希；CSV 包含每日极端 IC、周期 IC、逐股/逐交易所/逐行业诊断、基线、17-feature 单因子结果、尺度代理、重要性及目标期限行。`target_horizon_rows.csv` 保留逐条期限核对记录。

执行命令：

```powershell
.\.venv\Scripts\quantlab.exe research signal-diagnostics
```

新增 9 项离线测试覆盖冻结输入与哈希、日期边界、no-trade 过滤、预测输入对齐、尾部贡献、目标期限、行业标签警告、独占报告写入，以及不训练/不选股的静态守卫。最终全套 **132 项测试通过、0 失败**；`compileall`、`git diff --check` 通过；Python 3.14.6 环境 `uv --no-cache pip check` 检查 107 个包全部兼容。诊断命令实际返回 `root_cause_not_identified`。未访问 Legacy Test/Holdout，未下载行情，未训练模型。
