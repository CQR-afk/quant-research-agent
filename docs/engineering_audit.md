# Quant Research Agent 工程审计

审计日期：2026-10-04  
工作目录：`D:\Quantification`  
范围：读取现有源码、检查环境与 Git 状态、运行离线功能和自动化检查；未下载实时行情，未调用 GPT 云端。

## 结论

原型已能下载 Yahoo Finance 日线、构造价格/成交量特征、训练按时间切分的 Logistic Regression，并独立请求 GPT 新闻研究。审计确认原模型最后一个训练标签跨进留出期，形成边界泄漏；该边界已 purge，预测文件也增加标签结束日期。当前模型仍预测下一有效数据日涨跌，不是正式的五日收益模型。

Python 3.14.6 上列出的九个核心/规划依赖均完成导入检查；LightGBM、DuckDB、PyArrow、VectorBT、Numba 还通过了最小离线运算。未发现需要迁移到 Python 3.11 的兼容性问题。AKShare、正式 A/H 股数据覆盖、持久化仓储和回测仍未实现。

## 实际目录

```text
D:\Quantification\
├── .env                         # 本机配置，当前没有 API key
├── .env.example
├── .gitignore
├── README.md
├── pyproject.toml
├── setup.ps1
├── docs\engineering_audit.md
├── src\quantlab\
│   ├── cli.py
│   ├── config.py
│   ├── features.py
│   ├── market_data.py
│   ├── modeling.py
│   ├── news.py
│   ├── data\schema.py
│   └── providers\{base.py,yfinance.py}
└── tests\
    ├── test_environment_compat.py
    ├── test_features_modeling.py
    ├── test_market_schema.py
    └── test_news.py
```

项目同时有 `.venv` 与 `.uv-cache`，两者都在 `.gitignore` 中。没有 `requirements.txt`，依赖由 `pyproject.toml` 管理；没有锁文件。`src\quantlab.egg-info` 是本地安装生成的元数据，并已加入忽略规则。

## Git 与凭据

- `D:\Quantification` 不是 Git repository：`git status --short --branch` 返回 “not a git repository”。因此没有可报告的分支、暂存区或工作区状态，也无法用 Git 判断 `.env` 是否已经被跟踪。
- `.gitignore` 明确含有 `.env` 规则；源码中未找到硬编码密钥。当前 `.env` 的 `OPENAI_API_KEY` 为空，真实 GPT 请求已跳过。
- 旧 API key 曾出现在此前的聊天内容中。是否已在 OpenAI 控制台撤销无法从本地确认；在确认撤销前应视为泄露凭据。本次没有读取或复述该值。
- GPT 模型名从 `OPENAI_MODEL` 配置。新闻请求失败会转成不含原始 SDK 错误文本的提示；本地训练与新闻命令分开执行，云端不可用不会阻止本地训练。
- 成功的新闻响应现在在研究笔记记录来源链接、请求次数和 API 返回的输入/输出/总 Token。没有跨进程的累计调用账本，也不会在失败请求时记录 Token。

## 当前数据与模型行为

原行情源是 `yfinance.download`。原始调用设置 `auto_adjust=True`：开高低收采用 Yahoo Finance 自动复权价格，成交量仍是源数据成交股数；没有成交额、币种、市场、交易所或复权字段，也没有把原始行情保存到本地。此前只落盘留出预测 CSV 与指标 JSON，没有保存拟合模型。

当前特征为：1、5、20 个有效观测收益率，20 日滚动波动率，收盘价相对 5、20、50 日均线，单日成交量变化，以及成交量相对 20 日均量。所有特征只取预测日期当日及之前数据。单一证券的下一个可用数据行定义下一有效交易日；不把周末或缺失行合成交易日。CLI 按代码逐只下载和训练，没有跨股票合并标签。

