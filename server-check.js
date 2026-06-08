const express = require('express');
const WebSocket = require('ws');
const chokidar = require('chokidar');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFile, execFileSync, spawn } = require('child_process');

const app = express();
const port = Number(process.env.PORT || 3000);
const host = process.env.HOST || '100.114.61.65';
const pythonPath = process.env.PYTHON_PATH || 'C:\\Python312\\python.exe';
const rpScriptPath = process.env.RP_SCRIPT_PATH || 'C:\\RP\\rp_v5.0.py';
const kitchenScriptPath = process.env.KITCHEN_SCRIPT_PATH || 'C:\\6KAK\\6kak_v2.0.py';
const piSshKeyPath = process.env.PI_SSH_KEY_PATH || 'C:\\RP\\ssh\\6ka_pi_codex';
const piSshTarget = process.env.PI_SSH_TARGET || 'djay@6ka-pi';
const piStatusScript = process.env.PI_STATUS_SCRIPT || '/home/djay/bin/6ka_pi_status.py';
const salesCacheTtlMs = Number(process.env.SALES_CACHE_TTL_MS || 8000);
const piCacheTtlMs = Number(process.env.PI_CACHE_TTL_MS || 15000);
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
        matchTerms: ['rp_v5.0.py', 'rp_v5.0.txt', 'rp5.0.bat'],
    },
    {
        key: 'kitchen_dc',
        label: '廚房製作單系統',
        scriptPath: kitchenScriptPath,
        matchTerms: ['6kak_v2.0.py', '6kak2.0.bat'],
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

function getBusinessDate(req) {
    const value = req.query.date;
    if (typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
        return value;
    }
    return new Date().toISOString().slice(0, 10);
}

function querySalesSnapshot(businessDate) {
    const code = [
        'import importlib.util, json, sys',
        `spec = importlib.util.spec_from_file_location("rp_v5", ${JSON.stringify(rpScriptPath)})`,
        'module = importlib.util.module_from_spec(spec)',
        'spec.loader.exec_module(module)',
        'result = module.run_remote_sql_query(sys.argv[1])',
        'print(json.dumps(result, ensure_ascii=False))',
    ].join('; ');

    return new Promise((resolve, reject) => {
        execFile(
            pythonPath,
            ['-c', code, businessDate],
            {
                cwd: path.dirname(rpScriptPath),
                windowsHide: true,
                timeout: 45000,
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
                ok: true,
                last_success_at: new Date().toISOString(),
                last_error_at: kioskStatus.last_error_at,
                last_error: null,
                latency_ms: Date.now() - startedAt,
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
                latency_ms: Date.now() - startedAt,
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
    const targetPath = normalizeCommandText(path.resolve(service.scriptPath));
    const targetName = normalizeCommandText(path.basename(service.scriptPath));
    const terms = [targetPath, targetName, ...(service.matchTerms || []).map(normalizeCommandText)];
    return terms.some(term => term && commandLine.includes(term));
}

function queryPythonProcesses() {
    const command = `
$ErrorActionPreference = 'Stop'
Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^(python|pythonw)\\.exe$' } |
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

    const child = spawn(
        pythonPath,
        [path.basename(service.scriptPath)],
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

async function controlPiService(key, action) {
    const service = piServiceDefinitions[key];
    if (!service) {
        throw new Error(`Unknown Pi service: ${key}`);
    }
    if (!['start', 'stop'].includes(action)) {
        throw new Error(`Unsupported Pi service action: ${action}`);
    }

    await runSshCommand(['sudo', '-n', 'systemctl', action, service.serviceName]);
    piCache = null;
    return {
        ok: true,
        target: 'pi_service',
        service: key,
        action,
    };
}

function scheduleWindowsReboot() {
    execFile(
        'shutdown.exe',
        ['/r', '/t', '8', '/c', '6KAweb requested server reboot'],
        { windowsHide: true },
        () => {}
    );
    return { ok: true, target: 'server', action: 'reboot' };
}

async function schedulePiReboot() {
    await runSshCommand(['sudo', '-n', 'reboot'], 5000).catch(error => {
        const message = String(error.message || '');
        if (!/closed|reset|going down|connection/i.test(message)) {
            throw error;
        }
    });
    piCache = null;
    return { ok: true, target: 'pi', action: 'reboot' };
}

function serverStatus() {
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

function queryPiStatus() {
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
    });
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
        res.json(result);
    } catch (error) {
        res.status(502).json({ ok: false, error: error.message });
    }
});

app.get('/api/server/status', (req, res) => {
    res.json(serverStatus());
});

app.post('/api/control/local-service/:service/:action', (req, res) => {
    try {
        const { service, action } = req.params;
        if (!['start', 'stop'].includes(action)) {
            res.status(400).json({ ok: false, error: `Unsupported action: ${action}` });
            return;
        }
        const result = action === 'start'
            ? startLocalService(service)
            : stopLocalService(service);
        res.json(result);
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
        res.status(400).json({ ok: false, error: `Unknown reboot target: ${req.params.target}` });
    } catch (error) {
        res.status(500).json({ ok: false, error: error.message });
    }
});

app.use(express.static(path.join(__dirname, 'public'))); // 靜態文件夾

// 創建HTTP服務器與WebSocket服務器共享
const server = app.listen(port, host, () => {
    console.log(`Server is running on http://${host}:${port}`);
});

server.on('upgrade', (request, socket, head) => {
    wss.handleUpgrade(request, socket, head, socket => {
        wss.emit('connection', socket, request);
    });
});
