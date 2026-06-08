import asyncio
import json
import math
import os
import sys
import requests
from datetime import datetime, timedelta, time
from pathlib import Path
from bleak import BleakScanner

# -------------------------------
# 設定區
# -------------------------------
WEBHOOK_URL = os.environ.get("SWITCHBOT_DISCORD_WEBHOOK_URL", "")
BUSINESS_START, BUSINESS_END = time(11, 30), time(21, 0)

SENSORS = {
    "temp_1": {"mac": "DE:64:45:C6:41:22", "name": "右六", "role": "dining_point_a"},
    "temp_2": {"mac": "DE:64:44:06:50:0B", "name": "左四", "role": "dining_point_b"},
    "temp_3": {"mac": "DE:64:43:C6:31:28", "name": "廚房", "role": "reference_only"},
}
MAC_TO_ID = {config["mac"]: sid for sid, config in SENSORS.items()}

REPORT_INTERVAL_SECONDS = 30
STATUS_LOG_INTERVAL_SECONDS = int(os.getenv("SWITCHBOT_TEMP_STATUS_LOG_INTERVAL", "0"))
SENSOR_TIMEOUT_SECONDS  = 300  # 5分鐘
OFFLINE_NOTIFY_GRACE_SECONDS = 600  # 重啟後先給 BLE 掃描 10 分鐘寬限
SCANNER_RESTART_SECONDS = 3600
EMA_ALPHA = 0.2
VERSION = "2026.06.06-fuzzy-comfort"
STATUS_PATH = Path(os.getenv("SWITCHBOT_TEMP_STATUS_PATH", "/home/djay/projects/order_notify/switchbot_temp_status.json"))

# 舒適度參數
DINING_MIN_TEMP, DINING_MAX_TEMP = 23.0, 26.0
DINING_EDGE_TOLERANCE_C = float(os.getenv("DINING_EDGE_TOLERANCE_C", "0.5"))
DINING_EDGE_OBSERVE_SECONDS = int(os.getenv("DINING_EDGE_OBSERVE_SECONDS", "600"))
DINING_EDGE_TREND_DELTA_C = float(os.getenv("DINING_EDGE_TREND_DELTA_C", "0.2"))

# 出風口結露風險用露點判斷，不用相對濕度本身洗通知。
AC_VENT_CONDENSATION_DEW_POINT_C = float(os.getenv("AC_VENT_CONDENSATION_DEW_POINT_C", "21.0"))
AC_VENT_CONDENSATION_CLEAR_DEW_POINT_C = float(os.getenv("AC_VENT_CONDENSATION_CLEAR_DEW_POINT_C", "20.2"))
AC_VENT_CONDENSATION_MIN_HUMIDITY = float(os.getenv("AC_VENT_CONDENSATION_MIN_HUMIDITY", "70.0"))

# 終端機圖表顯示參數
CHART_MIN_TEMP = 20.0
CHART_MAX_TEMP = 30.0
CHART_WIDTH = 80  # 圖表固定寬度（點數）
TEMP_HISTORY = []

# -------------------------------
# 全域狀態
# -------------------------------
latest = {}
ema_values = {}
last_status = {"connection": "INIT", "comfort_status": "NORMAL"}
comfort_watch = {"candidate": None, "started_at": None, "start_temp": None}
START_TIME = datetime.now()
last_journal_status_key = None
last_journal_status_log_time = None

# 定時與警報頻率控制
last_regular_report_time = None  # 上次定時報告的時間
last_alert_sent_time = {}        # 記錄各類型警報最後發送時間，用於重複提醒


def is_business_hours():
    if not WEBHOOK_URL or not WEBHOOK_URL.startswith("http"):
        return False
    return BUSINESS_START <= datetime.now().time() <= BUSINESS_END


async def discord_notify(message):
    if not is_business_hours():
        # 不在營業時間時不洗頻，只保留簡短狀態
        return
    try:
        r = await asyncio.to_thread(lambda: requests.post(WEBHOOK_URL, json={"content": message}, timeout=10))
        if r.status_code not in [200, 204]:
            print(f"Discord 失敗: {r.status_code}")
    except Exception as e:
        print("Discord 發送異常:", e)


def atomic_write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)


