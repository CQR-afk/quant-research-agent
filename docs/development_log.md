# 开发日志

## 2026-10-05 — Step 4.5 宽度扩展预检

按要求先提交 Step 4.4 本地检查点 `c4ca573`。92 项 unittest 通过，`uv pip check` 检查 107 个包全部兼容。之后检查到 BaoStock SDK 0.9.4 可登录，但全量股票基础资料/行业请求持续无响应；AKShare 1.19.1 的 SSE/SZSE A 股上市清单请求失败于代理错误。Tushare 缺少凭据。没有可验证的候选证券元数据，因此没有冻结候选池、下载新行情或训练/评估；U11 control、Walk-forward 切分及实验产物均未改动。完整限制与恢复条件见 [`step_4_5_universe_expansion.md`](step_4_5_universe_expansion.md)。

## 2026-10-05 — Step 4.5R connectivity recovery

Python 环境的 HTTP/HTTPS/ALL 代理指向回环端口 9，但该端口无服务响应；DNS 解析正常，WinINET 未启用代理且 WinHTTP 为直连。通过已配置代理运行的 AKShare 1.19.1 SSE/SZSE 列表函数均在约 2 秒返回 `ProxyError`。BaoStock SDK 0.9.4 正常登录；单股基础资料字段为 `code, code_name, ipoDate, outDate, type, status`，单股日期行业字段为 `updateDate, code, code_name, industry, industryClassification`，各返回 1 条。全量 basic 请求经 60 秒看门狗及全量 industry 经 15 秒看门狗终止，均未获得完整 schema/数据。离线项目中没有有来源的证券列表表格文件。测试仍为 92/92 通过，环境 107 packages compatible。未修改代码、基线或实验产物；没有获取任何股票行情或建立候选池。由于没有获准的直接源访问，结果为 `metadata_access_still_blocked`，后续需修复进程代理或获批 direct route。

## 2026-10-05 — Step 4.5R.1 网络复核

起始 HEAD `0c63cef`，工作区干净，指定检查点均存在。当前代理仍为 loopback:9 且探测超时；没有明确项目级直连授权，因此本轮没有调用交易所在线列表。保留前次 BaoStock 单股成功结果。SDK 源码显示每页上限为 2,000 条，`ResultData.next()` 在满页后会请求下一页；初次响应和分页都经过无超时的阻塞 socket receive 循环。现有看门狗没有逐页日志，因此不能定位卡在初始响应、后续分页、传输体积/速度或终止符。92 项测试通过，107 个包兼容。未创建候选池或改动数据/模型。状态仍为 `metadata_access_still_blocked`。

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
## Step 4.2 价格口径校验与首个真实 LightGBM 基线（2026-10-04）

### 先决检查与本地检查点

执行修改前核对 Git 工作区，全部既有变更均为 Step 4.1 文件；`.env` 未被跟踪，`git ls-files -- data artifacts` 无结果，`.gitignore` 排除 `.env`、`.venv`、数据、数据库、artifacts、日志和模型。源码凭据模式扫描为 0 命中且未输出值。原有 61 项自动化测试全部通过，`uv pip check` 检查 107 包均兼容。按 Step 4.2 要求建立本地提交 `1b0796c chore: checkpoint step 4.1 multi-provider market data`，无远程、未推送。旧 688981.SH 数据未修复。

### qfq 实际接口与价格口径核对

真实短区间命令：

```powershell
quantlab data download --provider baostock --market ashare --ticker 000001.SZ 600519.SH 300750.SZ --start 20240101 --end 20240331 --adjustment qfq
```

每只实际返回 58 行，交易日 2024-01-02 至 2024-03-29；OHLC/成交量/成交额字段通过标准化和校验，接收 58、拒绝 0。相同命令重跑三只均为 skipped、获取/接收/更新均为 0。qfq 与 none 在 source、adjustment 分区隔离。

同源价格比较命令：

```powershell
quantlab data compare-bases --provider baostock --ticker 000001.SZ 600519.SH 300750.SZ --start 20240101 --end 20240331 --jump-threshold 0.2
```

