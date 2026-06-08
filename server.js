const express = require('express');
const WebSocket = require('ws');
const chokidar = require('chokidar');
const fs = require('fs');
const http = require('http');
const os = require('os');
const path = require('path');
const { execFile, execFileSync, spawn } = require('child_process');

const app = express();
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || '100.114.61.65';
const pythonPath = process.env.PYTHON_PATH || 'C:\\Python312\\python.exe';
const rpScriptPath = process.env.RP_SCRIPT_PATH || 'C:\\RP\\rp_v5.0.py';
const powershellPath = process.env.POWERSHELL_PATH || 'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe';
const rpConsoleLauncherPath = process.env.RP_CONSOLE_LAUNCHER_PATH || 'C:\\RP\\start-rp5-visible.ps1';
const salesCacheQueryPath = process.env.SALES_CACHE_QUERY_PATH || path.join(__dirname, 'query_sales_cache.py');
const salesCacheSyncWorkerPath = process.env.SALES_CACHE_SYNC_WORKER_PATH || path.join(__dirname, 'sales_cache_sync_worker.py');
const cashFinanceSyncWorkerPath = process.env.CASH_FINANCE_SYNC_WORKER_PATH || path.join(__dirname, 'cash_finance_sync_worker.py');
const cashFinanceSyncStatePath = process.env.CASH_FINANCE_SYNC_STATE_PATH || path.join(__dirname, 'data', 'finance_cache', 'sync_state.json');
const cashExceptionMonitorPath = process.env.CASH_EXCEPTION_MONITOR_PATH || path.join(__dirname, 'cash_exception_monitor.py');
const cashExceptionStatePath = process.env.CASH_EXCEPTION_STATE_PATH || path.join(__dirname, 'data', 'finance_cache', 'cash_exception_monitor_state.json');
const cashExceptionHeartbeatPath = process.env.CASH_EXCEPTION_HEARTBEAT_PATH || path.join(__dirname, 'data', 'finance_cache', 'cash_exception_monitor_heartbeat.json');
const cashExceptionControlPath = process.env.CASH_EXCEPTION_CONTROL_PATH || path.join(__dirname, 'data', 'finance_cache', 'cash_exception_monitor_control.json');
const cashboxEstimatorPath = process.env.CASHBOX_ESTIMATOR_PATH || path.join(__dirname, 'cashbox_estimator.py');
const cashboxEstimatePath = process.env.CASHBOX_ESTIMATE_PATH || path.join(__dirname, 'data', 'finance_cache', 'cashbox_estimate_latest.json');
const cashboxEstimatorHeartbeatPath = process.env.CASHBOX_ESTIMATOR_HEARTBEAT_PATH || path.join(__dirname, 'data', 'finance_cache', 'cashbox_estimator_heartbeat.json');
const cashboxEstimatorControlPath = process.env.CASHBOX_ESTIMATOR_CONTROL_PATH || path.join(__dirname, 'data', 'finance_cache', 'cashbox_estimator_control.json');
const kioskAgentStatusUrl = process.env.KIOSK_AGENT_STATUS_URL || 'http://100.113.224.68:3010/api/status';
const kioskAgentRestartShortcutUrl = process.env.KIOSK_AGENT_RESTART_SHORTCUT_URL || 'http://100.113.224.68:3010/api/control/restart-shortcut';
const kioskAgentTimeoutMs = Number(process.env.KIOSK_AGENT_TIMEOUT_MS || 2000);
const kioskMonitorBaseUrl = (process.env.KIOSK_MONITOR_BASE_URL || 'http://100.113.224.68:9581').replace(/\/+$/, '');
const kioskMonitorTimeoutMs = Number(process.env.KIOSK_MONITOR_TIMEOUT_MS || 4000);
const kioskScreenshotMaxWidth = Number(process.env.KIOSK_SCREENSHOT_MAX_WIDTH || 720);
const kioskScreenshotDir = process.env.KIOSK_SCREENSHOT_DIR || path.join(__dirname, 'data', 'kiosk_monitor');
const kioskScreenshotPath = path.join(kioskScreenshotDir, 'latest-screenshot.png');
const kioskScreenshotMetaPath = path.join(kioskScreenshotDir, 'latest-screenshot.json');
const kitchenScriptPath = process.env.KITCHEN_SCRIPT_PATH || 'C:\\6KAK\\6kak_v2.0.py';
const visibleServiceLauncherPath = process.env.VISIBLE_SERVICE_LAUNCHER_PATH || path.join(__dirname, 'run-local-service-visible.bat');
const piSshKeyPath = process.env.PI_SSH_KEY_PATH || 'C:\\RP\\ssh\\6ka_pi_codex';
const piSshTarget = process.env.PI_SSH_TARGET || 'djay@6ka-pi';
const piStatusScript = process.env.PI_STATUS_SCRIPT || '/home/djay/bin/6ka_pi_status.py';
const piAgentBaseUrl = process.env.PI_AGENT_BASE_URL || 'http://100.114.19.115:3011';
const piAgentTimeoutMs = Number(process.env.PI_AGENT_TIMEOUT_MS || 6000);
const audioFallbackApiUrl = process.env.AUDIO_FALLBACK_API_URL || 'https://6kas.jay-fbf.workers.dev/push';
const audioFallbackToken = process.env.AUDIO_FALLBACK_TOKEN || '558811566';
const salesCacheTtlMs = Number(process.env.SALES_CACHE_TTL_MS || 8000);
const businessTimeZone = process.env.BUSINESS_TIME_ZONE || 'Asia/Taipei';
const piCacheTtlMs = Number(process.env.PI_CACHE_TTL_MS || 15000);
const syncWorkerWatchdogMs = Number(process.env.SYNC_WORKER_WATCHDOG_MS || 60000);
const tempHistoryBucketMs = Number(process.env.TEMP_HISTORY_BUCKET_MS || 10 * 60 * 1000);
const tempHistorySpanMs = Number(process.env.TEMP_HISTORY_SPAN_MS || 12 * 60 * 60 * 1000);
const tempHistoryPollMs = Number(process.env.TEMP_HISTORY_POLL_MS || tempHistoryBucketMs);
const tempHistoryPath = process.env.TEMP_HISTORY_PATH || path.join(__dirname, 'data', 'temperature_history.json');
const salesCache = new Map();
const lastSalesByDate = new Map();
let piCache = null;
let kioskStatus = {
    ok: false,
    last_success_at: null,
    last_error_at: null,
    last_error: null,
    latency_ms: null,
};

const localServiceDefinitions = [
    {
        key: 'rp5',
        label: '營業額回報系統',
        scriptPath: rpScriptPath,
        hidden: true,
        matchTerms: ['rp_v5.0.py', 'rp_v5.0.txt', 'rp5.0.bat', 'start-rp5-visible.ps1'],
    },
    {
        key: 'kitchen_dc',
        label: '廚房製作單系統',
        scriptPath: kitchenScriptPath,
        hidden: true,
        matchTerms: ['6kak_v2.0.py', '6kak2.0.bat'],
    },
    {
        key: 'cash_exception',
        label: '找零機故障碼',
        scriptPath: cashExceptionMonitorPath,
        args: ['--source', 'agent', '--interval', '30'],
        hidden: true,
        autoStart: false,
        matchTerms: ['cash_exception_monitor.py'],
    },
    {
        key: 'cashbox_estimator',
        label: '錢箱推算',
        scriptPath: cashboxEstimatorPath,
        args: ['--watch', '--interval', '30'],
        hidden: true,
        autoStart: false,
        matchTerms: ['cashbox_estimator.py'],
    },
];

const piServiceDefinitions = {
    player: {
        label: '播放器',
        serviceName: 'order_notify.service',
    },
    temperature: {
        label: '溫度計',
        serviceName: 'switchbot_temp_monitor.service',
    },
};

const logTailBytes = Number(process.env.LOG_TAIL_BYTES || 256 * 1024);
const logTailDefaultLines = Number(process.env.LOG_TAIL_DEFAULT_LINES || 180);
const logTailMaxLines = Number(process.env.LOG_TAIL_MAX_LINES || 500);
const logDisplayMaxLines = Number(process.env.LOG_DISPLAY_MAX_LINES || 60);
const logReadMultiplier = Number(process.env.LOG_READ_MULTIPLIER || 5);
const logServiceDefinitions = {
    rp5: {
        label: 'rp5',
        type: 'file',
        source: () => path.join(process.env.RP_LOG_DIR || 'C:\\RP_log', 'rp5_console.log'),
        fallbackSource: () => path.join(process.env.RP_LOG_DIR || 'C:\\RP_log', `${localDateString()}.txt`),
    },
    kitchen_dc: {
        label: 'kitchen_dc',
        type: 'file',
        source: () => path.join('C:\\6KA_log', '6kak_runtime.log'),
    },
    cash_exception: {
        label: 'cash_exception',
        type: 'file',
        source: () => path.join(__dirname, 'logs', 'cash-exception-monitor.log'),
    },
    cashbox_estimator: {
        label: 'cashbox_estimator',
        type: 'file',
        source: () => path.join(__dirname, 'logs', 'cashbox-estimator.log'),
    },
    player: {
        label: 'player',
        type: 'journal',
        unit: piServiceDefinitions.player.serviceName,
    },
    temperature: {
        label: 'temperature',
        type: 'journal',
        unit: piServiceDefinitions.temperature.serviceName,
    },
};

app.use(express.json({ limit: '16kb' }));

// 只監控今天的 6KAK 解析檔。舊版監控整個 C:\6KA_log 會掃到數萬個歷史檔案。
function todayLogPath() {
    const today = new Date().toISOString().slice(0, 10);
    return path.join('C:\\6KA_log', today);
}

const watchPath = todayLogPath();