def sensor_snapshot(sid, now):
    cfg = SENSORS[sid]
    row = latest.get(sid)
    if not row:
        return {
            "id": sid,
            "name": cfg["name"],
            "role": cfg["role"],
            "online": False,
            "status": "not_seen",
        }

    age = int((now - row["last_seen"]).total_seconds())
    online = age <= SENSOR_TIMEOUT_SECONDS
    payload = {
        "id": sid,
        "name": cfg["name"],
        "role": cfg["role"],
        "online": online,
        "age_seconds": age,
        "last_seen": row["last_seen"].isoformat(timespec="seconds"),
    }
    if online:
        payload.update({
            "temperature": row["temperature"],
            "humidity": row["humidity"],
            "battery": row["battery"],
        })
    else:
        payload["status"] = f"超過 {SENSOR_TIMEOUT_SECONDS} 秒未更新"
    return payload


def write_status(now, connection, comfort, valid_sensors, smooth_temp=None, smooth_hum=None, status_text=""):
    atomic_write_json(
        STATUS_PATH,
        {
            "ok": True,
            "program": "switchbot_temp_monitor",
            "version": VERSION,
            "pid": os.getpid(),
            "updated_at": now.isoformat(timespec="seconds"),
            "business_hours": is_business_hours(),
            "connection": connection,
            "comfort": comfort,
            "ema": {
                "temperature": round(float(smooth_temp), 1) if smooth_temp is not None else None,
                "humidity": round(float(smooth_hum), 1) if smooth_hum is not None else None,
            },
            "valid_dining_sensor_count": len(valid_sensors),
            "status_text": status_text,
            "sensors": [sensor_snapshot(sid, now) for sid in SENSORS],
        },
    )


def parse_switchbot_meter(data: bytes):
    if len(data) < 11:
        return None
    temp = (data[9] & 0x7F) + (data[8] & 0x0F) / 10
    if not (data[9] & 0x80):
        temp = -temp
    return temp, data[10] & 0x7F


def update_ema(key, val):
    ema_values[key] = val if ema_values.get(key) is None else ema_values[key] * (1 - EMA_ALPHA) + val * EMA_ALPHA
    return ema_values[key]


def dew_point_c(temperature_c, humidity_percent):
    humidity = min(max(float(humidity_percent), 1.0), 100.0)
    a, b = 17.625, 243.04
    gamma = math.log(humidity / 100.0) + (a * float(temperature_c)) / (b + float(temperature_c))
    return (b * gamma) / (a - gamma)


def reset_comfort_watch():
    comfort_watch.update({"candidate": None, "started_at": None, "start_temp": None})


def edge_temperature_worsened(candidate, smooth_temp):
    start_temp = comfort_watch.get("start_temp")
    if start_temp is None:
        return False
    if candidate == "HOT":
        return smooth_temp >= start_temp + DINING_EDGE_TREND_DELTA_C
    return smooth_temp <= start_temp - DINING_EDGE_TREND_DELTA_C


def edge_temperature_ready(now, candidate, smooth_temp):
    if comfort_watch.get("candidate") != candidate:
        comfort_watch.update({"candidate": candidate, "started_at": now, "start_temp": smooth_temp})
        return False

    started_at = comfort_watch.get("started_at")
    if not started_at:
        comfort_watch.update({"candidate": candidate, "started_at": now, "start_temp": smooth_temp})
        return False

    observed_seconds = (now - started_at).total_seconds()
    return observed_seconds >= DINING_EDGE_OBSERVE_SECONDS and edge_temperature_worsened(candidate, smooth_temp)


def classify_temperature_comfort(now, smooth_temp, prev_comfort):
    if prev_comfort == "HOT":
        if smooth_temp <= DINING_MAX_TEMP:
            reset_comfort_watch()
            return "NORMAL", ""
        return "HOT", ""

    if prev_comfort == "COLD":
        if smooth_temp >= DINING_MIN_TEMP:
            reset_comfort_watch()
            return "NORMAL", ""
        return "COLD", ""

    if DINING_MIN_TEMP <= smooth_temp <= DINING_MAX_TEMP:
        reset_comfort_watch()
        return "NORMAL", ""

    if smooth_temp > DINING_MAX_TEMP:
        if smooth_temp >= DINING_MAX_TEMP + DINING_EDGE_TOLERANCE_C:
            reset_comfort_watch()
            return "HOT", ""
        if edge_temperature_ready(now, "HOT", smooth_temp):
            reset_comfort_watch()
            return "HOT", ""
        return "NORMAL", "溫度略高，觀察趨勢中"

    if smooth_temp <= DINING_MIN_TEMP - DINING_EDGE_TOLERANCE_C:
        reset_comfort_watch()
        return "COLD", ""
    if edge_temperature_ready(now, "COLD", smooth_temp):
        reset_comfort_watch()
        return "COLD", ""
    return "NORMAL", "溫度略低，觀察趨勢中"