三只标的各有 58 个共同日期；qfq/none 的 OHLC 比例逐字段相符，收盘价比例 first/median/last 分别为：000001.SZ `0.821514`、600519.SH `0.908734`、300750.SZ `0.930927`。共同日期中 volume、amount 全部相同（各 58/58）。20% 单日绝对收益阈值没有命中记录。跳变诊断只报告机械阈值，不赋予公司行动原因、不自动修复价格。报告：`artifacts/reports/price_basis_3d0d9c1b2eb5.json`。

### 11 股 qfq 历史回补

配置 `config/ashare_universe_qfq_pilot.json` 明确排除了已有缺失字段问题的 688981.SH。实际执行同一可恢复任务 4 次，每批最多 3 股：

```powershell
quantlab data backfill --market ashare --universe config/ashare_universe_qfq_pilot.json --start 20180101 --end 20251231 --adjustment qfq --provider baostock --limit 3
```

任务 ID `a6bbc3e4-cbc1-427c-aede-d3d232e8440c`，最终 `complete`，11/11 股票完成，校验拒绝 0，总计 21,257 行。10 股覆盖 2018-01-02 至 2025-12-31，各 1,942 行；300750.SZ 从 2018-06-11 开始，共 1,837 行。对该证券开始日期前区间不推断停牌或上市来源。现有 none 价格库与 none 研究集未覆盖。

### 独立数据集、manifest 与 readiness

实际命令：

```powershell
quantlab research build-dataset --universe config/ashare_universe_qfq_pilot.json --start 20180101 --end 20251231 --adjustment qfq --provider baostock --output data/processed/ashare_research_dataset_baostock_qfq_v1.parquet
quantlab research readiness --dataset data/processed/ashare_research_dataset_baostock_qfq_v1.parquet --manifest data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json
```

数据集 21,257 行、20,598 行特征完整、21,202 行标签有效。Manifest 为 BaoStock qfq 供应商快照，包含逐股下载快照时间、数据版本 SHA、feature `ashare_daily_v1`、target `target_5d_h5_v1`、池版本、5 期标签及显式 `NOT_POINT_IN_TIME` 提示。原 `ashare_research_dataset_none_v1.parquet` 不改写。

Readiness Gate 实际 PASS：可用标的 11，日历缺口比率 0、非交易日 0、重复样本 0、调整模式仅 qfq；Train 12,491、Validation 2,552、Test 5,280；测试 480 个独立日期，3+ 截面覆盖 100%；两个边界 label purge 通过。报告 `artifacts/reports/readiness_20261004T120839Z_7c36b3f6.json`。Gate PASS 仅表示预设工程门槛满足，不表示模型有效或盈利。

### 唯一一次真实模型实验

实际命令（本阶段只执行一次拟合）：

```powershell
quantlab model train --model lightgbm --dataset data/processed/ashare_research_dataset_baostock_qfq_v1.parquet --manifest data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json --name "Baseline Real Market Experiment v1"
```

配置参数原样取自 `config/research.json`，无调参。训练期 2018–2022，验证期 2023，测试期 2024–2025；Train only fit；train mean 和 momentum_5d 与 LightGBM 共用同一批测试键。

| 2024–2025 样本外诊断 | LightGBM | Train mean | momentum_5d |
|---|---:|---:|---:|
| MAE | 0.0317917 | 0.0298928 | 0.0430996 |
| RMSE | 0.0480043 | 0.0458668 | 0.0657300 |
| Pearson IC 均值 / 中位数 / 标准差 | -0.08343 / -0.07903 / 0.37399 | NA（常量预测） | 0.02663 / 0.03819 / 0.43661 |
| RankIC 均值 / 中位数 / 标准差 | -0.06794 / -0.08182 / 0.32806 | NA（常量预测） | 0.00780 / 0.01818 / 0.39546 |
| 有效 IC 日 / 测试预测行 | 480 / 5,280 | 0 / 5,280 | 480 / 5,280 |

LightGBM 2024 MAE/RMSE `0.037390/0.056463`，Pearson/RankIC `-0.03977/-0.03569`；2025 MAE/RMSE `0.026099/0.037495`，Pearson/RankIC `-0.12782/-0.10073`。四个测试年度季度区间各有分别报告。逐股票实际与预测均值、样本数、MAE/RMSE；预测分布；gain/split feature importance；每日 IC；验证和测试预测及固定测试输入都写入实验产物。模型文件重载后 5,280 固定输入 `allclose(rtol=0, atol=1e-12)` 通过，随后没有再次拟合。

