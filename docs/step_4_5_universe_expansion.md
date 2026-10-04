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
