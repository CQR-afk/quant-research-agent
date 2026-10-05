# Step 4.7 — PIT Price & Historical Universe Feasibility Audit

**研究角色：** `DATA_FOUNDATION_FEASIBILITY`  
**Historical Universe：** `historical_universe_partially_feasible`  
**PIT Price：** `pit_price_partially_feasible`  
**综合决定：** `data_foundation_upgrade_partial`；`historical_data_foundation_not_verified`

本报告只评估数据来源和小型证券区间样本。没有获取新行情、训练模型、重算 IC、修改 U11/U50/U100、读取 2024–2025 `LEGACY_OBSERVED_TEST` 或访问 2026+ sealed holdout。原 Step 4.2–4.6 实验、预测和治理结论保持不变。Step 4.6 工作区经 132 项测试、`compileall`、`git diff --check`、107 个依赖兼容和忽略规则检查后，建立本地检查点 `e385264 chore: checkpoint step 4.6 signal failure diagnostics`；没有 remote 或 push。

## Historical Universe 来源审计

现有 [SZSE 当前 A 股公开列表](https://www.szse.cn/market/product/stock/list/index.html) 于 2026-10-04T14:52:01Z 获取，返回 2,904 行，原始 xlsx SHA-256 `4792fa87b34b4659619f99d38b11f110a8949bf09df5a66919335c1675c93515`。规范化 Security Master 包含 `000001.SZ`，来源字段给出上市日 1991-04-03；列表不含 `002618.SZ`。这一差异本身不能枚举所有历史退市股，也不能证明 000001.SZ 在任一历史日的持续上市状态。SSE 的[暂停/终止上市公司页面](https://www.sse.com.cn/assortment/stock/list/delisting/)也存在，但本轮没有取得可核验、覆盖全市场的历史快照和完整分页，因此未把它视为完整历史名单。

对真实退市样本 `002618.SZ`，深交所托管的[2013 年公司披露文件](https://disc.static.szse.cn/disc/disk01/finalpage/2013-03-12/71546c34-1ec4-49c7-8223-d803baa27521.PDF)载明上市日 2011-09-20，深交所[原上市公告](https://www.szse.cn/disclosure/notice/company/t20110919_508659.html)也给出该日。2022 年[摘牌公告](https://disc.static.szse.cn/disc/disk03/finalpage/2022-06-22/401dca46-84da-4048-8012-ad15c1dc9425.PDF)载明证券为人民币普通股、最后交易日 2022-06-21、摘牌日 2022-06-22。两份 PDF 已实际保存并校验 `%PDF-` 文件头，SHA-256 分别为 `26acfaef2975acb8ab0804961792b54542b3196d27efbd61d79555c3a88acfcc` 与 `fe09378fd3facc5f6e0fc05018bb87fcc41a21ccb92216e3633b0c058b3ea5c5`。`www.szse.cn` 上市公告的本地直连返回 `ConnectionError`，故本地可重验文件采用 2013 披露 PDF；公告网页只作为交叉证据。

BaoStock 0.9.4 的 `query_stock_basic(code=...)` 在独立进程和 20 秒看门狗下，对两个已知代码都登录和查询成功，各返回一行，字段为 `code, code_name, ipoDate, outDate, type, status`：

| 代码 | ipoDate | outDate | type | status | 解释边界 |
|---|---|---|---|---|---|
| `sz.002618` | 2011-09-20 | 2022-06-22 | `1` | `0` | 与官方上市和摘牌文件日期一致；只证明该已知代码可单股补充元数据。 |
| `sz.000001` | 1991-04-03 | 空 | `1` | `1` | 与当前交易所名单的上市日一致；空 `outDate` 不证明历史期间从未中断或退市。 |

两次查询分别耗时 2.293 秒、2.767 秒，错误码均为 `0`。原始响应包装及 SHA-256 保存在机器报告；供应商中文名称在本机 SDK 输出中出现编码损坏，股票识别和区间判断只使用代码、日期、类型与状态，并以官方来源交叉检查。没有执行 BaoStock 无界全量 `query_stock_basic()`。

[Tushare `stock_basic` 文档](https://tushare.pro/document/2?doc_id=25)列出 `list_status=D`、`list_date`、`delist_date`，接口权限从 2000 积分起。[`bak_basic` 历史每天股票列表文档](https://tushare.pro/document/2?doc_id=262)称从 2016 年开始并支持 `trade_date`，正式权限需要 5000 积分。本机无 Tushare token，因此这些仅是文档中可调查的候选来源：没有真实返回行、原始响应哈希、覆盖/遗漏率或状态语义验证。尤其不能把它们当作已取得的历史 PIT Universe。

## 两证券区间 pilot

选择规则固定在 `config/step_4_7_pilot_sources.json`：在已核验的“当前交易所名单”类和“官方文件确认退市”类中各取代码字典序首项，再按 symbol 排序。输入只有这两个已核验候选，不代表对全市场完成候选搜索；没有使用价格、收益或模型表现。该 pilot 用于检验 `listing_date <= date < delisting_date` 的**挂牌区间**语义，不断言当日有成交、可交易、未停牌或不存在重新上市事件。

| 证券 | 研究日 | `listed_on` 结果 | 理由 |
|---|---|---|---|
| `002618.SZ` | 2011-09-19 | `not_listed` | 官方上市日前。 |
| `002618.SZ` | 2018-12-31 | `listed_interval_not_trading_status` | 位于已证实的上市与摘牌边界之间。 |
| `002618.SZ` | 2022-06-21 | `listed_interval_not_trading_status` | 摘牌前的公告所列最后交易日。 |
| `002618.SZ` | 2022-06-22 | `not_listed` | 官方摘牌日，结束边界不包含该日。 |
| `000001.SZ` | 2018-12-31 | `unknown` | 只有 2026 当前名单及单股元数据，缺历史状态快照。 |
| `000001.SZ` | 2026-10-04 | `listed_at_snapshot` | 当次官方名单快照可证。 |

这一结果表明已知退市证券的基本日期区间可以重建，BaoStock 可按已知代码补充真实 `outDate`。仍缺少覆盖 2018–2023 全部 A 股（尤其历史退市与重上市）的可核验枚举、状态变迁和历史快照。`historical_universe_partially_feasible` 是 pilot 级结论；幸存者偏差没有解决。

## PIT Price 与公司行动来源审计

当前工程保存的 BaoStock `none` 是未复权供应商历史价格；qfq 继续为 `vendor_snapshot_adjusted / NOT_POINT_IN_TIME`。本阶段没有打开行情行。BaoStock 0.9.4 的 `query_dividend_data(code="sh.600519", year=2023, yearType="operate")` 单次真实返回错误码 0、1 行，字段包括预案/股东大会/实施公告日、登记日、除权日、派息日、税前/税后现金股利、送股与转增。该行的实施公告日为 2023-06-26，除权日为 2023-06-30，税前每股现金股利为 25.911 元；响应是在 2026-10-05 获取，**没有记录 2023 当时那份数据版本**。本轮仅查询了该参数组合，不能据一行判断 BaoStock 全年公司行动的完整覆盖。

`query_adjust_factor(code="sh.600519", start_date="2023-12-18", end_date="2023-12-22")` 返回错误码 0、1 行：`dividOperateDate=2023-12-20`、`foreAdjustFactor=0.908734`、`backAdjustFactor=6.969311`、`adjustFactor=6.969311`。该行的**事件日期**不是供应商在 2023-12-20 当时发布的版本时间；本轮将其严格标记为 `historical_factor_snapshot`。两次 BaoStock 调用耗时分别为 5.264 秒和 1.734 秒，各自原始记录及哈希见下表与 JSON。

交易所的[贵州茅台 2023 年特别分红实施公告](https://www.sse.com.cn/disclosure/listedinfo/announcement/c/new/2023-12-14/600519_20231214_WR1C.pdf)在公开检索页面显示公告日期 2023-12-14、每股现金红利 19.106 元及 2023-12-20 除权日；但本机向静态 PDF 地址的直接请求只获得 HTML 访问挑战，保存的 7,373 字节**不是 PDF**。没有绕过访问限制，故这份公告没有进入本地可重验的价格重建输入。

[Tushare `adj_factor` 文档](https://tushare.pro/document/2?doc_id=28)只公开 `ts_code, trade_date, adj_factor`，说明提供按历史交易日取得的因子，并未给出每个历史因子“当时已发布版本”的字段。[`dividend` 文档](https://tushare.pro/document/2?doc_id=103)提供公告、实施公告、登记与除权日，以及现金/送转数量；但本机无 token，无法验证权限、修订历史和事件完整性。文档性信息不能替代实际 API 响应，`trade_date` 也不能充当版本时间。现金股利、送股、转增及配股的全套有效日与公告版本链尚未验证。

由于没有可重验的逐时点公司行动版本和完整事件链，本阶段**没有运行价格重建 pilot**，没有比较 none、重建序列和 qfq，也没有设计自有复权公式。`pit_price_partially_feasible` 表示部分原始事件和供应商因子来源可访问；它绝不表示已得到 PIT 调整价格。

## 来源清单与限制

| 来源 | 实际字段或内容 | 本轮行数 | SHA-256 | 取回时间 UTC / 限制 |
|---|---|---:|---|---|
| SZSE 2026 A 股 xlsx | 代码、简称、上市日期等 | 2,904 | `4792fa87b34b4659619f99d38b11f110a8949bf09df5a66919335c1675c93515` | 2026-10-04 14:52:27；当前名单。 |
| SZSE 托管 2013 上市资料 PDF | `002618`、上市日 | 1 个 pilot 提取 | `26acfaef2975acb8ab0804961792b54542b3196d27efbd61d79555c3a88acfcc` | 2026-10-05 03:20:38；非全市场名录。 |
| SZSE 托管 2022 摘牌公告 PDF | `002618`、证券种类、最后交易日、摘牌日 | 1 个 pilot 提取 | `fe09378fd3facc5f6e0fc05018bb87fcc41a21ccb92216e3633b0c058b3ea5c5` | 2026-10-05 03:20:05；非全市场名录。 |
| BaoStock 单股 `002618` | `code, code_name, ipoDate, outDate, type, status` | 1 | `51154cf99218e97ea9cf2bc0761dacce9672ceaca17f16ac8dd09f7016b446dc` | 2026-10-05 03:22:04；已知代码单股。 |
| BaoStock 单股 `000001` | 同上 | 1 | `645fff063f161dc8f14dcdbe58003e8eafc8f96eb76f4018faa3a4e44d655084` | 2026-10-05 03:22:07；已知代码单股。 |
| BaoStock 2023 分红 probe | 14 个源字段，含公告与除权日期 | 1 | `da165a7483a79a0553b9e9b2836c3c2e6f0492713484eadbb8711fb2c8f4a390` | 2026-10-05 03:22:53；当前供应商返回。 |
| BaoStock 2023-12 因子 probe | `code, dividOperateDate, foreAdjustFactor, backAdjustFactor, adjustFactor` | 1 | `7a49608a249f0e16facc906a0aa6815fa34282ffd9025a73d907ce8dad7b6bb9` | 2026-10-05 03:22:55；历史因子当前快照。 |
| SSE 2023 公告本地访问响应 | HTML 挑战，未得到 PDF | 0 | `6a43d991661ce8d00ac91549362dfcbc0ce093e67db8999ccb205cbb5ebfa515` | 2026-10-05 03:23:15；不能用于重建。 |

`config/step_4_7_pilot_sources.json` 固定官方 URL、源组织、原始文件路径、实际 SHA-256、取回时间、字段和抽取范围。机器报告 `artifacts/reports/data_foundation_feasibility_step_4_7.json` 另记录 BaoStock 错误码、版本、耗时、原始字段、所有区间检验、文档性候选接口及覆盖限制。文档性 Tushare 资料只记录接口和字段，其 `row_count` 与 `sha256` 为 `null`，明确表示**本轮没有实际取回数据**。

## 已知目标期限限制与决定矩阵

Step 4.6 已确认 `target_5d_h5_v1` 是每股**后续五个有效成交观察**，不是五个固定交易所 session。U100 有标签样本 143,411 行，其中 377 行超过五个交易所 session（约 0.2629%）；最大日历跨度 150 日，最大 session 距离 100。本轮未改目标、未删极端跨度行。可执行持有期需未来独立研究。

| 维度 | 状态 | 已验证证据 | 尚缺条件 |
|---|---|---|---|
| Historical Universe | `historical_universe_partially_feasible` | 一只官方退市样本的上市/摘牌边界与 BaoStock 单股 `outDate` 一致；当前名单提供在市样本。 | 2018–2023 全市场历史/退市枚举、重上市与逐日状态覆盖。 |
| PIT Price | `pit_price_partially_feasible` | BaoStock 可返回公司行动字段和历史因子日期；公告类来源存在。 | 当时可见且可重验的事件/因子版本、完整现金/送转/配股链、明确公式与重建校验。 |
| 合并 | `data_foundation_upgrade_partial` | 两条来源路径都有小范围可继续调查的证据。 | **`historical_data_foundation_not_verified`**；不能宣称现有研究已有 PIT 数据基础。 |

下一阶段建议先取得具有访问权限且可核验的历史每日证券名单或官方年度退市资料的完整覆盖，再交叉核对 BaoStock 单股 `outDate` 与重上市情况；同时建立可按公告时间回放的公司行动事件版本及方法文档。完成源覆盖、版本和单位验收后，才可单独申请极少数证券的价格重建 pilot。本阶段停在数据基础审计，不启动新的模型或策略实验。

## 运行与验证

```powershell
.\.venv\Scripts\python.exe -m quantlab.research.data_foundation
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
uv --no-cache pip check --python .\.venv\Scripts\python.exe
```

只读报告命令返回 `data_foundation_upgrade_partial`；相同来源再次运行返回 `reused=true`，不会改写既有 JSON。新增 10 项离线测试覆盖上市/退市边界、未知状态、来源哈希与格式、deterministic pilot、报告复现、当前与历史区分、因子版本用语以及训练/holdout 读取路径保护。Fixture 只证明本地逻辑；真实来源结论仅来自上表已实际获取并保存的原始证据。全套自动化测试实际执行 142 项，全部通过；`compileall`、`git diff --check` 通过，uv 检查 107 个包兼容。Python 版本为 3.14.6。
