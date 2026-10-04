# Quant Research Agent 当前项目状态

**核对日期：2026-10-04**  
**项目路径：`D:\Quantification`**  
**项目用途：A 股优先、港股其次的本地量化研究与预测；不连接券商、不自动下单。**

本文依据当前工作区的源码、配置、DuckDB 数据状态、测试结果及实验门禁报告编写。代码中已实现的能力、Fixture 验证的能力、真实数据实际运行的能力分别说明，未把开发目标当成实测结果。

## 当前结论

项目已有一条完整的研究工程路径：AKShare Provider → 原始数据归档 → 标准化和校验 → DuckDB / Parquet → 逐股特征和五观察期收益标签 → 日期边界 purge 的 Train / Validation / Test → Readiness Gate → LightGBM 与简单回归基线 → 独立实验文件和重载检查。

这条路径的代码和离线 Fixture 测试已完成。真实行情目前只有 2 只股票在 2024Q1 的小样本。对 2018–2025 的三只股票扩展请求均遇到 AKShare 上游 `RemoteDisconnected`。因此，真实数据集只有 116 行、106 个五日标签、0 个完整特征样本；Readiness Gate 正确阻止了正式模型训练。当前没有基于真实市场数据训练出的 LightGBM 模型或模型表现指标。

## 实现范围

### 命令行与模块结构

`src/quantlab/cli.py` 负责参数解析和调用独立服务模块，现有和新增命令包括：

| 命令 | 实际用途 |
|---|---|
| `quantlab run` | 兼容的 Yahoo Finance 日线与 Logistic Regression 方向分类基线 |
| `quantlab news` | 可选的独立 OpenAI 新闻研究；真实云端请求未在本阶段验证 |
| `quantlab data download/update/validate/status/query` | AKShare A 股采集、增量更新、校验、状态和查询 |
| `quantlab research build-dataset` | 从本地 DuckDB 构建研究数据集、manifest 和股票覆盖报告 |
| `quantlab research dataset-status` | 查看研究数据集及 manifest 状态 |
| `quantlab model train --model lightgbm` | 先执行 readiness gate，通过后才拟合模型 |
| `quantlab model evaluate --model lightgbm` | 重载已有实验并检查固定输入的预测一致性 |
| `quantlab model experiments` | 列出本地完成的实验 |

主要源码职责：

| 路径 | 职责 |
|---|---|
| `src/quantlab/providers/akshare.py` | AKShare A 股日线字段映射、超时、有限重试与 `Retry-After` 处理 |
| `src/quantlab/providers/yfinance.py` | Yahoo Finance 兼容数据源 |
| `src/quantlab/data/schema.py` | 统一日线 Schema 与基础标准化 |
| `src/quantlab/data/symbols.py` | A 股代码、前导零和 SSE/SZSE 映射 |
| `src/quantlab/data/validation.py` | OHLC、日期、缺失、代码、市场/交易所及调整方式质量校验 |
| `src/quantlab/data/store.py` | DuckDB 目录、Parquet 快照、版本、修订及查询 |
| `src/quantlab/data/service.py` | 单只和批量下载、增量更新、数据查询、更新状态及中断记录 |
| `src/quantlab/research/calendar.py` | 有界的 XSHG 日历封装 |
| `src/quantlab/research/features.py` | 按证券分别计算的因果特征 |
| `src/quantlab/research/dataset.py` | 从本地仓储构建特征、标签、数据版本和覆盖信息 |
| `src/quantlab/research/splits.py` | 全局固定日期区间、label purge、embargo 和切分报告 |
| `src/quantlab/research/readiness.py` | 训练前数据质量与样本量门禁 |
| `src/quantlab/experiments.py` | LightGBM 回归、简单基线、横截面指标与实验落盘 |
| `src/quantlab/modeling.py` | 保留原 Logistic Regression 方向分类基线 |
| `src/quantlab/news.py` | 可选 OpenAI 新闻/公告研究，不依赖于本地行情和模型路径 |

