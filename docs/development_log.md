# 开发日志

## 2026-10-04 — Step 3：A 股数据管线与本地研究数据库

### 实施内容

- 在既有 `MarketDataProvider` 契约下新增 AKShare Provider，使用 `stock_zh_a_hist`，固定 AKShare 1.19.1；添加 20 秒上游超时、最多 3 次请求重试、前复权/后复权/不复权映射，以及 SSE/SZSE 代码标准化。
- 将 AKShare 成交量从手换算为股、成交额保留人民币元。源接口、版本、下载时间与采集运行 ID 写入元数据；上游响应先独立写入 raw Parquet。
- 扩展质量校验和错误分类。坏数据不会静默删除后进入研究集；缺少交易日历时缺口状态为 Unknown。
- 新建 DuckDB 行情目录和采集运行、校验问题、Parquet 清单、历史修订表。验证数据按交易所、复权方式、年份分区写 Parquet；相同数据幂等，变化数据保留历史行及版本信息。
- 新增 `quantlab data download/update/validate/status/query`，保留 `quantlab run` 与 `quantlab news`。
- 更新依赖和 `.gitignore`，本地初始化 Git repository；无远程、无推送、无提交。

### 真实行情命令与结果

```powershell
.\.venv\Scripts\quantlab.exe data download --market ashare --ticker 000001.SZ 600519.SH --start 20240101 --end 20240331 --adjustment qfq
```

AKShare 两只证券各返回 58 行、校验接受 58 行、拒绝 0 行。行情日期为 2024-01-02 至 2024-03-29。DuckDB 行数：000001.SZ 58、600519.SH 58。Parquet 清洗层：SZSE/qfq/2024 为 58 行，SSE/qfq/2024 为 58 行。Raw 层每只证券保存一个成功请求响应。

后续实际执行并成功：

```powershell
.\.venv\Scripts\quantlab.exe data status
.\.venv\Scripts\quantlab.exe data validate --market ashare --ticker 000001.SZ --adjustment qfq
.\.venv\Scripts\quantlab.exe data validate --market ashare --ticker 600519.SH --adjustment qfq
.\.venv\Scripts\quantlab.exe data query --market ashare --ticker 000001.SZ --start 20240101 --end 20240331 --adjustment qfq
```

在受限网络下的首次调用报告代理连接拒绝；获准联网后的正式范围请求成功。之后一次线上重复下载在上游返回 `RemoteDisconnected`，没有成功重放，失败运行已记录在 DuckDB。保持该失败为真实结果；离线测试单独验证幂等行为及失败时不破坏已有数据。

### 测试及环境

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：29 项通过、0 项失败（原 Step 2 的 15 项全部保留，Step 3 新增 14 项）。覆盖模拟 AKShare 字段、代码和市场映射、复权隔离、坏数据、缺失行情、日期顺序和 Unknown 缺口、Parquet/DuckDB 往返、重复导入、历史修订、增量回溯、原始保留、上游失败和写入失败保护。

Python 3.14.6 未更换。实际检查 AKShare 1.19.1、DuckDB 1.5.6、PyArrow 25.0.1。CLI 顶层、数据命令帮助，及上述 status/validate/query 命令通过。

### 后续工作

按阶段再开发 AKShare 批量/历史更新策略与正式交易日历，随后开发 A 股特征、严格时序验证和 LightGBM 回归。当前不进行这些后续阶段。

环境一致性补充检查：`uv pip check --python .\.venv\Scripts\python.exe` 检查 98 个已安装包，结果为全部兼容。

## 2026-10-04 — Step 4：研究数据集与首个 LightGBM 基线

### 基线与环境

- Step 3 基线提交：`1c44c7c chore: checkpoint step 3 A-share data pipeline`。检查 `.gitignore` 后确认 `.env`、`.venv`、本地行情数据库与模型产物不进入 Git；本地仓库没有远程。修正 `/data/` 根目录规则，避免误忽略 `src/quantlab/data/`。
- 保留现有 Python 3.14.6 `.venv`。安装并锁定 `exchange-calendars==4.13.2`；LightGBM 作为正式模型依赖安装。没有更换系统 Python。
- 修改前既有 29 项 unittest 全部通过；Step 4 新增 15 项离线研究测试，另加 1 项 HTTP-date `Retry-After` 限流测试，包含数据集、特征、标签、固定时间切分和 purge、Readiness Gate、横截面 IC、LightGBM / 基线、模型保存重载、重复实验和 CLI。最后全套共 45 项通过。

### 实现内容

