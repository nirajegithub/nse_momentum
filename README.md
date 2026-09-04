# NSE Momentum Telegram Scanner V1

V1 universe: **NIFTY500MOMENTM50 (M50)** + **NIFTY200MOMENTM30 (M30)** only.

## Final schedule (Asia/Kolkata)
- 09:22: check NSE trading day; fetch M50/M30; merge + deduplicate; map Dhan security IDs; apply Price > ₹250 and previous-day Volume > 500,000; save fixed daily universe.
- 09:25 through 15:05: every 5 minutes, scan only today's universe for new A/A+ signals.
- 15:10 through 15:25: monitor existing active signals only.
- 15:30: finalise summary; exited signals use actual exit price, active-at-EOD signals use final LTP; clear runtime state.
- Saturday, Sunday and dates in `data/nse_holidays.json` are skipped.

## Data
DhanHQ-py 2.2.0 is used. Security IDs come from Dhan's security master, not hard-coded values. Intraday data is requested through the official SDK and resampled locally into completed 5M/15M candles. V1 does not use WebSocket or order placement.

## Telegram
Every message automatically appends the required disclaimer:

⚠️ Disclaimer: Above calls are not Buy or Sell levels. These calls are for educational purposes only, based on research. Consult your financial advisor before investing.

## Secrets
Set GitHub repository secrets: `DHAN_CLIENT_ID`, `DHAN_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.

Keep `DRY_RUN=true` until dry-run validation is complete.

## cron-job.org
Use Asia/Kolkata timezone.

1. 09:22 job: trigger GitHub Actions workflow dispatch with input `action=universe`.
2. Every 5 minutes from 09:25 to 15:30: trigger the same workflow. Use `scan` for 09:25–15:05, `monitor` for 15:10–15:25, and `summary` at 15:30. cron-job.org can use separate jobs for these windows to keep routing explicit.

The GitHub API workflow-dispatch request requires a GitHub token. Store that token in cron-job.org securely; never place Dhan or Telegram credentials in the URL.

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
