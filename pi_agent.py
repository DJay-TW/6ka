#!/usr/bin/env python3
import json
import os
import re
import socket
import subprocess
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import request as urlrequest
from urllib.parse import urlparse


HOST = os.getenv("PI_AGENT_HOST", "100.114.19.115")
PORT = int(os.getenv("PI_AGENT_PORT", "3011"))
TOKEN = os.getenv("PI_AGENT_TOKEN", "")

AUDIO_CARD = os.getenv("PI_AGENT_AUDIO_CARD", "LABTLM40XP")
AUDIO_CONTROL = os.getenv("PI_AGENT_AUDIO_CONTROL", "PCM")
APLAY_DEVICE = os.getenv("PI_AGENT_APLAY_DEVICE", "plughw:CARD=LABTLM40XP,DEV=0")
WAV_DIR = os.getenv("PI_AGENT_WAV_DIR", "/home/djay/projects/order_notify/wav")
PLAYER_SERVICE = "order_notify.service"
TEMP_SERVICE = "switchbot_temp_monitor.service"
PLAYER_LOCAL_API = os.getenv("PI_AGENT_PLAYER_LOCAL_API", "http://127.0.0.1:3021")


def run(cmd, timeout=5):
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except Exception as exc:
        return 1, "", str(exc)


def service_state(name):
    code, active, _ = run(["systemctl", "is-active", name])
    _, enabled, _ = run(["systemctl", "is-enabled", name])
    _, status, _ = run(["systemctl", "show", name, "--property=MainPID,ExecMainStartTimestamp,SubState", "--no-page"])
    detail = {}
    for line in status.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            detail[key] = value
    return {
        "name": name,
        "active": active if active else "unknown",
        "enabled": enabled if enabled else "unknown",
        "ok": code == 0 and active == "active",
        "main_pid": detail.get("MainPID"),
        "sub_state": detail.get("SubState"),
        "started_at": detail.get("ExecMainStartTimestamp"),
    }


def audio_status():
    _, aplay, _ = run(["aplay", "-l"])
    available = AUDIO_CARD in aplay
    code, mixer, err = run(["amixer", "-c", AUDIO_CARD, "sget", AUDIO_CONTROL])
    volume = None
    muted = None
    match = re.search(r"\[(\d+)%\].*\[(on|off)\]", mixer)
    if match:
        volume = int(match.group(1))
        muted = match.group(2) == "off"
    return {
        "available": available,
        "card": AUDIO_CARD,
        "control": AUDIO_CONTROL,
        "volume": volume,
        "muted": muted,
        "mixer_ok": code == 0,
        "error": err if code != 0 else "",
    }


def parse_temperature_log():
    _, log, _ = run(["journalctl", "-u", TEMP_SERVICE, "-n", "160", "--no-pager", "-o", "cat"], timeout=8)
    sensors = {}
    ema = {}
    connection = None
    comfort = None
    for raw in log.splitlines():
        line = raw.strip()
        status_match = re.search(r"連線:\s*(\S+)\s*\|\s*狀態:\s*(\S+)", line)
        if status_match:
            connection = status_match.group(1)
            comfort = status_match.group(2)
            continue
        sensor_match = re.search(r"^(右六|左四|廚房)\s+([\d.]+)°C\s+(\d+)%\s+電量\s+(\S+)\s+(\d+)秒前", line)
        if sensor_match:
            sensors[sensor_match.group(1)] = {
                "name": sensor_match.group(1),
                "temperature": float(sensor_match.group(2)),
                "humidity": int(sensor_match.group(3)),
                "battery": sensor_match.group(4),
                "age_seconds": int(sensor_match.group(5)),
                "online": True,
            }
            continue
        offline_match = re.search(r"^(右六|左四|廚房)\s+(.+未連線|超過.+未更新)", line)
        if offline_match:
            sensors[offline_match.group(1)] = {
                "name": offline_match.group(1),
                "online": False,
                "status": offline_match.group(2),
            }
            continue
        ema_match = re.search(r"用餐區 EMA .*:\s*([\d.]+)°C\s*/\s*濕度\s*([\d.]+)%", line)
        if ema_match:
            ema = {
                "temperature": float(ema_match.group(1)),
                "humidity": float(ema_match.group(2)),
            }
    return {
        "connection": connection,
        "comfort": comfort,
        "ema": ema,
        "sensors": list(sensors.values()),
    }