def callback(device, adv):
    mac = device.address.upper()
    if mac in MAC_TO_ID and adv.manufacturer_data:
        sid = MAC_TO_ID[mac]
        for _, data in adv.manufacturer_data.items():
            parsed = parse_switchbot_meter(data)
            if parsed:
                bat = next((d[2] & 0x7F for uuid, d in adv.service_data.items() if len(d) >= 3), None)
                latest[sid] = {
                    "name": SENSORS[sid]["name"],
                    "temperature": parsed[0],
                    "humidity": parsed[1],
                    "battery": bat,
                    "last_seen": datetime.now()
                }


def ansi_color_for_temp(temp):
    """
    < 23.0  偏冷：藍
    23~26   舒適：綠
    26~28   偏熱：黃
    >= 28   過熱：紅
    """
    if temp < DINING_MIN_TEMP:
        return "\033[44m"  # blue
    if temp <= DINING_MAX_TEMP:
        return "\033[42m"  # green
    if temp < 28.0:
        return "\033[43m"  # yellow
    return "\033[41m"      # red


def clear_console():
    if not sys.stdout.isatty():
        return
    # 呼叫系統原生指令清空終端機，防止殘影與破圖
    os.system('cls' if os.name == 'nt' else 'clear')


def make_temp_chart(history):
    # 確保資料量不超過設定寬度
    clipped = history[-CHART_WIDTH:] if len(history) >= CHART_WIDTH else history
    # 計算還需要補多少空白，才能維持固定寬度不破圖
    padding_count = CHART_WIDTH - len(clipped)
    
    rows = []

    # 由 30 度往 20 度畫
    for level in range(int(CHART_MAX_TEMP), int(CHART_MIN_TEMP) - 1, -1):
        line = f"{level:>2}° "
        
        # 先補左側空白，維持圖表右對齊（新資料從右邊進來）
        line += " " * padding_count
        
        # 繪製折線圖落點
        for temp in clipped:
            if level <= temp < level + 1:
                line += ansi_color_for_temp(temp) + " " + "\033[0m"
            else:
                line += " "
        rows.append(line)

    # 底部 X 軸也維持固定寬度
    axis = "    " + "─" * CHART_WIDTH
    return "\n".join(rows + [axis])


def format_sensor_row(sid, now):
    cfg = SENSORS[sid]
    row = latest.get(sid)

    if not row:
        return f"{cfg['name']:<4}  未連線"

    age = (now - row["last_seen"]).total_seconds()
    if age > SENSOR_TIMEOUT_SECONDS:
        return f"{cfg['name']:<4}  超過 {SENSOR_TIMEOUT_SECONDS} 秒未更新"

    battery_text = f"{row['battery']}%" if row["battery"] is not None else "N/A"
    return (
        f"{cfg['name']:<4}  "
        f"{row['temperature']:>4.1f}°C  "
        f"{row['humidity']:>2}%  "
        f"電量 {battery_text:<4}  "
        f"{int(age):>3}秒前"
    )


def render_dashboard(now, valid_sensors, smooth_temp=None, smooth_hum=None, status_text="", connection=None, comfort=None):
    if not sys.stdout.isatty():
        conn_text = connection or last_status.get("connection", "INIT")
        comfort_text = comfort or last_status.get("comfort_status", "NORMAL")
        ema_text = "-"
        if smooth_temp is not None and smooth_hum is not None:
            ema_text = f"{smooth_temp:.1f}C/{smooth_hum:.1f}%"
        log_journal_status(now, conn_text, comfort_text, ema_text, len(valid_sensors), status_text)
        return

    clear_console()

    business_text = "營業中" if is_business_hours() else "非營業時間"
    conn_text = connection or last_status.get("connection", "INIT")
    comfort_text = comfort or last_status.get("comfort_status", "NORMAL")

    print(f"店內溫濕度監控 | {now.strftime('%Y-%m-%d %H:%M:%S')} | {business_text}")
    print(f"連線: {conn_text} | 狀態: {comfort_text} | 顯示更新間隔: {REPORT_INTERVAL_SECONDS}秒")
    print()

    print(make_temp_chart(TEMP_HISTORY))
    print()

    print("即時溫度")
    for sid in SENSORS:
        print(format_sensor_row(sid, now))

    if smooth_temp is not None and smooth_hum is not None:
        label = "單點平均" if len(valid_sensors) == 1 else "兩點平均"
        print()
        print(f"用餐區 EMA {label}: {smooth_temp:.1f}°C / 濕度 {smooth_hum:.1f}%")

    if status_text:
        print()
        print(status_text)


