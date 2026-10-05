# Step 4.5B — Frozen U100 Historical Backfill & Data Quality Gate

执行日期：2026-10-05。结论：**`u100_data_ready`**（仅 development data readiness；没有训练模型、运行 walk-forward 或回测）。

## 检查点与冻结池

开始时 HEAD 为 `1edd013`（Step 4.5R.2 checkpoint）。未提交工作区的 12 个文件均为预期 Step 4.5A 代码、测试和文档；`.env`、`.venv`、`data/`、数据库、行情文件、模型和 artifacts 均由 `.gitignore` 排除，没有被跟踪。Python 为 3.14.6；106 项基线测试通过。默认 uv 缓存目录遇到访问拒绝后，以 `uv --no-cache pip check --python .\.venv\Scripts\python.exe` 重查，107 个包全部兼容。随后先建立本地提交 `eb8d9ec chore: checkpoint step 4.5A U50 historical data readiness`，未配置远程、未 push。

冻结文件保持原样，成员哈希以冻结顺序中的 `symbol` 以换行连接后计算 SHA-256：

| 池 | 成员 | 交易所数量 | 验证哈希 |
|---|---:|---|---|
| U50 | 50 | SSE 25 / SZSE 25 | `d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218` |
| U100 | 100 | SSE 50 / SZSE 50 | `05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9` |

U50 完整包含于 U100；U11 anchors 全部保留；无重复 symbol。两份冻结名单的 selection seed、candidate source hash 和 candidate `selection_order` 完全一致。未重新排序或按行情、收益、模型结果改选。新任务配置为 [`config/ashare_universe_breadth_100_additions_2018_2023_v1.json`](../config/ashare_universe_breadth_100_additions_2018_2023_v1.json)：U100 减 U50 共 50 只，按 U100 frozen member order 排列；包含 parent/reused hash、候选顺序、日期范围、创建时间和来源 provenance。U50 的历史数据从 DuckDB 复用，没有再次请求。

## 回填与 Pilot

范围固定为 `2018-01-01` 至 `2023-12-31`，Provider 为 BaoStock 0.9.4 / `query_history_k_data_plus`。none 与 qfq 使用不同的回填 job 和 adjustment partition，分别检查 BaoStock `adjustflag=3` 与 `adjustflag=2`。50 只新增成员每次最多 5 只；任务以 DuckDB checkpoint 恢复，失败股票可单独重试。

Pilot 为冻结顺序的前三只新增证券 `000532.SZ`、`603183.SH`、`000591.SZ`。两个 adjustment 均成功。三只的交易所日历均为 1,457 个有效 session；`603183.SH` 每种 adjustment 有 1,451 条 active bar 和 6 个 BaoStock `tradestatus=0` 日期，其余各有 1,457 条 active bar。Repeat download 对三只 none 均返回 `skipped`、0 fetched/updated rows。

其余批次出现一次隔离的 none 请求失败：`603900.SH` 首次三次有界尝试失败，因此暂停下一批；只重试该失败股票后成功。最终该股有 1,457 个 raw response 日期、1,456 个 active observation 和 1 个 provider-reported no-trade session，无拒收行。qfq 任务在单独 job 中完成。最终 scoped status 为：

| Adjustment | Job ID | 完成 | 失败 | 待处理 |
|---|---|---:|---:|---:|
| none | `8b3a5117-6584-4ffe-b1ad-ee6c56032f78` | 50/50 | 0 | 0 |
| qfq | `d28379f2-d417-448d-ad9d-b1eca03b3228` | 50/50 | 0 | 0 |

## 数据质量、no-trade 与来源

新增 `quantlab research u100-quality` 审计对全部 U100、两种 adjustment 逐股票执行，共 200 个 source/adjustment partitions。DuckDB 查询都明确限定 symbol、market、source、adjustment 和 `trade_date BETWEEN 2018-01-01 AND 2023-12-31`；raw Parquet 按 adjustment 单独检查，并对重叠采集尝试使用该日期最新的成功 Provider 响应进行 session 分类。BaoStock 状态不从另一个 adjustment 的共享 metadata 推断。

| 每个 adjustment 汇总 | 记录数 |
|---|---:|
| Expected eligible XSHG sessions（按 listing date 起算） | 145,595 |
| Latest successful raw response 所报 active trading observations | 143,911 |
| Provider-reported `tradestatus=0` no-trade sessions | 1,684 |
| Missing provider-response sessions | 0 |
| Unknown gaps | 0 |
| 质量通过 partitions | 100/100 |

`143,911 + 1,684 = 145,595`。因此 expected missing 为 0 并不表示每个日期都有成交。no-trade raw 记录及 `provider_reported_nontrading` issue 保留；未伪造 OHLC、成交量、成交额或停牌原因。

DuckDB 中仍保留有 961 条早期已写入的 bar 与该证券当前成功 Provider 响应的 `tradestatus=0` 日期重合。这些记录没有被删除或算作当前实际成交观察；研究数据集沿用既有 builder 语义，仅保留 BaoStock `tradestatus=1`，因此这些旧记录不进入 Feature/Target 样本。质量 CSV 同时列出每个 symbol/adjustment 的该项计数，便于逐股复核。