### 环境与依赖

项目使用 `D:\Quantification\.venv`，Python **3.14.6**。本次保留该虚拟环境，没有改动系统 Python。实际关键包版本如下：

| 组件 | 版本 | 当前用途/检查 |
|---|---:|---|
| NumPy | 2.5.3 | 数值运算；导入与离线功能测试 |
| Pandas | 3.0.6 | 行情、特征和标签处理 |
| Scikit-learn | 1.9.1 | Logistic Regression 和评估工具 |
| LightGBM | 4.7.0 | A 股五日收益回归器；离线 Fixture 完成拟合测试 |
| DuckDB | 1.5.6 | 本地行情目录和 SQL 查询 |
| PyArrow | 25.0.1 | Parquet 读写 |
| AKShare | 1.19.1 | A 股行情 Provider |
| exchange-calendars | 4.13.2 | XSHG 本地预计算交易日历 |
| VectorBT | 1.1.1 | 环境中已安装并通过最小离线兼容测试；本步骤没有策略回测 |
| Numba | 0.68.0 | 环境中已安装并通过最小离线兼容测试 |
| OpenAI SDK | 3.24.0 | 可选新闻研究；未进行真实云端请求 |
| yfinance | 1.7.0 | 旧兼容行情流程 |

`pyproject.toml` 管理项目依赖，`setup.ps1` 复用现有 `.venv`。运行 `uv pip check --python .\.venv\Scripts\python.exe` 检查 102 个已安装包，结果为全部兼容。单元测试入口是 Python 自带的 `unittest`，没有依赖 pytest。

### 行情、Schema 与存储逻辑

AKShare Provider 调用 `stock_zh_a_hist`，支持 `none`、`qfq`、`hfq`，默认研究口径在配置中设为 `qfq`。字段转换后写入统一 Schema：市场、交易所、字符串股票代码、交易日期、OHLC、成交量、成交额、复权方式、币种、来源、抓取时间及 Provider 版本等。股票代码以前导零字符串保留；`000001.SZ` 映射 SZSE，`600519.SH` 映射 SSE。AKShare 成交量源单位为“手”，标准化为股（每手 100 股）；成交额保留人民币元，不推导缺失值。

请求超时为 20 秒，网络和可重试 HTTP 错误最多尝试 3 次并退避；限流响应的数值型和 HTTP 日期型 `Retry-After` 都会被读取并受最大退避时间约束。批量请求逐只执行，单只失败结果独立记录，任务间默认暂停 1.5 秒；中断后可安全重新运行，不会覆盖已有正确数据。完整区间已存在时可跳过重复下载。实际 AKShare 网络服务稳定性依上游和当前网络而定。

质量校验涵盖必需字段、OHLC 一致性、负数/非有限数、日期顺序、重复标的日期、非交易日期、市场和交易所映射及复权混用。原始上游响应单独归档；有问题的数据会记录问题并阻止进入有效行情表。缺失预期交易日不会自动前值填充或推断为停牌，没有停牌资料时状态保留为未知。

本地存储分层如下：

```text
data/
├── raw/ashare/                 # 每次原始上游行情及采集元数据
├── raw/yahoo/                  # Yahoo 原始数据层预留
├── validated/ashare/           # 当前有效标准化行情 Parquet 快照
├── processed/                  # 特征、标签和研究集
└── database/quantlab.duckdb    # 行情目录、采集运行、校验和修订索引

artifacts/
├── models/lightgbm/            # 独立保存的模型文件
├── experiments/<experiment_id>/# 输入、样本外预测、每日 IC 和实验 manifest
└── reports/                    # readiness 和实验报告
```

清洗行情 Parquet 按交易所、调整方式和年份分区，避免逐日小文件。DuckDB 主键区分来源与复权方式；相同数据 upsert 幂等，历史修订会保存旧版本。`/data/` 和 `artifacts` 均由 `.gitignore` 排除，数据库、行情、模型和实验文件不会进入 Git。

