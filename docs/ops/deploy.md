# Deploy Runbook

此文件目前只定義 dry-run 與人工核准流程。尚未允許部署、重啟服務或修改資料庫。

## 禁止未授權操作

- 不要執行部署腳本。
- 不要重啟 WEB、RP5、6KAK、CashException、cash/sales sync worker。
- 不要修改 SQLite、DB、備份或 sync state。
- 不要操作售票機或出單。

## 部署前檢查

1. 確認要部署的目標：WEB、RP5、6KAK、Pi Agent、售票機 Agent、CashFinance Agent、Ticket Pad Controller。
2. 確認來源檔與目標路徑，例如 workspace -> `C:\6KAweb`。
3. 確認目前 live process 與 command line。
4. 確認最近 log 與健康檢查結果。
5. 確認 rollback 檔案或備份策略。
6. 取得使用者明確同意後才執行。

## Dry-run 腳本

- `scripts/deploy-server.ps1` 只列出部署 checklist，不會 copy、不會重啟。

## TODO

- 補上各元件的正式部署命令與 rollback 指令。
- 補上部署後 read-only 驗證清單。
- 補上需要管理員權限或 UAC 提示時的操作說明。
