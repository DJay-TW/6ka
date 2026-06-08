# Ticket Machine Notes

此文件只記錄售票機相關交接與安全邊界。沒有使用者明確同意前，不要對售票機送出控制指令、不要 WinRM 操作、不要出單。

## 已知元件

- 售票機 Agent：`KioskAgent.cs`
- 售票機輸入記錄：`KioskInputRecorder.cs`
- 售票機監控 Agent：`KioskMonitorAgent.cs`
- Ticket Pad Controller：`TicketPadController.cs`
- 部署腳本候選：`deploy_kiosk_agent.ps1`、`deploy_kiosk_input_recorder.ps1`、`deploy_kiosk_monitor_agent.ps1`、`deploy_ticket_pad_controller.ps1`

## 連線與秘密

- 售票機 host、帳號、密碼、auth mode 預期由 `.env` 管理。
- 不要 commit `.env`。
- 不要在文件或 log 裡貼出密碼、token 或完整 secret 值。

## Read-only 檢查候選

這些只列為候選，執行前仍需使用者同意。

- `GET http://100.113.224.68:3010/health`
- `GET http://100.113.224.68:3010/api/status`
- 若 cross-host HTTP 不穩，需另外確認是否允許在售票機本機查 `127.0.0.1:3010`。

## 禁止未授權動作

- 不要執行售票、列印、付款、退款、清機、重啟或 UI 自動點擊。
- 不要建立或刪除 scheduled task。
- 不要修改售票機 DB。
- 不要部署新的 EXE 或替換 Agent。

## TODO

- 確認售票機正式 host、服務名稱、安裝路徑與回復流程。
- 確認 Ticket Pad Controller 的 source 與 release 產物是否都要保留在 Git。
- 確認輸入錄製、截圖、測試輸出是否只保留在 `outputs/`。
