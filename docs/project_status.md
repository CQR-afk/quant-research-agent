# Quant Research Agent 项目状态

**状态核对日期：2026-10-05**
**项目目录：`D:\Quantification`**
**定位：A 股优先、港股其次的本地研究与预测平台；不连接券商账户、不自动下单。**

## Step 4.4 当前状态

Step 4.3 检查点已按要求提交：`636c26f chore: checkpoint step 4.3 walk-forward feature diagnostics`。没有 remote 或 push。Step 4.4 代码、测试和文档现在处于未提交工作区；本阶段只研究目标定义，保留 Feature v1、固定 LightGBM 参数和原三折。

主实验命令为 `quantlab research target-study --target-version all`，数据只读自 Step 4.3 materialized 的 `ashare_daily_v1` 2018–2023 development snapshot。新目标数据集分别为 `ashare_research_v1_target_raw_dev.parquet`、`ashare_research_v1_target_pool_excess_dev.parquet` 和 `ashare_research_v1_target_cs_rank_dev.parquet`，每个都有独立 manifest / dataset version。每个 manifest 明确写入 feature/target version、精确定义、5 observation horizon、minimum cross-section=8、development cap=2023-12-31、`development_walk_forward` role 及 PIT 限制。

目标定义：A=`target_5d_h5_v1` 原样保留现有 `close.shift(-5)/close - 1`；B=`target_5d_pool_excess_v1` 为同一 prediction date 有效 A 股工程池 raw return 减去该日 pool mean（不是市场超额收益）；C=`target_5d_cs_rank_v1` 为同日 raw return 按 `rank(method="average", pct=True)` 计算后乘 2 减 1。B/C 在有效收益少于 8 只的日期均为 NaN，不填补。三者 `label_end_date` 与原始 target 完全相同，target Builder 不改动任何 feature 列；模型只接收固定 v1 features。

所有目标在 common sample 上用模型 score 对真实 raw `target_5d` 计算 Pearson/Rank IC，Rank IC 为主要指标。common keys 要求同 symbol、prediction date、label end date、且原始收益一致；每折 common/各目标 native 行数、symbol、日期如下：

| Fold | Train 实际区间 / 行数（A/B/C） | Validation 实际区间 | Common 与 native 验证样本 |
|---|---|---|---|
| 1 | 2018-04-02–2020-12-17 / 7,156 | 2021-01-04–2021-12-17 | 2,563 行、11 股、233 日期 |
| 2 | 2018-04-02–2021-12-17 / 9,829 | 2022-01-04–2022-12-16 | 2,552 行、11 股、232 日期 |
| 3 | 2018-04-02–2022-12-16 / 12,491 | 2023-01-03–2023-12-15 | 2,552 行、11 股、232 日期 |

三个版本的各折 native 验证行数与 common 行数完全相同；时间边界一致，purge Train/Validation 为 110/110、110/110、110/55 行，均使用 5-session embargo、不 shuffle。

| Fold | Raw Rank IC / RankICIR / 正日比 | Pool Excess Rank IC / RankICIR / 正日比 | CS Rank Rank IC / RankICIR / 正日比 |
|---|---|---|---|
| 2021 | .05045 / .14158 / 56.22% | -.05076 / -.15121 / 42.49% | -.04663 / -.14430 / 44.21% |
| 2022 | .04620 / .12296 / 53.88% | .03440 / .08670 / 51.29% | .05600 / .15417 / 56.03% |
| 2023 | .02500 / .07523 / 49.14% | -.04036 / -.11497 / 45.26% | -.01877 / -.05914 / 46.12% |
| 等权跨折均值 | .04055 / .11326 / 53.08% | -.01891 / -.05983 / 46.35% | -.00313 / -.01642 / 48.79% |

Pearson IC 跨折均值 Raw / Pool Excess / CS Rank 为 `.06755 / -.00661 / .00926`。所有模型 Newey–West lag=5 的 Pearson/Rank IC mean、标准误及 t diagnostic 均保存在统一 JSON 报告中；这些只诊断重叠标签相关性，不是显著性或投资有效性的证明。跨目标 native MAE/RMSE 各自在其目标单位下单独报告，并标记 `NOT CROSS-TARGET COMPARABLE`；对于以横截面 rank 为目标的模型，不报告 score-to-return MAE/RMSE。

