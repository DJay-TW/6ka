# History

此文件記錄 repo 初始化與歷史文件邊界。

## 2026-06-08 Git 安全初始化

- 建立 `.gitignore`，排除秘密、資料庫、log、runtime output、依賴/cache、編譯產物。
- 建立交接文件：`AGENTS.md`、`docs/ops/`、`docs/sync/`。
- 建立 dry-run/checklist 腳本骨架：`scripts/check-server.ps1`、`scripts/check-ticket-machine.ps1`、`scripts/deploy-server.ps1`。
- 未執行 `git add`、`git commit`、`git remote add`。
- 未部署、未重啟、未修改資料庫、未操作售票機。

## 歷史文件邊界

- `deliverables/6KA_system_architecture_20260603.md` 視為歷史快照，不一定代表目前 live topology。
- `開發日誌.md` 與 `開發日誌_最新短版.md` 是既有交接資料，initial commit 前應檢查是否含 secret 或過期 live 狀態。

## TODO

- 決定是否把舊備忘與 mojibake backup 文件納入 Git，或移到 archive。
- 決定是否將 generated deliverables 納入 Git。
- 建立正式 initial commit 前，先跑一次不輸出 secret 值的敏感字掃描。