两种 adjustment 的 OHLC 均有限且关系正确；volume/amount 有限、非负；canonical duplicate 数为 0；raw latest response adjustment flag 正确；无缺失 listing date、超 listing/研究范围的有效行情或 latest raw response 的重复 session。两种 adjustment 的实际交易观察日期数在 100/100 股票上相同。历史失败/拒收尝试仍保留在 ingest ledger：全 U100 日期范围内 none 记录 5 次、qfq 4 次，包含 4+4 次 Step 4.5A 已识别并恢复的 no-trade/缺 amount 初始拒收，以及 `603900.SH` 一次已恢复的网络失败；当前两个正式 job 均 0 失败、0 待处理。

`data/processed/ashare_u100_symbol_quality_2018_2023.csv` 与 `data/processed/ashare_u100_data_quality_2018_2023.json` 保存逐股结果和摘要（均被 `.gitignore` 排除）。摘要状态为 **`u100_data_ready`**。逐股行还记录 first/last valid observation、expected/active/no-trade/missing/unknown sessions、attempt/rejection、issue types、来源版本、接口名称、复权旗标与 snapshot time。

## U100 development dataset 与 breadth gate

新建文件 [`data/processed/ashare_research_u100_qfq_v1_dev_2018_2023.parquet`](../data/processed/ashare_research_u100_qfq_v1_dev_2018_2023.parquet)，没有覆盖 U11/U50 数据集、Baseline v1 或 Step 4.3/4.4 产物。

| 字段 | 实际值 |
|---|---|
| Universe / reused U50 hash | `05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9` / `d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218` |
| Provider / adjustment | BaoStock / qfq |
| Feature / target | `ashare_daily_v1` / `target_5d_h5_v1` |
| Evaluation / cap | `development_only` / `2023-12-31` |
| Dataset rows | 143,911 |
| 完整 Feature 行 | 138,011（100/100 股票） |
| 有效 Target 行 | 143,411（100/100 股票） |
| 完整 Feature 且有效 Target 行 | 137,511 |
| Prediction date / 最大 label end date | `2018-01-02` – `2023-12-29` / `2023-12-29` |
| Dataset SHA-256 | `1814347050b9a35d23148f72a52355f40523ab699fdb1f5d9a2a4c76f1d1cf02` |

Manifest 记录逐股 adjusted snapshot timestamp（100 个不同时间）、`mixed_vendor_snapshot_times=true`、来源版本/接口、数据版本、Universe 和 reused-U50 hash，以及 `NOT_POINT_IN_TIME`。U50 在 2026-10-04 获取、U100 additions 于 2026-10-05 获取，因此不能把全池描述为同一复权快照。

breadth 的活动预测日定义为存在完整 Feature 且有有效标签的日期。1,393 个活动日期的有效横截面 min/P10/median/P90/max 为 **83 / 98 / 99 / 100 / 100**。固定门槛 80 股的日期为 1,393/1,393（100%）；90 股为 1,349/1,393（96.84%）。100/100 数据、完整特征、有效 target 与两个日期比例检查全部通过，Gate 为 **`u100_data_ready`**。没有根据结果降低门槛。

## 日期防护、CLI 与测试

新增 development-only 范围检查，范围为 `2018-01-01..2023-12-31`：行情 download/update/backfill、按范围聚合的 status、研究数据集 builder、U100 quality/breadth 均拒绝越界。增量 update 的 latest-date 查询也带日期范围，不会读全库旧 U11 的最晚日期。Prediction date 与 label end date 都受 2023-12-31 上限检查。旧 Legacy 行情保留；本阶段没有读取/评价 2024–2025 Legacy Test，也没有访问 2026+ Sealed Holdout。

实际执行命令：

```powershell
quantlab data backfill --universe config/ashare_universe_breadth_100_additions_2018_2023_v1.json --start 20180101 --end 20231231 --adjustment none --provider baostock --limit 3 --development-only
quantlab data backfill --universe config/ashare_universe_breadth_100_additions_2018_2023_v1.json --start 20180101 --end 20231231 --adjustment qfq --provider baostock --limit 3 --development-only
quantlab data backfill-status --universe config/ashare_universe_breadth_100_additions_2018_2023_v1.json --start 20180101 --end 20231231 --adjustment none --provider baostock --development-only
quantlab data status --universe config/ashare_universe_breadth_100_additions_2018_2023_v1.json --start 20180101 --end 20231231 --provider baostock --adjustment none --development-only
quantlab research build-dataset --start 20180101 --end 20231231 --adjustment qfq --provider baostock --universe config/ashare_universe_breadth_100.json --output data/processed/ashare_research_u100_qfq_v1_dev_2018_2023.parquet --feature-version ashare_daily_v1 --development-only --reused-universe-hash d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218 --summary-only
quantlab research u100-quality --universe config/ashare_universe_breadth_100.json --start 20180101 --end 20231231
quantlab research u100-breadth --dataset data/processed/ashare_research_u100_qfq_v1_dev_2018_2023.parquet --manifest data/processed/ashare_research_u100_qfq_v1_dev_2018_2023.manifest.json
```

完整离线测试 **111 passed / 0 failed**（原 106 项保留，新增 5 项），覆盖冻结关系、U100 固定 breadth 门槛、服务/数据集/status 日期边界；`compileall`、`git diff --check` 通过。`uv --no-cache pip check --python .\.venv\Scripts\python.exe` 检查 107 个包兼容。

qfq 仍是供应商快照调整、不是 PIT；名单来自当前交易所快照，历史退市证券缺失造成的幸存者偏差仍未解决。此结论仅表示开发期数据覆盖与横截面宽度达到预先固定的门槛，不代表模型有效或可交易。本阶段没有训练、walk-forward、调参、策略回测、自动下单，也未进入后续 Step。
