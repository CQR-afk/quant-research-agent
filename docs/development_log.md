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