实验 ID `20261004T120849Z_2cb6448746`；报告和模型、预测与诊断分别位于 `artifacts/reports/20261004T120849Z_2cb6448746.json`、`artifacts/models/lightgbm/20261004T120849Z_2cb6448746.joblib`、`artifacts/experiments/20261004T120849Z_2cb6448746/`。`quantlab model evaluate --model lightgbm --experiment-id 20261004T120849Z_2cb6448746` 重新加载并验证同输入预测一致。

这些值是一个 qfq 供应商快照、11 股非 PIT 幸存者工程池下的预测研究结果。未做组合/交易成本/持仓/换手建模，不是 VectorBT 策略回测或盈利结论。

### 本次代码及测试

新增同源 none/qfq 对比与机械跳变诊断、独立 qfq 样本池配置、数据集版本和逐股 qfq 快照 manifest 字段；readiness 根据显式 manifest adjustment 校验；模型 train CLI 可传 dataset/manifest/name；训练实验记录测试整体、按年/季度、按股诊断，预测分布、ICIR、覆盖、gain/split importance 和输入 lineage。Logistic Regression 与 none 基准保留。

新增离线 price basis Fixture 测试，扩展 manifest 与 LightGBM 报告测试。完整运行 `python -m compileall -q src tests` 和 `python -m unittest discover -s tests -v`：**62 passed / 0 failed**；`uv pip check`：107 packages compatible；CLI `model evaluate` reload check：`model_reload_same_input_consistent=true`。没有运行 VectorBT，没有进入后续阶段。

## 2026-10-04 — Step 4.3：Walk-forward、特征尺度审计与治理

### Git 与冻结基线

- Step 4.2 固定检查点为 `661596e chore: checkpoint step 4.2 real lightgbm baseline`。无远程仓库、未推送。Step 4.3 代码和文档仍在工作区，未提交。
- Baseline Real Market Experiment v1 (`20261004T120849Z_2cb6448746`) 按 immutable 处理，manifest 未编辑。2024–2025 Test Pearson IC `-0.08343`、Rank IC `-0.06794` 保留并登记为 `LEGACY_OBSERVED_TEST`；开发命令不读取其样本或指标。
- `config/research_governance.json` 为 2026-01-01 起的 sealed holdout 定义协议元数据，`used=false`。没有下载或评价 2026 年数据。

### 实现

- v1 的 scale-invariant 特征为 Return_1d/5d/10d/20d、Price_to_MA20、RSI_14、Volatility_5d/20d、Volume_Ratio；scale-dependent 特征为 MA_5/10/20/60、MACD、MACD_Signal、Volume_MA5/20。
- 新增 `ashare_daily_v2_scale_invariant`：Return_1d/5d/10d/20d、Close_to_MA5/10/20/60、RSI_14、MACD_to_Close、MACD_Signal_to_Close、Volatility_5d/20d、Volume_to_MA5/20、Volume_Change_1d。零分母和未形成窗口保留 NaN，不向前/未来填充；rolling 按 symbol 隔离；模型输入不含绝对尺度原值或 symbol ID。target 仍为 `target_5d_h5_v1`。
- dataset builder 支持 feature version 选择，并禁止 v2 构建跨入 2024 年。v2 本地开发数据集 15,922 行，其中 15,227 行完整特征、15,867 行有效标签；本阶段未拉取新行情。
- 新增 `quantlab research walk-forward` 服务和 CLI。三个 expanding folds 为 2018–2020→2021、2018–2021→2022、2018–2022→2023；全局日期边界、label-end purge、5-session embargo、不 shuffle，使用与 baseline 相同的固定 LightGBM 参数。报告包含 common sample 对齐、逐折误差/IC、月/季 IC、ICIR、Newey-West lag=5、gain/split importance 和预测分布。
- 实际日期/行数：Fold1 Train 2018-04-02–2020-12-17（v1/v2 7,156/7,120），Validation 2021-01-04–2021-12-17（2,563），purge 110/110；Fold2 Train 至 2021-12-17（9,829/9,793），Validation 2022-01-04–2022-12-16（2,552），purge 110/110；Fold3 Train 至 2022-12-16（12,491/12,455），Validation 2023-01-03–2023-12-15（2,552），purge 110/55。各折 11 个股票，v1/v2 验证键、target、label end date 完全一致。

