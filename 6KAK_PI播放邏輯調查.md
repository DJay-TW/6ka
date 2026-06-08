# 6KAK / Pi 播放邏輯調查

更新時間：2026-06-02

## 調查範圍

- 廚房製作單：`C:\6KAK\6kak_v2.0.py`
- Pi 播放器：`/home/djay/player/player_api.py`
- 目前新橋接層：`C:\Users\88698\Documents\6KA系統開發\pi_agent.py`
- WEB 轉發層：`C:\6KAweb\server.js`

## 目前總資料流

```text
售票機 ProtechFile Kitchen txt
  -> 6KAK 廚房製作單程式
  -> Cloudflare Worker 6KAS /push
  -> Pi player_api.py 每 2 秒 poll
  -> Pi 播放佇列
  -> wav 音檔序列
  -> sox 串接
  -> aplay 播放到 LABTLM40XP 聲霸
```

目前已新增但廚房製作單尚未切換的直連路徑：

```text
6KAK 或 WEB
  -> Pi Agent http://100.114.19.115:3011
  -> player_api.py 本地 API http://127.0.0.1:3021
  -> player_api.py 原 queue / build_sequence / process_queue / play_sequence
  -> 聲霸
```

## 6KAK 廚房製作單邏輯

### 監控來源

`6kak_v2.0.py` 每 3 秒掃描售票機共享路徑：

```text
PRIMARY_PATH   = //DESKTOP-B1C1NU5/ProtechFile
TAILSCALE_PATH = //100.113.224.68/ProtechFile
```

每輪會優先檢查 `PRIMARY_PATH`，不存在時改用 Tailscale 路徑。實際 Kitchen 目錄：

```text
<BASE_PATH>/<YYYY-MM-DD>/DeviceLog/Kitchen
```

只處理 `.txt` 檔。

### 防重複與穩定檢查

- 已處理檔案記錄在 `C:\6KA_log\processed_cache.json`。
- 每輪會用今日日期清掉舊紀錄，只保留今天的 processed cache。
- 新檔案需通過 `is_file_stable()`：
  - 等待 `FILE_STABLE_WAIT = 2` 秒。
  - 比對檔案大小與 mtime 是否不變。
  - 檔案大小需大於 0。

### 內容解析

`parse_content()` 會把售票機 Kitchen txt 轉成比較適合通知的格式：

- 只保留第一個桌名。
- 只保留第一個單號。
- 單號取最後三碼。
- 跳過 `數量`、`列印`、`Name:`、`Condition1:`、`Condition2:`。
- 有數字結尾的品項前面加 `###`。
- 其他行前面加 `└`。

`extract_order_number()` 的單號來源：

1. 優先從解析後內容的 `單號 xxx` 取。
2. 取不到時，從檔名 `單號-xxx` 抓。
3. 都取不到則回傳 `unknown`。

### 通知與播放事件

`process_order_file()` 成功解析後會：

1. 存一份解析後內容到：
   `C:\6KA_log\<YYYY-MM-DD>\<原檔名處理後>`
2. 呼叫 `send_notify()`。

`send_notify()` 會同時送：

- Discord
- Telegram
- 播音 API

目前播音 API 是：

```text
PLAYER_API_URL   = https://6kas.jay-fbf.workers.dev/push
PLAYER_API_TOKEN = 558811566
```

新訂單事件 payload：

```json
{
  "token": "558811566",
  "type": "new_order",
  "device": "windows_server",
  "message": "單號 123",
  "order_no": "123"
}
```

### 售票機連線狀態事件

6KAK 也會偵測 Kitchen 路徑是否可用：

- 連續 3 次不可用後，送 `device_offline`：

```json
{
  "type": "device_offline",
  "message": "連線中斷：無法連接售票機"
}
```

- 從斷線恢復時，送 `device_online`：

```json
{
  "type": "device_online",
  "message": "已成功連線售票機"
}
```

這兩種事件也會進 Discord / Telegram / 播音 API。

## Pi player_api.py 播放邏輯

### 基本設定

Pi 播放器目前由 systemd 啟動：

```text
service: order_notify.service
ExecStart=/usr/bin/python3 -u /home/djay/player/player_api.py
WorkingDirectory=/home/djay/player
```

主要設定：

