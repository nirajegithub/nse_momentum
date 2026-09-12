# NSE Momentum — Cron Job Setup

The repository code is now designed around **15M setup + completed 5M confirmation**.
The scanner does **not** need 1-minute execution.

## Recommended cron-job.org jobs

| Job | IST schedule | Action | Purpose |
|---|---|---|---|
| Dhan Token | 08:30 daily | token update | Refresh/validate Dhan token |
| Universe Initial | 09:22 daily | `universe` | Create today's initial universe |
| Universe Refresh | 09:30–15:00 every 15 min | `universe_refresh` | Discover and append new candidates |
| Scanner | 09:30–15:05 every 5 min | `scan` | Evaluate new 15M setups and completed 5M confirmation |
| Monitor | existing monitoring window | `monitor` | Monitor active alerts / LTP |
| Summary | 15:25 daily | `summary` | Send simple point-based summary |

## Important

The ZIP changes the repository/workflow code. It does **not** remotely change cron-job.org account settings.
Update the external cron-job.org jobs separately using the schedules above.

## Scanner cadence

The scanner should execute every 5 minutes because the strategy confirms entry using a **completed 5M candle**.

Do not use 1-minute confirmation.

Do not run a separate 1-minute scanner for this strategy.

## GitHub Actions UTC conversion

GitHub Actions cron uses UTC.

IST = UTC + 05:30.

Examples:

- 09:22 IST = 03:52 UTC
- 09:30 IST = 04:00 UTC
- 15:00 IST = 09:30 UTC
- 15:05 IST = 09:35 UTC
- 15:25 IST = 09:55 UTC

The included workflow files already use these UTC schedules.

## Universe behavior

09:22 creates a fresh universe.

Every later universe refresh reads the current day's stored universe and **appends only new `security_id` values**.

It never replaces the existing universe.

## Scanner behavior

Each 5-minute scanner execution:

1. Reads the latest completed 15M candle.
2. Creates/replaces the pending setup when a new 15M candle appears.
3. Reads the latest completed 5M candle.
4. Confirms BUY only when `5M close > stored 15M high`.
5. Confirms SELL only when `5M close < stored 15M low`.
6. Entry = confirming 5M close.
7. SL = stored 15M close.
8. Rejects Entry/SL distance below 0.50%.
9. Requires T1 at 2R or better.
10. Sends the concise Telegram alert only after all validation passes.
