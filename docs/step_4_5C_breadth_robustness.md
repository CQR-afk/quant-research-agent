# Step 4.5C — Frozen Universe Breadth Robustness Experiment

执行日期：2026-10-05。预注册结论：**`breadth_not_supported`**。U100 的 Daily Spearman Rank IC 在 2021、2023 两折为负，2022 为正；仅一折为正，未达到至少两折为正的要求。三折均值虽然为 +0.01173，不能改变“两折负值”的预定判定。结果仅适用于 2018–2023 development period 的当前供应商快照与冻结股票池。

## Checkpoint、冻结输入与环境

Step 4.5B 从 `eb8d9ec` 的工作区改动完成核对：111/111 测试、`compileall`、`git diff --check` 通过；`uv --no-cache pip check --python .\.venv\Scripts\python.exe` 检查 107 个包兼容。`.env`、`data/`、DuckDB、Parquet、模型和实验产物均未被 Git 跟踪。建立本地 checkpoint：`bb66b9a chore: checkpoint step 4.5B U100 historical data readiness`；没有 remote 或 push。

| Universe | 股票数 | 冻结成员 SHA-256 | Dataset SHA-256 |
|---|---:|---|---|
| U11 | 11 | `8b2fae187a7540c2801f1905e13a18543a9a031b8a5cd42d5508a94304d38d62`（本阶段由 U11 成员顺序计算） | `dfd043f5da9b7b0dc78fd8ebc92291fc2ceb91e465dd37dca03d4e4a0fe45bab` |
| U50 | 50 | `d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218` | `cf747f4503e8c11560422177ab11ac326e75cdaf6af83d8378831c82661334c2` |
| U100 | 100 | `05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9` | `1814347050b9a35d23148f72a52355f40523ab699fdb1f5d9a2a4c76f1d1cf02` |

U11 是 `config/ashare_universe_qfq_pilot.json` 的 11 个 anchors；U50/U100 是原冻结文件，selection seed 与 candidate source hash 相同。三组统一使用 BaoStock qfq、`ashare_daily_v1` 的 17 个特征、`target_5d_h5_v1`、`LGBMRegressor`。参数从 `config/research.json` 原 Baseline v1 读取：300 trees、learning rate .03、15 leaves、max depth 5、min child samples 30、seed 42、CPU 4 threads；沿用 Step 4.3 的 deterministic/force-col-wise 运行设置。未调参、未更换目标或特征。

实际环境：Python 3.14.6；pandas 3.0.6、NumPy 2.5.3、scikit-learn 1.9.1、LightGBM 4.7.0、DuckDB 1.5.6、PyArrow 25.0.1、exchange-calendars 4.13.2。各实验 manifest 保存了这些版本与固定参数。

## Dataset Consistency Gate 与历史 U11 对照

先以当前 `tradestatus=1` 规则单独构建 U11 development dataset（15,876 行），没有覆盖 Step 4.3 留存的 U11 快照。U50 为 71,622 行，U100 为 143,911 行。门禁在任何本轮模型训练前通过：U11 的 15,876 个键完整包含于 U50；U50 的 71,622 个键完整包含于 U100。共有键的 17 个特征、target、label end、source、adjustment、价格行哈希均一致。U100 的 143,911 个价格行哈希与按日期、来源、复权口径限定的 DuckDB canonical 行一致；逐股从这些有效成交观察重新计算特征、5 观察期 target 和 label end，与数据集一致。

按 BaoStock 最新日期元数据检查，U100 的 **143,911/143,911** 个研究样本均为 `tradestatus=1`；1,684 个供应商报告的 no-trade session 没有进入研究样本。Step 4.5B 记录的 961 条较早 canonical bars 与当前 no-trade 状态重合，仍保留作修订证据，当前训练只读取过滤后的独立数据集。供应商 no-trade 与未知缺口继续分开记录；U100 质量报告中的 missing provider response 与 unknown gap 均为 0。

Step 4.3 的旧 U11 快照作为 immutable reference 保留。旧快照 15,867 行，本轮 U11 15,876 行；共有 15,821 个键，其 canonical row hash 均相同。旧快照独有的 **46** 个键（000333.SZ 40、000651.SZ 6）全部在当前供应商元数据中为 `tradestatus=0`；本轮独有的 **55** 个键是 11 股各自末尾 5 个尚无开发期内标签的日期，训练切分会排除。剔除 no-trade 后，部分逐股滚动特征及 25 个共同键的五期 target/label end 发生变化。这解释了旧结果与本轮 U11 复现实验可能不同；旧报告、模型与预测没有改动。旧 Step 4.3 U11 v1 的三折 Rank IC 为约 0.05045 / 0.04620 / 0.02500，仅作历史背景，不用于本轮模型选择。