### 实验结果

实际执行：

```powershell
quantlab research walk-forward --dataset-v1 data/processed/ashare_research_dataset_baostock_qfq_v1.parquet --manifest-v1 data/processed/ashare_research_dataset_baostock_qfq_v1.manifest.json --dataset-v2 data/processed/ashare_research_dataset_baostock_qfq_v2_scale_invariant_dev_2018_2023.parquet --manifest-v2 data/processed/ashare_research_dataset_baostock_qfq_v2_scale_invariant_dev_2018_2023.manifest.json
```

报告：`artifacts/reports/walkforward_comparison_2ee1453e2be5.json`；v1 实验 `walkforward_ashare_daily_v1_e52bb0812502`，v2 实验 `walkforward_ashare_daily_v2_scale_invariant_c0656b5d5257`。每折指标依次为 MAE / RMSE / Pearson IC 均值 / Rank IC 均值 / ICIR / Rank ICIR / 正 Pearson 日比 / 正 Rank IC 日比：

| Fold | v1 | v2 |
|---|---|---|
| 2021 | .044050 / .057398 / .081075 / .050449 / .204665 / .141584 / 60.09% / 56.22% | .045209 / .058473 / .025537 / -.007608 / .065182 / -.021234 / 49.36% / 47.64% |
| 2022 | .042528 / .054860 / .073181 / .046199 / .196242 / .122961 / 54.31% / 53.88% | .041274 / .053504 / -.000698 / -.028840 / -.001754 / -.083275 / 46.55% / 46.12% |
| 2023 | .031445 / .039879 / .048397 / .025000 / .159386 / .075232 / 56.47% / 49.14% | .030184 / .039170 / -.013691 / -.015165 / -.035066 / -.043240 / 49.14% / 46.98% |

common-sample 跨折均值 v1→v2：MAE `.039341→.038889`、RMSE `.050712→.050382`、Pearson IC `.067551→.003716`、Rank IC `.040549→-.017204`、ICIR `.186764→.009454`、RankICIR `.113259→-.049250`、正 Pearson/Rank IC 日比例 `56.95%/53.08%→48.35%/46.91%`。v2 误差小幅降低，但三折 Rank IC 均负，因此状态为 `development_only`，不构成方向预测升级。没有依据 legacy test 反转 prediction。

v1 每股特征中位数与每股中位 close/volume 的 Pearson 描述性相关：MA_5–MA_60 `0.999851–0.9999998`，MACD/MACD_Signal `0.985718–0.991883`，Volume_MA5/20 `0.999890–0.999911`（11 股）。v1 top feature 为 Volatility_20d 两折、Volume_MA20 一折；v2 三折均为 Volatility_20d。平均 top-5 gain 占比 `.4816/.5018`，v2 的重要性分布略集中。预测均非 near-constant；预测/目标标准差中位比 `.305/.255`，预测标准差跨折最大/最小比 `1.79/2.18`。12 个季度里 Pearson/Rank IC 正季度数量 v1 为 9/7、v2 为 8/6。NW lag=5 仅诊断重叠标签造成的序列相关，不作为正式显著性或投资有效性证明。

### 测试和范围

- 新增 19 项离线 walk-forward 测试；全套 **81 passed / 0 failed**（原 62 项保留）。覆盖日期边界、purge/embargo、v2 不变性/逐股 rolling、零分母/NaN/Inf/零成交量、common sample 与 target、固定参数、重要性和预测稳定性、NW diagnostic、legacy test guard 及开发期截止。
- `compileall` 通过；`uv pip check` 检查 107 个包全部兼容。没有生成 2024+ 预测/指标；未运行 VectorBT、参数搜索、universe 扩展或其他模型，也未接入 GPT/news/Qwen/Hermes/交易。

Step 4.3 到此停止，等待后续阶段指令。

## 2026-10-05 — Step 4.4：横截面目标与排序评价对齐

### 检查点、固定条件与数据边界