// 創建WebSocket服務器
const wss = new WebSocket.Server({ noServer: true });

// 監控文件變化
const watcher = chokidar.watch(watchPath, {
    ignored: /(^|[\/\\])\../, // 忽略隱藏文件
    ignoreInitial: true,
    depth: 0,
    persistent: true,
});

watcher.on('add', (filePath) => {
    console.log(`File ${filePath} has been added`);
    const content = fs.readFileSync(filePath, 'utf8');
    const stats = fs.statSync(filePath);
    const fileInfo = {
        content: content,
        name: path.basename(filePath),
        time: stats.birthtime
    };
    wss.clients.forEach(client => {
        if (client.readyState === WebSocket.OPEN) {
            client.send(JSON.stringify({ type: 'file', data: fileInfo }));
        }
    });
});

function formatDateInTimeZone(date, timeZone) {
    const parts = new Intl.DateTimeFormat('en-US', {
        timeZone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
    }).formatToParts(date);
    const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
    return `${values.year}-${values.month}-${values.day}`;
}

function getBusinessDate(req) {
    const value = req.query.date;
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return value;
    }
    return formatDateInTimeZone(new Date(), businessTimeZone);
}

function querySalesSnapshot(businessDate) {
    return new Promise((resolve, reject) => {
        execFile(
            pythonPath,
            [salesCacheQueryPath, businessDate],
            {
                cwd: __dirname,
                windowsHide: true,
                timeout: 10000,
                maxBuffer: 1024 * 1024 * 5,
            },
            (error, stdout, stderr) => {
                if (error) {
                    const details = (stderr || stdout || error.message || '').trim();
                    reject(new Error(details || error.message));
                    return;
                }

                try {
                    resolve(JSON.parse(stdout));
                } catch (parseError) {
                    reject(new Error(`Unable to parse sales JSON: ${parseError.message}`));
                }
            }
        );
    });
}

function querySalesCacheStatus() {
    return new Promise((resolve, reject) => {
        execFile(
            pythonPath,
            [salesCacheQueryPath, '--status'],
            {
                cwd: __dirname,
                windowsHide: true,
                timeout: 5000,
                maxBuffer: 1024 * 1024,
            },
            (error, stdout, stderr) => {
                if (error) {
                    const details = (stderr || stdout || error.message || '').trim();
                    reject(new Error(details || error.message));
                    return;
                }

                try {
                    resolve(JSON.parse(stdout));
                } catch (parseError) {
                    reject(new Error(`Unable to parse sales cache status JSON: ${parseError.message}`));
                }
            }
        );
    });
}

function fetchJson(url, timeoutMs) {
    return new Promise((resolve, reject) => {
        const startedAt = Date.now();
        const request = http.get(url, { timeout: timeoutMs }, response => {
            let body = '';
            response.setEncoding('utf8');
            response.on('data', chunk => {
                body += chunk;
            });
            response.on('end', () => {
                if (response.statusCode < 200 || response.statusCode >= 300) {
                    reject(new Error(`HTTP ${response.statusCode}: ${body.slice(0, 200)}`));
                    return;
                }
                try {
                    resolve({
                        data: JSON.parse(body),
                        latency_ms: Date.now() - startedAt,
                    });
                } catch (error) {
                    reject(new Error(`Unable to parse JSON from ${url}: ${error.message}`));
                }
            });
        });
        request.on('timeout', () => {
            request.destroy(new Error(`Timeout after ${timeoutMs} ms`));
        });
        request.on('error', reject);
    });
}

function makeAbortError() {
    const error = new Error('Request aborted');
    error.code = 'ABORT_ERR';
    return error;
}

function fetchBuffer(url, timeoutMs, maxBytes = 8 * 1024 * 1024, abortSignal = null) {
    return new Promise((resolve, reject) => {
        if (abortSignal && abortSignal.aborted) {
            reject(makeAbortError());
            return;
        }
        const startedAt = Date.now();
        let done = false;
        let request = null;
        const finish = (fn, value) => {
            if (done) return;
            done = true;
            if (abortSignal) abortSignal.removeEventListener('abort', onAbort);
            fn(value);
        };
        const onAbort = () => {
            if (request) request.destroy(makeAbortError());
        };

        request = http.get(url, { timeout: timeoutMs }, response => {
            const chunks = [];
            let totalBytes = 0;

            response.on('data', chunk => {
                chunks.push(chunk);
                totalBytes += chunk.length;
                if (totalBytes > maxBytes) {
                    request.destroy(new Error(`Response exceeded ${maxBytes} bytes`));
                }
            });
            response.on('end', () => {
                const buffer = Buffer.concat(chunks);
                if (response.statusCode < 200 || response.statusCode >= 300) {
                    finish(reject, new Error(`HTTP ${response.statusCode}: ${buffer.toString('utf8', 0, 200)}`));
                    return;
                }
                finish(resolve, {
                    buffer,
                    content_type: response.headers['content-type'] || null,
                    latency_ms: Date.now() - startedAt,
                });
            });
            response.on('error', error => finish(reject, error));
        });
        if (abortSignal) abortSignal.addEventListener('abort', onAbort, { once: true });
        request.on('timeout', () => {
            request.destroy(new Error(`Timeout after ${timeoutMs} ms`));
        });
        request.on('error', error => finish(reject, error));
    });
}

function secondsBetween(left, right) {
    if (!left || !right) {
        return null;
    }
    const leftMs = new Date(String(left).replace(' ', 'T')).getTime();
    const rightMs = new Date(String(right).replace(' ', 'T')).getTime();
    if (!Number.isFinite(leftMs) || !Number.isFinite(rightMs)) {
        return null;
    }
    return Math.max(0, Math.round((leftMs - rightMs) / 1000));
}

function secondsSince(value) {
    if (!value) {
        return null;
    }
    const valueMs = new Date(String(value).replace(' ', 'T')).getTime();
    if (!Number.isFinite(valueMs)) {
        return null;
    }
    return Math.max(0, Math.round((Date.now() - valueMs) / 1000));
}

function latestTimestamp(items) {
    let latest = null;
    for (const item of items) {
        if (!item || !item.value) {
            continue;
        }
        const valueMs = new Date(String(item.value).replace(' ', 'T')).getTime();
        if (!Number.isFinite(valueMs)) {
            continue;
        }
        if (!latest || valueMs > latest.time) {
            latest = { ...item, time: valueMs };
        }
    }
    return latest;
}

function nullableNumber(value) {
    if (value == null) {
        return null;
    }
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

function readJsonFile(filePath) {
    try {
        return JSON.parse(fs.readFileSync(filePath, 'utf8'));
    } catch (error) {
        return {
            ok: false,
            last_error_at: new Date().toISOString(),
            last_error: error.message,
            path: filePath,
        };
    }
}

function readCashExceptionControlState() {
    try {
        const state = JSON.parse(fs.readFileSync(cashExceptionControlPath, 'utf8'));
        return {
            enabled: state.enabled !== false,
            updated_at: state.updated_at || null,
            updated_by: state.updated_by || null,
            path: cashExceptionControlPath,
        };
    } catch (error) {
        if (error && error.code === 'ENOENT') {
            return {
                enabled: true,
                updated_at: null,
                updated_by: null,
                path: cashExceptionControlPath,
            };
        }
        return {
            enabled: true,
            updated_at: null,
            updated_by: null,
            path: cashExceptionControlPath,
            error: error.message,
        };
    }
}

function writeCashExceptionControlState(enabled, updatedBy = 'dashboard') {
    const state = {
        enabled: Boolean(enabled),
        updated_at: new Date().toISOString(),
        updated_by: updatedBy,
    };
    fs.mkdirSync(path.dirname(cashExceptionControlPath), { recursive: true });
    fs.writeFileSync(cashExceptionControlPath, JSON.stringify(state, null, 2), 'utf8');
    return {
        ...state,
        path: cashExceptionControlPath,
    };
}

function readCashboxEstimatorControlState() {
    try {
        const state = JSON.parse(fs.readFileSync(cashboxEstimatorControlPath, 'utf8'));
        return {
            enabled: state.enabled !== false,
            updated_at: state.updated_at || null,
            updated_by: state.updated_by || null,
            path: cashboxEstimatorControlPath,
        };
    } catch (error) {
        if (error && error.code === 'ENOENT') {
            return {
                enabled: true,
                updated_at: null,
                updated_by: null,
                path: cashboxEstimatorControlPath,
            };
        }
        return {
            enabled: true,
            updated_at: null,
            updated_by: null,
            path: cashboxEstimatorControlPath,
            error: error.message,
        };
    }
}

function writeCashboxEstimatorControlState(enabled, updatedBy = 'dashboard') {
    const state = {
        enabled: Boolean(enabled),
        updated_at: new Date().toISOString(),
        updated_by: updatedBy,
    };
    fs.mkdirSync(path.dirname(cashboxEstimatorControlPath), { recursive: true });
    fs.writeFileSync(cashboxEstimatorControlPath, JSON.stringify(state, null, 2), 'utf8');
    return {
        ...state,
        path: cashboxEstimatorControlPath,
    };
}

function localDateString(date = new Date()) {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, '0');
    const d = String(date.getDate()).padStart(2, '0');
    return `${y}-${m}-${d}`;
}

