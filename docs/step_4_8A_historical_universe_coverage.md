# Step 4.8A — Historical Universe Coverage Validation

**审计角色：** `HISTORICAL_UNIVERSE_COVERAGE_AUDIT`

**最终状态：** `historical_universe_coverage_partial`
**结论：** 已获得真实的交易所年度资料和退市证券证据，足以证明当前名单会漏掉历史证券；仍未取得覆盖 2018–2023 全期、可核验的逐日沪深 A 股名册。

## 范围与检查点

Step 4.7 工作区经核对后保存为本地检查点 `d24055a chore: checkpoint step 4.7 data foundation feasibility audit`，无 remote、未 push。执行检查时 Python 为 3.14.6；Step 4.7 原有 142 项测试全部通过，`compileall` 通过，uv 报告 107 个包兼容。`.env`、`data/`、`artifacts/` 均被忽略且未跟踪。此前 Step 4.7 Markdown 含 Markdown 换行用的行尾空格；提交时 `git diff --cached --check` 报告了这三处继承格式，未修改其原报告。Step 4.7 的四个结论在本阶段保持不变：

- `historical_universe_partially_feasible`
- `pit_price_partially_feasible`
- `data_foundation_upgrade_partial`
- `historical_data_foundation_not_verified=true`

本阶段没有打开行情行、训练模型、重算 IC、评估 Legacy Test、访问 Sealed Holdout、重建 PIT 价格或建立新模型股票池。

## 来源审计

