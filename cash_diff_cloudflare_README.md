# 6KA Cash Diff Cloudflare

## Files

- `cloudflare_cash_api_worker.js`
  - Cloudflare Worker API.
  - `GET /api/cash/current`: public read endpoint for the staff cash-counting page.
  - `PUT /api/cash/current`: protected write endpoint for 6KAweb server push.
- `cash_diff_app.html`
  - External staff cash-counting page.
  - Loads system denomination quantities into column A.
- `cash_diff_cloudflare_push.py`
  - Local 6KAweb push helper.
  - Reads `C:\6KAweb\data\finance_cache\sync_state.json`.
- `wrangler.cash-diff.toml`
  - Wrangler deploy config.

## Cloudflare Worker

Set the KV binding:

```toml
[[kv_namespaces]]
binding = "CASH_DIFF_KV"
id = "<your KV namespace id>"
```

Set the secret:

```powershell
npx wrangler@3 secret put CASH_PUSH_TOKEN --config .\wrangler.cash-diff.toml
```

Deploy:

```powershell
npx wrangler@3 deploy --config .\wrangler.cash-diff.toml
```

## 6KAweb Push

Set these environment variables before starting `cash_finance_sync_worker.py`:

```powershell
$env:CASH_DIFF_CLOUD_PUSH_ENABLED = "1"
$env:CASH_DIFF_CLOUD_API_URL = "https://6ka-cash-diff-api.jay-fbf.workers.dev/api/cash/current"
$env:CASH_DIFF_CLOUD_API_TOKEN = "<same token as CASH_PUSH_TOKEN>"
```

The worker pushes only when the cash payload changes. Normal 10-second local sync loops do not call Cloudflare unless the data changed.

## Request Volume

- Staff page fetches once on load and once when pressing `更新系統`.
- 6KAweb pushes only on payload change.
- No browser polling is used.

## Note

If Cloudflare returns `error code: 1010` for local server push, the request is being blocked by a Cloudflare security layer before it reaches the Worker. Add a Cloudflare rule to skip Browser Integrity / security checks for this Worker route or use a custom domain with an API-safe rule.