native fit 的 MAE/RMSE（不可跨目标比较）：Raw 三折 `.04405/.05740, .04253/.05486, .03144/.03988`；Pool Excess `.03507/.04662, .02815/.03781, .02043/.02795`；CS Rank `.51167/.59718, .49841/.58082, .50685/.58571`。分布、p01/p10/median/p90/p99 以及每折月/季度 Rank IC 在报告中逐折保存。Pool Excess 在 1,452 个可用横截面日期的最大绝对日均值为 `1.77e-17`，全部通过 `1e-12` 中心性检查；CS Rank 在全部 1,452 日与 raw return 单调一致。11 股同截面的 rank target 分布均值为 `0.09091`、实际端点约 `-0.81818` 到 `1.0`，符合定义，不应期待 rank_pct 离散中心化后的样本均值严格为零。

候选筛选 A/B/C/D 对 Pool Excess 和 CS Rank 均未通过：两者各只有 1/3 折的 common Rank IC 为正、mean Rank IC 低于 Raw control、正 Rank IC 日比例低于 control，且另两折为负。最终为 **`no_target_upgrade`**，选择并保留 Raw Return；不进入扩大 Universe。实验报告 `artifacts/reports/target_comparison_28b36e885c5a.json`，逐日期中性与 breadth 诊断 `artifacts/reports/target_cross_section_diagnostics_28b36e885c5a.csv`。实验 IDs：`walkforward_target_raw_v1_f120d1fa7981`、`walkforward_target_pool_excess_v1_c8dd2bb960d6`、`walkforward_target_cs_rank_v1_601b71a85ac3`。

本阶段未访问 2024–2025 `LEGACY_OBSERVED_TEST`；也没有下载、读取或评价 2026+ `future_sealed_holdout`，治理中的 `used=false` 保持不变。没有回测、调参、换模型或扩展 11 股样本。完整离线测试 **92 passed / 0 failed**，`compileall` 通过，`uv pip check` 检查 107 个包全部兼容。qfq 是非 point-in-time vendor snapshot，工程股票池存在 survivorship bias。Baseline v1 的负 Legacy Test `Pearson IC=-0.08343 / Rank IC=-0.06794` 与 Step 4.3 v2 三折负 Rank IC 均保留，未用来挑选本阶段目标。

## Step 4.3 结果（历史记录）

Step 4.3 已完成并停止于预定范围：新增 scale-invariant v2 特征、development-only walk-forward 服务、样本/诊断报告和治理元数据。没有重新评估 2024–2025，没有创建新 holdout 行情，没有运行回测、调参或扩大股票池。其检查点 `636c26f chore: checkpoint step 4.3 walk-forward feature diagnostics` 已在 Step 4.4 开始前提交。

Baseline Real Market Experiment v1 (`20261004T120849Z_2cb6448746`) 保持 immutable。原始 2024–2025 测试 Pearson IC `-0.08343`、Rank IC `-0.06794` 保留，登记为 `LEGACY_OBSERVED_TEST`。治理登记在 `config/research_governance.json`，future sealed holdout 从 `2026-01-01` 起定义为协议元数据且 `used=false`。Walk-forward 报告声明未访问 baseline 测试数据、未生成 2024 年及以后预测/指标。基线原始 manifest 未修改。

开发集仍为 11 股 BaoStock qfq vendor-adjusted 快照，feature v1 与新增的 `ashare_daily_v2_scale_invariant` 使用相同 target `target_5d_h5_v1`、固定模型参数和相同 2018–2023 expanding-window 验证折。v1/v2 的所有验证 sample keys、target 和 `label_end_date` 对齐；v2 因 warm-up 训练行每折少 36 行。验证日期、行数及指标如下（指标顺序：MAE、RMSE、Pearson IC 均值、Rank IC 均值、ICIR、Rank ICIR、正 Pearson/Rank IC 日比例）：