function cashExceptionNotificationsForDate(state, targetDate) {
    let rows = Array.isArray(state.notifications) ? state.notifications : [];
    if (rows.length === 0 && state.notified && typeof state.notified === 'object') {
        rows = Object.entries(state.notified).map(([key, value]) => {
            const parts = String(key).split('|');
            if (parts.length < 3 || !value || typeof value !== 'object') return null;
            const message = parts.slice(2).join('|');
            return {
                date: String(parts[0]).slice(0, 10),
                timestamp: parts[0],
                code: parts[1],
                message,
                short_message: message,
                sent_at: value.sent_at || null,
            };
        }).filter(Boolean);
    }
    return rows
        .filter(item => {
            if (!item || typeof item !== 'object') return false;
            const itemDate = item.date || String(item.sent_at || '').slice(0, 10);
            return itemDate === targetDate;
        })
        .sort((a, b) => String(a.sent_at || a.timestamp || '').localeCompare(String(b.sent_at || b.timestamp || '')));
}

function summarizeCashExceptionNotifications(rows) {
    const summary = new Map();
    for (const item of rows) {
        const code = item.code || '-';
        const shortMessage = item.short_message || item.message || '-';
        const key = `${code}\u0000${shortMessage}`;
        if (!summary.has(key)) {
            summary.set(key, {
                code,
                short_message: shortMessage,
                count: 0,
                last_sent_at: null,
            });
        }
        const row = summary.get(key);
        row.count += 1;
        if (item.sent_at && (!row.last_sent_at || item.sent_at > row.last_sent_at)) {
            row.last_sent_at = item.sent_at;
        }
    }
    return Array.from(summary.values())
        .sort((a, b) => String(b.last_sent_at || '').localeCompare(String(a.last_sent_at || '')));
}

function queryCashExceptionMonitorStatus() {
    const targetDate = localDateString();
    const state = readJsonFile(cashExceptionStatePath);
    const heartbeat = readJsonFile(cashExceptionHeartbeatPath);
    const control = readCashExceptionControlState();
    const notifications = cashExceptionNotificationsForDate(state, targetDate);
    return {
        ok: heartbeat.ok !== false,
        date: targetDate,
        monitor_enabled: control.enabled,
        control_updated_at: control.updated_at,
        control_updated_by: control.updated_by,
        heartbeat_ok: heartbeat.ok !== false,
        heartbeat_status: heartbeat.status || null,
        heartbeat_updated_at: heartbeat.updated_at || null,
        notifications_today: notifications.length,
        active_alert: notifications.length > 0,
        last_notification: notifications.length ? notifications[notifications.length - 1] : null,
        notification_summary: summarizeCashExceptionNotifications(notifications),
        notifications: notifications.slice(-20).reverse(),
        state_error: state.ok === false ? state.last_error : null,
        heartbeat_error: heartbeat.ok === false ? heartbeat.last_error : null,
        control_error: control.error || null,
    };
}

function queryCashboxEstimatorStatus() {
    const control = readCashboxEstimatorControlState();
    const heartbeat = readJsonFile(cashboxEstimatorHeartbeatPath);
    const estimate = readJsonFile(cashboxEstimatePath);
    const cashSales = estimate.cash_sales || {};
    const cashMachine = estimate.cash_machine || {};
    const estimateBlock = estimate.estimate || {};
    const cashOperations = estimate.cash_operations || {};
    const clearCheck = estimateBlock.cashbox_clear_check || {};
    const cashSalesNet = nullableNumber(cashSales.net_amount);
    const cashSalesNetSinceClear = nullableNumber(estimateBlock.cash_sales_net_since_clear) ?? cashSalesNet;
    const todayCashboxFlowAmount = nullableNumber(cashMachine.today_cashbox_flow_amount);
    const todayUsableNetChange = nullableNumber(cashMachine.today_usable_net_change);
    const usableNetChangeSinceClear = nullableNumber(estimateBlock.usable_change_net_change_since_clear) ?? todayUsableNetChange;
    const cashboxConfirmedSinceClear = nullableNumber(estimateBlock.cashbox_confirmed_amount_since_clear) ?? todayCashboxFlowAmount;
    const cashSalesMinusCashboxFlow = nullableNumber(estimateBlock.cash_sales_minus_cashbox_flow)
        ?? (cashSalesNetSinceClear != null && cashboxConfirmedSinceClear != null ? cashSalesNetSinceClear - cashboxConfirmedSinceClear : null);
    const cashboxEstimatedAmount = nullableNumber(estimateBlock.cashbox_estimated_amount)
        ?? (cashSalesNetSinceClear != null && usableNetChangeSinceClear != null ? cashSalesNetSinceClear - usableNetChangeSinceClear : null);
    const cashboxUnpostedDifference = nullableNumber(estimateBlock.cashbox_unposted_difference)
        ?? (cashboxEstimatedAmount != null && cashboxConfirmedSinceClear != null ? cashboxEstimatedAmount - cashboxConfirmedSinceClear : null);
    return {
        ok: heartbeat.ok !== false && estimate.ok !== false,
        monitor_enabled: control.enabled,
        control_updated_at: control.updated_at,
        control_updated_by: control.updated_by,
        heartbeat_ok: heartbeat.ok !== false,
        heartbeat_status: heartbeat.status || null,
        heartbeat_updated_at: heartbeat.updated_at || null,
        heartbeat_age_seconds: secondsSince(heartbeat.updated_at),
        latest_generated_at: estimate.generated_at || null,
        latest_age_seconds: secondsSince(estimate.generated_at),
        business_date: estimate.business_date || null,
        output_json: cashboxEstimatePath,
        cash_sales_net: cashSalesNetSinceClear,
        cash_sales_net_today: cashSalesNet,
        cash_sales_count: nullableNumber(cashSales.order_count),
        today_cashbox_flow_amount: todayCashboxFlowAmount,
        cashbox_confirmed_amount: cashboxConfirmedSinceClear,
        cashbox_estimated_amount: cashboxEstimatedAmount,
        cashbox_unposted_difference: cashboxUnpostedDifference,
        cash_sales_minus_cashbox_flow: cashSalesMinusCashboxFlow,
        usable_change_total: nullableNumber(cashMachine.usable_change_total),
        today_usable_net_change: todayUsableNetChange,
        usable_change_net_change_since_clear: usableNetChangeSinceClear,
        pos_vs_cash_db_variance: nullableNumber(estimateBlock.pos_vs_cash_db_variance),
        cashbox_clear_check: clearCheck && typeof clearCheck === 'object' ? clearCheck : {},
        cashbox_clear_mismatch: Boolean(clearCheck && clearCheck.mismatch),
        cashbox_clear_difference: nullableNumber(clearCheck && clearCheck.difference),
        cashbox_clear_actual_amount: nullableNumber(clearCheck && clearCheck.cleared_amount),
        cashbox_clear_expected_amount: nullableNumber(clearCheck && clearCheck.estimated_amount),
        cashbox_clear_event_id: clearCheck ? clearCheck.event_id : null,
        cashbox_clear_event_time: clearCheck ? clearCheck.event_time : null,
        cash_operation_status: cashOperations.status || null,
        cash_operation_notified_count: Array.isArray(cashOperations.notified) ? cashOperations.notified.length : 0,
        last_cash_operation_id: nullableNumber(cashOperations.last_cash_operation_id),
        explanation: estimateBlock.explanation || null,
        estimate_error: estimate.ok === false ? estimate.last_error : null,
        heartbeat_error: heartbeat.ok === false ? heartbeat.last_error : null,
        control_error: control.error || null,
    };
}

function queryCashFinanceStatus() {
    const state = readJsonFile(cashFinanceSyncStatePath);
    const summary = state.summary || {};
    const sourceLatest = state.agent_latest_timestamp || null;
    const localLatest = summary.latest_cash_record_time || null;
    return {
        ok: Boolean(state.ok),
        mode: state.mode || null,
        last_success_at: state.last_success_at || null,
        last_error_at: state.last_error_at || null,
        last_error: state.last_error || null,
        latency_ms: state.elapsed_ms == null ? null : state.elapsed_ms,
        agent_latency_ms: state.agent_latency_ms == null ? null : state.agent_latency_ms,
        source_latest_time: sourceLatest,
        local_latest_time: localLatest,
        freshness_lag_seconds: secondsBetween(sourceLatest, localLatest),
        cache_age_seconds: secondsSince(state.last_success_at),
        latest_id: summary.latest_id || null,
        agent_latest_id: state.agent_latest_id || null,
        cache_rebuild_at: state.cache_rebuild_at || null,
        cache_rebuild_reason: state.cache_rebuild_reason || null,
        overlap_rows: state.overlap_rows == null ? null : state.overlap_rows,
        cash_record_rows: summary.cash_record_rows || null,
        usable_total_amount: summary.usable_total_amount || 0,
        usable_by_denomination: summary.usable_by_denomination || {},
        cashbox: summary.cashbox || {},
    };
}