- `config/ashare_universe.json` 建立 12 只、跨沪深多行业工程验证池，并记录股票池选取日期、范围、上市覆盖未知和幸存者偏差限制。
- `config/research.json` 配置 qfq、2018–2022 Train、2023 Validation、2024–2025 Test、5 session embargo、覆盖与样本阈值及 CPU LightGBM 参数。
- 新增 XSHG 交易日历适配层、逐股 `FeatureBuilder`、本地 DuckDB `build_research_dataset()`、五有效观察期收益及 `label_end_date`、全局日期 `FixedPeriodSplitter` 和训练门禁。
- 新增 LightGBM 五日回归实验服务，拟合前运行 Readiness Gate；若门禁通过，使用完全相同的测试样本比较 LightGBM、训练集均值和历史 5 日动量，按日期计算 Pearson IC / Spearman Rank IC 并记录 N/A 原因和覆盖率。实验 ID 独立保存模型、输入、样本外预测、每日 IC、指标和环境版本；固定输入重载复现预测。
- 新增 CLI：`quantlab research build-dataset`、`dataset-status`，`quantlab model train/evaluate/experiments`。现有 `run`、`news`、`data` 保留。
- 数据和实验落在 `.gitignore` 排除的 `data/` 与 `artifacts/` 中。Step 4 代码未提交，保留为可审阅工作区改动。

### 真实数据尝试与 Readiness Gate

按“先少量测试，稳定后再扩展”的要求，尝试以下真实网络命令：

```powershell
.\.venv\Scripts\quantlab.exe data download --market ashare --ticker 000001.SZ 600519.SH 300750.SZ --start 20180101 --end 20251231 --adjustment qfq
```

AKShare `stock_zh_a_hist` 对三只股票都在 3 次有界重试后失败，异常均为 `ConnectionError` / `RemoteDisconnected: Remote end closed connection without response`。每只结果都独立记录为失败；已有有效历史不变。因为测试阶段未稳定，本次没有扩大到其他股票，也没有绕过限制或伪造数据。

随即从本地 DuckDB 构建数据集：实际覆盖池 12 只中的 2 只；本地历史 000001.SZ、600519.SH 各 58 行，日期均为 2024-01-02 至 2024-03-29，均为 qfq；数据库总量 116 行。数据集有 116 行、106 个五日标签、0 个完整特征行（60 日均线预热窗口超出现有样本长度）。缺少数据的股票仍显示为无数据，未推断为停牌。

实际训练命令：

```powershell
.\.venv\Scripts\quantlab.exe model train --model lightgbm
```

Readiness Gate 失败：有数据股票 2（要求至少 8）；完整特征行 0；Train、Validation、Test 有效样本均为 0；测试交易日与横截面均为 0。没有拟合 LightGBM，也没有真实行情的模型指标。门禁报告：`artifacts/reports/readiness_20261004T095558Z_3f481243.json`。这次执行实际完成了阻止不合格训练的流程。

交易日历来自固定版本 `exchange_calendars` 的 XSHG 预计算日历，限定研究范围内有 1,942 个 session（2018-01-02 至 2025-12-31）；已用周末和春节等已知休市日期做离线检查。该版本日历非交易所实时 API，正式分析前需核对日历变更与个股停复牌资料。

### 测试结果及限制

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

最终运行：45 项通过、0 项失败（Step 3 的 29 项全部保留；Step 4 新增 16 项）。另外使用临时本地测试 Fixture 实际训练小型 LightGBM 模型、写入并重载、确认预测一致，以及连续两次实验写入不同 ID；该项仅验证工程路径，不是市场表现。

已更新 README，明确真实采集失败及门禁阻止拟合。Step 4.1 的新状态见下节；以下历史记录不作覆盖。

## 2026-10-04 — Step 4.1：多 Provider 行情与 canonical price layer

### 基线与实现

- Step 4 在本阶段前建立本地检查点：`e82f4c2 chore: checkpoint step 4 research dataset and lightgbm pipeline`。仓库没有 remote，也没有 push。Step 4.1 修改仍未提交。
- 保留 AKShare、Yahoo Finance、Raw/Validated/Processed、DuckDB/Parquet、12 股样本、17 个特征、五日标签、时间切分/purge/embargo、Readiness Gate、LightGBM 实验服务、Logistic Regression 和 OpenAI news。没有正式训练 LightGBM。
- 新增 Provider 能力模型和配置路由；Tushare、BaoStock、AKShare、Yahoo 的市场、复权、凭据需求和版本可由 `quantlab data providers` 查看。单只股票一次请求只使用一个 Provider；回填任务固定数据源；多源查询/数据集要求明确指定来源。
- Tushare 日线仅支持未复权价格。`vol` 从手转换为股，`amount` 从千元转换为人民币元；`adj_factor` 单独版本化，不覆盖 OHLC。无 `TUSHARE_TOKEN`，状态为 `unavailable: missing credential`，未调用上游，也没有声称权限可用。
- BaoStock 支持 none/qfq/hfq，将 `sz.000001`/`sh.600519` 映射到标准代码；成交量按股、成交额按人民币元；校验返回的 `adjustflag`；`tradestatus`、`isST`、`adjustflag` 存入供应商元数据表。AKShare 和 Yahoo Provider 保留。
- 新增 `data providers`、`backfill`、`backfill-status`、`reconcile`、`factors`、`adjusted-view` 及 `research readiness` 命令。回填分批检查点、可恢复、可只重试失败项。对账只比较并报告，不自动合并。Readiness 命令只跑门禁，不训练模型。
- `research.json` 不再固定 `source=akshare`，改用 market/price_basis/adjustment/date_range/universe。旧 qfq 行情和研究集未删改；新未复权数据集单独输出。