| Fold | Train 实际日期 / 行数 | Validation 实际日期 / 行数 | purge Train/Val；embargo | v1 指标 | v2 指标 |
|---|---|---|---|---|
| 1 | 2018-04-02–2020-12-17 / 7,156（v2 7,120） | 2021-01-04–2021-12-17 / 2,563 | 110 / 110；5 sessions | .044050, .057398, .081075, .050449, .204665, .141584, 60.09% / 56.22% | .045209, .058473, .025537, -.007608, .065182, -.021234, 49.36% / 47.64% |
| 2 | 2018-04-02–2021-12-17 / 9,829（v2 9,793） | 2022-01-04–2022-12-16 / 2,552 | 110 / 110；5 sessions | .042528, .054860, .073181, .046199, .196242, .122961, 54.31% / 53.88% | .041274, .053504, -.000698, -.028840, -.001754, -.083275, 46.55% / 46.12% |
| 3 | 2018-04-02–2022-12-16 / 12,491（v2 12,455） | 2023-01-03–2023-12-15 / 2,552 | 110 / 55；5 sessions | .031445, .039879, .048397, .025000, .159386, .075232, 56.47% / 49.14% | .030184, .039170, -.013691, -.015165, -.035066, -.043240, 49.14% / 46.98% |

跨折等权平均（mean of fold metrics）v1 为 MAE `.039341`、RMSE `.050712`、Pearson IC `.067551`、Rank IC `.040549`、ICIR `.186764`、Rank ICIR `.113259`、正 Pearson/Rank IC 日比例 `56.95% / 53.08%`；v2 对应 `.038889`、`.050382`、`.003716`、`-.017204`、`.009454`、`-.049250`、`48.35% / 46.91%`。每折 common validation alignment 分别为 2,563、2,552、2,552 个完全一致 keys，以上亦是 common-sample comparison。v2 的误差略低，但其 Rank IC 三折均为负，未达到正 Rank IC 至少两折的内部筛选条件，故状态为 `development_only`。

v1 scale-dependent 特征为 MA_5/10/20/60、MACD、MACD_Signal、Volume_MA5/20；scale-invariant 特征为四个收益率、Price_to_MA20、RSI_14、两项波动率和 Volume_Ratio。按 11 股每股特征中位数与每股中位 close/volume 的 Pearson 相关，MA_5–MA_60 为 `.999851–.9999998`，MACD/MACD_Signal 为 `.985718–.991883`，Volume_MA5/20 为 `.999890–.999911`。这是描述性尺度关系，不是因果判断。v1 fold top feature 为 Volatility_20d 两次、Volume_MA20 一次；v2 三折均为 Volatility_20d。平均 top-5 gain 占比为 `48.16% / 50.18%`，v2 略集中。

两版模型均未出现 near-constant 预测；预测标准差/目标标准差的跨折中位比例 v1 为 `.305`、v2 为 `.255`。预测标准差最大/最小折间比 v1 为 `1.79`、v2 为 `2.18`。12 个季度的 IC 方向仍波动：v1 有 9/12 季度 Pearson、7/12 季度 Rank IC 为正；v2 为 8/12、6/12。NW lag=5 的逐折 t diagnostic 已写入报告；它只用于考虑重叠标签下的序列相关，不是正式显著性或投资有效性证明。

完整机器可读结果：`artifacts/reports/walkforward_comparison_2ee1453e2be5.json`；实验目录：`artifacts/experiments/walkforward_ashare_daily_v1_e52bb0812502/` 与 `artifacts/experiments/walkforward_ashare_daily_v2_scale_invariant_c0656b5d5257/`。v2 development dataset 上限为 2023-12-31，15,922 行，15,227 行完整特征、15,867 行标签有效。更多实现历史见 `docs/development_log.md`。

## Step 4.2 基线结论（历史记录）

Step 4.2 已在 Step 4.1 多源数据基础上完成：BaoStock qfq 真实行情校验、2018–2025 的 11 股独立回补、独立 qfq model-ready 数据集、readiness PASS，以及一次固定参数的真实 LightGBM 回归基线实验。原 canonical `none` 数据集和行情未被覆盖。LightGBM 模型固定输入重载校验通过。

该实验是预测误差和横截面排序的研究基线，不是投资策略回测，也不说明策略盈利能力。qfq 是供应商复权快照，没有 point-in-time 公司行动重建；训练池是当前选出的工程样本，存在幸存者偏差。

## 工程与环境

