# Ticket Pad Controller 維運日誌

## 2026-06-08 live 狀態

- 目的：讓售票機本機獨立提供滑鼠、鍵盤、觸控板與 ACT 巨集控制 UI；此程式控制的是售票機，不控制本機開發電腦。
- 售票機 URL：`http://100.113.224.68:9580/`
- 售票機部署路徑：`C:\6KA\ticket-pad-controller`
- 售票機執行檔：`C:\6KA\ticket-pad-controller\TicketPadController.exe`
- 背景啟動：排程 `6KA Ticket Pad Controller` 執行 `wscript.exe "C:\6KA\ticket-pad-controller\start-hidden.vbs"`，避免跳 console 視窗。
- 目前版本：`0.2.3`
- Port：`9580`
- PIN：目前未啟用。
- Danger macros：已啟用，使用按壓確認。
- Cursor overlay：目前狀態為 enabled，idle hide `5000ms`。
- 售票機虛擬螢幕：`1080x1920`，直立螢幕。
- WebSocket：前端已使用 `/ws`，一般控制事件優先走 WebSocket；失敗時才 fallback HTTP。

## 目前 ACT 巨集

線上 `/api/state` 只保留以下 ACT：

- `restart_ticket_app` / `重啟售票程式` / danger press
- `close_ticket_app` / `關閉售票程式` / danger press
- `restart_machine` / `重新啟動` / danger press
- `shutdown_machine` / `關機` / danger press

已移除：

- `回到後台` / `進入後台`
- `游標 開/關`
- `游標置中`

機器層級的 `重新啟動`、`關機` 目前走桌面捷徑：

- `C:\Users\M-220\Desktop\重新啟動.lnk`
- `C:\Users\M-220\Desktop\關機.lnk`

售票機程式層級的 `重啟售票程式`、`關閉售票程式` 目前仍走 controller fallback：

- 關閉：kill `Kiosk.Standard.App`
- 重啟：kill 後啟動 `TICKET_APP_EXE`，fallback `C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe`
- 尚未找到桌面上對應的「重啟售票機程式 / 關閉售票機程式」捷徑。

## 2026-06-08 UI / 操作修正

- Modifier HUD 延遲已從硬編碼 `430ms` 改成 `SHORTCUT_HUD_DELAY_MS = 80`。原因：單獨按 `Ctrl` / `Alt` / `Shift` 時，原本 430ms 等待加上動畫會讓 HUD 體感慢至少半秒。
- 工具列取消選取新增兩種方式：
  - 當已有工具列模式被選取時，點左手區滑鼠左鍵會先取消選取，不送出 `mouse_down`。
  - 橫式畫面時，點控制器最右邊框外的空間會取消選取。
- ACT 的 `回到後台`、游標開關、游標置中已從前端清單移除；顯示游標視為控制器基本要求，不做成 ACT。
- 右手區三指手勢目前程式邏輯：
  - 三指上滑：送 `Win+D`
  - 三指左/右滑：送 `Alt+Tab`
  - 僅在 trackpad/touchField 模式運作，ACT/text/numpad 等工具面板開啟時不觸發。

## 已驗證

- `http://100.113.224.68:9580/api/state` 回傳 `ok=true`、version `0.2.3`。
- 線上 `static/app.js` 已包含：
  - `new WebSocket(websocketUrl())`
  - `const SHORTCUT_HUD_DELAY_MS = 80;`
  - `function cancelActiveMode`
  - `function isRightOutsideController`
- ACT 線上清單只有四項：`重啟售票程式`、`關閉售票程式`、`重新啟動`、`關機`。

## 已知限制 / 待辦

- 售票機營業前景可能會隱藏 Windows 真游標；離開售票機前景後游標又會正常顯示。原因尚未完全定位到系統層或該前景程式設定。
- 如果最後必須仰賴 fake cursor，還要把 overlay cursor 調整為與 Windows 真游標同尺寸、純紅色、點擊效果非紫色，並盡量讓圖層高於開始功能表。
- `Win+D` 透過控制器 API 可送出，但先前測試在 `SearchUI` 前景時沒有可見效果；需再用售票機前景或桌面狀態分開驗證。
- 後台返回流程曾嘗試自動化，但不穩定且已停止；目前不保留 `回到後台` ACT。
- 手機/平板如果已開著控制器頁面，部署後要重新整理，才會載入新的 `app.js`。

## 維運命令