### 股票池、日历和数据集

`config/ashare_universe.json` 配置 12 只沪深 A 股、名称、交易所、行业、选取日期和研究范围（2018–01–01 至 2025–12–31）。这只是当前股票池工程验证样本，不是历史时点成分股；没有退市证券和历史成分股数据，存在幸存者偏差。对数据开始日期不做上市日期推断，股票上市前与源数据缺失不能混为一谈。

`config/research.json` 固定 `source=akshare`、`adjustment=qfq`、样本门槛和模型参数。日历使用 `exchange_calendars` 4.13.2 的 XSHG 预计算日历，覆盖请求研究范围内 1,942 个交易 session。它不是交易所实时服务；正式研究还应核对休市变更和个股停复牌资料。

数据集构建只从 `MarketDataStore`/DuckDB 查询，不能绕过仓储直接把下载结果送进模型。每只股票在各自的有效行情序列上分别计算特征和标签：

- **预测时点**：每条样本的 `prediction_date` 对应当日收盘后已知的信息。
- **特征**：17 个特征，包括 `Return_1d/5d/10d/20d`、`MA_5/10/20/60`、`Price_to_MA20`、`RSI_14`、`MACD`、`MACD_Signal`、`Volatility_5d/20d`、`Volume_MA5/20`、`Volume_Ratio`。滚动指标逐股计算；预热期保持 NaN，不做前填或未来回填；Inf 转成缺失值。特征版本与参数写入数据集。
- **标签**：`target_5d = close.shift(-5) / close - 1`，在单只股票的未来第五个有效行情观测计算，不是向前五个自然日。每条标签同时保存 `label_end_date`。特征与目标使用同一调整口径。
- **溯源**：保存来源、调整方式、抓取时间、采集 ID、记录版本、修订 ID 和数据版本摘要。Manifest 记录股票池、日历、特征配置、覆盖情况及 qfq 时点限制。

目前 qfq 是事后复权快照，可能随公司行动被上游修订；保留了修订元数据，但没有重建历史时点可见的公司行动，不构成严格 Point-in-Time 数据。

### 训练、验证和评估逻辑

默认时间区间在配置中为：

| 集合 | 日期 |
|---|---|
| Train | 2018–01–01 至 2022–12–31 |
| Validation | 2023–01–01 至 2023–12–31 |
| Test | 2024–01–01 至 2025–12–31 |

所有股票使用相同的全局日期边界，不做随机打乱。标签结束日需早于下一个集合边界，并在边界前额外设置 5 个交易 session embargo；跨越边界的 Train/Validation 样本会 purge。报告记录每段实际预测日期、标签截止日期、purge 数量及 sample key 唯一性。协议预留切分策略接口，但完整 walk-forward 搜索尚未实现。

正式回归器为 CPU `LGBMRegressor`，建议配置：300 棵树、学习率 0.03、15 个叶节点、最大深度 5、最少叶子样本 30、随机种子 42、4 个线程。拟合只用 Train，Validation 报告诊断，Test 仅在拟合后评估，不用于调参。参照项为 Train 标签均值和过去 5 个有效观测收益动量；三者在同一测试样本上比较 MAE 和 RMSE。Pearson IC 与 Spearman Rank IC 按每个预测日横截面计算，同时记录每日样本数、有效覆盖率；不足 3 个样本、实际收益常数、预测常数或相关未定义时记为 N/A 和具体原因。

训练前门禁要求：至少 8 只股票有数据、各可用股票区间缺失率不超过 2%、没有非交易日行情和重复样本、复权方式唯一、各 Train/Validation/Test 至少 200 个有效样本、标签有限且已实现、标签边界检查通过、测试至少 100 个独立交易日，并至少在 80% 测试日期覆盖 3 只股票。门禁失败只生成报告，不拟合模型。

