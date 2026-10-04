# Quant Research Agent 项目状态

**状态核对日期：2026-10-04**
**项目目录：`D:\Quantification`**
**定位：A 股优先、港股其次的本地研究与预测平台；不连接券商账户、不自动下单。**

## Step 4.3 当前状态

Step 4.3 已完成并停止于预定范围：新增 scale-invariant v2 特征、development-only walk-forward 服务、样本/诊断报告和治理元数据。没有重新评估 2024–2025，没有创建新 holdout 行情，没有运行回测、调参或扩大股票池。Step 4.2 固定检查点为 `661596e chore: checkpoint step 4.2 real lightgbm baseline`；Step 4.3 修改仍在工作区，尚未提交。

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

- Step 4.2 开始前的本地检查点：`661596e chore: checkpoint step 4.2 real lightgbm baseline`。无远程仓库，没有推送；Step 4.3 实施后工作区有未提交改动。
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