- Step 4.2 检查点：`661596e chore: checkpoint step 4.2 real lightgbm baseline`。无远程仓库，没有推送。
- Python：`.venv` 中 Python 3.14.6。未迁移或卸载系统 Python。
- 关键包：NumPy 2.5.3、Pandas 3.0.6、scikit-learn 1.9.1、LightGBM 4.7.0、DuckDB 1.5.6、PyArrow 25.0.1、AKShare 1.19.1、BaoStock 0.9.4、Tushare 1.4.29、exchange-calendars 4.13.2、VectorBT 1.1.1、Numba 0.68.0、OpenAI SDK 3.24.0、yfinance 1.7.0。
- `uv pip check --python .venv\Scripts\python.exe` 检查 107 个已安装包，全部兼容。`compileall` 通过；全套 unittest 为 **62 passed**。
- `.env`、`.venv`、`data/`、模型、报告及大型产物由 `.gitignore` 排除；`.env` 未跟踪，源码凭据模式扫描无命中。没有打印或提交任何密钥。

## 当前架构与数据流

```text
BaoStock / AKShare / Tushare / Yahoo
        ↓ provider capabilities + router（一个证券区间固定一个来源）
data/raw → 标准 Schema → quality validation
        ↓
DuckDB 目录 + versioned Parquet
        ↓ 显式 provider / adjustment 过滤
因果特征 + 每股五观察期 forward return + label_end_date
        ↓ 固定全局时间切分 + 五交易日 purge/embargo + readiness gate
LightGBM 回归 / 训练均值 / 5 日动量基线
        ↓
只读研究报告、预测明细、模型、实验 manifest 和重载一致性检查
```

标准行情键包括市场、交易所、字符串股票代码、交易日期、OHLC、成交量/额、复权方式、币种、来源、抓取时间、供应商版本及修订元数据。行情源与调整口径独立隔离。qfq/hfq 单独保存，更新可覆盖当前有效快照并保留版本；历史复权可能变化。

特征为逐股滚动计算，包含收益率、移动均线、价格/均线比、RSI、MACD、波动率、成交量均线和成交量比。使用每只股票自身有序交易记录计算：`target_5d = close.shift(-5) / close - 1`，另保存预测日后的实际标签结束日。缺少标签/窗口的尾部或 warm-up 行不填充。训练、验证、测试时间固定为 2018–2022、2023、2024–2025，边界样本按 label end date 清除；不打乱时间序列。

## 真实 qfq 数据验证

历史回补任务 `a6bbc3e4-cbc1-427c-aede-d3d232e8440c` 固定 BaoStock、qfq、2018-01-01 至 2025-12-31，使用每批最多 3 只股票的可恢复模式。11 只全部成功，验证拒绝为 0，总计 21,257 条：

| 标的 | 行数 | 观测区间 |
|---|---:|---|
| 000001.SZ、000002.SZ、000333.SZ、000651.SZ、000858.SZ、002594.SZ、600036.SH、600519.SH、601318.SH、601888.SH | 各 1,942 | 2018-01-02 至 2025-12-31 |
| 300750.SZ | 1,837 | 2018-06-11 至 2025-12-31 |

另按任务要求对 `000001.SZ`、`600519.SH`、`300750.SZ` 做 2024Q1 真实 qfq 测试，各 58 行（2024-01-02 至 2024-03-29），全部接受。完全重复的下载请求均返回 skipped，未生成重复行情行。none 与 qfq 同为 BaoStock、同区间比较：共同日期均 58；qfq/none OHLC 比例逐项一致，close 比例为 0.821514、0.908734、0.930927；成交量和成交额在 58 日逐日相同。20% 日变化阈值下没有机械跳变标记。比较报告：`artifacts/reports/price_basis_3d0d9c1b2eb5.json`。

旧的 `688981.SH` 问题保持原样：此前 none 上游记录存在 6 个交易日成交量/成交额缺失；没有合成数据或推断停牌，也没有把它加入本次 qfq 样本池。

## qfq 数据集与 Gate

- 数据集：`data/processed/ashare_research_dataset_baostock_qfq_v1.parquet`。
- Manifest：`data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json`。
- 池配置：`config/ashare_universe_qfq_pilot.json`，11 只工程样本；canonical research 配置和原 none 数据集保持原值。
- 数据版本：`39f379ff77ff4de9b150ece1e27db6ff6130cda04660895026804796c0782c75`；数据 21,257 行、完整特征 20,598 行、已实现标签 21,202 行。
- Manifest 明确记录 BaoStock、qfq、`vendor_snapshot_adjusted`、每只股票各自的 `downloaded_at` 快照时间、feature `ashare_daily_v1`、target `target_5d_h5_v1`，并标记 `NOT_POINT_IN_TIME`。
- Readiness：PASS。11 只有效标的、日历缺口率 0、无非交易日期、重复样本 0、只有 qfq；Train 12,491、Validation 2,552、Test 5,280；测试 480 日，所有测试日期有 11 股截面；purge 边界通过。报告：`artifacts/reports/readiness_20261004T120839Z_7c36b3f6.json`。

