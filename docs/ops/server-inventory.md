# Server Inventory

此文件是交接用 inventory 骨架，尚未執行 live verification。不要把此文件視為目前服務狀態證明。

## Repo 路徑

- 工作區：`C:\Users\88698\Documents\6KA系統開發`
- 常見部署/執行路徑：`C:\6KAweb`、`C:\RP`、`C:\6KAK`

## 主要元件

- WEB 控制面：`server.js`
- Dashboard 靜態頁：`index.html`
- RP5 銷售通知/監控：`rp_v5.0.py`、`suit_repository_sales_monitor.py`
- 6KAK 播放/廚房鏈：`6kak_v2.0.py`
- 銷售快取同步：`sales_cache_sync_worker.py`、`query_sales_cache.py`
- 現金/財務同步：`cash_finance_sync_worker.py`、`cash_diff_cloudflare_push.py`
- CashException 監控：`cash_exception_monitor.py`
- Pi Agent：`pi_agent.py`、`player_api.py`
- 售票機 Agent：`KioskAgent.cs`
- CashFinance Agent：`CashFinanceAgent.cs`
- Ticket Pad Controller：`TicketPadController.cs`

## Read-only 健康檢查候選

執行前必須確認使用者同意是否要碰 live endpoint。

- WEB：`GET /health`
- WEB：`GET /api/server/status`
- Pi：`GET /api/pi/status`
- 售票機 Agent：`GET http://100.113.224.68:3010/api/status`
- CashFinance Agent：`GET http://100.113.224.68:3012/api/finance/incremental`

## Runtime 檔案位置

這些預設不進 Git。

- `data/`
- `logs/`
- `backups/`
- `exports/`
- `outputs/`
- `*.sqlite`、`*.db`、`*.bak`、`*.log`

## TODO

- 確認 `wrangler.cash-diff.toml` 是否只含非秘密設定，或需要改成 example + local override。
- 確認 `github-pages-6ka/` 是原始碼、發布產物，還是獨立 repo。
- 確認 `x64/`、`x86/` 的 SQLite native DLL 是否要以外部依賴方式管理。
