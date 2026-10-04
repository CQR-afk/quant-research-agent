# Quant Research Agent

Quant Research Agent 是一个 A 股优先、港股其次的本地金融研究项目。它采集并验证市场数据，构建研究特征与标签，提供 readiness-gated 的 LightGBM 五观察期收益率实验。项目用于研究和预测，不连接券商账户，也不提供自动下单。

## 当前实现

- `quantlab run` 保留 Yahoo Finance 日线和 Logistic Regression Baseline Direction Classifier，按时间顺序评估。
- `quantlab news` 是可选的独立 OpenAI Responses API 新闻研究入口；需显式配置密钥，不参与离线数据处理或训练。
- `quantlab data` 使用 AKShare `stock_zh_a_hist` 采集 A 股日线。Raw 响应与标准化数据分层保存，质量检查后进入 DuckDB + Parquet。采集失败按股票记录，不覆盖已有有效行情。
- `quantlab research build-dataset` 从本地 DuckDB 构建股票逐日特征、五观察期收益标签和覆盖报告；标签记录 `prediction_date`、`label_end_date` 及数据版本。支持显式 `none`、`qfq`、`hfq` 单一复权口径。
- `quantlab model train --model lightgbm` 先执行数据就绪门禁；只有所有门槛通过才拟合模型。成功时按独立实验 ID 保存模型、样本外预测、每日 IC 和报告。`evaluate` 重载固定模型并核验预测一致性，`experiments` 列出历史实验。
- LightGBM 输入包含 17 个逐股计算的价格收益、均线、RSI、MACD、波动率及成交量特征。Train/Validation/Test 使用全局日期切分，标签边界 purge 并设置交易日 embargo。训练集均值和 5 日历史动量是同一测试样本上的回归参照。

该模型服务与离线合成 Fixture 测试已实现；**当前真实数据未通过 Readiness Gate，因此本地没有用真实 A 股样本完成的正式 LightGBM 训练结果。**不得将离线 Fixture 指标当作市场研究结果。

## 数据和研究限制

股票池在 [`config/ashare_universe.json`](config/ashare_universe.json) 中配置，目前是 12 只当前可见股票组成的工程验证样本，未包含历史成分股及退市证券，存在幸存者偏差。数据研究范围配置为 2018–2025；不推断股票上市前历史，也不把缺失交易记录判作停牌。

交易日历使用固定版本 `exchange_calendars` 的 XSHG 预计算日历，在本项目配置的 2018–2025 范围内覆盖上海交易所交易日和节假日。[XSHG 日历定义及其节假日来源说明](https://github.com/gerrymanoim/exchange_calendars/blob/master/exchange_calendars/exchange_calendar_xshg.py) [固定依赖版本](https://pypi.org/project/exchange_calendars/4.13.2/)。它是本地预计算日历，不是交易所实时交易日服务；正式研究前应核对日历更新及个股停复牌资料。

当前 qfq 历史价格是事后获取的复权快照，可能因后续公司行动而修订；系统保留数据版本及历史修订记录，但没有 Point-in-Time 公司行动还原，不能据此声称严格无前视的历史可交易回测。当前固定股票池也不能代表同期可投资全市场。

## 环境与测试

当前环境保留 Python 3.14.6。AKShare 1.19.1、LightGBM、DuckDB、PyArrow 和 `exchange-calendars` 使用项目虚拟环境；不需要 GPT 或 Ollama 才能管理数据或运行本地模型。用 PowerShell 安装或复用项目环境：

```powershell
.\setup.ps1
```

测试仅使用本地 Fixtures，可离线运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## 常用命令

```powershell
# 按配置股票池逐只下载，单只失败会单独记录
quantlab data download --market ashare --ticker 000001.SZ 600519.SH --start 20180101 --end 20251231 --adjustment qfq

# 更新最近数据，默认回看重抓以捕获历史修订
quantlab data update --market ashare --ticker 000001.SZ --adjustment qfq

quantlab data status
quantlab data validate --market ashare --ticker 000001.SZ --adjustment qfq
quantlab data query --market ashare --ticker 000001.SZ --start 20240101 --end 20240331 --adjustment qfq

quantlab research build-dataset --start 20180101 --end 20251231 --adjustment qfq
quantlab research dataset-status
quantlab model train --model lightgbm
quantlab model evaluate --model lightgbm
quantlab model experiments

# 保留的旧入口
quantlab run --ticker SPY --period 5y
quantlab news --ticker 000001.SZ
```

`data/` 保存 raw、validated、processed Parquet 及 `database/quantlab.duckdb`。`artifacts/models/`、`artifacts/experiments/` 与 `artifacts/reports/` 保存本地模型及研究报告。这些目录、`.env`、虚拟环境和日志均由 `.gitignore` 排除。

## Step 4 实测状态（2026-10-04）

本地数据库包含来自此前 Step 3 实际 AKShare 下载的 `000001.SZ` 和 `600519.SH`，各 58 条 2024-01-02 至 2024-03-29 的 qfq 日线，共 116 条。为扩展历史，真实尝试 `000001.SZ`、`600519.SH`、`300750.SZ` 的 2018-01-01 至 2025-12-31 qfq 行情；三只股票均在 3 次请求后返回上游 `RemoteDisconnected`，所以没有新增历史记录，也没有继续批量请求。

真实本地数据集写入 `data/processed/ashare_research_dataset_v1.parquet`，覆盖配置池 12 只股票；其中 2 只在本地有数据，数据集 116 行、106 个五日标签，但因最大窗口为 60 个观察值，完整特征行为 0。Readiness Gate 因股票数量不足、有效特征与各切分样本不足而失败，LightGBM 没有拟合。门禁报告位于 `artifacts/reports/readiness_20261004T095558Z_3f481243.json`。交易日历在 2018-01-02 至 2025-12-31 提供 1,942 个 session。

新增 16 项 Step 4 离线测试及原有 29 项测试均通过（总计 45 项）。详细实施记录见 [`docs/development_log.md`](docs/development_log.md)。

当前源码、环境、数据、模型逻辑、实测结果、Git 状态与限制的详细汇总见 [`docs/project_status.md`](docs/project_status.md)。

后续开发应先恢复可靠的 AKShare 网络采集，分批补齐配置池的历史数据，再重建数据集并评估门禁；本步骤到此暂停。VectorBT、GPT 新闻增强、Hermes、Dashboard 与交易功能尚未开发。
