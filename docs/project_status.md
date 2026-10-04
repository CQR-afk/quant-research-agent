# Quant Research Agent 项目状态

**状态核对日期：2026-10-04**
**项目目录：`D:\Quantification`**
**定位：A 股优先、港股其次的本地研究与预测平台；不连接券商账户、不自动下单。**

## 当前结论

Step 4.2 已在 Step 4.1 多源数据基础上完成：BaoStock qfq 真实行情校验、2018–2025 的 11 股独立回补、独立 qfq model-ready 数据集、readiness PASS，以及一次固定参数的真实 LightGBM 回归基线实验。原 canonical `none` 数据集和行情未被覆盖。LightGBM 模型固定输入重载校验通过。

该实验是预测误差和横截面排序的研究基线，不是投资策略回测，也不说明策略盈利能力。qfq 是供应商复权快照，没有 point-in-time 公司行动重建；训练池是当前选出的工程样本，存在幸存者偏差。

## 工程与环境

- 本地检查点：`1b0796c chore: checkpoint step 4.1 multi-provider market data`。无远程仓库，没有推送。本阶段 Step 4.2 源码及文档变更仍在工作区。
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
