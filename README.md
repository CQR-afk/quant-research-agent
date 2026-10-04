# Quant Research Agent

Quant Research Agent 是一个 A 股优先、港股其次的本地量化研究项目。它提供行情接入、数据质量验证、本地数据存储、研究数据集和 readiness-gated LightGBM 基线工程。当前定位是研究与预测；不连接券商账户，不提供自动下单。

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

新增 `ashare_daily_v2_scale_invariant`，不包含绝对价格/成交量均线或 symbol ID，target 仍为 `target_5d`。在同一 11 股 BaoStock qfq 快照和固定 LightGBM 参数下，使用 2018–2023 的三个 expanding-window folds 对比 v1/v2。每折均按全局日期切分、五期 `label_end_date` purge 和 5-session embargo；v1/v2 验证 sample keys、target 和 label end date 完全相同。完整结果见 [`artifacts/reports/walkforward_comparison_2ee1453e2be5.json`](artifacts/reports/walkforward_comparison_2ee1453e2be5.json)，逐折产物在 `artifacts/experiments/walkforward_*/`。

本轮 v2 误差均值略低（MAE `0.03889` 对 `0.03934`；RMSE `0.05038` 对 `0.05071`），但横截面方向诊断变差：v1 的 fold mean Rank IC 为 `0.05045 / 0.04620 / 0.02500`，v2 为 `-0.00761 / -0.02884 / -0.01516`；正 Rank IC 日比例均值为 `53.08%` 对 `46.91%`。因此 v2 标记 `development_only`，不是候选模型或已验证模型。v1 的跨股票尺度审计发现，MA_5 与每股中位收盘价相关系数 `0.9999998`，Volume_MA20 与每股中位成交量相关系数 `0.9999115`（11 股描述性诊断）。Step 4.3 只做研究诊断，不做参数搜索、策略回测或扩大股票池；详情见 [`docs/project_status.md`](docs/project_status.md)。

## Step 4.4：横截面目标研究

Step 4.4 沿用 Feature v1、固定 LightGBM 参数及 2018–2023 三个 expanding folds，只改变连续监督目标。`quantlab research target-study --target-version all` 从 Step 4.3 预先隔离的 development-only 数据集派生 Raw Return、`target_5d_pool_excess_v1` 和 `target_5d_cs_rank_v1` 三个版本。Pool Excess 是每只股票未来五观察期收益减去同日工程池有效收益均值；CS Rank 使用同日 `rank(method="average", pct=True)` 并变换为 `2*rank_pct-1`。B/C 要求每个预测日至少 8 只有效股票；不足时整日目标保留 NaN。

在 A∩B∩C 完全相同的验证键上，三折 Rank IC（Raw / Pool Excess / CS Rank）为 `0.05045 / -0.05076 / -0.04663`、`0.04620 / 0.03440 / 0.05600`、`0.02500 / -0.04036 / -0.01877`。跨折均值为 `0.04055 / -0.01891 / -0.00313`，正 Rank IC 日比例为 `53.08% / 46.35% / 48.79%`。两种新目标都未满足预设候选条件，结论为 **`no_target_upgrade`**，保留 Raw Return control。目标构造检查中，Pool Excess 1,452 个有效日期的最大绝对日均值为 `1.77e-17`；CS Rank 对原收益的逐日排序单调一致。

报告为 [`target_comparison_28b36e885c5a.json`](artifacts/reports/target_comparison_28b36e885c5a.json)，逐日目标构造诊断为 `artifacts/reports/target_cross_section_diagnostics_28b36e885c5a.csv`。三个独立实验和带版本的数据集清单均保存在本地 `artifacts/` 与 `data/processed/`，并由 `.gitignore` 排除。测试环境仍是 Python 3.14.6。qfq 是供应商历史快照而非 point-in-time 还原；当前 11 股工程池存在幸存者偏差。没有读取 Legacy Test 或 2026+ Holdout，也没有进行回测、调参或扩大 Universe。

## Step 4.5：Universe breadth expansion 状态

Step 4.4 本地检查点为 `c4ca573 chore: checkpoint step 4.4 cross-sectional target study`。Step 4.5 已检查本地数据 Provider，但候选元数据请求受网络代理错误阻断：BaoStock 登录成功后全量基本资料/行业查询未返回记录，AKShare 交易所股票列表返回 `ProxyError`。因此没有冻结 U50/U100、下载任何新行情或运行新增模型实验；U11 control 保持原样。详细执行证据与恢复条件见 [`docs/step_4_5_universe_expansion.md`](docs/step_4_5_universe_expansion.md)。当前不能对 breadth 支持性作研究结论。