## Baseline Real Market Experiment v1

实验 ID：`20261004T120849Z_2cb6448746`，于 2026-10-04 运行一次。训练数据显式来自上述 BaoStock qfq 数据集和 manifest，参数取 `config/research.json` 中既有 CPU LightGBM 参数，没有调参。LightGBM、训练集均值、五日动量基线使用相同的 train/validation/test 样本键；模型只在 Train 拟合。

| 测试指标（2024–2025） | LightGBM | 训练均值 | 5 日动量 |
|---|---:|---:|---:|
| MAE | 0.03179 | 0.02989 | 0.04310 |
| RMSE | 0.04800 | 0.04587 | 0.06573 |
| Daily Pearson IC mean | -0.08343 | NA（预测常量） | 0.02663 |
| Daily Spearman Rank IC mean | -0.06794 | NA（预测常量） | 0.00780 |
| 有效 Pearson / RankIC 日数 | 480 / 480 | 0 / 0 | 480 / 480 |
| 平均 / 最小横截面 | 11 / 11 | 11 / 11 | 11 / 11 |

LightGBM 2024 年 MAE/RMSE 为 0.03739/0.05646，Pearson/RankIC 均值 -0.03977/-0.03569；2025 年 MAE/RMSE 为 0.02610/0.03750，Pearson/RankIC 均值 -0.12782/-0.10073。8 个季度均有单独指标。11 股的逐股样本量、实际/预测均值及 MAE/RMSE 均写入实验 manifest。所有预测列、日 IC、特征 gain/split importance、固定测试输入均独立保存。模型重载后对 5,280 个固定测试输入的预测一致（`atol=1e-12`）。

产物位置：

- 实验 manifest / 报告：`artifacts/experiments/20261004T120849Z_2cb6448746/manifest.json`、`artifacts/reports/20261004T120849Z_2cb6448746.json`
- 模型：`artifacts/models/lightgbm/20261004T120849Z_2cb6448746.joblib`
- 测试预测和固定输入：实验目录内 `test_predictions.parquet`、`test_model_inputs.parquet`
- 逐日 IC 和重要性：实验目录内 `daily_ic.csv`、`feature_importance.csv`
- 验证预测及 IC：实验目录内 `validation_predictions.parquet`、`validation_daily_ic.csv`

这些数值是该 qfq 快照与当前幸存者样本的单次历史研究结果。未经 PIT 校正，存在样本选择偏差，且不包含交易成本/组合构造/持仓约束；不能称为回测收益、超额收益或可交易信号有效性。

## 入口及尚未实现

常用入口仍有 `quantlab run`、`quantlab news`；另有 `quantlab data download/update/validate/status/query/backfill/backfill-status/providers/reconcile/compare-bases/factors/adjusted-view`、`quantlab research build-dataset/dataset-status/readiness`、`quantlab model train/evaluate/experiments`。`data compare-bases` 对同源 none/qfq 比价并仅机械标记超过阈值的单日变化，不做自动公司行动判断或价格修复。

尚未完成：point-in-time 公司行动因子还原、历史成分股/退市股宇宙、HK Provider、全面停牌日历、正式因子研究、VectorBT 策略回测、Streamlit Dashboard、Qwen/Hermes Agent，以及任何券商交易连接。Step 4.2 到此暂停，未开始下一阶段。

## Step 4.5 执行状态

Step 4.4 检查点 `c4ca573 chore: checkpoint step 4.4 cross-sectional target study` 已建立，提交前全套 92 项测试通过，`uv pip check` 的 107 个包兼容。Step 4.5 在候选 Universe 冻结之前停止：BaoStock 0.9.4 登录成功，但全量证券基础资料/行业元数据查询在 30 秒以上仍未返回；AKShare 1.19.1 的 SSE/SZSE A 股列表请求失败于 `ProxyError`。Tushare 未配置凭据。尚无候选记录、U50/U100 清单或新增行情；没有继续进入特征或模型运行。U11 control 未改动。执行记录见 [`step_4_5_universe_expansion.md`](step_4_5_universe_expansion.md)。后续需要先恢复到 BaoStock 或交易所列表端点的网络访问，再从实际返回的元数据构建候选顺序。