### OFFLINE TEST RESULT

原 45 项测试全部保留；Step 4.1 新增 16 项离线测试，全套 `61 passed / 0 failed`。覆盖 Provider 字段/单位映射、缺凭据与权限错误脱敏、复权因子隔离/修订、来源 fallback 与防混源、adjusted view、对账差异/失败报告、回填检查点/断点续跑/失败后继续/只重试失败项、异常行 raw 保留和校验问题写入。测试不依赖任何 Provider 网络或 GPT。

### REAL PROVIDER RESULT：短区间

BaoStock 不复权 2024Q1 实际 CLI 结果：

| 股票 | 获取行数 | 接受/拒绝 | 实际日期 | 状态 |
|---|---:|---|---|---|
| 000001.SZ | 58 | 58 / 0 | 2024-01-02 至 2024-03-29 | success |
| 600519.SH | 58 | 58 / 0 | 2024-01-02 至 2024-03-29 | success |
| 300750.SZ | 58 | 58 / 0 | 2024-01-02 至 2024-03-29 | success |

AKShare/BaoStock 对 `000001.SZ`、相同区间和 none 口径的真实对账无法完成：BaoStock 返回 58 行；AKShare 三次请求均因本机代理 `127.0.0.1:9` 拒绝连接失败。失败报告为 `artifacts/reports/reconcile_0bccf96f2f464d029d0db361637e20f1.json`，状态 `blocked`，没有合并数据。

### REAL PROVIDER RESULT：2018–2025 分批回填

使用 `quantlab data backfill --market ashare --universe config/ashare_universe.json --start 20180101 --end 20251231 --adjustment none --provider auto --limit 3` 运行四批，job 固定选择 BaoStock。11/12 完成，共 21,257 条有效行情：

| 股票 | 状态 | 首日 | 末日 | 行数 | 复权 |
|---|---|---|---|---:|---|
| 000001.SZ | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 000002.SZ | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 000333.SZ | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 000651.SZ | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 000858.SZ | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 002594.SZ | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 300750.SZ | completed | 2018-06-11 | 2025-12-31 | 1,837 | none |
| 600036.SH | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 600519.SH | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 601318.SH | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 601888.SH | completed | 2018-01-02 | 2025-12-31 | 1,942 | none |
| 688981.SH | failed validation | — | — | 0 accepted | none |

688981.SH 的上游原始响应有 1,327 条记录，2025-09-01 至 2025-09-08 六条记录成交量和成交额为空。Raw Parquet 保留，校验问题为 `missing_field`，没有填数或推断停牌，未写入正式表。无法确认上市日期/Provider 覆盖的区间仍标记 unknown。Step 3 的 AKShare/qfq 116 行仍保留；Tushare 因无 token 未请求，因子表为 0 行。

### Dataset 与 Readiness Gate

```powershell
.\.venv\Scripts\quantlab.exe research build-dataset --start 20180101 --end 20251231 --adjustment none --provider baostock --output data/processed/ashare_research_dataset_none_v1.parquet
.\.venv\Scripts\quantlab.exe research readiness --dataset data/processed/ashare_research_dataset_none_v1.parquet --manifest data/processed/ashare_research_dataset_none_v1.manifest.json
```

数据集为 21,257 行、21,202 个实现的五观察期标签、20,598 个完整特征样本，11 只股票有数据。Gate `PASS`：Train 12,491、Validation 2,552、Test 5,280；测试期 480 个日期、横截面覆盖率 100%；时序边界 purge 和 5-session embargo 均通过。最终报告 `artifacts/reports/readiness_20261004T114455Z_c61aea87.json`。这是数据充分性/时序门禁，不证明预测能力；没有拟合 LightGBM。

运行环境仍为 Python 3.14.6；NumPy 2.5.3、Pandas 3.0.6、scikit-learn 1.9.1、LightGBM 4.7.0、DuckDB 1.5.6、PyArrow 25.0.1、AKShare 1.19.1、BaoStock 0.9.4、Tushare 1.4.29、exchange-calendars 4.13.2。`uv pip check` 检查 107 个安装包并全部兼容；`compileall` 通过。qfq/hfq 仍是可变供应商快照，未实现 point-in-time 公司行动还原；当前股票池有幸存者偏差。Step 4.1 到此停止。
