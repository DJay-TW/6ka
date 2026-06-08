#!/bin/sh
set -eu

AGENT=/home/djay/bin/6ka_pi_agent.py
LOG=/home/djay/bin/6ka_pi_agent.log
PIDFILE=/home/djay/bin/6ka_pi_agent.pid

if [ -f "$PIDFILE" ]; then
    PID="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
        exit 0
    fi
fi

if pgrep -f "$AGENT" >/dev/null 2>&1; then
    pgrep -f "$AGENT" | head -n 1 > "$PIDFILE"
    exit 0
fi

nohup /usr/bin/python3 -u "$AGENT" >> "$LOG" 2>&1 &
echo "$!" > "$PIDFILE"