```text
API_BASE      = https://6kas.jay-fbf.workers.dev
TOKEN         = 558811566
WAV_DIR       = /home/djay/projects/order_notify/wav
POLL_INTERVAL = 2
PLAY_TIMEOUT  = 15
APLAY_DEVICE  = plughw:CARD=LABTLM40XP,DEV=0
AMIXER_CARD   = LABTLM40XP
AMIXER_CONTROL= PCM
```

### Cloudflare 輪詢

`main()` 每 2 秒執行：

1. `check_hourly_event()`
2. `poll_api()`
3. 有事件就 `enqueue_event(event)`
4. `process_queue()`
5. 檢查硬體音量是否變化，變化時回報 Cloudflare `/update-status`

`poll_api()` 呼叫：

```text
GET https://6kas.jay-fbf.workers.dev/poll?device=pi_player
```

可接受回傳格式：

- `{ "has_event": false }`
- `{ "events": [...] }`
- `{ "event": {...} }`
- 直接 `{ "type": ... }`
- event list

### 事件優先權

播放佇列 `queue` 使用 priority 排序：

```text
volume         0
new_order      1
device_online  2
device_offline 2
hourly         3
```

音量事件最高優先，整點報時最低。

### 音檔組合

`build_sequence(event)` 將事件轉成 wav 檔序列。

新訂單：

```text
type = new_order
order_no / number / message 裡取數字
取最後三碼並補零

播放：
new_order.wav
number.wav
<三碼單號>.wav
```

例：

```text
order_no = 7
-> new_order.wav, number.wav, 007.wav
```

裝置上線：

```text
state.wav, online.wav
```

裝置離線：

```text
state.wav, offline.wav
```

整點報時：

```text
time.wav, hHH.wav
```

例：

```text
18:00 -> time.wav, h18.wav
```

音量事件：

```text
type = volume
不播放音檔，直接 set_volume()
```

### 實際播放

`play_sequence(files)` 會：

1. 檢查每個 wav 是否存在。
2. 用 `sox` 把多個 wav 串成 stdout：

```text
sox <files...> -t wav -
```

3. pipe 給 `aplay`：

```text
aplay -D plughw:CARD=LABTLM40XP,DEV=0
```

4. 最多等待 `PLAY_TIMEOUT = 15` 秒。
5. `aplay` return code 非 0 或 timeout 視為播放失敗。

### 音量控制

`set_volume(event)`：

1. 從 `event.volume` 或 `event.message` 抓數字。
2. 限制在 `0~100`。
3. 執行：

```text
amixer -c LABTLM40XP sset PCM <volume>%
```

4. 成功後呼叫 Cloudflare `/update-status` 回報音量。

### 音效裝置故障告警

`is_audio_device_available()` 用：

```text
aplay -l
```

確認 `LABTLM40XP` 是否存在。

播放 `new_order`、`device_online`、`device_offline` 失敗時，會送 Discord 告警：

```text
# 聲音裝置尚未啟動
```

告警冷卻時間為 600 秒。

## 2026-06-02 更新：player_api 改為本地 API 控制

重要原則：

```text
player_api.py 的播放核心不可更動。
尤其是多個 wav 由 sox 串接後一次交給 aplay 播放，這是避免聲霸吞音/靜音的關鍵。
```

已將 `player_api.py` 從「Cloudflare/KV poller」改成「Pi 本地播放器 API」：

- 本地 API：`http://127.0.0.1:3021`
- `GET /health`
- `POST /api/audio/event`
- `POST /api/audio/volume`
- 預設 `PLAYER_ENABLE_CLOUD_POLL=0`，不再輪詢 Cloudflare Worker / KV。
- 預設 `PLAYER_ENABLE_CLOUD_REPORT=0`，不再回報 Cloudflare 音量。
- 整點報時仍保留在 `player_api.py`。
- 原本 queue / priority / `build_sequence()` / `process_queue()` / `play_sequence()` 均保留。

Pi Agent 現在只作為外部 HTTP 門面：

```text
POST http://100.114.19.115:3011/api/audio/volume
POST http://100.114.19.115:3011/api/audio/test
```

Pi Agent 會轉送到：

```text
POST http://127.0.0.1:3021/api/audio/volume
POST http://127.0.0.1:3021/api/audio/event
```

