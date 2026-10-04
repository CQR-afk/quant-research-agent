# Step 4.5 execution record — universe breadth expansion

Date: 2026-10-05  
Status: **blocked before universe freeze; no Step 4.5 model evaluation run**

## Checkpoint and scope controls

The required Step 4.4 checkpoint is present at `c4ca573` (`chore: checkpoint step 4.4 cross-sectional target study`). Before it was created, all 92 unittest cases passed and `uv pip check` reported 107 compatible packages. `data/`, `.env`, model/experiment/report artifacts and local databases are excluded by `.gitignore`.

No new price bars were requested or downloaded. U11 remains the only available development control. No Legacy Observed Test or sealed holdout dataset was opened or evaluated for this step. U50/U100 manifests were not frozen, so there are no breadth metrics or universe comparisons to report.

## Provider and metadata checks

The local environment is Python 3.14.6 with BaoStock 0.9.4 and AKShare 1.19.1. `quantlab data providers` reported BaoStock and AKShare SDKs available, Tushare unavailable because no credential is configured, and Yahoo available for its declared markets. BaoStock's market-data Provider capability declaration does not expose stock metadata, although the installed SDK includes `query_stock_basic` and `query_stock_industry` interfaces.

An actual BaoStock login returned success. The subsequent full `query_stock_basic()` / `query_stock_industry()` metadata request did not return before the probe process remained idle for more than 30 seconds; it was stopped without receiving candidate rows, response fields, or a provider error code. Therefore the documented basic fields (`code`, `code_name`, `ipoDate`, `outDate`, `type`, `status`) and industry fields were not confirmed against a live response in this run.

As a fallback check, AKShare 1.19.1 was asked for the SSE Main Board A-share list, SSE STAR Market list, and SZSE A-share list. The request failed with `ProxyError`; the sanitized probe record is in ignored local raw data at `data/raw/ashare/akshare_exchange_listings_current.json`. It contains no security rows. The network restriction prevented both candidate enumeration and the required source/version/field verification.

## Why the workflow stopped

Without a returned exchange-aware candidate universe and listing/security metadata, there is no defensible way to freeze the candidate order, derive outcome-independent U50/U100, or make replacements based on documented source failures. Creating a hard-coded list would violate the requested deterministic, evidence-based selection rules. No market data or modeling was started after the metadata gate failed.

The declared `breadth_supported` / `breadth_not_supported` / `breadth_inconclusive` research assessment is **not applicable yet**: no candidate breadth experiment ran. This execution is operationally blocked before assessment; it is not evidence that wider breadth supports or fails to support the baseline.

## Resume condition and unchanged requirements

Resume after the runtime can reach BaoStock's metadata endpoints or the exchange listing endpoints through an approved network path. Then capture the actual metadata response and its field names, freeze and hash the ordered candidate list before downloading prices, and run U50 first using only 2018-01-01 through 2023-12-31 none/qfq bars. Proceed to U100 only after U50 data and feature-pipeline checks succeed. Keep the existing U11 data as the control and retain the prescribed seed, model, folds, purge and embargo unchanged.

## Step 4.5R connectivity recovery and preflight (2026-10-05)

Final result: **`metadata_access_still_blocked`**. The connectivity issue was isolated, and BaoStock single-security metadata calls now work. The complete candidate universe still cannot be retrieved, so no candidate pool was frozen.

### Environment and route diagnosis

- Git HEAD before this work was `046e682 docs: record step 4.5 metadata access block`; the required commits `c4ca573` and `046e682` both exist. The initial worktree was clean.
- Python is 3.14.6. `uv pip check` found 107 compatible packages. The existing suite has 92 tests; all 92 pass (`13.308s`). No code, Baseline v1, or existing experiment artifact was modified.
- Python inherited upper- and lower-case HTTP/HTTPS/ALL proxy variables, all resolving to a loopback proxy on port 9; no credentials were present. `NO_PROXY` was present but its value was suppressed. A bounded socket check to loopback port 9 timed out, so no usable local proxy listener answered. Requests routed both SSE and SZSE requests to that proxy and received `ProxyError`.
- WinINET proxy is disabled (a proxy value remains configured in the registry); WinHTTP reports direct access. DNS resolved BaoStock, SSE, SSE query, and SZSE endpoints. Direct bounded TCP checks to BaoStock port 10030 and exchange HTTPS endpoints connected. With `requests.Session.trust_env=False`, the SSE landing page returned HTTP 200 in 3.97s; the SZSE landing page raised `ReadTimeout` at 5.11s. These were connectivity-only page requests, not stock-list downloads. No global proxy setting was changed, and direct access was not used to fetch candidate data because an approved direct route was not established.
- Local AKShare 1.19.1 list functions were invoked separately with bounded Requests timeouts: SSE `stock_info_sh_name_code(symbol="主板A股")` and SZSE `stock_info_sz_name_code(symbol="A股列表")`. Both failed with `ProxyError` after about 2.05s / 2.02s, before any response columns or rows. This points to the configured local proxy route; it does not establish that the exchanges rejected the requests or that the APIs changed.

