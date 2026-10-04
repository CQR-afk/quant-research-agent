# Quant Research Agent

Quant Research Agent 是一个面向 A 股优先、港股其次的本地量化研究项目。它提供研究数据采集、验证和本地存储，并保留 Yahoo Finance 兼容行情、Logistic Regression 基线和可选 GPT 新闻研究。项目仅用于研究和预测，不连接券商，也不提供自动下单。

## 当前已实现

- `quantlab run`：通过 Yahoo Finance 下载日线并训练 Logistic Regression Baseline Direction Classifier，按时间顺序切分样本，输出指标和留出预测。
- `quantlab news`：独立调用 OpenAI Responses API 网页搜索；模型名可配置，新闻结果记录来源、调用数及 Token 用量。新闻功能不影响离线训练。
- `quantlab data`：通过 AKShare `stock_zh_a_hist` 下载 A 股日线，支持不复权、前复权和后复权，保留原始响应，标准化并验证后写入 DuckDB 与 Parquet。
- A 股股票代码标准化保留前导零，并映射至 SSE/SZSE。AKShare 返回成交量单位为手，进入研究数据前换算为股；成交额按元保留。成交额缺失时不合成数据。
- 数据检查覆盖日期顺序、重复行、异常周末日期、OHLC 关系、价格/成交量/成交额、字段缺失、代码及交易所映射、复权混合。交易日缺口会标为 Unknown，不据此推断停牌。
- 重复导入保持幂等；数据修订保留旧记录及版本元数据。前复权、后复权和不复权数据分别存储。复权历史可能会被上游之后修订。
- `make_forward_target(..., horizon=5)` 已提供五期收益标签基础函数，标签附带结束日期；正式 LightGBM 训练模块尚未开发。

AKShare 适配器基于官方接口 `stock_zh_a_hist`；接口字段为中文日期、OHLC、成交量和成交额，源单位及 API 包版本会随原始记录保留。[AKShare 行情接口文档](https://github.com/akfamily/akshare/blob/main/docs/data/stock/stock.md) [AKShare 发布版本](https://pypi.org/project/akshare/)。DuckDB 验证数据以较粗的 exchange / adjustment / year 分区写入 Parquet，避免每只股票每日单文件。[DuckDB Parquet 分区说明](https://duckdb.org/docs/current/data/partitioning/hive_partitioning)。

## 数据目录

```text
data/
├── raw/ashare/             # 每次 AKShare 返回的原始响应与采集元数据
├── raw/yahoo/              # 为未来 Yahoo 数据入仓预留
├── validated/ashare/       # 当前有效快照的分区 Parquet
├── processed/              # 特征和标签的后续存放位置
└── database/quantlab.duckdb # 行情目录、采集运行、问题、版本和 Parquet 清单
```

DuckDB 的 `market_bars` 表是标准化数据查询入口；`bar_revisions` 保存被修订记录，`ingest_runs` 和 `validation_issues` 保存采集与校验结果。原始采集使用独立 ingest id 文件。数据目录、数据库和大型本地文件均在 `.gitignore` 中排除。

## 环境

项目当前使用 Python 3.14.6。数据管线依赖 AKShare 1.19.1、DuckDB 1.5.6 和 PyArrow 25.0.1；项目环境还包含 NumPy、Pandas、Scikit-learn、LightGBM、VectorBT、Numba、Yahoo Finance 和 OpenAI SDK。运行安装脚本：

```powershell
.\setup.ps1
```

安装脚本会复用现有 `.venv`，不会清除环境。`.env` 被 Git 忽略；新闻 API 密钥只从环境配置读取。本项目不需要 GPT 或 Ollama 才能运行数据命令。

## 数据命令

在项目根目录使用：

```powershell
quantlab data download --market ashare --ticker 000001.SZ 600519.SH --start 20240101 --end 20240331 --adjustment qfq
quantlab data update --market ashare --ticker 000001.SZ --adjustment qfq
quantlab data update --market ashare --ticker 000001.SZ --full-refresh --adjustment hfq
quantlab data validate --market ashare --ticker 000001.SZ --adjustment qfq
quantlab data status
quantlab data query --market ashare --ticker 000001.SZ --start 20240101 --end 20240331 --adjustment qfq
```

`update` 默认从最近已存交易日期之前 45 个日历日重抓，用于接收上游历史修订；首次更新从 1990 年开始，可通过 `--start` 限定范围。`--full-refresh` 重新拉取指定范围或默认完整可用范围。查询时应指定复权方式，避免把不同价格序列合并。

旧研究入口继续可用：

```powershell
quantlab run --ticker SPY --period 5y
quantlab news --ticker 000001.SZ
```

`SPY` 可用于 Yahoo 兼容性验证，不是本项目核心研究市场。离线检查命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 本阶段实测

2026-10-04，AKShare 实际返回 000001.SZ 和 600519.SH 在请求区间 2024-01-01 至 2024-03-31 内各 58 行；有效行情日期为 2024-01-02 至 2024-03-29。两只证券均通过字段及 OHLC 校验，写入 `market_bars` 共 116 行，并分别落入 SSE/SZSE Parquet 分区。真实重复抓取时，上游在第二次请求中断连接；离线重复导入、幂等写入及增量重抓逻辑均通过自动化测试。完整记录见 [`docs/development_log.md`](docs/development_log.md) 和 [`docs/engineering_audit.md`](docs/engineering_audit.md)。

## 尚未实现

港股数据源、正式交易日历与停牌资料、全市场采集、公司行动 point-in-time 还原、LightGBM 研究训练、VectorBT 策略回测、Streamlit 界面和本地 Agent 调度均未在本阶段实现。数据源覆盖和服务许可由上游决定。策略研究结果不是投资建议或交易指令。