## 固定三折与实际样本

全局日期边界统一：2018–2020→2021、2018–2021→2022、2018–2022→2023。按实际 `label_end_date` purge，5 个交易 session embargo，无 shuffle。prediction 与 label end 均不超过 2023-12-31；真实 2024–2025 Legacy Test 和 2026+ Sealed Holdout 没有被读取或评价。

| Universe | Fold | 实际 Train 日期 | Train 行 / 股 | 实际 Validation 日期 | Validation 行 / 股 | Purged Train / Validation |
|---|---|---|---:|---|---:|---:|
| U11 | 2021 | 2018-04-02–2020-12-17 | 7,120 / 11 | 2021-01-04–2021-12-17 | 2,563 / 11 | 110 / 110 |
| U11 | 2022 | 2018-04-02–2021-12-17 | 9,793 / 11 | 2022-01-04–2022-12-16 | 2,552 / 11 | 110 / 110 |
| U11 | 2023 | 2018-04-02–2022-12-16 | 12,455 / 11 | 2023-01-03–2023-12-15 | 2,552 / 11 | 110 / 55 |
| U50 | 2021 | 2018-04-02–2020-12-17 | 31,872 / 49 | 2021-01-04–2021-12-17 | 11,622 / 50 | 490 / 499 |
| U50 | 2022 | 2018-04-02–2021-12-17 | 43,984 / 50 | 2022-01-04–2022-12-16 | 11,589 / 50 | 499 / 500 |
| U50 | 2023 | 2018-04-02–2022-12-16 | 56,072 / 50 | 2023-01-03–2023-12-15 | 11,600 / 50 | 500 / 250 |
| U100 | 2021 | 2018-04-02–2020-12-17 | 64,433 / 99 | 2021-01-04–2021-12-17 | 23,230 / 100 | 988 / 999 |
| U100 | 2022 | 2018-04-02–2021-12-17 | 88,651 / 100 | 2022-01-04–2022-12-16 | 23,180 / 100 | 999 / 1,000 |
| U100 | 2023 | 2018-04-02–2022-12-16 | 112,830 / 100 | 2023-01-03–2023-12-15 | 23,181 / 100 | 1,000 / 500 |

首折 U50/U100 训练股数比全池少 1，是上市/特征/标签可用期所致；没有修改 Fold 日期或成员。每折具体 label cutoff 和边界检查保存在 experiment manifest。

## Native breadth：主要排序结果

Daily Spearman Rank IC 在各池当天的有效横截面计算。Fold mean 是三个 Fold 日均 Rank IC 的等权平均；ICIR 是各折日均 IC 除以该折日 IC 标准差。

| Universe | 平均有效宽度 | 2021 Rank IC | 2022 Rank IC | 2023 Rank IC | Fold mean | 正 Rank IC 日比例均值 |
|---|---:|---:|---:|---:|---:|---:|
| U11 | 11.00 | +0.01104 | +0.04252 | +0.04056 | +0.03137 | 53.09% |
| U50 | 49.94 | +0.00008 | +0.04748 | +0.00113 | +0.01623 | 52.81% |
| U100 | 99.84 | −0.00291 | +0.04668 | −0.00857 | +0.01173 | 55.67% |

| Universe | 2021 / 2022 / 2023 Rank ICIR | 2021 / 2022 / 2023 日 IC std | 每折有效 IC 日期 | Pearson IC 三折均值 | MAE / RMSE 三折均值 |
|---|---|---|---|---:|---|
| U11 | .0305 / .1119 / .1246 | .3623 / .3798 / .3256 | 233 / 232 / 232 | .05160 | .03954 / .05098 |
| U50 | .0004 / .2714 / .0070 | .2016 / .1749 / .1606 | 233 / 232 / 232 | .03874 | .04131 / .06067 |
| U100 | −.0200 / .3578 / −.0654 | .1453 / .1305 / .1310 | 233 / 232 / 232 | .03367 | .04132 / .06120 |

各 Fold 的 Rank IC median、P10/P90、IQR、正日比例、NW lag=5 诊断、Pearson IC/ICIR、预测分布，以及按月和季度的 IC 分散度，均在独立实验 manifest 与 `ic_dispersion.json` 中。NW 是重叠五期标签下的序列相关诊断，不构成投资显著性证明。

## Common-date 与 U11 anchor-only 诊断