也就是：

```text
WEB / Dashboard
  -> Pi Agent
  -> player_api.py 本地 API
  -> 原 player_api queue
  -> 原 sox -> aplay 一次播放
```

### 新本地事件格式

player_api 本地 API 可接受：

```json
{ "type": "order", "order_no": "123" }
{ "type": "new_order", "order_no": "123" }
{ "type": "online" }
{ "type": "offline" }
{ "type": "device_online" }
{ "type": "device_offline" }
```

本地 API 會把 `order` 對應為 `new_order`，`online/offline` 對應為 `device_online/device_offline`，再送進原本 `enqueue_event()`。

音量：

```json
{ "volume": 80 }
```

音量也會進原本 queue，優先權仍是 `volume = 0`。

## 兩套邏輯的關係

舊 Cloudflare poll 路徑已停用，但仍可用環境變數恢復：

```text
PLAYER_ENABLE_CLOUD_POLL=1
PLAYER_ENABLE_CLOUD_REPORT=1
```

目前播放控制實際路徑：

```text
Dashboard / WEB / 6KAK
  -> WEB /api/control/pi-audio
  -> Pi Agent
  -> player_api.py localhost API
  -> 原 player_api queue
  -> 原 sox -> aplay 播放
```

Dashboard 聲霸控制已改成：

```text
Dashboard
  -> WEB /api/control/pi-audio
  -> Pi Agent
  -> 播放或調音量
```

WEB 有 fallback：

```text
Pi Agent 失敗
  -> Cloudflare Worker
```

## 2026-06-02 更新：6KAK 已改送 WEB 播放 API

正式 `C:\6KAK\6kak_v2.0.py` 已從 Cloudflare Worker 改成 WEB 播放 API：

```text
6KAK
  -> WEB /api/control/pi-audio
  -> Pi Agent
  -> player_api.py 本地 API
  -> player_api.py 原播放佇列
```

原因：

- WEB 已有 Pi Agent fallback Cloudflare Worker。
- 6KAK 不需要知道 Pi Agent IP、port、未來 token。
- 可以集中記錄播放 API 成功/失敗。
- 未來要切回/切換播放路由，只改 WEB。

6KAK 原本：

```text
send_player_api_notify()
  -> https://6kas.jay-fbf.workers.dev/push
```

目前已改成：

```text
send_player_api_notify()
  -> http://100.114.61.65:3000/api/control/pi-audio
```

正式設定：

```text
PLAYER_API_URL = http://100.114.61.65:3000/api/control/pi-audio
PLAYER_API_TOKEN = ""
```

新訂單 payload 對 WEB 可用：

```json
{
  "type": "new_order",
  "message": "單號 123",
  "order_no": "123"
}
```

WEB 會轉成 Pi Agent：

```json
{
  "type": "order",
  "order_no": "123"
}
```

離線/上線 payload：

```json
{ "type": "device_offline", "message": "連線中斷：無法連接售票機" }
{ "type": "device_online", "message": "已成功連線售票機" }
```

WEB 會轉成 Pi Agent：

```json
{ "type": "offline" }
{ "type": "online" }
```

## 改造注意點

1. 不要把正式訂單播放改成 Pi Agent 自己播放。

2. 不要拆掉 `player_api.py` 的 `sox -> aplay` 一次播放流程。

3. 目前 6KAK 仍同時通知 Discord / Telegram。切播放路徑時，不應影響 Discord / Telegram。

4. WEB `/api/control/pi-audio` 目前仍保留 Cloudflare fallback。切換期可以保留。

已備份正式檔：

```text
C:\6KAK\6kak_v2.0.py.bak-20260602-192017-web-pi-audio
```

已重啟正式廚房製作單：

```text
kitchen_dc active
PID 17676
```

已驗證：

```text
直接呼叫正式 6KAK send_player_api_notify(..., event_type="device_online")
  -> WEB /api/control/pi-audio
  -> Pi Agent
  -> player_api local API
  -> player_api journal 顯示 START/DONE device_online
```

## 建議下一步

1. 觀察 1~2 個營業時段。
2. 確認 `new_order`、`device_online`、`device_offline` 都穩定進入 player_api queue。
3. 穩定後再評估是否移除 Cloudflare fallback。
