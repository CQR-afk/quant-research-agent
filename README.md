# Quant Research Agent

**A local-first quantitative research platform focused on China A-shares, with Hong Kong market support planned as a later extension.**

Quant Research Agent 是一个 A 股优先、港股后续扩展的本地量化研究项目，当前工作集中在金融数据工程、可复现研究、预测诊断和研究治理。它不连接券商账户、不执行自动下单、不提供投资建议，也不声称已验证可盈利的 Alpha；项目不是 trading bot。

## Research findings and limitations

- The original 2024–2025 legacy test did not validate the LightGBM signal: its saved cross-sectional Pearson IC and Rank IC means were negative. This is a historical research result, not a strategy return or a new evaluation in this release.
- Feature v2 and alternative targets were not promoted. The preregistered U100 breadth result is `breadth_not_supported`; Step 4.6 ended `root_cause_not_identified`.
- Step 4.7 remains `data_foundation_upgrade_partial`; Step 4.8A remains `historical_universe_coverage_partial`. Historical A-share membership is not fully reconstructed, and survivorship bias remains unresolved.
- BaoStock qfq is `vendor_snapshot_adjusted`, not strict point-in-time reconstructed pricing. The five-observation target uses five subsequent valid trading observations, not necessarily five exchange trading sessions.
- Current scope includes development-only data and model research. No strategy profitability, investable performance, or production readiness is claimed.

## 目前实现

- `quantlab run` 保留 Yahoo Finance 日线和 Logistic Regression 基线方向分类器，使用时间顺序评估；SPY 仍作为兼容性样例。
- `quantlab news` 是可选 OpenAI Responses API 新闻研究入口。需要在本地 `.env` 配置新密钥。新闻入口独立于本地数据和模型流程。
- `quantlab data download/update` 通过 Provider Router 接入 BaoStock、AKShare 和 Tushare。路由遵循 `config/data_sources.json` 的顺序，单次证券/日期区间只接受一个 Provider 的完整响应；不会把缺口从不同供应商静默拼起来。Tushare 无令牌时会显示不可用并跳过。
- BaoStock 支持不复权、前复权、后复权，并保存 `tradestatus`、`isST` 等供应商元数据。AKShare 保留原有字段映射。Tushare 日线作为不复权基础价格，复权因子单独存入 DuckDB，不会直接改写 OHLC。
- 标准化行情经质量校验后进入 DuckDB + Parquet；原始响应和校验后研究数据分层保存。来源、接口版本、采集时间、数据修订及回填任务状态均有独立记录。
- `quantlab data backfill` 每次按小批次运行、使用 DuckDB 检查点恢复；默认使用 `config/ashare_universe.json` 的样本池，已完成股票不会重复抓取。每个回填任务固定一个 Provider。
- `quantlab data reconcile` 对两个 Provider 的有限日期响应作并列对账，输出日期缺口和字段差异报告；它不会自动选源或写入行情库。
- `quantlab research build-dataset` 从本地 DuckDB 构建逐股特征、未来五个有效观察期收益标签及覆盖报告。数据集只选一个 Provider；若指定复权口径存在多个来源，必须显式指定 `--provider`。
- `quantlab research walk-forward` 使用固定 LightGBM 参数，在 2018–2023 三个 expanding-window folds 对比 v1 与 scale-invariant v2 特征；统一样本键，生成逐折预测、IC、重要性、预测分布和重叠标签诊断。该命令不评价 2024 年及以后的表现。
- `quantlab model train --model lightgbm --dataset ... --manifest ...` 运行前执行数据就绪门禁；门禁失败时不拟合。已完成一次固定参数的真实 BaoStock qfq 基线实验，数据为供应商复权快照，明确不是 point-in-time 价格。
- Historical coverage tooling audits official SSE/SZSE sources and records where historical membership evidence remains incomplete. The current Security Master is a current-listing snapshot, not a point-in-time historical universe.
- The research stack includes a 17-feature pipeline, forward-return targets, purge/embargo safeguards, readiness gates, fixed-parameter LightGBM experiments, development-only walk-forward studies, and deterministic U11/U50/U100 breadth diagnostics. Signal-failure diagnostics preserve inconclusive findings rather than promoting a cause.