旧标签是 `close.shift(-1) / close - 1` 的方向分类，评估使用前 80% 训练、后 20% 留出，不随机打乱。Scaler 只在训练集拟合。原切分没有 purge：最后的训练目标使用了留出期第一日收盘价。现已按 `label_end_date < holdout_start` 剔除跨界训练标签，留出预测也保存标签结束日期；指标包括 accuracy、balanced accuracy、Brier score 和未扣交易成本的方向收益。

新增的 `make_forward_target(..., horizon=5)` 按单只证券的有效数据行计算五期收益并保留标签结束日期；遇到多个 symbol 会拒绝跨股票计算。本次没有把它接入正式 LightGBM 训练，也没有完整的训练/验证/测试滚动流程。VectorBT 只做了最小组合计算兼容检查，没有回测现有策略。

## 风险分级与已修正事项

### Critical

1. **旧标签越过训练/留出边界。** 训练样本的下一日结果可能来自测试期，造成评估偏乐观。现已按标签结束日期 purge，并增加自动化边界测试。
2. **旧 API key 曾公开在聊天中。** 本机无新 key，未进行云端请求；本地无法确认旧 key 是否已撤销。需要在 API 控制台确认撤销状态。

### Major

1. **行情源不匹配最终研究优先级。** 当前只有 Yahoo Finance 适配器；没有 AKShare、专业 A/H 股金融数据 API，也未实测 A 股代码在线覆盖。SPY 仅保留为兼容性示例。
2. **数据没有研究仓储。** 行情不保存；原始/清洗数据未分层；DuckDB、Parquet 当前仅做本地兼容检查，没有接入读写。
3. **正式目标与模型尚未替换。** Logistic Regression 继续作为 Baseline Direction Classifier。当前训练仍是下一有效日方向分类；五日目标函数仅有基础实现，没有 LightGBM Regressor 训练器。
4. **评估仍是单一留出切分。** 没有独立验证集、滚动窗口、模型选择或用于五日标签的边界隔离流程；模型文件未保存。当前未扣成本的方向收益只能作诊断。
5. **行情适配后的成交额可能缺失。** 标准 Schema 接受空 `amount`；Yahoo 适配器没有来源成交额时保留空值，不会推算。停牌状态要依赖之后接入的正式交易日历和行情源。

### Minor

1. **目录不是 Git repository。** 无版本历史，且无法实际验证 `.env` 未被跟踪。忽略规则本身已存在。
2. **没有依赖锁文件。** `pyproject.toml` 使用最低版本约束，长期复现仍应增加经过验证的锁定文件。
3. **A 股/港股接入仍需交易所级校验。** Schema 保留字符串代码和市场/交易所字段，可检查 OHLC、日期重复、非负成交量/成交额并报告给定日历中的缺失日期；当前没有内置交易日历，缺失日期不会自动判作停牌。

## Python 3.14.6 环境检查

解释器路径：`D:\Quantification\.venv\Scripts\python.exe`  
Python：3.14.6

| 包 | 实际版本 | 导入与最小功能检查 |
|---|---:|---|
| NumPy | 2.5.3 | 通过；数组计算 |
| Pandas | 3.0.6 | 通过；Series/DataFrame 运算 |
| Scikit-learn | 1.9.1 | 通过；Logistic Regression 拟合与预测 |
| LightGBM | 4.7.0 | 通过；小型 `LGBMRegressor` 拟合与预测 |
| DuckDB | 1.5.6 | 通过；内存 SQL 查询 |
| PyArrow | 25.0.1 | 通过；内存 Parquet 写入与读取 |
| VectorBT | 1.1.1 | 通过；持有组合收益计算 |
| Numba | 0.68.0 | 通过；JIT 函数执行 |
| OpenAI SDK | 3.24.0 | 通过；Responses 客户端接口可用，未发送请求 |
| yfinance | 1.7.0 | 通过导入；Provider 用 mock 数据验证字段映射，未请求在线行情 |
| python-dotenv | 1.2.4 | 通过导入；从本地 `.env` 配置加载 |