def log_journal_status(now, connection, comfort, ema_text, valid_count, status_text=""):
    global last_journal_status_key, last_journal_status_log_time
    key = (connection, comfort, valid_count, status_text)
    if key == last_journal_status_key and last_journal_status_log_time:
        if STATUS_LOG_INTERVAL_SECONDS <= 0:
            return
        if (now - last_journal_status_log_time).total_seconds() < STATUS_LOG_INTERVAL_SECONDS:
            return
    last_journal_status_key = key
    last_journal_status_log_time = now
    suffix = f" note={status_text}" if status_text else ""
    print(
        f"[{now.strftime('%Y-%m-%d %H:%M:%S')}] status connection={connection} comfort={comfort} ema={ema_text} valid={valid_count}{suffix}",
        flush=True,
    )


async def check_and_report():
    global last_status, last_regular_report_time, last_alert_sent_time
    now = datetime.now()

    startup_age_seconds = (now - START_TIME).total_seconds()
    is_init_phase = startup_age_seconds < 45

    t1, t2 = latest.get("temp_1"), latest.get("temp_2")
    has_seen_dining_sensor = bool(t1 or t2)
    t1_stale = True if not t1 else (now - t1["last_seen"]).total_seconds() > SENSOR_TIMEOUT_SECONDS
    t2_stale = True if not t2 else (now - t2["last_seen"]).total_seconds() > SENSOR_TIMEOUT_SECONDS

    dining_stale_count = sum(1 for stale in (t1_stale, t2_stale) if stale)
    if dining_stale_count == 0:
        current_conn = "OK"
    elif dining_stale_count == 1:
        current_conn = "DEGRADED"
    else:
        current_conn = "OFFLINE"

    startup_scan_grace = (
        current_conn == "OFFLINE"
        and not has_seen_dining_sensor
        and startup_age_seconds < OFFLINE_NOTIFY_GRACE_SECONDS
    )

    # 斷線告警邏輯
    if not is_init_phase and current_conn != last_status["connection"]:
        if current_conn == "OFFLINE":
            if not startup_scan_grace:
                await discord_notify(f"溫度監控異常：用餐區感測器都超過 {SENSOR_TIMEOUT_SECONDS // 60} 分鐘未更新")
                last_status["connection"] = current_conn
        elif current_conn == "OK" and last_status["connection"] == "OFFLINE":
            await discord_notify("溫度監控恢復：用餐區感測器正常")
            last_status["connection"] = current_conn
        else:
            last_status["connection"] = current_conn

    valid_sensors = [
        t for t in [t1, t2]
        if t and (now - t["last_seen"]).total_seconds() <= SENSOR_TIMEOUT_SECONDS
    ]

    status_text = ""

    if valid_sensors:
        raw_temp_avg = sum(s["temperature"] for s in valid_sensors) / len(valid_sensors)
        raw_hum_avg = sum(s["humidity"] for s in valid_sensors) / len(valid_sensors)

        smooth_temp = update_ema("dining_temp_avg", raw_temp_avg)
        smooth_hum = update_ema("dining_hum_avg", raw_hum_avg)

        # 圖表使用即時原始平均值，不論是否在初始化階段都推進去，確保寬度提早就定位
        TEMP_HISTORY.append(raw_temp_avg)
        if len(TEMP_HISTORY) > CHART_WIDTH:
            del TEMP_HISTORY[:-CHART_WIDTH]

        if not is_init_phase:
            label = "單點平均" if len(valid_sensors) == 1 else "兩點平均"
            data_info = f"（{label}：{smooth_temp:.1f} °C / 濕度：{smooth_hum:.1f} %）"

            prev_comfort = last_status["comfort_status"]
            business_hours = is_business_hours()
            smooth_dew_point = dew_point_c(smooth_temp, smooth_hum)
            condensation_threshold = (
                AC_VENT_CONDENSATION_CLEAR_DEW_POINT_C
                if prev_comfort == "CONDENSATION_RISK"
                else AC_VENT_CONDENSATION_DEW_POINT_C
            )
            condensation_risk = (
                business_hours
                and smooth_hum >= AC_VENT_CONDENSATION_MIN_HUMIDITY
                and smooth_dew_point >= condensation_threshold
            )
            
            # 狀態轉移邏輯：濕度本身不通知；結露只在營業時間冷氣可能運轉時成立。
            if condensation_risk:
                current_comfort = "CONDENSATION_RISK"
                reset_comfort_watch()
            else:
                current_comfort, comfort_note = classify_temperature_comfort(now, smooth_temp, prev_comfort)
                if comfort_note:
                    status_text = comfort_note

            # 告警發送邏輯
            if current_comfort != prev_comfort:
                if current_comfort == "HOT":
                    await discord_notify(f"溫度偏高：用餐區 {smooth_temp:.1f}°C")
                    last_alert_sent_time["comfort"] = now
                elif current_comfort == "COLD":
                    await discord_notify(f"溫度偏低：用餐區 {smooth_temp:.1f}°C")
                    last_alert_sent_time["comfort"] = now
                elif current_comfort == "CONDENSATION_RISK":
                    await discord_notify(f"出風口結露風險：用餐區露點 {smooth_dew_point:.1f}°C（{smooth_temp:.1f}°C / {smooth_hum:.1f}%）")
                    last_alert_sent_time["comfort"] = now
                elif current_comfort == "NORMAL" and prev_comfort in ["HOT", "COLD"]:
                    await discord_notify(f"溫濕度恢復正常：用餐區 {smooth_temp:.1f}°C / {smooth_hum:.1f}%")

                last_status["comfort_status"] = current_comfort
                # 狀態一變動，重設常態回報計時點，避免恢復正常後沒多久又馬上跳定時報告
                last_regular_report_time = now
            else:
                # 持續異常不重複洗 DC；狀態留在 heartbeat 給伺服器端看。
                pass

            last_regular_report_time = now
        else:
            status_text = "系統初始化中，正在收集感測器訊號..."

        write_status(now, current_conn, last_status["comfort_status"], valid_sensors, smooth_temp, smooth_hum, status_text)
        render_dashboard(
            now,
            valid_sensors,
            smooth_temp=smooth_temp if not is_init_phase else None,
            smooth_hum=smooth_hum if not is_init_phase else None,
            status_text=status_text,
            connection=current_conn,
            comfort=last_status["comfort_status"],
        )

    else:
        if is_init_phase:
            status_text = "系統初始化中，正在收集感測器訊號..."
        elif startup_scan_grace:
            status_text = "正在掃描用餐區感測器，暫不發送 DC 通知。"
        else:
            status_text = "警告：無用餐區有效感測器數據，無法計算平均溫濕度。"

        write_status(now, current_conn, last_status["comfort_status"], valid_sensors, status_text=status_text)
        render_dashboard(
            now,
            valid_sensors,
            status_text=status_text,
            connection=current_conn,
            comfort=last_status["comfort_status"],
        )


async def run_scanner():
    scanner = BleakScanner(callback)
    await scanner.start()
    try:
        await asyncio.sleep(SCANNER_RESTART_SECONDS)
    finally:
        await scanner.stop()


async def main_loop():
    print("店內溫濕度監控啟動中...")
    last_report = datetime.now() - timedelta(seconds=REPORT_INTERVAL_SECONDS)

    while True:
        scanner_task = asyncio.create_task(run_scanner())
        try:
            while not scanner_task.done():
                await asyncio.sleep(1)
                if (datetime.now() - last_report).total_seconds() >= REPORT_INTERVAL_SECONDS:
                    await check_and_report()
                    last_report = datetime.now()
            await scanner_task
        except Exception as e:
            print("藍牙重啟異常處理:", e)
            if not scanner_task.done():
                scanner_task.cancel()
                try:
                    await scanner_task
                except Exception:
                    pass
            await asyncio.sleep(5)


if __name__ == "__main__":
    if os.name == 'nt':
        os.system('')

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        print("\n程式已停止。")