### Step 4.5R 更新（2026-10-05）

已确认 Python 大小写代理变量指向无响应的 loopback:9；WinINET 未启用代理、WinHTTP 为直连；DNS 正常。BaoStock 0.9.4 登录、单股基础资料与 2018-01-01 日期参数的单股行业查询成功，并取得真实字段名；`query_stock_basic()` 全量查询 60 秒外部看门狗超时，全行业查询 15 秒超时。AKShare SSE/SZSE 列表函数均经配置代理失败于 `ProxyError`。SSE 页面仅在诊断性直连请求中返回 200，SZSE 页面直连 ReadTimeout；没有使用直连下载数据或修改全局网络设置。项目内没有官方证券列表 CSV/Excel 文件。故状态仍是 `metadata_access_still_blocked`，没有 Security Master 或候选清单。全套 92 项测试通过，Python 3.14.6，107 个包兼容。详见 [`step_4_5_universe_expansion.md`](step_4_5_universe_expansion.md)。

### Step 4.5R.1 更新

起始 HEAD `0c63cef` 且工作区干净；`c4ca573` 与 `046e682` 检查点存在。代理环境仍指向无响应的 loopback:9。本次没有明确直连授权，因此未发起 SSE 主板、SSE 科创板或 SZSE 的在线列表请求。静态检查发现 BaoStock 每页最多 2,000 条，`ResultData.next()` 在满页后会请求下一页；它与初始响应都使用无超时的阻塞 socket 接收循环。现有看门狗没有逐页进度日志，无法判断卡在首个响应还是后续分页。92 项测试通过，107 个依赖兼容。结果保持 `metadata_access_still_blocked`；详见执行报告 Step 4.5R.1。

### Step 4.5R.2 更新（2026-10-05）

后续用户已授权仅对 SSE/SZSE 公开证券列表项目级直连。独立会话成功获取 SSE 主板 1,702、科创板 618、SZSE A 股 2,904 条，已建立 5,224 条去重 Security Master；U50/U100 按固定 seed 冻结且保持 25/25、50/50 的交易所分布。新增 8 项离线测试后，全套 100 项通过，`compileall` 通过。正式哈希、来源文件 SHA-256、选股规则和 rejection ledger 见 [`step_4_5_universe_expansion.md`](step_4_5_universe_expansion.md)。当前来源仅为 2026 年交易所快照，不能消除历史幸存者偏差。没有下载新行情或运行模型。Step 4.5R.2 已达到 `metadata_source_ready` 并停止。

## Step 4.5A — Frozen U50 Historical Backfill（2026-10-05）

状态为 `u50_data_ready`（仅数据工程）。先在 `1edd013` 建立 Step 4.5R.2 本地检查点；冻结 U50 hash 与 U11 anchor 未变化。BaoStock 0.9.4 对 39 个新增成员分开完成 none/qfq 回填，U11 的 11 个 anchor 只读复用。完整的 `ashare_daily_v1` / `target_5d_h5_v1` development-only 数据集为 71,622 行，50/50 股票具备有效特征与标签；train/validation 为 56,072 / 11,850 行，breadth gate 通过。qfq 快照非 PIT；幸存者偏差未解决。未训练模型、未访问实际 Legacy Test 或 2026+ Holdout。逐股质量、每年覆盖、CLI、环境与范围偏差见 [`step_4_5A_u50_data_validation.md`](step_4_5A_u50_data_validation.md)。本阶段代码和报告尚未提交。

## Step 4.5B — Frozen U100 开发期数据状态（2026-10-05）

Step 4.5A 在提交 `eb8d9ec` 建立本地 checkpoint 后，U100 冻结配置通过成员数、SSE/SZSE 分布、U50/U11 包含关系、重复项、candidate source hash、selection seed 与顺序校验。只对 U100−U50 新增的 50 只进行 BaoStock none/qfq 回填，两个独立 job 均 50/50 完成；日期范围为 2018-01-01 至 2023-12-31。U100 quality 与 breadth gate 为 `u100_data_ready`，新增开发期数据集有 143,911 行；完整 Feature+Target 有 137,511 行。活动日期横截面 min/P10/median/P90/max 为 83/98/99/100/100，达到至少 80 只和 90 只的活动日期比例分别为 100% 和 96.84%。