部署到售票機：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File deploy_ticket_pad_controller.ps1
```

查 live 狀態：

```powershell
Invoke-RestMethod -Uri http://100.113.224.68:9580/api/state -TimeoutSec 10
```

確認線上前端是否載入指定修正：

```powershell
$js = (Invoke-WebRequest -Uri 'http://100.113.224.68:9580/static/app.js' -UseBasicParsing -TimeoutSec 10).Content
$js.Contains('const SHORTCUT_HUD_DELAY_MS = 80;')
$js.Contains('function cancelActiveMode')
```

本機主要檔案：

- `TicketPadController.cs`
- `build_ticket_pad_controller.ps1`
- `deploy_ticket_pad_controller.ps1`
- `outputs\ticket-pad-ui-20260607-220947\static\app.js`
- `outputs\ticket-pad-controller-release\publish`
- `outputs\ticket-pad-controller-release\payload`

## 2026-06-08 六花鍵鼠維修索引

### 程式碼與發佈路徑

- 主程式碼：`TicketPadController.cs`
- 建置腳本：`build_ticket_pad_controller.ps1`
- 部署腳本：`deploy_ticket_pad_controller.ps1`
- 前端來源：`outputs\ticket-pad-ui-20260607-220947\static\app.js`
- 前端 HTML：`outputs\ticket-pad-ui-20260607-220947\static\index.html`
- 發佈目錄：`outputs\ticket-pad-controller-release\publish`
- payload 目錄：`outputs\ticket-pad-controller-release\payload`
- 網站 icon：`outputs\ticket-pad-controller-release\publish\static\6ka-mouse-icon.png`

### 售票機 live 路徑

- live 目錄：`C:\6KA\ticket-pad-controller`
- live 執行檔：`C:\6KA\ticket-pad-controller\TicketPadController.exe`
- live 前端：`C:\6KA\ticket-pad-controller\static\index.html` / `static\app.js`
- live icon：`C:\6KA\ticket-pad-controller\static\6ka-mouse-icon.png`
- 啟動器：`C:\6KA\ticket-pad-controller\start-hidden.vbs`
- 排程：`6KA Ticket Pad Controller`
- 排程 action：`wscript.exe "C:\6KA\ticket-pad-controller\start-hidden.vbs"`
- 版本：`0.2.3`
- Port：`9580`

### 維修入口

- Tailscale URL：`http://100.113.224.68:9580/`
- LAN URL：`http://192.168.50.65:9580/`
- API 狀態：`http://100.113.224.68:9580/api/state`
- WebSocket：`ws://100.113.224.68:9580/ws`
- LAN 介面：`Ethernet 2`
- LAN MAC：`00-90-05-0F-9C-F7`
- 注意：DJAY-SERVER 實體 IP 是 `192.168.100.45`，售票機是 `192.168.50.x`，兩者不是同一個 LAN subnet；伺服器維護請優先走 Tailscale。

### 驗證命令

```powershell
Invoke-RestMethod -Uri http://100.113.224.68:9580/api/state -TimeoutSec 10

$html = (Invoke-WebRequest -Uri 'http://100.113.224.68:9580/' -UseBasicParsing -TimeoutSec 10).Content
$html.Contains('<title>六花鍵鼠</title>')

$js = (Invoke-WebRequest -Uri 'http://100.113.224.68:9580/static/app.js' -UseBasicParsing -TimeoutSec 10).Content
$js.Contains('new WebSocket(websocketUrl())')
$js.Contains('keys: ["win", "d"]')
```

### 今日清理狀態

- 已刪除一次性測試排程：`6KA Ticket Pad Capture Once`、`6KA Touch Smoke Test *`、已停用的 `6KA Kiosk Input Recorder`。
- 已刪除測試資料夾：`capture-once`、`manual-flow-watch`、`touch-smoke-test`、`absolute-mouse-hold-test`、`touch-variant-test`、`uia-dump`。
- 已刪除零散測試腳本：`capture-once.ps1/.vbs`、`manual-flow-watch.ps1/.vbs`、`renew-lan-ip.ps1`。
- 已刪除已停用的 `C:\6KA\input-recorder`。
- 清理後 `C:\6KA\ticket-pad-controller` 約 `14.23 MB`；正式 `TicketPadController.exe`、`KioskAgent.exe`、`CashFinanceAgent.exe` 均仍在跑。

### 售票機原廠 UI 素材位置