### BaoStock isolated results

BaoStock SDK 0.9.4 uses a raw TCP socket to its configured server, so HTTP proxy variables do not route that SDK traffic. Each probe ran in an independent Python process; a parent-process watchdog terminated slow requests. No full market bars were requested.

| Step | Interface and request | Result | Fields / count / error |
|---|---|---|---|
| Login | `bs.login()` | Success, 2.143s | Error code `0` |
| Single basic record | `query_stock_basic(code="sz.000001")` | Success, 1.403s | `code, code_name, ipoDate, outDate, type, status`; 1 row; provider error code `0` |
| Narrow basic filter | `query_stock_basic(code_name="平安银行")` | Success, 11.019s | Same six fields; 1 row; provider error code `0` |
| All basic records | `query_stock_basic()` | External watchdog timeout at 60s | Login succeeded; query remained in progress; no provider error code, fields, or complete rows returned |
| Date-scoped single industry record | `query_stock_industry(code="sz.000001", date="2018-01-01")` | Success, 6.165s | `updateDate, code, code_name, industry, industryClassification`; 1 row; provider error code `0` |
| All industry records | `query_stock_industry(date="2018-01-01")` | External watchdog timeout at 15s | Login succeeded; no provider error code, fields, or complete rows returned |

The basic API supports code/name filters, not a date-range/pagination argument in its installed SDK signature. The rows returned for the known symbol verify schema behavior only; they are not a securities universe. Positive raw probes (one row each) are retained under ignored `data/raw/ashare/step45_baostock_basic_one.json`, `step45_baostock_basic_narrow.json`, and `step45_baostock_industry_one.json`. Their SHA-256 values are respectively `be99616b6b9e4da67477ca6b908935197a2b42e0c3a34063a62404d9a9740c85`, `3acd4d5920684458ad380052bbab15b1c6a8b93bc9166771cd8e8d97668f5219`, and `074301b0e5deb0a60519b667f27ce5fe461ba6b90c5d9c0f1debbc5db6dd0b97`. The timed-out calls have no complete result snapshot.

### Security master and freeze decision

No local CSV, XLS, or XLSX security-master source file was found in the project. There is no source-documented official exchange file available offline to import. The AKShare functions are the only local exchange-list adapters discovered, and the current proxy route blocks them. No importer or guessed schema mapping was added without a real source file to validate. The prior 11-stock anchor configuration remains unchanged.

The single-symbol BaoStock responses do not establish complete candidate coverage, row de-duplication across exchanges, candidate counts, or an SSE/SZSE distribution. No normalized Security Master, universe hash, candidate ordering, nested U50/U100 manifests, or replacement log was created. Listing/date and security-type eligibility therefore remain unverified for new candidates; current-universe survivorship also remains unresolved. No new market bars, features, LightGBM work, or walk-forward evaluation occurred. Existing Legacy Test and sealed-holdout data were not opened or evaluated.

### Required connection change to resume

The Python proxy currently points at an unresponsive loopback port 9. A network/runtime administrator must provide a working, approved proxy endpoint in the process environment (and confirm BaoStock raw TCP 10030 is an approved route), or explicitly authorize a direct exchange connection if organizational policy permits it. Do not replace the proxy with an unapproved route. After that change, rerun only the failed full metadata and exchange-list probes, preserve their returned fields and provenance, and freeze candidates only after a complete verified source is available.

## Step 4.5R.1 — network recovery only (2026-10-05)

Starting state was exactly HEAD `0c63cef`, with a clean worktree. Checkpoint `c4ca573` and Step 4.5 record `046e682` are present. Python 3.14.6 is active; the existing 92-test suite passed (`13.502s`), `uv pip check` found 107 compatible packages, and `.env`, `data/`, databases, and `artifacts/{models,experiments,reports}` remain ignored.