## Provider 单位与价格口径

标准行情使用 `market/exchange/symbol/trade_date`、OHLC、`volume`、`amount`、`adjustment/currency/source/downloaded_at` 等字段。代码始终作为字符串保存，以保留前导零。

| Provider | 原始行情与单位 | 复权/可用信息 |
|---|---|---|
| Tushare Pro | `daily` 返回不复权 OHLC；`vol` 按手计、`amount` 按千元计，项目转换为股与人民币元 | `adj_factor` 单独按供应商和交易日版本化保存；因子不自动用于重算 OHLC |
| BaoStock | 日线 `volume` 为股、`amount` 为人民币元；附带 `adjustflag`、`tradestatus`、`isST` | `adjustflag` 区分 hfq/qfq/none；复权结果标记为供应商快照 |
| AKShare | `stock_zh_a_hist` 中文字段映射；成交量从手转换为股，成交额按人民币元保存 | none/qfq/hfq 按独立数据键与分区保存 |
| Yahoo Finance | 保留美股及兼容性接口 | 供应商调整数据标记为 `vendor_adjusted`，不作为 A 股主数据源 |

口径参考：[Tushare daily 文档](https://tushare.pro/document/1?doc_id=27)、[Tushare 复权因子文档](https://tushare.pro/document/2?doc_id=28)、[BaoStock SDK 文档](https://pypi.org/project/baostock/)。供应商返回值及复权快照都带接口与版本元数据。`none` 是当前规范不复权价格基准；qfq/hfq 是供应商快照，后续公司行动可能改写历史值。系统尚未做 point-in-time 因子还原，不应把调整快照当作严格 point-in-time 价格。

## 本地数据流程

```text
Provider response -> data/raw -> schema normalization -> validation
                   -> data/validated Parquet + DuckDB catalog
                   -> research dataset / adjusted view
```

`data/raw/`、`data/validated/`、`data/processed/` 和 `data/database/quantlab.duckdb` 均在 `.gitignore` 中排除。行键包含 Provider 与复权方式，更新采用版本化快照；同一响应重复导入保持幂等。不同来源、复权口径互不覆盖。缺失交易日期若没有权威停牌/交易日历证据，仍报告为 Unknown。

Tushare 凭据可在 `.env` 中设 `TUSHARE_TOKEN=`；OpenAI 新闻密钥使用 `OPENAI_API_KEY=`。本地数据与模型命令不要求配置上述密钥。日志和 CLI 状态只显示是否缺少凭据，不输出密钥。`.env` 不提交。

## 环境和测试

保留项目 Python 3.14.6 虚拟环境，不替换系统 Python。依赖通过 `pyproject.toml` 安装：

```powershell
.\setup.ps1
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本地检查包括 provider 单位映射、复权隔离、因子修订、回填恢复、查询防混源、对账、Parquet/DuckDB 持久化，以及原有模型和 GPT Mock 测试。Mock/Fixture 结果仅说明离线代码路径通过，不代表上游实时服务可用。

## 常用命令

```powershell
# Provider 能力、SDK 和凭据可用性（不发起行情请求）
quantlab data providers

# 单只股票下载，none/qfq/hfq 分开保存；auto 按配置顺序选择一个来源
quantlab data download --market ashare --ticker 000001.SZ --start 20240101 --end 20240331 --adjustment none --provider auto

# 按来源单独更新并重抓重叠区间，以捕获历史修订
quantlab data update --market ashare --ticker 000001.SZ --adjustment none --provider baostock

# 小批次、可恢复的历史回填
quantlab data backfill --start 20180101 --end 20251231 --adjustment none --provider auto --limit 3
quantlab data backfill-status

# Provider 对账；只比较，不自动合并
quantlab data reconcile --ticker 000001.SZ --provider-a baostock --provider-b akshare --start 20240101 --end 20240331 --adjustment none

# 独立下载 Tushare 复权因子、检查数据状态及来源
quantlab data factors --ticker 000001.SZ --start 20240101 --end 20240331
quantlab data status
quantlab data query --market ashare --ticker 000001.SZ --start 20240101 --end 20240331 --adjustment none --provider baostock
quantlab data adjusted-view --ticker 000001.SZ --start 20240101 --end 20240331 --adjustment qfq --provider baostock

# 研究数据集（小样本下仅建数据集和就绪报告，不自动训练）
quantlab research build-dataset --start 20180101 --end 20251231 --adjustment none --provider baostock
quantlab research dataset-status

# 明确构建独立 BaoStock qfq 快照数据集并评估门禁（不覆盖 none 数据集）
quantlab research build-dataset --universe config/ashare_universe_qfq_pilot.json --start 20180101 --end 20251231 --adjustment qfq --provider baostock --output data/processed/ashare_research_dataset_baostock_qfq_v1.parquet
quantlab research build-dataset --universe config/ashare_universe_qfq_pilot.json --start 20180101 --end 20231231 --adjustment qfq --provider baostock --feature-version ashare_daily_v2_scale_invariant --output data/processed/ashare_research_dataset_baostock_qfq_v2_scale_invariant_dev_2018_2023.parquet
quantlab research readiness --dataset data/processed/ashare_research_dataset_baostock_qfq_v1.parquet --manifest data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json
quantlab model train --model lightgbm --dataset data/processed/ashare_research_dataset_baostock_qfq_v1.parquet --manifest data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json --name "Baseline Real Market Experiment v1"
quantlab research walk-forward --dataset-v1 data/processed/ashare_research_dataset_baostock_qfq_v1.parquet --manifest-v1 data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json --dataset-v2 data/processed/ashare_research_dataset_baostock_qfq_v2_scale_invariant_dev_2018_2023.parquet --manifest-v2 data/processed/ashare_research_dataset_baostock_qfq_v2_scale_invariant_dev_2018_2023.manifest.json
quantlab research target-study --target-version all

# 对比同一 Provider 的 none/qfq 价格；跳变只会被标记，不会自动解释或修复
quantlab data compare-bases --provider baostock --ticker 000001.SZ 600519.SH 300750.SZ --start 20240101 --end 20240331 --jump-threshold 0.2

# 保留的旧入口
quantlab run --ticker SPY --period 5y
quantlab news --ticker 000001.SZ
```

数据采集、回填、对账、walk-forward 研究均不要求 GPT、Ollama 或本地 Agent。当前仍未实现 VectorBT 完整策略、Dashboard、Qwen/Hermes 调度或真实交易。

## 实际数据状态

Step 4.2 已从 BaoStock 实际下载 2024Q1 qfq：`000001.SZ`、`600519.SH`、`300750.SZ` 各 58 行，58 个共同交易日复权/不复权收盘价比例分别约为 0.821514、0.908734、0.930927，成交量与成交额逐日一致。重复下载均被跳过。2018–2025 qfq 历史回补使用独立的 11 股工程样本池，任务已完成、合计 21,257 行；原 `none` 数据仍独立保留。`688981.SH` 因此前已确认的缺失成交量/成交额异常未纳入本轮池，也未修补。

独立数据集为 `data/processed/ashare_research_dataset_baostock_qfq_v1.parquet`，manifest 记录 BaoStock、qfq、逐股调整快照时间、数据版本、特征/目标版本和非 PIT 限制。Readiness Gate PASS：训练 12,491、验证 2,552、测试 5,280 行，测试期 480 个日期、每日截面 11 股。已完成且仅完成一次 `Baseline Real Market Experiment v1`：测试 MAE 0.03179、RMSE 0.04800、Pearson IC 均值 -0.08343、Rank IC 均值 -0.06794。它是预测误差与横截面排序结果，不是策略回测或盈利能力结论。模型与全部诊断产物见 `artifacts/experiments/20261004T120849Z_2cb6448746/`；重新加载后固定输入预测一致。

qfq 是 2026-10-04 获取的 BaoStock 供应商快照；历史公司行动信息并非当时可见的 point-in-time 重建。样本池存在幸存者偏差，结果仅为研究基线，不能据此推断可交易表现。Step 4.2 的实际下载、门禁、模型指标和未完成事项记录在 [`docs/project_status.md`](docs/project_status.md) 与 [`docs/development_log.md`](docs/development_log.md)。

## Step 4.3：Walk-forward 与 scale-invariant 特征诊断

Step 4.3 检查点 `636c26f chore: checkpoint step 4.3 walk-forward feature diagnostics` 已在 Step 4.4 前建立；当前 Step 4.4 修改留在工作区。原 Baseline Real Market Experiment v1 保持不变，其 2024–2025 Test Pearson IC `-0.08343`、Rank IC `-0.06794` 仍保留。该区间登记为 `LEGACY_OBSERVED_TEST`，本阶段没有为任何新目标生成 2024 年及以后预测或指标。

新增 `ashare_daily_v2_scale_invariant`，不包含绝对价格/成交量均线或 symbol ID，target 仍为 `target_5d`。在同一 11 股 BaoStock qfq 快照和固定 LightGBM 参数下，使用 2018–2023 的三个 expanding-window folds 对比 v1/v2。每折均按全局日期切分、五期 `label_end_date` purge 和 5-session embargo；v1/v2 验证 sample keys、target 和 label end date 完全相同。完整文字结果见[项目状态记录](docs/project_status.md)；逐折机器产物保存在本地忽略目录 `artifacts/experiments/walkforward_*/`，不会随公开仓库发布。

本轮 v2 误差均值略低（MAE `0.03889` 对 `0.03934`；RMSE `0.05038` 对 `0.05071`），但横截面方向诊断变差：v1 的 fold mean Rank IC 为 `0.05045 / 0.04620 / 0.02500`，v2 为 `-0.00761 / -0.02884 / -0.01516`；正 Rank IC 日比例均值为 `53.08%` 对 `46.91%`。因此 v2 标记 `development_only`，不是候选模型或已验证模型。v1 的跨股票尺度审计发现，MA_5 与每股中位收盘价相关系数 `0.9999998`，Volume_MA20 与每股中位成交量相关系数 `0.9999115`（11 股描述性诊断）。Step 4.3 只做研究诊断，不做参数搜索、策略回测或扩大股票池；详情见 [`docs/project_status.md`](docs/project_status.md)。

## Step 4.4：横截面目标研究

Step 4.4 沿用 Feature v1、固定 LightGBM 参数及 2018–2023 三个 expanding folds，只改变连续监督目标。`quantlab research target-study --target-version all` 从 Step 4.3 预先隔离的 development-only 数据集派生 Raw Return、`target_5d_pool_excess_v1` 和 `target_5d_cs_rank_v1` 三个版本。Pool Excess 是每只股票未来五观察期收益减去同日工程池有效收益均值；CS Rank 使用同日 `rank(method="average", pct=True)` 并变换为 `2*rank_pct-1`。B/C 要求每个预测日至少 8 只有效股票；不足时整日目标保留 NaN。

在 A∩B∩C 完全相同的验证键上，三折 Rank IC（Raw / Pool Excess / CS Rank）为 `0.05045 / -0.05076 / -0.04663`、`0.04620 / 0.03440 / 0.05600`、`0.02500 / -0.04036 / -0.01877`。跨折均值为 `0.04055 / -0.01891 / -0.00313`，正 Rank IC 日比例为 `53.08% / 46.35% / 48.79%`。两种新目标都未满足预设候选条件，结论为 **`no_target_upgrade`**，保留 Raw Return control。目标构造检查中，Pool Excess 1,452 个有效日期的最大绝对日均值为 `1.77e-17`；CS Rank 对原收益的逐日排序单调一致。

结果与研究结论记录在[项目状态记录](docs/project_status.md)；逐日诊断、机器报告和数据集清单保存在本地 `artifacts/` 与 `data/processed/`，并由 `.gitignore` 排除，不会随公开仓库发布。测试环境仍是 Python 3.14.6。qfq 是供应商历史快照而非 point-in-time 还原；当前 11 股工程池存在幸存者偏差。没有读取 Legacy Test 或 2026+ Holdout，也没有进行回测、调参或扩大 Universe。

## Step 4.5：Universe breadth expansion 状态

Step 4.4 本地检查点为 `c4ca573 chore: checkpoint step 4.4 cross-sectional target study`。Step 4.5 最初受网络代理阻断：BaoStock 全量证券资料未返回，AKShare 交易所列表返回 `ProxyError`；当时未冻结候选池。恢复过程及后续来源验证见 [`docs/step_4_5_universe_expansion.md`](docs/step_4_5_universe_expansion.md)。

Step 4.5R 当时定位到 Python 的 HTTP/HTTPS/ALL 代理指向无响应的本地端口 9；DNS 正常。BaoStock 0.9.4 的单股基础资料与日期行业查询可成功返回字段，但全量查询在 60 秒/15 秒进程看门狗下超时。AKShare 1.19.1 的两个交易所列表函数经该代理均为 `ProxyError`。该阶段记录为 **`metadata_access_still_blocked`**；此后交易所公开列表在用户授权的项目级直连下获取并冻结候选池。

## Step 4.5A — Frozen U50 开发期数据状态

Step 4.5A 已完成并通过 U50 data breadth gate（`u50_data_ready`）。冻结 U50 hash 为 `d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218`；现有 11 个 anchors 复用本地数据，新增 39 只从 BaoStock 分别获取 none 与 qfq，日期范围严格为 2018-01-01 至 2023-12-31。生成独立数据集 `data/processed/ashare_research_u50_qfq_v1_dev_2018_2023.parquet`，使用 `ashare_daily_v1` 与 `target_5d_h5_v1`，无模型训练。qfq 是供应商调整快照，不是 point-in-time；Security Master 的当前名单仍有幸存者偏差。逐股复权口径、快照时间、数据缺口和宽度指标见 [`docs/step_4_5A_u50_data_validation.md`](docs/step_4_5A_u50_data_validation.md)。

## Step 4.5B — Frozen U100 开发期数据状态

Step 4.5B 已通过 U100 数据质量与 breadth gate（`u100_data_ready`）。U100 冻结 hash 为 `05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9`，仍为 SSE/SZSE 各 50 只，U50 全部复用、只回填 50 只新增成员。BaoStock none/qfq 两个任务分别 50/50 完成，范围严格为 2018-01-01 至 2023-12-31。有效研究观察 143,911 条；1,684 个 session 为供应商明确报告的 no-trade，缺响应和 unknown gap 均为 0。新数据集 `data/processed/ashare_research_u100_qfq_v1_dev_2018_2023.parquet` 包含 100/100 股票的完整特征和有效目标；1,393 个活动日期中，100% 达到 80 股宽度，96.84% 达到 90 股宽度。qfq 是 100 个不同采集时点的 BaoStock 快照，`mixed_vendor_snapshot_times=true`，不是 point-in-time。未训练模型或运行回测。逐股质量、状态语义、限制和复现命令见 [`docs/step_4_5B_u100_data_validation.md`](docs/step_4_5B_u100_data_validation.md)。

## Step 4.5C — 冻结股票池宽度稳健性

在 `bb66b9a` 的 Step 4.5B 检查点上，`quantlab research breadth-study` 对 U11/U50/U100 分别按固定 Feature v1、五观察期 Raw Target、原 LightGBM 参数进行 2018–2023 三折 Walk-forward。训练前的一致性门禁通过：三个当前数据集的共有样本与特征/标签一致，143,911 条 U100 研究观察均有 BaoStock `tradestatus=1` 证明。旧 Step 4.3 U11 快照保留为历史参考，本轮使用当前 no-trade 规则重新构建 U11。

U100 的 2021/2022/2023 Rank IC 为 `-0.00291 / +0.04668 / -0.00857`，两折为负，预注册结论为 **`breadth_not_supported`**。三池 common-date 比较无日期丢失；anchor-only 诊断、动量/训练均值基线、月/季度 IC、特征重要性及模型重载复现均已保存。详细 Fold 结果和限制见 [`docs/step_4_5C_breadth_robustness.md`](docs/step_4_5C_breadth_robustness.md)。qfq 仍非 PIT，当前名单仍有幸存者偏差；该结论不代表可交易策略表现。

## Step 4.6 — Signal Failure Diagnostics

Step 4.6 只对 Step 4.5C 已保存的 U11/U50/U100 开发期预测、验证输入、固定模型、数据集及治理报告做事后诊断。结论为 `root_cause_not_identified`；U100 三折 Rank IC 为 `-0.00291 / +0.04668 / -0.00857`。报告记录每日 IC 尾部、2022 分期与分组、逐股和当前行业标签限制、股票尺度代理、全部 17 个单因子描述性结果、no-trade/目标期限、已保存基线及治理核对。所有指标标记 `POST_HOC_DIAGNOSTIC`，单因子结果标记 `EXPLORATORY_UNIVARIATE_DIAGNOSTIC`，不构成模型验证。

报告见 [`docs/step_4_6_signal_failure_diagnostics.md`](docs/step_4_6_signal_failure_diagnostics.md)，本地 JSON/CSV 位于被忽略的 `artifacts/reports/signal_failure_diagnostics_20261005T021422Z_83390a3990_final_v2/`。使用 `quantlab research signal-diagnostics` 可复核固定输入；此命令不训练模型。Step 4.6 不访问 Legacy Test 或 Sealed Holdout，不下载行情，也未修改冻结研究输入。qfq 仍非 point-in-time，幸存者偏差 unresolved；目标期限是五个有效成交观察，不保证五个交易所交易日。

## Step 4.7 — 历史证券与 PIT 价格可行性

Step 4.6 已保存为本地检查点 `e385264`。Step 4.7 只核查证券历史日期和公司行动来源。深交所官方上市/摘牌文件与 BaoStock 单股 `outDate` 对一只真实退市证券日期一致；现有 2026 名单仍无法枚举 2018–2023 全部退市证券。BaoStock 分红与历史因子可获取，但本轮未验证它们在历史日期当时的版本，因而没有重建 PIT 价格。结论为 `historical_universe_partially_feasible`、`pit_price_partially_feasible`，合并为 **`data_foundation_upgrade_partial`**；`historical_data_foundation_not_verified`。来源哈希、样本区间、访问限制和下一步条件见 [`docs/step_4_7_pit_historical_universe_feasibility.md`](docs/step_4_7_pit_historical_universe_feasibility.md)。本地只读核验命令为 `.\.venv\Scripts\python.exe -m quantlab.research.data_foundation`，机器报告写入被忽略的 `artifacts/reports/data_foundation_feasibility_step_4_7.json`。

## Step 4.8A — 历史股票池覆盖验证

Step 4.7 已保存为本地检查点 `d24055a`。本阶段获得上交所 2023 卷统计年鉴、深交所 2023 市场统计年鉴，并核验三只真实退市证券的交易所披露和 BaoStock 单股元数据；当前 2026 Security Master 不含这三只证券，证明当前名单不能代表历史全集。年鉴年度表和个股事件能支持部分历史重建，但四个固定日期的逐日 roster 仍未取得，故状态为 `historical_universe_coverage_partial`，不创建历史 Universe artifact。Tushare token 本机不可用，接口仅做文档审计。报告见 [`docs/step_4_8A_historical_universe_coverage.md`](docs/step_4_8A_historical_universe_coverage.md)，离线复核命令为 `.\.venv\Scripts\python.exe -m quantlab.research.historical_universe_coverage`，机器 JSON/CSV 位于被忽略的 `artifacts/reports/`。
