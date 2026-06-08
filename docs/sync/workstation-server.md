# Workstation And Server Sync

此文件描述工作站、WEB server、售票機與外部同步鏈的交接骨架。此版本未做 live endpoint 驗證。

## 銷售資料鏈

- 售票機 Agent 提供銷售增量資料。
- `sales_cache_sync_worker.py` 同步到本機 SQLite cache。
- `query_sales_cache.py` 提供 WEB 與 RP5 查詢。
- `data/sales_cache.sqlite` 是 runtime cache，不進 Git。

## 現金/財務鏈

- CashFinance Agent 提供 finance incremental data。
- `cash_finance_sync_worker.py` 同步到 `data/finance_cache/`。
- `cash_diff_cloudflare_push.py` 將現金狀態推到 Cloudflare Worker/KV 路徑。
- `data/finance_cache/*.db`、`*.sqlite`、`sync_state.json`、reports、snapshots 都是 runtime state，不進 Git。

## 狀態與 log

- 同步狀態：`data/**/sync_state.json`
- 銷售同步 log：`logs/sales-cache-sync.log`
- 現金同步 log：`logs/cash-finance-sync.log`
- CashException log：`logs/cash-exception-monitor.log`

## Git 邊界

建議 commit source、dry-run scripts、docs、example config。不要 commit DB、runtime state、log、local token、export/report output。

## TODO

- 建立 `.env.example`，只放變數名稱與假值。
- 確認 `wrangler.cash-diff.toml` 的敏感程度。
- 補上同步失敗時的 read-only diagnosis flow。