- 开始前核对 Step 4.3 工作区、81 项测试、Python 3.14.6 环境；`uv pip check` 检查 107 个包全部兼容。`.env`、`data/`、`artifacts/`、`.venv/` 均由 `.gitignore` 排除且没有进入 Git。
- 按要求建立本地检查点 `636c26f chore: checkpoint step 4.3 walk-forward feature diagnostics`；没有远程仓库，没有 push。当前 Step 4.4 变更仍未提交。
- 研究只使用 Step 4.3 materialized 的 `ashare_daily_v1` development-only 数据和 11 股 BaoStock qfq 快照。Arrow 日期过滤仅保留 prediction/label end 均早于 2024-01-01 的行。Legacy 2024–2025 test 未读取；2026+ holdout 未下载、读取或评价，`used=false`。
- 固定 LightGBM LGBMRegressor 参数、seed、Feature v1、5-session target horizon 和三个 fold；没有参数搜索或修改特征。初次真实命令在 A/B/C common-key 合并校验处安全中止，排查后将模型 native fit target 与统一评价所用 raw return 字段显式分离，再完成唯一有效 comparison report。没有把中止尝试当作研究结果。

### Target Builder 与数据集

- A：保留 `target_5d_h5_v1`，定义仍为每只股票自己的 `close.shift(-5)/close-1`。
- B：新增 `target_5d_pool_excess_v1 = raw target - same prediction_date valid pool mean`；用 pool 专名，不声称代表市场超额收益。
- C：新增 `target_5d_cs_rank_v1 = 2 * rank(method="average", pct=True) - 1`，同日按实际 raw return 排序。Ties 使用平均秩。
- B/C 每日有效 raw target 少于 8 只时整日目标均为 NaN。无未来填充、零填补或其他日期填充。三 target 共用同一原始 `label_end_date`；构造过程中所有 v1 feature 列逐值不变，target 名称被拒绝作为 feature 输入。
- 每个目标有独立 Parquet、manifest、dataset version。三份数据各 15,867 行；均有 15,867 个有效目标，cross-section 不足门槛的日期为 0。Manifest 标明 feature/target version、精确定义、horizon=5、minimum breadth=8、中心/排名方式、development cap、evaluation role 及 PIT/survivorship 限制。

### 命令、实验与结果

实际命令：

```powershell
quantlab research target-study --target-version all
```

目标实验 IDs：

- Raw: `walkforward_target_raw_v1_f120d1fa7981`
- Pool Excess: `walkforward_target_pool_excess_v1_c8dd2bb960d6`
- CS Rank: `walkforward_target_cs_rank_v1_601b71a85ac3`

所有目标沿用同样的 expanding fold 边界、5-session embargo 与 label-end purge；每折 A/B/C 的训练及验证行数相同。Fold1 Train 2018-04-02–2020-12-17 共 7,156 行，Validation 2021-01-04–2021-12-17 共 2,563 行 / 233 日期；Fold2 Train 至 2021-12-17 共 9,829 行，Validation 2022-01-04–2022-12-16 共 2,552 行 / 232 日期；Fold3 Train 至 2022-12-16 共 12,491 行，Validation 2023-01-03–2023-12-15 共 2,552 行 / 232 日期。purged train/validation 行分别 110/110、110/110、110/55。共同键确认 symbol、prediction date、label end date 和 raw target 一致，每折覆盖 11 股。

主要指标在完全相同的 A∩B∩C 验证键上计算：模型输出只被当成横截面 score，并与 raw `target_5d` 计算 Rank IC。逐折指标如下：

| Fold | Raw Rank IC / RankICIR / 正日比例 | Pool Excess Rank IC / RankICIR / 正日比例 | CS Rank Rank IC / RankICIR / 正日比例 |
|---|---|---|---|
| 2021 | .05045 / .14158 / 56.22% | -.05076 / -.15121 / 42.49% | -.04663 / -.14430 / 44.21% |
| 2022 | .04620 / .12296 / 53.88% | .03440 / .08670 / 51.29% | .05600 / .15417 / 56.03% |
| 2023 | .02500 / .07523 / 49.14% | -.04036 / -.11497 / 45.26% | -.01877 / -.05914 / 46.12% |