以上只证明当前安装组合完成了指定的导入和小型离线操作，不等于大数据量、生产负载或市场数据质量验证。由于所需依赖在 Python 3.14.6 均可完成检查，本次没有安装 Python 3.11，也没有替换或删除当前环境。

## 已完成修正

- `setup.ps1` 复用现有 `.venv`，不再使用 `uv venv --clear` 覆盖环境；量化栈依赖以 `quant-stack` extra 声明。
- Logistic Regression 保留并显式作为基线模型；训练切分按标签结束日期 purge，逐条预测文件包含 `label_end_date`。
- 新增五期收益和结束日期的基础目标函数，单标的计算并拒绝多标的串行混算。
- 新增统一行情 Schema、A 股/港股/美股元数据映射、基础字段和 OHLC 校验、成交量/成交额单位说明以及缺失预期交易日检测接口。
- 保留 Yahoo Finance 兼容层；Schema 中 Yahoo 复权明确标注 `vendor_adjusted`，成交量按股，缺少的成交额留空。
- 新闻研究采用 mock 测试；输出整理来源和 Token 用量，且缺少 API key 时不会构造客户端。
- README 改为反映当前实际状态，未声称 AKShare、仓储、正式 LightGBM 训练或回测已完成。

## 实际执行检查

命令：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

结果：15 项通过，0 项失败。覆盖依赖最小运算、五期目标和结束日期、特征前视检查、标签边界 purge、Schema 单位/前导零/异常校验、缺失交易日检测、Yahoo Provider mock 映射、新闻来源与 Token 记录及无密钥行为。

CLI 帮助命令通过：

```powershell
.\.venv\Scripts\quantlab.exe --help
.\.venv\Scripts\quantlab.exe run --help
```

没有执行行情下载或真实新闻调用，因此 `run` 的线上数据链路、A 股覆盖和 GPT 账户可用性仍未验证。

## 尚未实现与下一阶段

下一阶段可按顺序接入 AKShare 或专业 A 股数据源，验证市场代码与复权/单位；定义原始与清洗 Parquet 分区及 DuckDB 查询层；完成分股票五日标签、带 purge/embargo 的时间验证流程和 LightGBM 回归；再接入 VectorBT 的成本与风险假设。之后才进入 Qwen/Hermes Agent 调度和 Streamlit 研究界面。真实券商接入和自动下单不在项目范围。

## Step 3 补充审计：A 股数据管线（2026-10-04）

以下结果更新上文“AKShare 和仓储留待下一阶段”的描述；Step 2 部分保留为历史基线。

### 当前项目结构（新增与相关源码）

```text
src/quantlab/
  cli.py
  data/{schema.py,symbols.py,validation.py,store.py,service.py}
  providers/{base.py,akshare.py,yfinance.py}
  features.py, market_data.py, modeling.py, news.py
tests/
  test_ashare_data_pipeline.py
  test_environment_compat.py
  test_features_modeling.py
  test_market_schema.py
  test_news.py
docs/
  engineering_audit.md
  development_log.md
```

### Provider、单位与复权隔离

新增 `AKShareProvider`，实际调用 AKShare 1.19.1 的 `stock_zh_a_hist(symbol, period="daily", start_date, end_date, adjust, timeout)`。中文字段 `日期`、`开盘`、`最高`、`最低`、`收盘`、`成交量` 和 `成交额` 映射至统一字段。上游成交量单位为手，标准化后以股表示，按 1 手 100 股换算；成交额按人民币元保留，缺失时不合成。Provider 版本、接口名、来源、采集时间与 ingest id 被记录；超时设为 20 秒，失败最多重试 3 次。`none`、`qfq` 和 `hfq` 使用不同主键和分区，Yahoo Provider 仍保留。

标准 Schema 另含 `provider_version`、`source_interface`、`ingest_id`、`revision_id`、`record_version`、`first_seen_at`、`recorded_at` 和 `row_hash`，用于记录上游版本及后续数据修订。

### 质量校验

