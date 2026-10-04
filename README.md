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
- `quantlab model train --model lightgbm` 运行前执行数据就绪门禁；门禁失败时不拟合。正式真实行情训练仍取决于充分、可审计的真实历史数据。

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

# 保留的旧入口
quantlab run --ticker SPY --period 5y
quantlab news --ticker 000001.SZ
```

数据采集、回填和对账不要求 GPT、Ollama 或本地 Agent。当前仍未实现 VectorBT 完整策略、Dashboard、Qwen/Hermes 调度或真实交易。

## 实际数据状态

此前 Step 3/4 的本地库有 AKShare 获取的 `000001.SZ` 和 `600519.SH`，各 58 行、2024 年第一季度 qfq 快照，共 116 行。Step 4 的五日收益研究数据集有 116 行、106 个标签，但 60 日窗口不足以产生完整特征。真实历史扩展曾因上游 `RemoteDisconnected` 失败；Step 4 的 Readiness Gate 因数据不足阻止了真实行情训练。

Step 4.1 新 Provider 的离线测试与真实网络验证分别记在 [`docs/development_log.md`](docs/development_log.md) 和 [`docs/engineering_audit.md`](docs/engineering_audit.md)。只把实际成功响应计入线上验证；不可用凭据、网络拦截或上游限制会明确列出。