async function queryKioskStatus() {
    const [cacheResult, agentResult] = await Promise.allSettled([
        querySalesCacheStatus(),
        fetchJson(kioskAgentStatusUrl, kioskAgentTimeoutMs),
    ]);

    const cache = cacheResult.status === 'fulfilled' ? cacheResult.value : null;
    const agent = agentResult.status === 'fulfilled' ? agentResult.value.data : null;
    const agentLatency = agentResult.status === 'fulfilled' ? agentResult.value.latency_ms : null;
    const sync = cache && cache.sync_state ? cache.sync_state : {};
    const counts = cache && cache.counts ? cache.counts : {};
    const database = agent && agent.database ? agent.database : {};
    const agentOnline = Boolean(agent && agent.agent && agent.agent.online);
    const databaseOk = Boolean(database.ok);
    const sourceLatest = database.latest_order_time || sync.source_latest_order_time || null;
    const actualLocalLatest = counts.local_latest_order_time || null;
    const syncLocalLatest = sync.local_latest_order_time || null;
    const salesCacheMismatch = Boolean(
        (Number(sync.order_rows || 0) > Number(counts.order_rows || 0))
        || (Number(sync.order_product_rows || 0) > Number(counts.order_product_rows || 0))
        || (Number(sync.order_payment_rows || 0) > Number(counts.order_payment_rows || 0))
        || (syncLocalLatest && actualLocalLatest && secondsBetween(syncLocalLatest, actualLocalLatest) > 0)
    );
    const localLatest = salesCacheMismatch ? actualLocalLatest : (syncLocalLatest || actualLocalLatest);
    const cashFinance = queryCashFinanceStatus();
    const salesFreshnessLagSeconds = secondsBetween(sourceLatest, localLatest);
    const lastError = agentResult.status === 'rejected'
        ? agentResult.reason.message
        : (cacheResult.status === 'rejected' ? cacheResult.reason.message : (sync.last_error || database.error || null));
    const latestSyncError = latestTimestamp([
        { value: sync.last_error_at || null, source: 'sales', message: sync.last_error || null },
        { value: cashFinance.last_error_at || null, source: 'cash_finance', message: cashFinance.last_error || null },
    ]);
    const latestCorrection = latestTimestamp([
        { value: sync.cache_rebuild_at || null, source: 'sales', reason: sync.cache_rebuild_reason || null },
        { value: cashFinance.cache_rebuild_at || null, source: 'cash_finance', reason: cashFinance.cache_rebuild_reason || null },
    ]);

    kioskStatus = {
        ok: agentOnline && databaseOk,
        online: agentOnline,
        database_ok: databaseOk,
        agent_seen_at: agentOnline ? new Date().toISOString() : null,
        agent_latency_ms: agentLatency,
        latency_ms: agentLatency,
        last_success_at: sync.last_success_at || (agentOnline ? new Date().toISOString() : null),
        last_error_at: sync.last_error_at || (!agentOnline ? new Date().toISOString() : null),
        last_error: lastError,
        latest_sync_error_at: latestSyncError ? latestSyncError.value : null,
        latest_sync_error_source: latestSyncError ? latestSyncError.source : null,
        latest_sync_error_message: latestSyncError ? latestSyncError.message : null,
        latest_correction_at: latestCorrection ? latestCorrection.value : null,
        latest_correction_source: latestCorrection ? latestCorrection.source : null,
        latest_correction_reason: latestCorrection ? latestCorrection.reason : null,
        source_latest_order_time: sourceLatest,
        local_latest_order_time: localLatest,
        freshness_lag_seconds: salesFreshnessLagSeconds,
        order_rows: counts.order_rows || sync.order_rows || null,
        order_product_rows: counts.order_product_rows || sync.order_product_rows || null,
        order_payment_rows: counts.order_payment_rows || sync.order_payment_rows || null,
        sales: {
            ok: Boolean(cache && cache.ok && !salesCacheMismatch),
            last_success_at: sync.last_success_at || null,
            source_latest_time: sourceLatest,
            local_latest_time: localLatest,
            freshness_lag_seconds: salesFreshnessLagSeconds,
            cache_age_seconds: secondsSince(sync.last_success_at),
            order_rows: counts.order_rows || sync.order_rows || null,
            cache_rebuild_at: sync.cache_rebuild_at || null,
            cache_rebuild_reason: sync.cache_rebuild_reason || null,
            cache_mismatch: salesCacheMismatch,
            sync_local_latest_time: syncLocalLatest,
            actual_local_latest_time: actualLocalLatest,
        },
        cash_finance: cashFinance,
    };

    return kioskStatus;
}

function getCachedSalesSnapshot(businessDate) {
    const now = Date.now();
    const cached = salesCache.get(businessDate);
    if (cached && now - cached.createdAt < salesCacheTtlMs) {
        return cached.promise;
    }

    const startedAt = Date.now();
    const promise = querySalesSnapshot(businessDate)
        .then(result => {
            lastSalesByDate.set(businessDate, result);
            kioskStatus = {
                ...kioskStatus,
                ok: kioskStatus.ok,
                last_success_at: new Date().toISOString(),
                last_error_at: kioskStatus.last_error_at,
                last_error: null,
                cache_latency_ms: Date.now() - startedAt,
            };
            return result;
        })
        .catch(error => {
            salesCache.delete(businessDate);
            kioskStatus = {
                ...kioskStatus,
                ok: false,
                last_error_at: new Date().toISOString(),
                last_error: error.message,
                cache_latency_ms: Date.now() - startedAt,
            };
            throw error;
        });
    salesCache.set(businessDate, { createdAt: now, promise });
    return promise;
}

function normalizeCommandText(value) {
    return String(value || '').replace(/\//g, '\\').toLowerCase();
}

function serviceMatchesProcess(service, processInfo) {
    const commandLine = normalizeCommandText(processInfo.CommandLine);
    if (/\bpythonw?\.exe"?\s+-c\b/.test(commandLine)) {
        return false;
    }
    const targetPath = normalizeCommandText(path.resolve(service.scriptPath));
    const targetName = normalizeCommandText(path.basename(service.scriptPath));
    const terms = [targetPath, targetName, ...(service.matchTerms || []).map(normalizeCommandText)];
    return terms.some(term => term && commandLine.includes(term));
}

function queryPythonProcesses() {
    const command = `
$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^(python|pythonw|powershell|pwsh|cmd)\\.exe$' } |
    Select-Object ProcessId, Name, CommandLine, @{
        Name = 'CreationDate'
        Expression = {
            if ($_.CreationDate) { $_.CreationDate.ToString('o') } else { $null }
        }
    } |
    ConvertTo-Json -Compress
`;
    const stdout = execFileSync(
        'powershell.exe',
        ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', command],
        {
            windowsHide: true,
            timeout: 5000,
            maxBuffer: 1024 * 1024,
            encoding: 'utf8',
        }
    ).trim();

    if (!stdout) {
        return [];
    }

    const parsed = JSON.parse(stdout);
    return Array.isArray(parsed) ? parsed : [parsed];
}

function localServiceStatus() {
    let processes = [];
    let processError = null;

    try {
        processes = queryPythonProcesses();
    } catch (error) {
        processError = error.message;
    }

    return Object.fromEntries(localServiceDefinitions.map(service => {
        const match = processes.find(item => serviceMatchesProcess(service, item));
        const cashExceptionStatus = service.key === 'cash_exception'
            ? queryCashExceptionMonitorStatus()
            : null;
        const cashboxEstimatorStatus = service.key === 'cashbox_estimator'
            ? queryCashboxEstimatorStatus()
            : null;
        const extra = cashExceptionStatus
            ? { enabled: cashExceptionStatus.monitor_enabled, cash_exception: cashExceptionStatus }
            : (cashboxEstimatorStatus
                ? { enabled: cashboxEstimatorStatus.monitor_enabled, cashbox_estimator: cashboxEstimatorStatus }
                : {});

        return [
            service.key,
            {
                label: service.label,
                script_path: service.scriptPath,
                ok: Boolean(match),
                active: Boolean(match),
                pid: match ? match.ProcessId : null,
                process_name: match ? match.Name : null,
                started_at: match ? match.CreationDate : null,
                error: processError,
                ...extra,
            },
        ];
    }));
}

function findLocalServiceDefinition(key) {
    const service = localServiceDefinitions.find(item => item.key === key);
    if (!service) {
        throw new Error(`Unknown local service: ${key}`);
    }
    return service;
}

function powershellSingleQuote(value) {
    return `'${String(value).replace(/'/g, "''")}'`;
}

function startProcessHidden(command, args, cwd) {
    const psArgs = (args || []).map(powershellSingleQuote).join(', ');
    const commandText = [
        '$ErrorActionPreference = "Stop"',
        `$arguments = @(${psArgs})`,
        `Start-Process -FilePath ${powershellSingleQuote(command)} -ArgumentList $arguments -WorkingDirectory ${powershellSingleQuote(cwd)} -WindowStyle Hidden`,
    ].join('; ');
    const child = spawn(
        powershellPath,
        ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-Command', commandText],
        {
            cwd,
            detached: true,
            stdio: 'ignore',
            windowsHide: true,
        }
    );
    child.unref();
    return child;
}

function startLocalService(key) {
    const service = findLocalServiceDefinition(key);
    const processes = queryPythonProcesses();
    const existing = processes.find(item => serviceMatchesProcess(service, item));
    if (existing) {
        return {
            ok: true,
            action: 'start',
            service: key,
            already_running: true,
            pid: existing.ProcessId,
        };
    }

    if (service.launcherCommand) {
        if (!fs.existsSync(service.launcherCommand)) {
            throw new Error(`Service launcher not found: ${service.launcherCommand}`);
        }
        const child = service.hidden
            ? startProcessHidden(service.launcherCommand, service.launcherArgs || [], service.launcherCwd || path.dirname(service.scriptPath))
            : spawn(
                service.launcherCommand,
                service.launcherArgs || [],
                {
                    cwd: service.launcherCwd || path.dirname(service.scriptPath),
                    detached: true,
                    stdio: 'ignore',
                    windowsHide: false,
                }
            );
        if (!service.hidden) {
            child.unref();
        }
        return {
            ok: true,
            action: 'start',
            service: key,
            pid: child.pid,
        };
    }

    if (service.hidden) {
        if (!fs.existsSync(service.scriptPath)) {
            throw new Error(`Service script not found: ${service.scriptPath}`);
        }
        const child = spawn(
            pythonPath,
            [service.scriptPath, ...(service.args || [])],
            {
                cwd: path.dirname(service.scriptPath),
                detached: true,
                stdio: 'ignore',
                windowsHide: true,
            }
        );
        child.unref();
        return {
            ok: true,
            action: 'start',
            service: key,
            pid: child.pid,
        };
    }

    const child = spawn(
        'cmd.exe',
        [
            '/d',
            '/c',
            visibleServiceLauncherPath,
            path.dirname(service.scriptPath),
            pythonPath,
            path.basename(service.scriptPath),
        ],
        {
            cwd: path.dirname(service.scriptPath),
            detached: true,
            stdio: 'ignore',
            windowsHide: false,
        }
    );
    child.unref();
    return {
        ok: true,
        action: 'start',
        service: key,
        pid: child.pid,
    };
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

function stopLocalService(key) {
    const service = findLocalServiceDefinition(key);
    const processes = queryPythonProcesses();
    const matches = processes.filter(item => serviceMatchesProcess(service, item));
    if (matches.length === 0) {
        return {
            ok: true,
            action: 'stop',
            service: key,
            stopped: 0,
        };
    }

    for (const processInfo of matches) {
        process.kill(Number(processInfo.ProcessId));
    }

    return {
        ok: true,
        action: 'stop',
        service: key,
        stopped: matches.length,
        pids: matches.map(item => item.ProcessId),
    };
}

async function restartLocalService(key) {
    const stopped = stopLocalService(key);
    await sleep(1000);
    const started = startLocalService(key);
    return {
        ok: true,
        action: 'restart',
        service: key,
        stopped,
        started,
    };
}

function runSshCommand(args, timeout = 15000) {
    return new Promise((resolve, reject) => {
        execFile(
            'ssh',
            [
                '-i',
                piSshKeyPath,
                '-o',
                'BatchMode=yes',
                '-o',
                'StrictHostKeyChecking=accept-new',
                piSshTarget,
                ...args,
            ],
            {
                windowsHide: true,
                timeout,
                maxBuffer: 1024 * 1024,
            },
            (error, stdout, stderr) => {
                if (error) {
                    reject(new Error((stderr || stdout || error.message || '').trim()));
                    return;
                }
                resolve({ stdout: stdout.trim(), stderr: stderr.trim() });
            }
        );
    });
}

function clampLogLines(value) {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed) || parsed <= 0) {
        return logTailDefaultLines;
    }
    return Math.min(logTailMaxLines, parsed);
}

