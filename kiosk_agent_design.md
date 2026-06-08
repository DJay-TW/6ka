# 售票機 Agent 設計草案

更新時間：2026-06-02 Asia/Taipei

## 目標

在售票機本機建立只讀 Agent，透過 Tailscale 供 Windows 訊息中心存取，取代目前不穩定的 WinRM 即時查詢。Agent 負責本機 SQL Server 查詢、增量同步、健康狀態回報，以及必要時的事件通知。

## 原則

- 售票機是交易核心，Agent 必須只讀 SQL。
- Dashboard 不直接查售票機 SQL，也不承受 WinRM timeout。
- Agent API 只允許 Tailscale/內網授權來源存取。
- 同步資料落地在 Windows 訊息中心本機 SQLite，作為 Dashboard 查詢來源。
- 狀態必須分辨：
  - Agent 活著，但 SQL 連不上。
  - Agent 活著，SQL 正常，但同步失敗。
  - Agent 整體離線。
  - 本機 cache 有資料，但資料新鮮度落後。

## API

### `GET /health`

用途：最輕量呼吸檢查。訊息中心可高頻呼叫。

建議回傳：

```json
{
  "ok": true,
  "service": "6ka-kiosk-agent",
  "version": "0.1.0",
  "host": "DESKTOP-B1C1NU5",
  "started_at": "2026-06-02T15:30:00+08:00",
  "uptime_seconds": 120,
  "time": "2026-06-02T15:32:00+08:00"
}
```

### `GET /api/status`

用途：完整狀態。訊息中心用它判斷售票機是不是活著、SQL 是否可讀、同步是否健康。

建議回傳：

```json
{
  "ok": true,
  "agent": {
    "online": true,
    "version": "0.1.0",
    "host": "DESKTOP-B1C1NU5",
    "started_at": "2026-06-02T15:30:00+08:00",
    "uptime_seconds": 120
  },
  "database": {
    "ok": true,
    "instance": "localhost\\SQLEXPRESS",
    "database": "SuitRepository",
    "latency_ms": 22,
    "latest_order_time": "2026-06-02 14:46:28",
    "max_business_date": "2026-06-02",
    "order_rows": 96781
  },
  "sync": {
    "running": false,
    "last_success_at": "2026-06-02T15:10:05+08:00",
    "last_error_at": null,
    "last_error": null,
    "source_latest_order_time": "2026-06-02 14:46:28",
    "last_exported_order_time": "2026-06-02 14:46:28"
  }
}
```

### `POST /api/sync/run`

用途：要求 Agent 立即同步。可支援 `mode`：

- `incremental`：預設，只同步最後成功時間之後的資料。
- `today`：重抓今天資料。
- `range`：指定日期區間。

建議請求：

```json
{
  "mode": "incremental"
}
```

建議回傳：

```json
{
  "ok": true,
  "accepted": true,
  "job_id": "20260602-153000"
}
```

### `GET /api/sync/status`

用途：查同步工作狀態。

### `GET /api/sales/export?since=2026-06-02T14:46:28`

用途：由 Windows 訊息中心拉取增量資料。初期可回 JSON；若資料量大，改成壓縮 CSV/NDJSON 檔案下載。

## 新鮮度判斷

訊息中心應保存兩組時間：

- `agent_seen_at`：最後一次成功打到 Agent 的時間。
- `agent_source_latest_order_time`：Agent 回報的售票機 DB 最新訂單時間。
- `local_last_success_at`：Windows 本機 SQLite 最後同步成功時間。
- `local_latest_order_time`：Windows 本機 SQLite 最新訂單時間。

判斷方式：

- Agent 打不到：售票機 Agent offline。
- Agent 打得到但 `database.ok=false`：Agent online，售票機 SQL 異常。
- `agent_source_latest_order_time > local_latest_order_time`：本機資料落後。
- `local_last_success_at` 太久沒更新：同步健康度有風險。

## 訊息中心整合

`server.js` 可新增 `queryKioskAgentStatus()`，呼叫售票機 Tailscale API：

```text
GET http://<kiosk-tailscale-ip>:3010/api/status
```

並把結果整理進 `/api/server/status` 的 `kiosk`：

```json
{
  "kiosk": {
    "ok": true,
    "online": true,
    "agent_seen_at": "2026-06-02T15:40:00+08:00",
    "agent_latency_ms": 35,
    "database_ok": true,
    "source_latest_order_time": "2026-06-02 14:46:28",
    "local_latest_order_time": "2026-06-02 14:46:28",
    "freshness_lag_seconds": 0,
    "last_success_at": "2026-06-02 15:10:05",
    "last_error": null
  }
}
```

建議 timeout 控在 2 秒內。Agent timeout 時不要拖住整個 `/api/server/status`；直接回 `online=false`，並沿用本機 SQLite 的最後同步狀態。

## 服務部署

初期建議：

- Python + Flask/FastAPI。
- Windows 開機排程任務啟動。
- 先跑在 `127.0.0.1` 或 Tailscale IP 綁定的 port。
- 確認穩定後再包成 Windows Service。

## 事件通知

售票機 Agent 或另一個 File Watcher Agent 可監控當天廚房製作單資料夾：

- 新檔案建立或修改時，呼叫訊息中心 webhook。
- 訊息中心通知廚房製作單程式掃描該檔案或該日資料夾。
- 保留低頻補掃，例如每 5 或 10 分鐘一次，避免事件漏掉時永遠不補。

建議 webhook：

```http
POST /api/events/kitchen-log-changed
```

```json
{
  "source": "kiosk-agent",
  "event": "created",
  "path": "C:\\6KA_log\\2026-06-02\\...",
  "detected_at": "2026-06-02T15:35:00+08:00"
}
```