三池共同有效 IC 日期：2021 年 233、2022 年 232、2023 年 232；各池 dropped-date 均为 **0**。因此本次 common-date Rank IC 汇总与上表 native-date 数值完全相同，没有根据结果筛选日期。Native 比较同时改变训练池与评价横截面，不能把差异全部归因为训练样本数。

在三个模型共同拥有预测的原 U11 锚点 symbol/date 键上，以相同 raw target 重算 Rank IC（**`ANCHOR_ONLY_DIAGNOSTIC`**）：

| Fold | 共同键 | U11 模型 | U50 模型 | U100 模型 |
|---|---:|---:|---:|---:|
| 2021 | 2,563 | +0.01104 | +0.06855 | +0.03734 |
| 2022 | 2,552 | +0.04252 | +0.02210 | +0.03362 |
| 2023 | 2,552 | +0.04056 | −0.03236 | −0.03997 |

扩池模型在锚点上的表现不稳定；该诊断不代替 U50/U100 的原生横截面评价。

## IC 分散度、基线与特征重要性

三池 Fold 日 IC std 均值依次约 .3559 / .1790 / .1356，随宽度增加而下降；横截面增大本身可能降低 IC 抽样波动，所以这不能单独说明预测能力增强。按 Fold 的 daily breadth 与 Rank IC、absolute Rank IC 的 Spearman 描述性关联保存在报告中；U11 宽度固定为 11，此关联为 NA。U50/U100 有效宽度接近满额，相关系数仅作描述，不作因果判断。

每组 Fold 的训练均值常量预测与历史 5 日动量 `Return_5d` 使用与模型完全相同的验证键。训练均值的日横截面 IC 均记录为 **NA**，没有填 0；其 MAE/RMSE 正常计算。动量基线的 Rank IC 三折分别为 U11 −.02130/−.04937/+.00333，U50 −.02615/−.02811/−.01865，U100 −.03090/−.02988/−.01878。完整基线 Pearson IC、ICIR、误差和预测分布见报告 JSON。训练均值在若干 Fold 的 MAE/RMSE 优于模型，因此不能据模型 Rank IC 的某一正值推断整体预测误差优势。

U11/U50/U100 的前三折 top-5 gain share 均值分别为 48.77% / 48.24% / 43.69%；Fold 间 gain-rank Spearman 相关范围分别为 .880–.966 / .794–.882 / .831–.946。`Volatility_20d` 在 U11/U50 的平均 gain share 中排首位，在 U100 排第二；U100 的 `MA_60`、`Volume_MA20`、`MACD_Signal` 仍居前，绝对尺度特征继续重要。每 Fold 的 gain、split、top features 及逐月/季度诊断已保存；本轮未据此修改 Feature。

## 产物、复现与结论边界

- 总报告：`artifacts/reports/breadth_robustness_20261005T021422Z_83390a3990.json`；一致性门禁：`artifacts/reports/breadth_consistency_*.json`（路径写在总报告中）。
- 独立实验：`breadth_u11_20261005T021422Z_83390a3990`、`breadth_u50_20261005T021422Z_83390a3990`、`breadth_u100_20261005T021422Z_83390a3990`。每个 Fold 保留模型、固定验证输入、预测/实际、daily IC、月/季分散度、gain/split importance 和文件 SHA-256。
- 重新加载 9 个模型，以已保存的固定验证输入重放预测与 Rank IC，**9/9 通过**。报告及实验产物均在 `.gitignore` 保护范围内；原 Baseline v1 与 Step 4.3/4.4 产物未改动。
- 本阶段仅判断当前固定数值模型的开发期排序稳定性。qfq 为不同证券/时间的供应商复权快照，**NOT_POINT_IN_TIME**；冻结名单来自当前交易所快照，缺少历史退市股票，幸存者偏差仍未解决。结果不代表可交易信号、样本外正式验证或策略盈利。

执行入口：

```powershell
.\.venv\Scripts\quantlab.exe research build-dataset --start 20180101 --end 20231231 --adjustment qfq --provider baostock --universe config/ashare_universe_qfq_pilot.json --output data/processed/ashare_research_u11_qfq_v1_dev_current_2018_2023.parquet --feature-version ashare_daily_v1 --development-only --summary-only
.\.venv\Scripts\quantlab.exe research breadth-study
.\.venv\Scripts\quantlab.exe research breadth-verify --report artifacts/reports/breadth_robustness_20261005T021422Z_83390a3990.json
```

自动化测试 **123/123 通过**（原 111 项保留，新增 12 项）；`compileall`、`git diff --check` 通过，uv 检查 107 个包兼容。没有下载新行情、开展第四个 Universe 实验、调参或回测。