质量汇总每种 adjustment 有 143,911 个实际成交观察、1,684 个 BaoStock 明确报告的 no-trade session、0 个 provider-response 缺失和 0 个 unknown gap。旧 canonical 层保留 961 条与当前 no-trade 状态重叠的历史 bars，但它们不计作当前成交观察且被研究数据集过滤；没有删除旧行情。qfq 为逐股不同时间获取的供应商快照，标记 `mixed_vendor_snapshot_times=true`、`NOT_POINT_IN_TIME`；幸存者偏差仍未解决。日期查询与研究入口均被限制到开发期边界，不读取 Legacy Test 或 Sealed Holdout。Step 4.5B 没有训练模型、walk-forward 或回测。Python 3.14.6，111 项自动化测试通过，uv 检查的 107 个包兼容。详细逐股质量和命令见 [`step_4_5B_u100_data_validation.md`](step_4_5B_u100_data_validation.md)。Step 4.5B 后续已提交为本地 checkpoint `bb66b9a`。

## Step 4.5C — Universe Breadth Robustness（2026-10-05）

在冻结 U11/U50/U100 与当前 BaoStock qfq 数据上，先通过 Dataset Consistency Gate：共有样本的特征、标签、label end 和价格行哈希一致；U100 143,911 条研究样本全部为 `tradestatus=1`。Step 4.3 历史 U11 数据集与本轮当前 U11 数据集存在 46 条旧 no-trade 行、55 条本轮期末无标签行及后续特征/标签差异，旧产物保持不变。固定 Feature v1、Raw Target、Baseline v1 LightGBM 参数和三折时间协议后，U100 Rank IC 为 −0.00291 / +0.04668 / −0.00857，预注册结果 **`breadth_not_supported`**。三池共同有效 IC 日期与原生日期相同；9 个模型均在保存的输入上重载复现。没有使用 Legacy Test、Sealed Holdout 或进行回测。完整报告见 [`step_4_5C_breadth_robustness.md`](step_4_5C_breadth_robustness.md)。

## Step 4.6 — Signal Failure Diagnostics（2026-10-05）

Step 4.5C 已提交为本地 checkpoint `a307a55`（`chore: checkpoint step 4.5C universe breadth robustness`），无 remote/push。对冻结 U11/U50/U100 及 2018–2023 BaoStock qfq development-only 材料进行只读诊断，核验九个已保存 fold 模型与验证输入哈希并复现预测。无训练、行情下载、特征/目标/参数/Universe 修改；2024–2025 Legacy Test 与 2026+ Sealed Holdout 未访问。结论为 `root_cause_not_identified`；所有结果均为 `POST_HOC_DIAGNOSTIC`，17 个固定特征的单因子结果为 `EXPLORATORY_UNIVARIATE_DIAGNOSTIC`。

主要观察：U100 三折 Rank IC 为 -0.00291/+0.04668/-0.00857；2021 正 IC 日比例 53.65% 但负向日幅度较大，2023 负 IC 日占比 51.29%；2022 正向结果分布于多个季度，但强度不均。当前行业快照每组最多 7 只，无法达到预设 8 只组内诊断宽度，且行业标签非历史 PIT。五有效成交观察目标与交易所 session 的间隔在少数样本中显著拉长（U100 最大日历跨度 150 日、最大 session 距离 100）；这不改变既有标签计算正确性，但限制统一持有期解释。Baseline v1 manifest hash、Step 4.3/4.4/4.5C 状态、旧 U11 版本差异、非 PIT 和 survivorship 限制均核对通过。机器结果在被忽略的 `artifacts/reports/signal_failure_diagnostics_20261005T021422Z_83390a3990_final_v2/`；分析和后续建议见 [`step_4_6_signal_failure_diagnostics.md`](step_4_6_signal_failure_diagnostics.md)。

全套 132 项测试通过，`compileall` 与 `git diff --check` 通过；Python 3.14.6，uv 检查 107 个包兼容。新增 Step 4.6 代码和文档尚未提交，本阶段到此停止。
