# 6KA 麵店系統交接規則

此 repo 是 6KA 麵店系統的工作區整理版。安全初始化期間只允許整理 Git、文件與 dry-run 腳本；不要部署、不要重啟服務、不要修改資料庫、不要操作售票機。

## 基本原則

- 先確認實際執行端，再判斷要改哪份檔案。常見執行端包含 `C:\6KAweb`、`C:\RP`、`C:\6KAK`、售票機 Agent、Pi Agent。
- `.env` 與 `.env.*` 視為秘密來源，可能包含售票機帳密、token、webhook、Cloudflare 或其他部署參數；不要 commit、不要回報值。
- `data/`、`logs/`、`backups/`、`exports/`、`outputs/` 是 runtime 或產出資料，預設不進 Git。
- 售票機相關工作先以文件與 dry-run 清單確認。未取得明確同意前，不要使用 WinRM、不要送出控制動作、不要出單。
- 部署與重啟必須明確得到同意，且要先說明會觸碰的主機、路徑、服務與 rollback 檢查。

## 快速檢查入口

- 伺服器檢查清單：`scripts/check-server.ps1`
- 售票機檢查清單：`scripts/check-ticket-machine.ps1`
- 伺服器部署 dry-run：`scripts/deploy-server.ps1`
- 操作文件：`docs/ops/`
- 同步鏈文件：`docs/sync/workstation-server.md`

## Git 初始化注意

目前工作區曾出現 Git `dubious ownership` 保護。除非使用者同意，不要自行寫入 `git config --global --add safe.directory ...`。可以回報完整 repo 路徑、擁有者與目前使用者，並等待使用者決定。

## Commit 前檢查

- 確認 `.gitignore` 已排除秘密、資料庫、log、runtime data、node_modules、cache、編譯產物。
- 搜尋敏感字只回報檔名，不回報 secret 內容。
- 不要把 `.env`、token local 檔、SQLite/DB、備份、log、outputs、exports、EXE/DLL 放進 initial commit。
- 不確定是否要版控的項目先列成 TODO，不要直接加入 commit。