function tailText(text, lines) {
    const normalized = String(text || '').replace(/\r\n/g, '\n');
    return normalized.split('\n').slice(-lines).join('\n').trimEnd();
}

function compactLogTimestamp(datePart, timePart) {
    const today = localDateString();
    if (datePart === today) {
        return timePart.slice(0, 8);
    }
    return `${datePart.slice(5)} ${timePart.slice(0, 5)}`;
}

function compactRawLogLine(line) {
    let text = String(line || '').trim();
    if (!text) {
        return null;
    }

    let match = text.match(/^\[(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
    if (match) {
        return {
            date: match[1],
            time: compactLogTimestamp(match[1], match[2]),
            message: match[3].trim(),
        };
    }

    match = text.match(/^\[(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
    if (match) {
        return {
            date: match[1],
            time: compactLogTimestamp(match[1], match[2]),
            message: match[3].trim(),
        };
    }

    match = text.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:[+-]\d{2}:\d{2})?\s+\S+\s+(.*)$/);
    if (match) {
        let message = match[3].trim();
        message = message.replace(/^systemd\[\d+\]:\s*/, 'systemd: ');
        message = message.replace(/^[\w.-]+\[\d+\]:\s*/, '');
        const inner = message.match(/^\[(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
        if (inner) {
            return {
                date: inner[1],
                time: compactLogTimestamp(inner[1], inner[2]),
                message: inner[3].trim(),
            };
        }
        return {
            date: match[1],
            time: compactLogTimestamp(match[1], match[2]),
            message,
        };
    }

    return {
        date: null,
        time: '',
        message: text,
    };
}

function shortenLogMessage(service, message) {
    let text = String(message || '').trim();

    text = text.replace(/HTTPConnectionPool\(host='100\.113\.224\.68'.*$/i, 'agent_timeout');
    text = text.replace(/同步錯誤：<urlopen error timed out>/g, 'kiosk_timeout');
    text = text.replace(/<urlopen error timed out>/g, 'kiosk_timeout');
    text = text.replace(/\s+next_check=\d+s/g, '');
    text = text.replace(/\s+source=\/\/100\.113\.224\.68\/ProtechFile\\\d{4}-\d{2}-\d{2}\\DeviceLog\\Kitchen/g, ' source=Kitchen');
    text = text.replace(/\s+source=\/\/100\.113\.224\.68\/ProtechFile\/\d{4}-\d{2}-\d{2}\/DeviceLog\/Kitchen/g, ' source=Kitchen');
    text = text.replace(/output=C:\\6KAweb\\data\\finance_cache\\cashbox_estimate_latest\.json/g, 'output=latest_json');
    text = text.replace(/command=C:\\Python312\\python\.exe\s+-u\s+C:\\RP\\rp_v5\.0\.py/g, 'command=rp5');
    text = text.replace(/command=C:\\Python312\\python\.exe\s+C:\\RP\\rp_v5\.0\.py/g, 'command=rp5');
    text = text.replace(/^處理完成:\s*狀態-.*?單號-([A-Z0-9]+)\.txt$/, '訂單已處理 order=$1');
    text = text.replace(/^訂單已處理 file=.*?order=([A-Z0-9-]+).*$/, '訂單已處理 order=$1');

    if (/^RP5 (console )?started/.test(text) || /^RP5 monitor started/.test(text)) {
        return '啟動 RP5';
    }
    if (/^6KAK monitor started version /.test(text)) {
        return text.replace(/^6KAK monitor started version /, '啟動 6KAK v');
    }
    if (/^啟動 6KAK version=/.test(text)) {
        const version = text.match(/version=([^\s]+)/);
        return `啟動 6KAK v${version ? version[1] : ''}`.trim();
    }
    if (/^started watch mode /.test(text)) {
        return text.replace(/^started watch mode /, '啟動 watch ');
    }
    if (service === 'player') {
        text = text.replace(/^\[SYSTEM\]\s*/, '');
        text = text.replace(/^\[LOCAL API\]\s*/, 'local_api ');
        text = text.replace(/^systemd: Started order_notify\.service - Order Notify Player\.$/, 'systemd started');
        text = text.replace(/^systemd: Stopping order_notify\.service - Order Notify Player\.\.\.$/, 'systemd stopping');
        text = text.replace(/^systemd: Stopped order_notify\.service - Order Notify Player\.$/, 'systemd stopped');
    }
    if (service === 'temperature') {
        text = text.replace(/^systemd: Started switchbot_temp_monitor\.service - SwitchBot Temperature Monitor\.$/, 'systemd started');
        text = text.replace(/^systemd: Stopping switchbot_temp_monitor\.service - SwitchBot Temperature Monitor\.\.\.$/, 'systemd stopping');
        text = text.replace(/^systemd: Stopped switchbot_temp_monitor\.service - SwitchBot Temperature Monitor\.$/, 'systemd stopped');
    }

    return text;
}

function logDisplayGroup(service, message) {
    const text = String(message || '');

    if (!text || /GET \/health\b/.test(text)) return null;
    if (/outside business hours .*skip kiosk polling/.test(text)) return null;
    if (/^(主要資料來源|Agent 不可用時備援掃描):/.test(text)) return null;
    if (/Consumed .* CPU time/.test(text) || /Deactivated successfully/.test(text)) return null;
    if (/提示: Kiosk Agent HTTP 不可用/.test(text)) return null;
    if (/售票機 Kitchen log 讀取正常，不送初始恢復通知/.test(text)) return null;

    if (service === 'rp5') {
        if (/C:\\RP_log\\\d{4}-\d{2}-\d{2}\.txt\b/.test(text)) return null;
        if (/售票機同步異常 count=/.test(text)) return null;
        if (/狀態 sync_error/.test(text)) return 'rp5-sync-error';
        const rp5Ok = text.match(/狀態 ok date=(\d{4}-\d{2}-\d{2}) orders=(\d+) bowls=(\d+) revenue=\$?(\d+)/);
        if (rp5Ok) return `rp5-ok-${rp5Ok[1]}-${rp5Ok[2]}-${rp5Ok[3]}-${rp5Ok[4]}`;
        if (/狀態 ok date=/.test(text)) return 'rp5-ok';
        if (/通知 sent kiosk_offline/.test(text)) return 'rp5-kiosk-offline-notify';
        if (/啟動 RP5/.test(text)) return 'rp5-start';
    }

    if (service === 'kitchen_dc') {
        if (/檔案尚未穩定|延後處理/.test(text)) return null;
        if (/狀態 ok date=/.test(text)) return 'kitchen-ok';
        if (/等待當天 Kitchen 資料夾生成/.test(text)) return 'kitchen-waiting-folder';
        if (/狀態 waiting_folder/.test(text)) return 'kitchen-waiting-folder';
        if (/資料來源切換/.test(text)) return 'kitchen-source';
        if (/狀態 online initial=true/.test(text)) return 'kitchen-online';
        if (/狀態 offline/.test(text)) return 'kitchen-offline';
        if (/啟動 6KAK/.test(text)) return 'kitchen-start';
    }

    if (service === 'cash_exception') {
        if (/狀態 outside_business_hours/.test(text)) return 'cash-exception-outside-hours';
        if (/狀態 kiosk_unavailable/.test(text)) return 'cash-exception-kiosk-unavailable';
        if (/啟動 CashException/.test(text)) return 'cash-exception-start';
    }

    if (service === 'cashbox_estimator') {
        if (/啟動 watch /.test(text)) return 'cashbox-start';
        if (/狀態 ok /.test(text)) return 'cashbox-status';
    }

    if (service === 'player') {
        if (/^\[SKIP\] hourly/.test(text)) return null;
        if (/^systemd (stopping|stopped|started)$/.test(text)) return `player-${text}`;
        if (/^local_api listening/.test(text)) return 'player-local-api';
        if (/^player_api started/.test(text)) return 'player-start';
        if (/^init volume:/.test(text)) return 'player-volume';
        if (/^audio_unavailable|^hourly|audio unavailable/.test(text)) return 'player-audio-unavailable';
    }

    if (service === 'temperature') {
        if (/^systemd (stopping|stopped|started)$/.test(text)) return `temperature-${text}`;
        if (/^status connection=/.test(text) || /^connection=/.test(text)) return 'temperature-status';
        if (/溫濕度監控啟動/.test(text)) return 'temperature-start';
    }

    return undefined;
}

function cleanServiceLogContent(service, rawContent, requestedLines) {
    const parsedLines = String(rawContent || '')
        .replace(/\r\n/g, '\n')
        .split('\n')
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => compactRawLogLine(line))
        .filter(Boolean);
    const today = localDateString();
    const hasToday = parsedLines.some((parsed) => parsed.date === today);
    const grouped = new Map();
    const kept = [];

    parsedLines.forEach((parsed, index) => {
        if (hasToday && parsed.date && parsed.date !== today) return;

        const message = shortenLogMessage(service, parsed.message);
        const group = logDisplayGroup(service, message);
        if (group === null) return;

        const display = `${parsed.time ? `${parsed.time} ` : ''}${message}`.trim();
        const entry = { index, display };
        if (group === undefined) {
            kept.push(entry);
            return;
        }
        if (service === 'rp5' && /^rp5-ok-\d{4}-\d{2}-\d{2}-/.test(group) && grouped.has(group)) {
            return;
        }
        grouped.set(group, entry);
    });

    const displayLines = kept
        .concat(Array.from(grouped.values()))
        .sort((a, b) => a.index - b.index)
        .map((entry) => entry.display)
        .filter(Boolean);
    const limit = Math.max(1, Math.min(logDisplayMaxLines, requestedLines));
    return displayLines.slice(-limit).join('\n');
}

function readFileTail(filePath, lines) {
    const stats = fs.statSync(filePath);
    const start = Math.max(0, stats.size - logTailBytes);
    const length = stats.size - start;
    const buffer = Buffer.alloc(length);
    const handle = fs.openSync(filePath, 'r');
    try {
        fs.readSync(handle, buffer, 0, length, start);
    } finally {
        fs.closeSync(handle);
    }
    let text = buffer.toString('utf8');
    if (start > 0) {
        text = text.replace(/^[^\n]*(\n|$)/, '');
    }
    return tailText(text, lines);
}

async function readServiceLog(key, lines) {
    const definition = logServiceDefinitions[key];
    if (!definition) {
        const allowed = Object.keys(logServiceDefinitions).join(', ');
        const error = new Error(`Unknown log service: ${key}. Allowed: ${allowed}`);
        error.statusCode = 404;
        throw error;
    }

    if (definition.type === 'file') {
        let filePath = definition.source();
        if (!fs.existsSync(filePath) && definition.fallbackSource) {
            filePath = definition.fallbackSource();
        }
        const rawLines = Math.min(logTailMaxLines, Math.max(lines, lines * logReadMultiplier));
        const rawContent = readFileTail(filePath, rawLines);
        return {
            label: definition.label,
            source: filePath,
            content: cleanServiceLogContent(key, rawContent, lines),
        };
    }

    if (definition.type === 'journal') {
        const rawLines = Math.min(logTailMaxLines, Math.max(lines, lines * logReadMultiplier));
        const result = await runSshCommand([
            'journalctl',
            '-u',
            definition.unit,
            '-n',
            String(rawLines),
            '--no-pager',
            '-o',
            'short-iso',
        ], 12000);
        return {
            label: definition.label,
            source: `pi:${definition.unit}`,
            content: cleanServiceLogContent(key, result.stdout || result.stderr || '', lines),
        };
    }

    const error = new Error(`Unsupported log source type: ${definition.type}`);
    error.statusCode = 500;
    throw error;
}

async function fetchJsonWithTimeout(url, options = {}, timeoutMs = piAgentTimeoutMs) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        const response = await fetch(url, {
            ...options,
            signal: controller.signal,
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.ok === false) {
            throw new Error(data.error || `HTTP ${response.status}`);
        }
        return data;
    } finally {
        clearTimeout(timer);
    }
}

function postJson(url, payload, timeoutMs = piAgentTimeoutMs) {
    return fetchJsonWithTimeout(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    }, timeoutMs);
}

function queryPiAgentStatus() {
    return fetchJsonWithTimeout(`${piAgentBaseUrl}/api/status`, {}, piAgentTimeoutMs);
}

function appendAudioControlLog(message) {
    const logPath = path.join(__dirname, 'logs', 'audio-control.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] ${message}\n`, 'utf8');
}

async function logPiAudioPreflight(payload, test) {
    if (!['order', 'online', 'offline'].includes(test)) {
        return;
    }

    try {
        const status = await fetchJsonWithTimeout(`${piAgentBaseUrl}/api/status`, {}, 2500);
        const playerOk = Boolean(status.services?.player?.ok);
        const audioAvailable = Boolean(status.audio?.available);
        if (playerOk && audioAvailable) {
            return;
        }

        const reasons = [];
        if (!playerOk) {
            reasons.push(`player=${status.services?.player?.active || status.services?.player?.sub_state || 'not_ok'}`);
        }
        if (!audioAvailable) {
            reasons.push(`audio=${status.audio?.error || 'unavailable'}`);
        }
        appendAudioControlLog(`preflight unavailable before forwarding to Pi type=${payload.type || payload.test || test} reason=${reasons.join(',')}`);
    } catch (error) {
        appendAudioControlLog(`preflight status failed type=${payload.type || payload.test || test} error=${error.message}`);
    }
}

async function controlPiService(key, action) {
    const service = piServiceDefinitions[key];
    if (!service) {
        throw new Error(`Unknown Pi service: ${key}`);
    }
    if (!['start', 'stop'].includes(action)) {
        throw new Error(`Unsupported Pi service action: ${action}`);
    }

    try {
        await postJson(`${piAgentBaseUrl}/api/service/${key}/${action}`, {});
    } catch (agentError) {
        await runSshCommand(['sudo', '-n', 'systemctl', action, service.serviceName]);
    }
    piCache = null;
    return {
        ok: true,
        target: 'pi_service',
        service: key,
        action,
    };
}

function scheduleWindowsReboot() {
    const scheduledAt = new Date().toISOString();
    const logPath = path.join(__dirname, 'logs', 'control.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `[${scheduledAt}] server reboot requested from WEB\n`, 'utf8');

    const rebootScriptPath = path.join(__dirname, 'restart-server.ps1');

    const child = spawn(
        'C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe',
        ['-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden', '-File', rebootScriptPath, '-LogPath', logPath],
        {
            windowsHide: true,
        }
    );
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] server reboot helper spawned pid=${child.pid || 'unknown'} script=${rebootScriptPath}\n`, 'utf8');
    child.stdout.on('data', data => {
        fs.appendFileSync(logPath, `[${new Date().toISOString()}] server reboot helper stdout: ${data.toString('utf8').trim()}\n`, 'utf8');
    });
    child.stderr.on('data', data => {
        fs.appendFileSync(logPath, `[${new Date().toISOString()}] server reboot helper stderr: ${data.toString('utf8').trim()}\n`, 'utf8');
    });
    child.on('error', error => {
        fs.appendFileSync(logPath, `[${new Date().toISOString()}] server reboot launch failed: ${error.message}\n`, 'utf8');
    });
    child.on('close', code => {
        fs.appendFileSync(logPath, `[${new Date().toISOString()}] server reboot helper process closed code=${code}\n`, 'utf8');
    });

    return { ok: true, target: 'server', action: 'reboot', scheduled_at: scheduledAt };
}

async function requestKioskRestartShortcut() {
    const requestedAt = new Date().toISOString();
    const logPath = path.join(__dirname, 'logs', 'control.log');
    fs.mkdirSync(path.dirname(logPath), { recursive: true });
    fs.appendFileSync(logPath, `[${requestedAt}] kiosk restart shortcut requested from WEB url=${kioskAgentRestartShortcutUrl}\n`, 'utf8');

    const result = await postJson(kioskAgentRestartShortcutUrl, {}, 7000);
    fs.appendFileSync(logPath, `[${new Date().toISOString()}] kiosk restart shortcut accepted action=${result.action || 'unknown'}\n`, 'utf8');

    return {
        ok: true,
        target: 'kiosk',
        action: 'restart_shortcut',
        requested_at: requestedAt,
        agent: result,
    };
}

function isPngBuffer(buffer) {
    return Buffer.isBuffer(buffer)
        && buffer.length >= 8
        && buffer[0] === 0x89
        && buffer[1] === 0x50
        && buffer[2] === 0x4e
        && buffer[3] === 0x47
        && buffer[4] === 0x0d
        && buffer[5] === 0x0a
        && buffer[6] === 0x1a
        && buffer[7] === 0x0a;
}

function readKioskScreenshotMeta() {
    const exists = fs.existsSync(kioskScreenshotPath);
    let stat = null;
    try {
        stat = exists ? fs.statSync(kioskScreenshotPath) : null;
    } catch (error) {
        stat = null;
    }

    let meta = {};
    try {
        meta = JSON.parse(fs.readFileSync(kioskScreenshotMetaPath, 'utf8'));
    } catch (error) {
        meta = {};
    }

    return {
        ok: true,
        exists,
        updated_at: meta.updated_at || (stat ? stat.mtime.toISOString() : null),
        bytes: meta.bytes || (stat ? stat.size : null),
        latency_ms: meta.latency_ms ?? null,
        source_url: meta.source_url || null,
        image_url: exists ? '/api/kiosk/screenshot/latest' : null,
    };
}

async function refreshKioskScreenshot(abortSignal = null) {
    const maxWidth = Number.isFinite(kioskScreenshotMaxWidth) && kioskScreenshotMaxWidth > 0
        ? Math.round(kioskScreenshotMaxWidth)
        : 720;
    const sourceUrl = `${kioskMonitorBaseUrl}/api/screenshot?max_width=${maxWidth}`;
    const result = await fetchBuffer(sourceUrl, kioskMonitorTimeoutMs, 8 * 1024 * 1024, abortSignal);
    if (abortSignal && abortSignal.aborted) {
        throw makeAbortError();
    }
    if (!isPngBuffer(result.buffer)) {
        throw new Error('Kiosk monitor returned a non-PNG screenshot');
    }

    fs.mkdirSync(kioskScreenshotDir, { recursive: true });
    if (abortSignal && abortSignal.aborted) {
        throw makeAbortError();
    }
    fs.writeFileSync(kioskScreenshotPath, result.buffer);
    const meta = {
        ok: true,
        updated_at: new Date().toISOString(),
        bytes: result.buffer.length,
        content_type: result.content_type || 'image/png',
        latency_ms: result.latency_ms,
        source_url: sourceUrl,
        image_url: '/api/kiosk/screenshot/latest',
    };
    fs.writeFileSync(kioskScreenshotMetaPath, JSON.stringify(meta, null, 2), 'utf8');
    return meta;
}

function ensureLocalServicesStarted() {
    for (const service of localServiceDefinitions) {
        if (service.autoStart === false) {
            continue;
        }
        try {
            const result = startLocalService(service.key);
            console.log(`[local-service] ${service.key}: ${result.already_running ? 'already running' : 'start requested'}`);
        } catch (error) {
            console.error(`[local-service] ${service.key} start failed:`, error.message);
        }
    }
}

function ensureCashExceptionMonitorStarted() {
    if (process.env.AUTO_START_CASH_EXCEPTION_MONITOR === '0') {
        return;
    }
    if (!readCashExceptionControlState().enabled) {
        return;
    }
    try {
        const result = startLocalService('cash_exception');
        console.log(`[cash-exception] ${result.already_running ? 'already running' : 'start requested'}${result.pid ? ` pid=${result.pid}` : ''}`);
    } catch (error) {
        console.error('[cash-exception] start failed:', error.message);
    }
}

function ensureCashboxEstimatorStarted() {
    if (process.env.AUTO_START_CASHBOX_ESTIMATOR === '0') {
        return;
    }
    if (!readCashboxEstimatorControlState().enabled) {
        return;
    }
    try {
        const result = startLocalService('cashbox_estimator');
        console.log(`[cashbox-estimator] ${result.already_running ? 'already running' : 'start requested'}${result.pid ? ` pid=${result.pid}` : ''}`);
    } catch (error) {
        console.error('[cashbox-estimator] start failed:', error.message);
    }
}

function ensureSalesSyncWorkerStarted() {
    try {
        const processes = queryPythonProcesses();
        const targetName = normalizeCommandText(path.basename(salesCacheSyncWorkerPath));
        const existing = processes.find(item => normalizeCommandText(item.CommandLine).includes(targetName));
        if (existing) {
            console.log(`[sales-sync] already running pid=${existing.ProcessId}`);
            return;
        }

        const child = spawn(
            pythonPath,
            [salesCacheSyncWorkerPath],
            {
                cwd: path.dirname(salesCacheSyncWorkerPath),
                detached: true,
                stdio: 'ignore',
                windowsHide: true,
            }
        );
        child.unref();
        console.log(`[sales-sync] start requested pid=${child.pid}`);
    } catch (error) {
        console.error('[sales-sync] start failed:', error.message);
    }
}

function ensureCashFinanceSyncWorkerStarted() {
    try {
        if (!fs.existsSync(cashFinanceSyncWorkerPath)) {
            console.log(`[cash-finance-sync] worker not found: ${cashFinanceSyncWorkerPath}`);
            return;
        }

        const processes = queryPythonProcesses();
        const targetName = normalizeCommandText(path.basename(cashFinanceSyncWorkerPath));
        const existing = processes.find(item => normalizeCommandText(item.CommandLine).includes(targetName));
        if (existing) {
            console.log(`[cash-finance-sync] already running pid=${existing.ProcessId}`);
            return;
        }

        const child = spawn(
            pythonPath,
            [cashFinanceSyncWorkerPath],
            {
                cwd: path.dirname(cashFinanceSyncWorkerPath),
                detached: true,
                stdio: 'ignore',
                windowsHide: true,
            }
        );
        child.unref();
        console.log(`[cash-finance-sync] start requested pid=${child.pid}`);
    } catch (error) {
        console.error('[cash-finance-sync] start failed:', error.message);
    }
}

function ensureSyncWorkersStarted() {
    if (process.env.AUTO_START_SALES_SYNC !== '0') {
        ensureSalesSyncWorkerStarted();
    }
    if (process.env.AUTO_START_CASH_FINANCE_SYNC !== '0') {
        ensureCashFinanceSyncWorkerStarted();
    }
    ensureCashExceptionMonitorStarted();
    ensureCashboxEstimatorStarted();
}

function startSyncWorkerWatchdog() {
    if (syncWorkerWatchdogMs <= 0) {
        return;
    }
    const timer = setInterval(ensureSyncWorkersStarted, syncWorkerWatchdogMs);
    timer.unref();
}

async function schedulePiReboot() {
    await postJson(`${piAgentBaseUrl}/api/reboot`, {}, 5000).catch(async () => {
        await runSshCommand(['sudo', '-n', 'reboot'], 5000);
    }).catch(error => {
        const message = String(error.message || '');
        if (!/closed|reset|going down|connection/i.test(message)) {
            throw error;
        }
    });
    piCache = null;
    return { ok: true, target: 'pi', action: 'reboot' };
}

function baseServerStatus() {
    const totalMem = os.totalmem();
    const freeMem = os.freemem();
    const usedMem = totalMem - freeMem;
    return {
        ok: true,
        generated_at: new Date().toISOString(),
        server: {
            hostname: os.hostname(),
            platform: os.platform(),
            uptime_seconds: Math.round(os.uptime()),
            load_average: os.loadavg(),
            memory: {
                total_mb: Math.round(totalMem / 1024 / 1024),
                free_mb: Math.round(freeMem / 1024 / 1024),
                used_percent: Math.round((usedMem / totalMem) * 1000) / 10,
            },
            node: {
                pid: process.pid,
                uptime_seconds: Math.round(process.uptime()),
                memory_mb: Math.round(process.memoryUsage().rss / 1024 / 1024),
            },
        },
        web_host: {
            host,
            port,
        },
        local_services: localServiceStatus(),
        kiosk: kioskStatus,
    };
}

async function serverStatus() {
    await queryKioskStatus().catch(error => {
        kioskStatus = {
            ...kioskStatus,
            ok: false,
            online: false,
            last_error_at: new Date().toISOString(),
            last_error: error.message,
        };
    });
    return baseServerStatus();
}

function queryPiStatus() {
    return queryPiAgentStatus().catch(() => new Promise((resolve, reject) => {
        execFile(
            'ssh',
            [
                '-i',
                piSshKeyPath,
                '-o',
                'BatchMode=yes',
                '-o',
                'StrictHostKeyChecking=accept-new',
                piSshTarget,
                piStatusScript,
            ],
            {
                windowsHide: true,
                timeout: 20000,
                maxBuffer: 1024 * 1024,
            },
            (error, stdout, stderr) => {
                if (error) {
                    const details = (stderr || stdout || error.message || '').trim();
                    reject(new Error(details || error.message));
                    return;
                }

                try {
                    resolve(JSON.parse(stdout));
                } catch (parseError) {
                    reject(new Error(`Unable to parse PI status JSON: ${parseError.message}`));
                }
            }
        );
    }));
}

function getCachedPiStatus() {
    const now = Date.now();
    if (piCache && now - piCache.createdAt < piCacheTtlMs) {
        return piCache.promise;
    }

    const promise = queryPiStatus().catch(error => {
        piCache = null;
        throw error;
    });
    piCache = { createdAt: now, promise };
    return promise;
}

function normalizeTemperatureHistory(points, now = Date.now()) {
    const cutoff = now - tempHistorySpanMs;
    const byTime = new Map();
    for (const point of Array.isArray(points) ? points : []) {
        const t = Math.floor(Number(point && point.t) / tempHistoryBucketMs) * tempHistoryBucketMs;
        if (!Number.isFinite(t) || t < cutoff) {
            continue;
        }
        byTime.set(t, { ...point, t });
    }
    return Array.from(byTime.values()).sort((a, b) => a.t - b.t);
}

function readTemperatureHistory() {
    try {
        return normalizeTemperatureHistory(JSON.parse(fs.readFileSync(tempHistoryPath, 'utf8')));
    } catch (error) {
        return [];
    }
}

function writeTemperatureHistory(points) {
    fs.mkdirSync(path.dirname(tempHistoryPath), { recursive: true });
    fs.writeFileSync(tempHistoryPath, JSON.stringify(normalizeTemperatureHistory(points), null, 2), 'utf8');
}

function temperaturePointFromStatus(status, now = Date.now()) {
    const sensors = status && status.temperature && Array.isArray(status.temperature.sensors)
        ? status.temperature.sensors
        : [];
    const point = { t: Math.floor(now / tempHistoryBucketMs) * tempHistoryBucketMs };
    for (const sensor of sensors) {
        if (sensor && sensor.online && sensor.name && sensor.temperature != null) {
            point[sensor.name] = Number(sensor.temperature);
        }
    }
    return Object.keys(point).length > 1 ? point : null;
}

function recordTemperatureHistory(status) {
    const point = temperaturePointFromStatus(status);
    if (!point) {
        return readTemperatureHistory();
    }
    const history = readTemperatureHistory();
    history.push(point);
    writeTemperatureHistory(history);
    return readTemperatureHistory();
}

async function sampleTemperatureHistory() {
    try {
        const status = await getCachedPiStatus();
        recordTemperatureHistory(status);
    } catch (error) {
        console.error('[temperature-history] sample failed:', error.message);
    }
}

function startTemperatureHistorySampler() {
    if (tempHistoryPollMs <= 0) {
        return;
    }
    sampleTemperatureHistory();
    const timer = setInterval(sampleTemperatureHistory, tempHistoryPollMs);
    timer.unref();
}

app.get('/health', (req, res) => {
    res.json({
        ok: true,
        service: '6KA message center',
        host,
        port,
        time: new Date().toISOString(),
    });
});

app.get('/api/sales/today', async (req, res) => {
    const businessDate = getBusinessDate(req);
    try {
        const result = await getCachedSalesSnapshot(businessDate);
        res.json({
            ok: true,
            stale: false,
            generated_at: result.generated_at,
            summary: result.summary,
            month_summary: result.month_summary,
            payments: result.payments || [],
            latest_orders: result.latest_orders || [],
            sales_buckets: result.sales_buckets || [],
        });
    } catch (error) {
        const stale = lastSalesByDate.get(businessDate);
        if (stale) {
            res.json({
                ok: false,
                stale: true,
                offline: true,
                error: error.message,
                generated_at: stale.generated_at,
                summary: stale.summary,
                month_summary: stale.month_summary,
                payments: stale.payments || [],
                latest_orders: stale.latest_orders || [],
                sales_buckets: stale.sales_buckets || [],
            });
            return;
        }
        res.status(502).json({ ok: false, offline: true, error: error.message });
    }
});

app.get('/api/sales/month', async (req, res) => {
    try {
        const result = await getCachedSalesSnapshot(getBusinessDate(req));
        res.json({
            ok: true,
            generated_at: result.generated_at,
            month_summary: result.month_summary,
        });
    } catch (error) {
        res.status(502).json({ ok: false, error: error.message });
    }
});

app.get('/api/payments/today', async (req, res) => {
    try {
        const result = await getCachedSalesSnapshot(getBusinessDate(req));
        res.json({
            ok: true,
            generated_at: result.generated_at,
            business_date: result.summary && result.summary.business_date,
            payments: result.payments || [],
        });
    } catch (error) {
        res.status(502).json({ ok: false, error: error.message });
    }
});

app.get('/api/pi/status', async (req, res) => {
    try {
        const result = await getCachedPiStatus();
        const temperatureHistory = recordTemperatureHistory(result);
        res.json({ ...result, temperature_history: temperatureHistory });
    } catch (error) {
        res.status(502).json({ ok: false, error: error.message });
    }
});

app.get('/api/temperature/history', (req, res) => {
    res.json({
        ok: true,
        generated_at: new Date().toISOString(),
        bucket_ms: tempHistoryBucketMs,
        span_ms: tempHistorySpanMs,
        history: readTemperatureHistory(),
    });
});

app.get('/api/server/status', async (req, res) => {
    res.json(await serverStatus());
});

app.get('/api/logs/:service', async (req, res) => {
    const service = String(req.params.service || '').trim();
    const lines = clampLogLines(req.query.lines);
    try {
        const log = await readServiceLog(service, lines);
        const content = log.content || '';
        res.json({
            ok: true,
            service,
            label: log.label,
            source: log.source,
            lines,
            displayed_lines: content ? content.split('\n').filter(Boolean).length : 0,
            generated_at: new Date().toISOString(),
            content,
        });
    } catch (error) {
        const statusCode = error.statusCode || (error.code === 'ENOENT' ? 404 : 500);
        res.status(statusCode).json({
            ok: false,
            service,
            error: error.message,
        });
    }
});

app.post('/api/control/local-service/:service/:action', async (req, res) => {
    try {
        const { service, action } = req.params;
        if (!['start', 'stop', 'restart'].includes(action)) {
            res.status(400).json({ ok: false, error: `Unsupported action: ${action}` });
            return;
        }
        let control = null;
        if (service === 'cash_exception') {
            if (action === 'stop') {
                control = writeCashExceptionControlState(false);
            } else {
                control = writeCashExceptionControlState(true);
            }
        } else if (service === 'cashbox_estimator') {
            if (action === 'stop') {
                control = writeCashboxEstimatorControlState(false);
            } else {
                control = writeCashboxEstimatorControlState(true);
            }
        }
        const result = action === 'restart'
            ? await restartLocalService(service)
            : (action === 'start' ? startLocalService(service) : stopLocalService(service));
        res.json(control ? { ...result, monitor_enabled: control.enabled, control } : result);
    } catch (error) {
        res.status(500).json({ ok: false, error: error.message });
    }
});

app.post('/api/control/pi-service/:service/:action', async (req, res) => {
    try {
        const result = await controlPiService(req.params.service, req.params.action);
        res.json(result);
    } catch (error) {
        res.status(500).json({ ok: false, error: error.message });
    }
});

app.post('/api/control/pi-audio', async (req, res) => {
    const payload = req.body || {};
    try {
        if (payload.type === 'volume') {
            const volume = Number(payload.volume ?? payload.message);
            res.json(await postJson(`${piAgentBaseUrl}/api/audio/volume`, { volume }));
            piCache = null;
            return;
        }

        const tests = {
            new_order: 'order',
            device_online: 'online',
            device_offline: 'offline',
            order: 'order',
            online: 'online',
            offline: 'offline',
        };
        const test = tests[payload.type] || tests[payload.test];
        if (!test) {
            res.status(400).json({ ok: false, error: `Unsupported audio action: ${payload.type || payload.test}` });
            return;
        }
        await logPiAudioPreflight(payload, test);
        res.json(await postJson(`${piAgentBaseUrl}/api/audio/test`, { ...payload, type: test }));
        piCache = null;
    } catch (agentError) {
        try {
            const fallback = await fetchJsonWithTimeout(audioFallbackApiUrl, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    token: audioFallbackToken,
                    device: 'pi_player',
                    ...payload,
                }),
            }, 8000);
            res.json({ ok: true, fallback: 'cloudflare_worker', result: fallback });
        } catch (fallbackError) {
            res.status(502).json({
                ok: false,
                error: agentError.message,
                fallback_error: fallbackError.message,
            });
        }
    }
});

app.post('/api/control/reboot/:target', async (req, res) => {
    try {
        if (req.params.target === 'server') {
            res.json(scheduleWindowsReboot());
            return;
        }
        if (req.params.target === 'pi') {
            res.json(await schedulePiReboot());
            return;
        }
        if (req.params.target === 'kiosk') {
            res.json(await requestKioskRestartShortcut());
            return;
        }
        res.status(400).json({ ok: false, error: `Unknown reboot target: ${req.params.target}` });
    } catch (error) {
        res.status(500).json({ ok: false, error: error.message });
    }
});

app.get('/api/kiosk/screenshot/status', (req, res) => {
    res.json(readKioskScreenshotMeta());
});

app.get('/api/kiosk/screenshot/latest', (req, res) => {
    if (!fs.existsSync(kioskScreenshotPath)) {
        res.status(404).json({ ok: false, error: 'No kiosk screenshot has been captured yet' });
        return;
    }
    res.setHeader('Cache-Control', 'no-store, max-age=0');
    res.type('png');
    res.sendFile(kioskScreenshotPath);
});

app.post('/api/kiosk/screenshot/refresh', async (req, res) => {
    const abortController = new AbortController();
    const abortRefresh = () => {
        abortController.abort();
    };
    req.on('aborted', abortRefresh);
    res.on('close', () => {
        if (!res.writableEnded) abortController.abort();
    });
    try {
        const meta = await refreshKioskScreenshot(abortController.signal);
        res.json({
            ...meta,
            image_url: `${meta.image_url}?t=${encodeURIComponent(meta.updated_at)}`,
        });
    } catch (error) {
        if (abortController.signal.aborted || error.code === 'ABORT_ERR') {
            if (!res.headersSent && !res.writableEnded) {
                res.status(499).json({ ok: false, error: 'Request aborted' });
            }
            return;
        }
        res.status(502).json({ ok: false, error: error.message });
    }
});

app.use(express.static(path.join(__dirname, 'public'))); // 靜態文件夾

// 創建HTTP服務器與WebSocket服務器共享
const server = app.listen(port, host, () => {
    console.log(`Server is running on http://${host}:${port}`);
    if (process.env.AUTO_START_LOCAL_SERVICES !== '0') {
        setTimeout(ensureLocalServicesStarted, 2500);
    }
    if (process.env.AUTO_START_SALES_SYNC !== '0') {
        setTimeout(ensureSalesSyncWorkerStarted, 4000);
    }
    if (process.env.AUTO_START_CASH_FINANCE_SYNC !== '0') {
        setTimeout(ensureCashFinanceSyncWorkerStarted, 5500);
    }
    if (process.env.AUTO_START_CASH_EXCEPTION_MONITOR !== '0') {
        setTimeout(ensureCashExceptionMonitorStarted, 6500);
    }
    if (process.env.AUTO_START_CASHBOX_ESTIMATOR !== '0') {
        setTimeout(ensureCashboxEstimatorStarted, 8000);
    }
    startSyncWorkerWatchdog();
    startTemperatureHistorySampler();
});

server.on('upgrade', (request, socket, head) => {
    wss.handleUpgrade(request, socket, head, socket => {
        wss.emit('connection', socket, request);
    });
});