校验检查缺失字段、日期不可解析或未升序、重复标的日期、周末交易日期、负数或非有限价格与成交量、OHLC 关系、负数或非有限成交额、代码有效性、市场/交易所映射和复权混用。坏记录仍保存在原始 Parquet，并把问题写到 DuckDB `validation_issues`；有校验问题时整批不会进入正式研究表。缺少交易日历/停牌资料时，日期缺口标为 `Unknown`，不推断为停牌。

### DuckDB、Parquet 与增量更新

原始快照按 `data/raw/ashare/source=.../symbol=.../adjustment=.../ingest_id=....parquet` 存档。清洗数据按 `data/validated/ashare/exchange=.../adjustment=.../year=.../snapshot-....parquet` 写成较粗粒度分区，避免日级小文件。数据库为 `data/database/quantlab.duckdb`，包含 `market_bars`、`ingest_runs`、`validation_issues`、`parquet_manifest` 和 `bar_revisions` 表。研究表主键包含市场、交易所、证券、交易日、来源和复权类型。

相同记录再次导入不产生重复研究记录或 Parquet 快照；变化记录将旧行保存到修订表、增加版本号并记录修订 ID。新 Parquet 快照先写临时文件并原子重命名，再通过 DuckDB 事务更新当前表和清单。失败时已有清单指针不会切换。`update` 默认向前回溯 45 个日历日检测上游历史修订；初次更新默认从 1990-01-01 开始，可指定起止日期或完整刷新。本次真实在线范围严格限于用户指定两只股票，增量流程由离线测试覆盖。

### 环境、Git 与凭据

保留 Python 3.14.6 `.venv`，无环境迁移。Step 3 实际版本为 AKShare 1.19.1、DuckDB 1.5.6、PyArrow 25.0.1；DuckDB 与 PyArrow 已列为项目核心依赖。既有 Step 2 依赖版本与离线最小运算测试仍适用。

凭据扫描未在 `.env` 中发现已配置的 OpenAI 密钥，也未在源码、测试、文档、项目配置及 setup 脚本发现密钥格式文本；仅检查布尔结果及文件名，没有输出密钥值。`.gitignore` 忽略 `.env`、`.venv`、`.uv-cache`、数据、数据库、日志、模型和临时 Parquet。本地 Git 仓库已初始化；没有设置远程、推送或提交行情文件。

### 实际 A 股验证

成功执行：

```powershell
.\.venv\Scripts\quantlab.exe data download --market ashare --ticker 000001.SZ 600519.SH --start 20240101 --end 20240331 --adjustment qfq
```

两只证券各从 AKShare 获取 58 行，58 行通过校验，0 行拒绝。实际交易日期为 2024-01-02 至 2024-03-29。DuckDB 每只股票 58 行，SSE/SZSE Parquet 各 58 行、合计 116 行；原始层两只股票各存一份成功采集快照。`data status`、两只证券的 `data validate` 和 000001.SZ 的区间 `data query` 均实际运行通过。

实时重复抓取尝试过，但第二轮在首只股票处遭遇上游 `RemoteDisconnected`；因此没有把线上重复抓取说成成功。失败运行被记入 `ingest_runs`，先前的成功数据保持完整。重复写入幂等及增量服务验证由无网络自动化测试完成。

### 测试与未完成范围

保留原有 15 项测试，新增 14 项无网络测试，覆盖代码映射、AKShare 字段/单位、复权隔离、幂等、异常 OHLC、缺失成交额、市场和交易所错误、日期顺序与 Unknown 缺口、Parquet 往返、DuckDB 查询、历史修订、增量回溯、上游失败保护、写入失败保护及原始数据保留。实际执行 `python -m unittest discover -s tests -v`：29 项通过，0 项失败。Mock/Fixture 结果不作为实时数据源证据；线上情况按前述单独记录。

本阶段没有接入港股源、正式交易日历/停牌资料、全市场采集、point-in-time 公司行动数据库、LightGBM 正式训练、VectorBT 回测、Dashboard、本地 Agent 或券商。复权历史仍可能由 AKShare 上游修订；当前保存采集和版本元数据，但不还原历史时点可见的公司行动。