成功实验才会分配新 `experiment_id`，保存独立模型文件、固定测试输入、Validation/Test 预测、每日 IC、JSON manifest 和报告，不覆盖旧实验。程序还会从文件重载模型，在同一测试输入上核验预测一致。上述模型落盘与评估流程已通过离线合成 Fixture 的工程测试；真实市场模型产物尚不存在。

### 当前真实数据与实际运行结果

DuckDB 当前实际有效行情记录：

| 股票代码 | 记录数 | 首日 | 末日 | 来源/复权 |
|---|---:|---|---|---|
| 000001.SZ | 58 | 2024-01-02 | 2024-03-29 | AKShare / qfq |
| 600519.SH | 58 | 2024-01-02 | 2024-03-29 | AKShare / qfq |

合计 116 条。小范围历史扩展尝试 `000001.SZ`、`600519.SH`、`300750.SZ` 的 2018–2025 行情，三只请求均在 3 次尝试后因 `RemoteDisconnected` 失败；失败运行写入 `ingest_runs`，没有新行情写入。由于小批量连通性测试不稳定，没有扩大到剩余 9 只股票。

实际运行命令与结果：

```powershell
.\.venv\Scripts\quantlab.exe data download --market ashare --ticker 000001.SZ 600519.SH 300750.SZ --start 20180101 --end 20251231 --adjustment qfq
# 三只均失败，AKShare RemoteDisconnected，有限重试结束。

.\.venv\Scripts\quantlab.exe research build-dataset --start 20180101 --end 20251231 --adjustment qfq
# 成功从本地 DuckDB 生成 Parquet 与 manifest；12 只中 2 只有本地数据。
# 116 行、106 个五日标签、0 个完整特征行。

.\.venv\Scripts\quantlab.exe model train --model lightgbm
# Data Readiness Gate FAIL；无模型拟合、无真实 LightGBM 预测指标。
```

门禁报告：`artifacts/reports/readiness_20261004T095558Z_3f481243.json`。已通过的日历检查只说明现有 2024Q1 行情日期都落在 session 上，不能说明整个 2018–2025 数据覆盖完整。当前没有可报告的真实 Train/Validation/Test 样本和模型指标。

## 测试、Git 与安全状态

运行命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

最近完整测试：**45 项通过，0 项失败**。包含 Step 2/3 既有回归测试，以及 Step 4 对字段映射、数据往返、修订和幂等、逐股特征、未来数据不变性、标签结束日、全局边界 purge、交易日历、缺失/混合复权门禁、IC 边界情况、Fixture LightGBM 拟合、简单基线、保存/重载、重复实验和 CLI 的离线测试。Fixture 指标仅证明程序路径，不代表真实市场表现。

Step 3 基线提交：`1c44c7c chore: checkpoint step 3 A-share data pipeline`。Step 4 实现目前在工作区未提交；本地 Git 没有远程，未推送代码。`.env`、`.venv`、数据库、行情、模型和报告已验证由 `.gitignore` 排除。凭据检查及历史 key 处置说明见 `docs/engineering_audit.md`；本阶段没有真实 OpenAI API 调用。

## 尚未完成与建议后续

当前主要阻塞是 AKShare 网络连接。恢复可靠数据访问后，建议先以 2–3 只股票重试并核对日期、记录量和质量，再逐批补齐股票池 2018–2025 的历史；确认历史数据范围和上市覆盖后重建数据集，查看缺口、60 日特征预热后样本量及 Purge 数量，再运行 readiness gate。只有门禁通过，才得到可解释的真实 LightGBM 基线。

尚未完成：港股 Provider、历史成分股/退市股票池、可靠停复牌资料、Point-in-Time 公司行动还原、walk-forward 验证、参数选择研究、真实数据 LightGBM 训练、VectorBT 策略回测、GPT 新闻增强、本地 Qwen/Hermes Agent、Streamlit Dashboard、券商连接和自动下单。

该项目目前是量化研究基础设施与模型实验原型，不是已验证的投资策略或收益系统。