- 售票主程式：`C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe`
- 外部首頁/宣傳圖：`C:\Protech\Suit.Site.Media\Home`
- 商品/分類縮圖：`C:\Protech\Suit.Site.Media\Small`
- 發票/列印 logo：`C:\Protech\Attachment\InvoiceLogo.jpg`
- WPF UI 皮膚與支付圖示多半內嵌於 `C:\Protech\Suit.Kiosk\Kiosk.Themes.dll`、`Kiosk.Standard.Ui.dll`，不建議直接動 live DLL。
- 代表圖與抽出資源：`outputs\kiosk-ui-asset-inspect`

## 2026-06-08 0.2.4 置頂與背景執行紀錄

- `TicketPadController.cs` 版本升到 `0.2.4`，只改 cursor overlay 的 z-order：overlay form 增加 `WS_EX_TOPMOST`，並在 `OnShown`、游標更新、timer tick 時用 `SetWindowPos(HWND_TOPMOST, SWP_NOACTIVATE)` 補位，避免被開始選單壓住。
- 已重新編譯 `outputs\ticket-pad-controller-release\publish\TicketPadController.exe`，並用 `deploy_ticket_pad_controller.ps1` 部署到售票機 `C:\6KA\ticket-pad-controller`。部署後 `/api/state` 回 `ok=true`、`version=0.2.4`。
- 背景執行確認：`6KA Ticket Pad Controller`、`6KA Kiosk Agent`、`6KA Cash Finance Agent` 三個正式排程都用 `wscript.exe "...start-hidden.vbs"` 啟動，`LastTaskResult=0`。
- 六花鍵鼠主程式是 `/target:winexe`，正式程序沒有 console 視窗；`TicketPadController.exe`、`KioskAgent.exe`、`CashFinanceAgent.exe` 的 `MainWindowTitle` 均為空。
- 檢查後沒有殘留 `powershell.exe`、`cmd.exe`、`wscript.exe`、`RvRvpnGui.exe`；一次性 `6KA Ticket Pad ZOrder Check` 測試排程不存在，`C:\Windows\Temp\6ka-zorder-check` 也已不存在。
- 售票機上的 `conhost.exe` 不是這次測試殘留：其中一個是 `CashFinanceAgent.exe` 的隱藏 console host，其餘來自原廠 Protech/IIS dotnet 程式。
- Radmin VPN 開機跳窗已移除：刪除 `HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run` 的 `RadminVPN` GUI 啟動值，以及 `C:\Users\M-220\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\Radmin VPN.lnk`。保留 `RvControlSvc` 與 `RServer3` 服務，兩者仍為 `Running / Auto`。
- 之後若要測試開始選單 z-order，避免用可見 PowerShell 截圖 task；優先用已部署的程式版本與現場肉眼確認，或改寫成完全無視窗的專用測試工具再跑。

## 2026-06-08 0.2.5 WebSocket macro id 修正

- 問題：點 ACT / 關機沒有反應。live log 顯示 `event ok type=run_macro id=2081 message=macro not implemented: 2081`，代表前端送到後端的 macro id 被 WebSocket request id 覆蓋，不是原本的 `shutdown_machine`。
- 根因：`app.js` 的 `sendWebSocketPayload()` 使用 `const message = { ...payload, id }`，剛好把 `run_macro` payload 裡的 `id` 覆蓋成遞增數字。HTTP fallback 不受影響，但改用 WebSocket 後 ACT macro 會壞。
- 修正：前端改成 `request_id` 追蹤 WebSocket request；後端 `AddPayloadId()` 優先回傳 `request_id` 作為 ack id，保留 macro 自己的 `id` 給 `RunMacro()` 使用。
- 版本：`TicketPadController.cs` 升到 `0.2.5`；同步修改 `outputs\ticket-pad-controller-release\publish\static\app.js`、`payload\static\app.js`、`outputs\ticket-pad-ui-20260607-220947\static\app.js`。
- 為避免瀏覽器沿用舊 JS，`index.html` script query 已升為 `/app.js?v=20260608-macro-ws-id-fix`。
- 驗證：部署後 `/api/state` 回 `version=0.2.5`；live `app.js` 含 `request_id: id` 且不含舊的 `const message = { ...payload, id };`。無害 WebSocket probe `run_macro id=probe_macro_after_deploy request_id=5151` 回 `{"ok":true,"message":"macro not implemented: probe_macro_after_deploy","id":5151}`，證明 macro id 沒再被覆蓋。
- 注意：未實際觸發 `shutdown_machine`，避免把售票機關機；`關機.lnk` 與 `重新啟動.lnk` 已確認存在於 `C:\Users\M-220\Desktop`，目標分別是 `shutdown.exe -s -t 0` 與 `shutdown.exe -r -t 0`。