依赖一致性额外检查：`uv pip check --python .\.venv\Scripts\python.exe` 检查 98 个安装包，全部兼容。

## Step 4.1 多数据源实现审计（2026-10-04）

### 代码与环境实际状态

当前工作区在 Step 4 检查点 `e82f4c2 chore: checkpoint step 4 research dataset and lightgbm pipeline` 之上包含 Step 4.1 增量修改；无 Git remote、无 push。项目仍使用 `.venv` Python 3.14.6。项目环境导入实际版本：NumPy 2.5.3、Pandas 3.0.6、scikit-learn 1.9.1、LightGBM 4.7.0、DuckDB 1.5.6、PyArrow 25.0.1、AKShare 1.19.1、BaoStock 0.9.4、Tushare 1.4.29、exchange-calendars 4.13.2。Tushare 与 BaoStock SDK 已安装；没有更换系统 Python。`uv pip check` 检查 107 个已安装包、全部兼容，`compileall` 通过。

`.env` 仍在 `.gitignore`，没有被 Git 跟踪。密钥检查不输出值，只汇报是否匹配。Tushare 环境当前没有配置 token；CLI 返回 `unavailable: missing credential`。没有执行 Tushare 行情或 `adj_factor` 云端请求，因此权限状态未知。OpenAI 仍未真实调用。

### Provider 实现和口径

| Provider | 当前实现 | SDK/凭据 | 支持市场/复权 | Step 4.1 真实请求 |
|---|---|---|---|---|
| Tushare Pro | 日线标准化、独立复权因子表、权限异常脱敏 | SDK 1.4.29；需要 `TUSHARE_TOKEN`，本机未配置 | CN_A / none；独立 factor 接口 | 未尝试，缺 token |
| BaoStock | 日线、raw/validated、元数据、none/qfq/hfq | SDK 0.9.4；无需 token | CN_A / none、qfq、hfq | 成功，短区间和历史回填 |
| AKShare | 原有 Provider 保留 | SDK 1.19.1；无 token | CN_A / none、qfq、hfq | 短对账失败，代理连接被拒 |
| Yahoo Finance | 原兼容 Provider 保留 | yfinance 1.7.0；无 token | US、CN_A、HK / none、vendor_adjusted | 本阶段未联网请求；原有 SPY/Mock 接口保留 |