| 来源 | 本次访问与实际证据 | 覆盖范围 | 结论和限制 |
|---|---|---|---|
| 上交所统计年鉴（2023 卷） | 官方公开 PDF 已保存，808 页；SHA-256 `2487b78b…bd6cce13`。选取 PDF 第 40、154 页，识别到 `600209` 的年度证券记录。 | 年鉴展示年度统计，所见为 2022 年证券记录。 | 证明退市证券仍能出现在历史年度材料中，但该年度统计不是完整逐日 roster；全证券行数未解析。 [年鉴来源](https://star.sse.com.cn/aboutus/publication/yearly/) |
| 上交所暂停/终止上市及已退市公司页面 | 两个官方页面可访问，但页面正文未提供可保存、可核对的完整名单记录。 | 公开页面入口。 | 不能据页面存在推断其完整历史覆盖。 [暂停/终止上市公司](https://www.sse.com.cn/assortment/stock/list/delisting/)、[已退市公司信息](https://www.sse.com.cn/services/information/delisting/) |
| 上交所 600209 终止上市决定 | 官方决定页实际保存为 HTML，SHA-256 `2d9c793b…4def20dcfc`，决定 2022-05-17 终止上市。 | 单证券事件。 | 确认终止上市决定；该决定页本身不列确切 IPO 日和摘牌日。 [决定页](https://www.sse.com.cn/disclosure/announcement/listing/stock/c/c_20220518_85433080.shtml) |
| 上交所托管的发行人评级文件 | `600209` 摘牌信息的补充交叉来源，PDF 保存成功；SHA-256 `1441fadd…f16754ed`。 | 单证券。 | 报告复述 2022-06-22 摘牌日；这是交易所托管的二级披露文件，不替代交易所原始摘牌公告。 |
| 深交所市场统计年鉴（2023） | 官方公开 PDF 已保存，1,645 页；SHA-256 `954a7198…be0bb4`。第 1,020 页“除牌证券”表抽取一行 `300336`：上市日 2012-07-10、最后交易日 2023-07-04、退市原因。 | 2023 年除牌证券表的一行 pilot。 | 提供实际可核验的历史退市枚举路径，但本轮没有提取全表，也没有解析 2018–2023 所有年鉴。 [年鉴目录](https://www.szse.cn/market/periodical/year/index.html) |
| 深交所 `300336` 摘牌公告 | 官方托管 PDF 保存成功，SHA-256 `0d860faf…842bed`。载明人民币普通股、最后交易日 2023-07-04、摘牌日 2023-07-05。 | 单证券事件。 | 与年鉴最后交易日和 BaoStock `outDate` 交叉一致。 [公告 PDF](https://disc.static.szse.cn/disc/disk03/finalpage/2023-07-04/c6d9364b-0d82-433f-baee-cb94078309eb.PDF) |
| 深交所 `002618` 上市/摘牌文件 | 复用 Step 4.7 已验证的两份交易所托管 PDF。 | 单证券事件。 | 官方资料确认上市日 2011-09-20、最后交易日 2022-06-21、摘牌日 2022-06-22。 |
| BaoStock `query_stock_basic(code=...)` | 0.9.4 SDK 对 `sh.600209`、`sz.002618`、`sz.300336` 分别在有界独立进程请求成功；每个返回 1 行，字段为 `code, code_name, ipoDate, outDate, type, status`。耗时分别 3.171、此前 Step 4.7 的 2.293、2.022 秒，错误码均为 `0`。 | 已知代码逐股 enrichment。 | 三个例子日期与可交叉的文件相符；不能枚举未知股票、衡量全集遗漏，也不能推断每日可交易状态。代码名称的 SDK 输出有编码损坏，因此不用名称作识别字段。 |
| Tushare `stock_basic` / `bak_basic` | 查询文档；本机环境变量与 `.env` 中没有 Tushare token，因此状态为 `credential_unavailable`，没有发出 API 请求。 | 文档级能力，不是 live result。 | `bak_basic` 文档称可按日期取历史列表、从 2016 年开始，单次最多 7,000 行，需 5,000 积分；`stock_basic` 文档列有 `list_status=D`、`list_date`、`delist_date`。权限、历史状态语义、记录数、缺失率和点时完整性都未实测。 [bak_basic 文档](https://tushare.pro/document/2?doc_id=262)、[stock_basic 文档](https://tushare.pro/document/2?doc_id=25) |

直连 SSE 官方摘牌 PDF 静态地址时返回 7,373 字节 HTML challenge，文件保存在忽略目录并计算 SHA-256 `26bbc351…d087148115`；未尝试绕过。官方决定 HTML 与页面仍可读取。此限制没有被误报为 SSE 全部接口不可用。

## 退市 pilot 与区间语义

固定选择规则是在当前已核验证据内按交易所、代码排序，每个交易所最多取前两只；它只用于交叉检查，不是完整候选池生成。三只样本都不在现有 2026 Security Master（5,224 只，SSE 2,320、SZSE 2,904）中：

| 证券 | Listing date | Last trading date | Delisting date | 交叉验证状态 |
|---|---|---|---|---|
| `600209.SH` | 1999-03-25，BaoStock | 2022-06-15，年度/发行人材料 | 2022-06-22 | BaoStock 与交易所托管发行人报告对摘牌日一致；精确 IPO 日仍缺独立官方原始记录。 |
| `002618.SZ` | 2011-09-20，官方资料与 BaoStock | 2022-06-21，官方资料 | 2022-06-22，官方资料与 BaoStock | 官方和 Provider 日期一致。 |
| `300336.SZ` | 2012-07-10，深交所年鉴与 BaoStock | 2023-07-04，深交所年鉴、公告与 BaoStock | 2023-07-05，公告与 BaoStock | 官方和 Provider 日期一致。 |

`listing_interval_status` 只说明日期是否落在有证据的上市区间。`trading_status_known=false`：暂停、停牌、暂停上市等日状态未知，不能把区间成员资格当成可交易。缺摘牌日的证券被标记为开放区间且历史状态未知，不把空日期转成仍在市。

三只 pilot 证券与当前名单比较的结果是 3 个 pilot historical-only、0 个 overlap；这**不是**全市场 historical-only 数量。现有名单中 5,224 个代码不在这个三只样本中，不能据此认为它们是 current-only 历史成员。

## 日期覆盖测试

候选审计日期固定为 2018-12-28、2020-12-31、2022-12-30、2023-12-29。没有取得表示这些确切日期市场成员资格的来源，因此四个日期的总数、沪深分布、当前仍上市数、后来退市数、状态未知数、重复数和 source coverage 均为 `null` / `not_measured`。年度交易记录或年度退市表不能替代这些逐日 roster。

没有创建 `data/validated/security_master/historical_universe_pilot_2018_2023.*`，因为其日期覆盖和全集完整性尚未通过。可复现 pilot 数据仅存在于机器报告，不替换 U100。

## 最终结论与下一步

最终状态为 **`historical_universe_coverage_partial`**。目前可以确认：基于 2026 当前名单构建历史池会漏掉后来退市证券；交易所年鉴和公告能补到一部分证券及其日期；BaoStock 能给已知代码补充上市/摘牌元数据。现有证据还不能回答任意 2018–2023 交易日“当时完整有哪些股票”。

下一步需要有权限的 Tushare `bak_basic` live pilot（2018、2020、2022、2023 固定日期），或从 SSE/SZSE 获得并完整解析对应日期/年度官方名单，再与退市公告和 BaoStock 单股资料核对；之后才有资格生成覆盖报告和小型 historical master。当前代码未持有 token，也没有擅自尝试其他凭据。

## 输出和验证

- 本报告：`docs/step_4_8A_historical_universe_coverage.md`
- 机器报告：`artifacts/reports/historical_universe_coverage_step_4_8A.json`
- 证券 pilot CSV：`artifacts/reports/historical_universe_coverage_step_4_8A.csv`
- 可复现模块：`src/quantlab/research/historical_universe_coverage.py`
- 来源/字段配置：`config/step_4_8A_historical_sources.json`
- 在线 pilot 原始 BaoStock JSON 与交易所原件留在 `.gitignore` 排除的 `data/raw/historical_universe_coverage/`；未保存行情。

全套自动化测试实际执行 154 项，全部通过；其中新增离线覆盖测试 12 项。`compileall`、`git diff --check` 通过，uv 检查 107 个包兼容；Python 3.14.6。Step 4.8A 文件保留在工作区，没有创建新提交或推送。