### ACT 前兩項實測

- 以 WebSocket `run_macro confirmed=true` 實測 `restart_ticket_app`：回 `ticket app restart requested`，並成功啟動 `C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe`。
- 接著實測 `close_ticket_app`：回 `ticket app close requested`，`Kiosk.Standard.App.exe` 程序消失。
- 為避免售票機停在關閉狀態，最後再送一次 `restart_ticket_app` 恢復；驗證後 `Kiosk.Standard.App.exe` 正在跑。
- 本次仍未實際觸發 `restart_machine` / `shutdown_machine`；實測這兩項會造成遠端斷線或關機，需現場/時機確認後再做。

## 2026-06-08 0.2.6 售票程式重開改走桌面捷徑

- 問題：原廠售票程式的正常啟動路徑在桌面捷徑，不應只直接執行 `C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe`。
- 已確認桌面捷徑 `C:\Users\Public\Desktop\Suit.Kiosk.exe.lnk` 目標為 Windows Installer wrapper：`C:\Windows\Installer\{4FA3FB12-A2C7-4CCE-B419-5B7ECD8F75B9}\Kiosk.Standard.App_EDCE5CE1799C46E582FA5662383FE9A5.exe`，WorkingDirectory 為 `C:\Protech\Suit.Kiosk\`。
- `TicketPadController.cs` 升到 `0.2.6`：`restart_ticket_app` 改為優先使用 `TICKET_APP_SHORTCUT`，未設定時啟動桌面 `Suit.Kiosk.exe.lnk`，找不到才 fallback 到 `TICKET_APP_EXE` / `C:\Protech\Suit.Kiosk\Kiosk.Standard.App.exe`。
- 2026-06-08 06:30 實測：先送 `close_ticket_app`，確認 `Kiosk.Standard.App.exe` 消失；再送 `restart_ticket_app`，確認 `Kiosk.Standard.App.exe` 重新出現。
- 啟動觀察：第一張畫面顯示 `Device Service 啟動中...`，約 25 秒後第二張畫面進入麵屋六花正常首頁；未見錯誤彈窗，Application log 也未抓到 `Kiosk.Standard` / `.NET Runtime` / `Application Error` / `Windows Error Reporting` 相關錯誤。

## 2026-06-08 即時畫面 / 監控通道

- `KioskMonitorAgent.cs` 部署到售票機 `C:\6KA\kiosk-monitor-agent`，排程 `6KA Kiosk Monitor Agent` 以 `wscript.exe "C:\6KA\kiosk-monitor-agent\start-hidden.vbs"` 背景啟動，HTTP port `9581`。
- 監控通道目前只做觀察用途：`/api/status`、`/api/screenshot?max_width=720`、`/api/processes`、`/api/windows`、`/api/desktop`、`/api/logs?name=...`，不提供遠端命令執行。
- 六花鍵鼠 `TicketPadController.cs` 版號到 `0.2.7`，補了備援 `GET /api/screenshot`，截圖直接以 PNG 回傳，不在售票機落地圖片檔。
- WEB `C:\6KAweb\server.js` 新增 `/api/kiosk/screenshot/status`、`/api/kiosk/screenshot/refresh`、`/api/kiosk/screenshot/latest`，來源是 `http://100.113.224.68:9581/api/screenshot?max_width=720`。
- WEB 只保留最新一張：`C:\6KAweb\data\kiosk_monitor\latest-screenshot.png` 與 `latest-screenshot.json`；每次 refresh 會覆蓋，不累積歷史圖檔。
- Dashboard 售票機區塊新增「即時畫面」欄位，右側改為和「最新訂單」相同的純文字最後截圖時間；點開 overlay 才開始每秒刷新，關閉 `X`、點背景或 Escape 會停止 timer 並 abort 當下 refresh。
- 離線時仍顯示 WEB 快取的最後一張圖，meta 顯示 `售票機離線 · 最後截圖 HH:MM:SS · 5秒後重試`；若完全沒有快取才顯示 placeholder。
- WEB refresh timeout 改成 4000ms；已驗證售票機離線時 `/api/kiosk/screenshot/latest` 仍回 200，latest cache bytes `277739`，abort 測試約 `520ms` 得 `AbortError`。