BaoStock 日线 volume 使用股、amount 使用人民币元；返回 `adjustflag` 与请求复权模式校验，`tradestatus`/`isST` 以 provider metadata 单独保存。Tushare `daily.vol` 是手、`amount` 是千元，标准化转换为股/元；`adj_factor` 不覆盖 base OHLC。此处单位与 endpoint 能力依据 [Tushare daily 接口](https://tushare.pro/document/1?doc_id=27)、[Tushare 复权因子接口](https://tushare.pro/document/2?doc_id=28) 和 [BaoStock SDK 文档](https://pypi.org/project/baostock/)；适配器实现映射通过离线 Fixture 验证。AKShare/HFQ/QFQ 历史仍是可修订的 provider snapshot，不构成 point-in-time safe 序列。

### 新增接口和存储

- `ProviderCapabilities` 将日线、原始价格、市场、复权类型、复权因子、交易日历、停牌状态、股票元数据和凭据需求作为显式能力。`config/data_sources.json` 配置下载和历史回填顺序/请求间隔/每批数量。
- `download_market_data_routed` 与 `update_market_data_routed` 对一个证券的完整日期范围先选单一 Provider；失败留有 ingest 结果后，自动路由可以尝试下一个来源。已保存记录的 key 仍包含 `source` 和 `adjustment`。
- 新增 `provider_bar_metadata`、`adjustment_factors`、`adjustment_factor_revisions`、`backfill_jobs`、`backfill_items`。因子修订留版本；raw bar 不随因子变化。Backfill job 固定 provider 和请求口径，已完成证券跳过，失败可单独重试。
- `build_adjusted_price_view` 返回来源、价格基础、调整方式、调整快照时间；不把供应商复权快照冒称 PIT。查询/研究若遇多个 provider 必须显式选择。对账报告只用于观察差异，不会修改正式库。
- 正常化遇到坏 OHLC/成交量/成交额时，先保留 raw，再让 validation 记录问题并阻止异常记录进入正式表；不补成交量或成交额。停牌未知仍是 `Unknown`。688981.SH 的缺字段属于真实上游数据质量问题。
- `.gitignore` 排除 `.env`、虚拟环境、数据库、行情 Parquet、artifacts、日志和模型文件。Step 4.1 未提交，也没有上传数据。

### OFFLINE TEST RESULT

完整 `python -m unittest discover -s tests -v` 在 Step 4 基线 45 项后新增 16 项 provider/data tests；本阶段最终全量执行结果为 61 passed / 0 failed。新增离线用例覆盖 Tushare 日线单位/无凭据/权限错误脱敏/因子修订、BaoStock 代码和复权字段、来源隔离、auto fallback、adjusted view、真实对账报告结构（由 mock）、回填恢复和失败重试、坏行 raw 归档与拒绝。Provider/gpt 联网不是 unittest 前置条件。

### REAL PROVIDER RESULT

- BaoStock 2024Q1：`000001.SZ`、`600519.SH`、`300750.SZ` 各 58 行，交易日 2024-01-02 至 2024-03-29，各 58 行校验接受、0 行拒绝。
- BaoStock 2018–2025 不复权回填：11/12 成功，完成行数合计 21,257。除 300750.SZ 从 2018-06-11 开始（1,837 行）外，其他 10 只成功标的各有 1,942 行，范围 2018-01-02 至 2025-12-31。688981.SH 请求返回 1,327 条 raw；6 个日期（2025-09-01、09-02、09-03、09-04、09-05、09-08）成交量与成交额为空，标准化后分别出现缺字段问题，正式研究库接收 0 行。未猜测该证券的上市时间或停牌状态。
- AKShare/BaoStock 000001.SZ 短区间/none 对账：BaoStock 得到 58 行；AKShare 3 次请求被配置中的本机代理地址 `127.0.0.1:9` 拒绝，报告状态 blocked，见 `artifacts/reports/reconcile_0bccf96f2f464d029d0db361637e20f1.json`。结果无匹配统计，不声称两个 Provider 数值一致。
- 复权层：既有 AKShare qfq 116 行保留，与 BaoStock none 行隔离；BaoStock qfq/hfq 代码路径通过离线映射测试，但没有在本阶段单独真实下载这两种口径。Tushare adjustment factor 0 行，因为无凭据且无请求。

### 数据集和 Gate

以明确参数构建 `data/processed/ashare_research_dataset_none_v1.parquet`，未覆盖原 Step 4 qfq 数据集。实际得到 21,257 行、21,202 个五期标签、20,598 个完整特征行；池内 11 只标的有数据。独立 `quantlab research readiness` 结果为 PASS：Train 12,491、Validation 2,552、Test 5,280、测试期 480 日期、横截面覆盖 100%，边界 purge/5-session embargo 均通过。报告：`artifacts/reports/readiness_20261004T114455Z_c61aea87.json`。本阶段没有拟合真实 LightGBM，因此没有真实模型指标。

### 已知限制

尚未完成事项：Tushare 凭据/权限验证、有效 Tushare factor snapshot、AKShare 连接恢复和实际双源数值比较、688981.SH 缺失数据问题、BaoStock qfq/hfq 线上验证、港股 Provider、历史股票池/退市样本、point-in-time 公司行动还原、VectorBT、Dashboard、Qwen/Hermes、券商交易。Readiness PASS 是数据可用性门槛，不是策略收益或模型有效性结论。Step 4.1 已按用户要求暂停，不自动进入模型实验。