The current process still inherits upper- and lower-case HTTP/HTTPS/ALL proxy variables to loopback port 9. No credentials were present; NO_PROXY values were not displayed. A 2-second local socket probe to that port timed out. This task contained no explicit approval for project-level direct access, so no AKShare list request was made. In the previous authorized connectivity diagnosis, SSE main-board and SZSE list calls through the configured proxy returned `ProxyError`; SSE STAR was not tested. No authenticated/approved proxy is configured. Therefore all three live list checks (SSE main, SSE STAR, SZSE) remain unverified in this run. The required access condition is a working approved process proxy, or explicit user authorization to use direct sessions only for the public exchange-list requests.

The prior successful BaoStock single-symbol responses remain the only online metadata evidence: one basic row for `sz.000001` and one industry row for `sz.000001` at the requested date. They are retained in ignored `data/raw/ashare/step45_baostock_basic_one.json` and `step45_baostock_industry_one.json`; they do not constitute a complete list.

Static inspection of the installed BaoStock 0.9.4 SDK shows why the existing all-record probes cannot localize the stall. Basic/industry methods request page 1 with `BAOSTOCK_PER_PAGE_COUNT=2000`; `ResultData.next()` iterates the buffered page and, when it contains exactly 2,000 rows, requests another page. `send_msg()` calls blocking `recv(8192)` repeatedly and accumulates response bytes until its protocol terminator appears. It sets neither a per-socket timeout nor a maximum response-body size. The prior watchdog did not log when the first query returned, page boundaries, rows consumed, or receive chunks. Thus the stall may be in first-page reception, a later page request triggered by `next()`, slow/large response transfer, or a missing terminator; current evidence cannot rule out SDK pagination/row iteration. No additional full query was issued in this task. Any future diagnostic should record page transitions and chunk/byte/timing counts in a killable process.

This run created no code changes, metadata import, Security Master, candidate list, universe manifest, bars, or experiments. Baseline and holdout artifacts remain untouched. Final status remains **`metadata_access_still_blocked`**.

## Step 4.5R.2 — public exchange-list direct access and freeze (2026-10-05)

Final status: **`metadata_source_ready`**; deterministic candidate freeze completed. Direct HTTP access was used only by `src/quantlab/data/exchange_security_lists.py`, whose dedicated `requests.Session` sets `trust_env=False`. The inherited proxy variables still point to the unavailable loopback port 9; no Windows proxy or other Provider configuration was changed.

Starting state was HEAD `483d698` with a clean worktree. Python is 3.14.6; `uv pip check` passed for 107 packages. The existing 92-test suite passed before changes. `.env`, `data/`, databases, and generated artifacts remain ignored. BaoStock's unbounded full-list calls were not repeated; after candidate freeze, the existing single-symbol basic interface was called once for each U100 member.

The three authorized exchange requests all returned complete data:

| Source list | Official endpoint / request | Rows and completeness evidence | Duplicate symbols | SHA-256 |
|---|---|---:|---:|---|
| SSE Main Board A | `https://query.sse.com.cn/sseQuery/commonQuery.do`, `STOCK_TYPE=1` | 1,702 rows; `pageHelp.total=1702`, `pageCount=1` | 0 | `f7065409360e44c04249b83b36a3d67921e4b28df12e83cf2fdf65ceb9ae767e` |
| SSE STAR Market | same endpoint, `STOCK_TYPE=8` | 618 rows; `pageHelp.total=618`, `pageCount=1` | 0 | `0fe2e9b77df8e71659904b553e0843fa2475f3e1f8780328bc9549e70ac8204c` |
| SZSE A-share workbook | `https://www.szse.cn/api/report/ShowReport`, `SHOWTYPE=xlsx`, `CATALOGID=1110`, `TABKEY=tab1` | 2,904 data rows in the returned official workbook | 0 | `4792fa87b34b4659619f99d38b11f110a8949bf09df5a66919335c1675c93515` |

The SSE response exposed `AREA_NAME`, `AREA_NAME_DESC`, `A_STOCK_CODE`, `B_STOCK_CODE`, `COMPANY_ABBR`, `COMPANY_ABBR_EN`, `COMPANY_CODE`, `CSRC_CODE`, `CSRC_CODE_DESC`, `DELIST_DATE`, `FULL_NAME`, `FULL_NAME_IN_ENGLISH`, `LIST_BOARD`, `LIST_DATE`, `NUM`, `PRODUCT_STATUS`, `SEC_NAME_CN`, `SEC_NAME_FULL`, `STATE_CODE`, `STATE_CODE_STOCK`, and `STOCK_TYPE`. The SZSE workbook exposed 22 columns: `板块`, `公司全称`, `英文名称`, `注册地址`, `A股代码`, `A股简称`, `A股上市日期`, `A股总股本`, `A股流通股本`, `B股代码`, `B股简称`, `B股上市日期`, `B股总股本`, `B股流通股本`, `地区`, `省份`, `城市`, `所属行业`, `公司网址`, `目前尚未盈利`, `具有表决权差异安排`, and `具有协议控制架构`. Its source filename header was GBK-decoded as `A股列表.xlsx`; the preserved workbook bytes are unchanged.

