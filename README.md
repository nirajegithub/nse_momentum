# NSE Momentum Telegram Scanner V1

Universe discovery uses NSE most-active volume, most-active value, and gainers from **NIFTY**, **NIFTYNEXT50**, and **FOSec**. M50/M30 discovery is disabled.

## Final schedule (Asia/Kolkata)
- 09:22: check NSE trading day; fetch configured NSE discovery sources; merge + deduplicate; map Dhan security IDs; apply the previous-day Dhan price and volume filters; save the daily universe.
- 09:30 through 15:05: every 5 minutes, scan only today's universe for new A/A+ signals using a completed 15M regime, 5M setup, and 1M entry trigger.
- During every scan cycle through 15:05: monitor existing active signals for stop-loss and T1 exits.
- 15:10 through 15:25: monitor existing active signals only.
- 15:25: finalise summary; exited signals use actual exit price, active-at-EOD signals use final LTP; back up the completed state to `state/backups/runtime_state_YYYY-MM-DD.json`, then clear runtime state.
- Saturday, Sunday and dates in `data/nse_holidays.json` are skipped.

## Data
DhanHQ-py 2.2.0 is used. Security IDs come from Dhan's security master, not hard-coded values. Intraday data is requested through the official SDK; completed 1M, 5M, and 15M candles are used for entry, setup/risk structure, and regime respectively. V1 does not use WebSocket or order placement.

## Telegram
Every message automatically appends the required disclaimer:

⚠️ Disclaimer: Above calls are not Buy or Sell levels. These calls are for educational purposes only, based on research. Consult your financial advisor before investing.

## Secrets
Set GitHub repository secrets: `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Keep `DRY_RUN=true` until dry-run validation is complete.

## cron-job.org
Use Asia/Kolkata timezone.

1. 09:22 job: trigger GitHub Actions workflow dispatch with input `action=universe`.
2. Every 5 minutes from 09:30 to 15:05: trigger the scanner workflow with `action=scan`. Continue using `monitor` for 15:10–15:25 and `summary` at 15:25. cron-job.org can use separate jobs for these windows to keep routing explicit.

The GitHub API workflow-dispatch request requires a GitHub token. Store that token in cron-job.org securely; never place Dhan or Telegram credentials in the URL.

## Signal quality gates
Alerts require confirmed 15M market structure and reject RVOL above `20.0x` by default. Set `ALERT_REQUIRE_STRUCTURE=false` or adjust `ALERT_MAX_RVOL` only after reviewing the resulting signals. `MIN_STOP_ATR` controls the minimum technical stop distance relative to ATR; its default is `0.5`.

Signals use the completed 1M trigger close as an indicative entry. With no order execution or partial-position model, T1 is treated as the full theoretical exit; T2 and T3 are reference levels only.

## Local test
```bash
python -m venv .venv
# activate the environment
pip install -r requirements.txt
pytest -q
python -m app.main
```

## First rollout
1. Run one-symbol/data validation with POLYCAB.
2. Keep Telegram in dry-run.
3. Validate Dhan security mapping and candle timestamps.
4. Validate duplicate protection and EXIT accounting.
5. Enable full M50+M30 universe only after validation.

## Important
NSE and Dhan response schemas can change. Run the first deployment in dry-run and inspect logs before enabling live Telegram messages.
