$ErrorActionPreference = 'Stop'

function Load-DotEnv {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }

    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith('#') -or -not $line.Contains('=')) {
            continue
        }

        $parts = $line.Split('=', 2)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim().Trim('"').Trim("'")
        if ($name -and -not [Environment]::GetEnvironmentVariable($name)) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

Load-DotEnv -Path (Join-Path $PSScriptRoot '.env')

$remoteHost = [Environment]::GetEnvironmentVariable('KIOSK_HOST')
if (-not $remoteHost) { $remoteHost = '100.113.224.68' }

$session = New-PSSession -ComputerName $remoteHost -Authentication Negotiate
try {
    Invoke-Command -Session $session -ScriptBlock {
        $dbPath = 'C:\Protech\Suit.Kiosk\Database\finance.db'
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) {
            $python = Get-Command py -ErrorAction SilentlyContinue
        }
        if (-not $python) {
            throw 'python not found on kiosk'
        }

        $code = @'
import json
import sqlite3

db_path = r"C:\Protech\Suit.Kiosk\Database\finance.db"
conn = sqlite3.connect("file:" + db_path + "?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

tables = []
for row in cur.execute("select name, sql from sqlite_master where type='table' order by name"):
    name = row["name"]
    count = cur.execute('select count(*) as c from "{}"'.format(name.replace('"', '""'))).fetchone()["c"]
    columns = [
        {
            "cid": col[0],
            "name": col[1],
            "type": col[2],
            "notnull": col[3],
            "default": col[4],
            "pk": col[5],
        }
        for col in cur.execute('pragma table_info("{}")'.format(name.replace('"', '""')))
    ]
    sample = []
    if count:
        sample = [
            dict(item)
            for item in cur.execute('select * from "{}" limit 5'.format(name.replace('"', '""')))
        ]
    tables.append({
        "name": name,
        "count": count,
        "columns": columns,
        "sample": sample,
        "sql": row["sql"],
    })

print(json.dumps({"database": db_path, "tables": tables}, ensure_ascii=False))
'@

        $tempScript = Join-Path $env:TEMP 'probe_finance_db.py'
        Set-Content -LiteralPath $tempScript -Value $code -Encoding UTF8
        & $python.Source $tempScript
        Remove-Item -LiteralPath $tempScript -Force -ErrorAction SilentlyContinue
    } | ConvertFrom-Json | ConvertTo-Json -Depth 20
} finally {
    Remove-PSSession $session
}