The snapshot was fetched at `2026-10-04T14:52:01.406647+00:00` (Sydney local date 2026-10-05). The combined source-files hash is `36b385c51dbc0c4cc14078f4cafad32292fd0f75e2aea227af5b366672557043`. The canonicalized Security Master contains 5,224 unique symbols: 2,320 SSE and 2,904 SZSE; all rows have source listing date and industry values. Board classification counts are 3,197 main board, 618 STAR, and 1,409 ChiNext. The previously unsupported `302` SZSE prefix was present in the official SZSE A-share code column, so the exchange-aware symbol normalizer now recognizes that prefix; it is no longer rejected as a parsing failure.

The reproducible freeze uses `quantlab-step4.5R2-2026-10-05-v1` and `exchange-industry-hash-round-robin-v1`. It retains all 11 members of `ashare_engineering_2026_10_04_qfq_pilot_v1`, including `300750.SZ` (listed 2018-06-11) as an explicitly marked anchor exception. New members require an official listing date no later than 2017-12-31; eligible names are grouped by exchange and source industry, ordered within strata by `SHA256(symbol + seed)`, then chosen by deterministic round-robin across stable-hash-ordered strata. No returns, prices, features, model outputs, or popularity measures enter selection. There were 3,225 eligible new candidates; U50 adds the first 39 and U100 adds the first 89. Exchange distribution is 25 SSE / 25 SZSE for U50 and 50 / 50 for U100. The maximum industry share is 12% in U50 and 7% in U100.

Frozen outputs and source artifacts:

- Security Master: `data/validated/security_master/security_master_36b385c51dbc0c4c.parquet`
- Raw source manifest: `data/raw/security_master/20261004T145201Z/manifest.json`
- U50: `config/ashare_universe_breadth_50.json`, hash `d6dbbb64fb44564efc37304889812b35bcb5ee0b532210722dcd2096a7ae9218`
- U100: `config/ashare_universe_breadth_100.json`, hash `05278729087acebc4bddc201f4f0f03068b5781b0c1b56e9377bbd14cecd37e9`
- Full ordered eligible list (3,225 candidates, used for deterministic future replacement): `data/validated/security_master/universe_candidate_order.json`; the first 89 entries were selected for U100.
- Rejection/defer ledger: `data/validated/security_master/universe_candidate_rejections.json`
- Full freeze manifest: `data/validated/security_master/universe_freeze_*.json`
- BaoStock U100 metadata enrichment: `data/validated/security_master/baostock_candidate_enrichment.json`

The ledger records 1,988 names rejected by the pre-2018 listing-date rule and 3,136 eligible names ordered after the U100 cutoff. The source is a current exchange snapshot, not a historical point-in-time security master; manifests carry `SURVIVORSHIP_LIMITED_PROVIDER_UNIVERSE` and survivorship bias remains unresolved. BaoStock 0.9.4 `query_stock_basic(code=...)` then returned exactly one record for all 100 frozen U100 members. Actual fields were `code, code_name, ipoDate, outDate, status, type`; all 100 `ipoDate` values matched the exchange listing date and there were no failed/non-single-row requests. Per-symbol raw responses are in `data/raw/security_master/20261004T145201Z/baostock_candidate_enrichment.jsonl` (SHA-256 `52c4d9edabb19daf2eed3379bf6b861e9b28721db7f98d7e2802c24c023590d7`) and the normalized sidecar is `data/validated/security_master/baostock_candidate_enrichment.json`. BaoStock's single-symbol industry result for anchor `000001.SZ` is dated 2018-01-01, so it was not treated as current industry; the verified exchange lists supply industry for all candidates. BaoStock status/outDate values are retained as returned and do not determine selection or imply historical survivorship coverage.

Eight new offline tests cover direct-session isolation, pagination completeness, source hashing, exchange mapping (including the source-confirmed `302` prefix), duplicate rejection, bounded retries, deterministic selection, nested pools, anchor exceptions, listing eligibility, and outcome independence. The complete suite passed **100 tests / 0 failures** in 45.117 seconds; `compileall` passed; `uv pip check` passed with the project-local cache (107 compatible packages). No new market bars were downloaded, no Legacy Test or sealed Holdout data was accessed, and no model or strategy work ran. This phase stops after metadata freeze; the next authorized phase must explicitly begin price acquisition.