def pi_status():
    uptime_seconds = None
    try:
        with open("/proc/uptime", "r", encoding="utf-8") as handle:
            uptime_seconds = int(float(handle.read().split()[0]))
    except Exception:
        pass

    cpu_temp = None
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r", encoding="utf-8") as handle:
            cpu_temp = round(int(handle.read().strip()) / 1000, 1)
    except Exception:
        pass

    mem = {}
    try:
        values = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                values[key] = int(value.strip().split()[0])
        total = values.get("MemTotal")
        available = values.get("MemAvailable")
        if total and available:
            mem = {
                "total_mb": round(total / 1024),
                "available_mb": round(available / 1024),
                "used_percent": round((1 - available / total) * 100, 1),
            }
    except Exception:
        pass

    return {
        "hostname": socket.gethostname(),
        "time": datetime.now().isoformat(timespec="seconds"),
        "uptime_seconds": uptime_seconds,
        "load_average": list(os.getloadavg()),
        "cpu_temperature": cpu_temp,
        "memory": mem,
    }


def status_payload():
    return {
        "ok": True,
        "source": "pi_agent",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "pi": pi_status(),
        "services": {
            "player": service_state(PLAYER_SERVICE),
            "temperature": service_state(TEMP_SERVICE),
        },
        "audio": audio_status(),
        "temperature": parse_temperature_log(),
    }


def post_player(path, body):
    encoded = json.dumps(body or {}).encode("utf-8")
    req = urlrequest.Request(
        PLAYER_LOCAL_API + path,
        data=encoded,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlrequest.urlopen(req, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "player local API returned ok=false")
    return payload


def set_volume(volume):
    result = post_player("/api/audio/volume", {"volume": max(0, min(100, int(volume)))})
    result["audio"] = audio_status()
    return result


def test_audio(kind, body):
    if kind == "order":
        payload = {
            "type": "order",
            "order_no": body.get("order_no") or body.get("number") or body.get("message") or "001",
            "message": body.get("message") or "",
        }
    elif kind == "online":
        payload = {"type": "online", "message": body.get("message") or ""}
    elif kind == "offline":
        payload = {"type": "offline", "message": body.get("message") or ""}
    else:
        raise RuntimeError(f"unsupported audio test: {kind}")
    result = post_player("/api/audio/event", payload)
    result["test"] = kind
    return result


def control_service(service, action):
    names = {
        "player": PLAYER_SERVICE,
        "temperature": TEMP_SERVICE,
    }
    if service not in names:
        raise RuntimeError(f"unknown service: {service}")
    if action not in ("start", "stop", "restart"):
        raise RuntimeError(f"unsupported action: {action}")
    code, out, err = run(["sudo", "-n", "systemctl", action, names[service]], timeout=10)
    if code != 0:
        raise RuntimeError(err or out or f"systemctl returned {code}")
    return {"ok": True, "service": service, "action": action}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        print(f"[{datetime.now().isoformat(timespec='seconds')}] {self.client_address[0]} {fmt % args}", flush=True)

    def send_json(self, status, payload):
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        except Exception as exc:
            print(f"[send_json error] {exc}", flush=True)

    def read_json(self):
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def check_token(self):
        if not TOKEN:
            return True
        return self.headers.get("X-6KA-Token") == TOKEN

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/health":
                self.send_json(200, {"ok": True, "service": "6ka-pi-agent", "time": datetime.now().isoformat(timespec="seconds")})
                return
            if path == "/api/status":
                self.send_json(200, status_payload())
                return
            self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})

    def do_POST(self):
        path = urlparse(self.path).path
        if not self.check_token():
            self.send_json(403, {"ok": False, "error": "forbidden"})
            return
        try:
            body = self.read_json()
            if path == "/api/audio/volume":
                self.send_json(200, set_volume(body.get("volume", body.get("message", 50))))
                return
            if path == "/api/audio/test":
                self.send_json(200, test_audio(str(body.get("type") or body.get("test") or ""), body))
                return
            service_match = re.fullmatch(r"/api/service/([^/]+)/(start|stop|restart)", path)
            if service_match:
                self.send_json(200, control_service(service_match.group(1), service_match.group(2)))
                return
            if path == "/api/reboot":
                code, out, err = run(["sudo", "-n", "reboot"], timeout=5)
                self.send_json(200 if code == 0 else 500, {"ok": code == 0, "error": err or out})
                return
            self.send_json(404, {"ok": False, "error": "not found"})
        except Exception as exc:
            self.send_json(500, {"ok": False, "error": str(exc)})


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.request_queue_size = 20
    print(f"6ka-pi-agent listening on http://{HOST}:{PORT}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