等权跨折 Rank IC / RankICIR / positive-day ratio：Raw `.04055 / .11326 / 53.08%`；Pool Excess `-.01891 / -.05983 / 46.35%`；CS Rank `-.00313 / -.01642 / 48.79%`。跨折 Pearson IC 均值分别为 `.06755 / -.00661 / .00926`。Pearson、rank daily IC、NW lag=5 standard error/t、月度和季度 Rank IC 均写入 report。NW 仅用于重叠标签相关性诊断。

Native Target MAE/RMSE（均明确为 `NOT CROSS-TARGET COMPARABLE`）：Raw `.04405/.05740, .04253/.05486, .03144/.03988`；Pool Excess `.03507/.04662, .02815/.03781, .02043/.02795`；CS Rank `.51167/.59718, .49841/.58082, .50685/.58571`。Rank score 对 raw return 的 common investment metrics 不计算 MAE/RMSE。

Target diagnostics：Pool Excess 在 1,452 个可用预测日上的 maximum absolute daily mean 为 `1.77e-17`；CS Rank 的同日顺序在 1,452 日都与 raw realized return 单调一致。11-stock截面按指定 `rank_pct` 变换后的 rank target 日均值为 `.09091`，实际 min/max `-.81818 / 1.0`，与平均秩离散端点一致。

按预定候选条件评估，Pool Excess 和 CS Rank 都只有 1/3 folds 的 Rank IC 为正，跨折 mean Rank IC 与 positive Rank IC day ratio 也都低于 Raw control，故都不符合升级条件。最终为 **`no_target_upgrade`**、选中保留 `target_5d_h5_v1`。该结果不是回测或投资有效性判断。

报告 `artifacts/reports/target_comparison_28b36e885c5a.json`，逐日 target 构造检查 `artifacts/reports/target_cross_section_diagnostics_28b36e885c5a.csv`。Target B/C datasets 分别位于 `data/processed/ashare_research_v1_target_pool_excess_dev.parquet` 和 `data/processed/ashare_research_v1_target_cs_rank_dev.parquet`，Raw version 为同前缀 `target_raw_dev.parquet`；文件和实验模型都由 `.gitignore` 排除。

### 自动化测试与边界

- 新增 11 项离线 target-study 测试；全套 **92 passed / 0 failed**。覆盖 Raw Target 不变、Pool Excess 定义与日均值、CS rank tie/单调、最小截面、label end 和 feature 不变、duplicate/目标输入保护、2024 行过滤/target dataset 幂等、common-key 一致性、unified score-vs-raw IC、native metrics 非可比标记、固定参数和未使用 holdout 守卫。
- `compileall` 通过；`uv pip check`：107 packages compatible；target-study CLI help 与真实三目标命令运行成功。
- Baseline v1 负 Legacy Test、Step4.3 v2 negative Rank IC、qfq non-PIT 和 11 股工程池 survivorship bias 都继续保留。未扩 Universe、未看 2024/2025 或 2026+、未回测、未调参、未换模型、未进入 AI layer。

Step 4.4 达到停止条件，到此暂停。

## Step 4.5R.2（2026-10-05）

在用户授权的项目级范围内，通过独立 `trust_env=False` 会话获取 SSE 主板、科创板和 SZSE A 股公开清单，验证行数分别为 1,702、618、2,904，写入原始文件、来源元数据和 SHA-256，并生成 5,224 条标准化 Security Master。扩展 SZSE `302` 代码段映射后，三源均无重复证券代码。按固定 seed、交易所/行业分层哈希和原 U11 锚点规则冻结 U50/U100；完整 3,225 个 eligible candidate 排序已保留以支持后续 deterministic replacement。BaoStock 0.9.4 `query_stock_basic(code=...)` 为 U100 的 100 只股票各返回一条记录，IPO 日期与交易所名单 100/100 一致；它不参与选股，行业仍采用交易所来源。选股不使用收益/模型字段，current snapshot 的幸存者偏差仍 unresolved。新增 8 项离线测试，全套 100 项通过，`compileall` 通过；将 uv 缓存指向项目内 `.uv-cache` 后，107 个依赖全部兼容。没有行情回填、Legacy Test/Holdout 访问或模型运行。

## Step 4.5A（2026-10-05）

- 将尚未提交的 Step 4.5R.2 修改保存为本地检查点 `1edd013`；U50/U100 hash、3 个官方来源文件 SHA-256 与 Security Master 检查通过。`.env`、`data/`、数据库和 artifacts 处于忽略状态。
- 在现有 backfill 逻辑中修复按 frozen universe order 处理批次；为 BaoStock `tradestatus=0` 行增加显式 `provider_reported_nontrading` 分类。Raw 原样保留，非交易记录不写入新 canonical 成交行/研究观察，不合成 volume/amount。缺行停牌原因仍 Unknown。调整口径的完成判断只允许精确的 source/adjustment 行与 no-trade metadata 证明覆盖，避免 qfq 元数据错误跳过 none。
- Pilot：冻结顺序首 3 个新增 symbol 的 none/qfq 均成功，重复请求均跳过。之后 39 个新增成员的两个回填 job 均 39/39 完成；U11 anchors 无新行情请求。每个口径有 4 次初始校验拒绝（12 行，均为 `tradestatus=0` 的空量额），在保留 raw 后按已验证 no-trade 规则重试成功。
- 完成 U50 逐股/年度行情覆盖检查与 qfq development-only 数据集。71,622 行，68,672 完整特征，71,372 有效标签；train/validation 56,072/11,850；宽度门禁通过。qfq 的 50 个证券快照时间各不相同，仍标记 NOT_POINT_IN_TIME。
- 自动化测试 106/106 通过，`compileall`、`git diff --check` 通过，Python 3.14.6 环境 `uv pip check` 检查 107 个包兼容。未训练模型，未访问实际 Legacy Test 或 Sealed Holdout。一次初始预检聚合查询读取了旧 U11 记录的 max-date 元数据（至 2025-12-31），未读取/分析其 OHLC、收益或指标；之后的 U50 数据查询和数据集严格截止 2023-12-31。完整记录：[`step_4_5A_u50_data_validation.md`](step_4_5A_u50_data_validation.md)。

## Step 4.5B（2026-10-05）

- 先以 `eb8d9ec chore: checkpoint step 4.5A U50 historical data readiness` 保存 Step 4.5A；没有 push。验证正式冻结 U100=100（SSE/SZSE 各 50）、U50=50（各 25）、U11 anchors 全部保留、无重复且 frozen selection metadata/hash 不变。新增任务配置仅列 U100−U50 的 50 个 additions。
- 依 frozen order 先完成 3 只 none/qfq pilot，再分批回填新增成员。BaoStock none 与 qfq 使用隔离 job，各 50/50 完成；`603900.SH` 的有界请求失败后暂停批次并仅重试该证券，之后成功。U50 历史数据复用，没有重复下载。
- 对 U100 两种 adjustment、200 个证券口径分区执行 2018–2023 质量审计。每种口径按交易所 sessions 统计 145,595 expected、143,911 actual trading observations、1,684 provider-reported no-trade、0 missing response、0 unknown gaps。961 条较早 canonical bars 与当前 no-trade 元数据重叠，保留在库但不计为成交观察，研究集按 `tradestatus=1` 筛选。未合成量价字段或推断停牌原因。
- 构建独立 qfq development dataset：143,911 行、100/100 完整 feature 与有效 target 股票、137,511 行同时有完整 Feature/Target；SHA-256 为 `1814347050b9a35d23148f72a52355f40523ab699fdb1f5d9a2a4c76f1d1cf02`。逐股 qfq snapshot 时间不同，标记 `mixed_vendor_snapshot_times=true` / `NOT_POINT_IN_TIME`。
- U100 breadth gate：1,393 活动日期的 min/P10/median/P90/max=83/98/99/100/100；≥80 宽度覆盖 100%，≥90 覆盖 96.84%；状态 `u100_data_ready`。修改含 service/backfill/store/dataset/CLI 的显式 development date cap，并添加 U100 quality、breadth 与边界测试。
- 全套 111 项测试通过，`compileall`、`git diff --check` 通过；Python 3.14.6，uv 检查 107 个包兼容。无训练、walk-forward、回测、Legacy Test 或 Sealed Holdout 评估。逐股证据和命令见 [`step_4_5B_u100_data_validation.md`](step_4_5B_u100_data_validation.md)。Step 4.5B 文件尚未提交。
