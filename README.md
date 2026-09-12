# NSE Momentum Scanner — Final Implementation Bundle

This replacement bundle implements the final strategy agreed for the NSE/Dhan/Telegram scanner.

## Strategy

```text
Completed 15M candle
        ↓
15M BUY / SELL quality filters
        ↓
Demand/Supply trade-quality score 0–7
        ↓
Score >= 5
        ↓
Store exact 15M setup
        ↓
Wait for subsequent completed 5M candles
        ↓
BUY: 5M close > stored 15M high
SELL: 5M close < stored 15M low
        ↓
Entry = confirming 5M close
SL = stored 15M close
        ↓
Minimum SL distance >= 0.50%
        ↓
T1 >= 2R
        ↓
Market-structure validation
        ↓
Telegram alert
```

## Main changes

- 15M setup timeframe.
- 5M confirmation/entry timeframe.
- No 1M confirmation.
- Scanner cadence: approximately every 5 minutes.
- 15M setup is stored persistently.
- Pending setup expires when the next completed 15M candle appears.
- BUY requires completed 5M close above stored 15M high.
- SELL requires completed 5M close below stored 15M low.
- Entry is the completed 5M close.
- Initial SL is exactly the stored 15M close.
- Minimum Entry/SL distance is 0.50%.
- T1/T2/T3 = 2R/3R/4R.
- New trade-quality score is 0–7 with minimum 5.
- Old score/grade gates are removed from signal generation.
- Telegram is intentionally concise.
- Detailed decisions remain in logs.
- Summary is point based from the original Entry.
- Universe refreshes every 15 minutes and appends new candidates.

## Telegram alert

```text
🚀 BUY ALERT

XYZ

Entry: ₹500
SL: ₹495

T1: ₹510
T2: ₹515
T3: ₹520

Setup Quality: 6.0/7
```

## Summary

```text
📊 NSE MOMENTUM SUMMARY

XYZ Stocks +15.00 points
INFY Stocks -5.00 points
TCS Stocks +8.00 points
```

## Small-stop rejection

A signal such as:

```text
Entry = ₹1,259.90
SL = ₹1,259.30
Risk = ₹0.60
Risk % ≈ 0.048%
```

is rejected as:

```text
SIGNAL_REJECTED | reason=STOP_DISTANCE_TOO_SMALL
```

No Telegram alert is sent.

## Files in this bundle

- `app/config.py`
- `app/indicators.py`
- `app/main.py`
- `app/risk.py`
- `app/scoring.py`
- `app/state.py`
- `app/strategy.py`
- `app/summary.py`
- `app/telegram.py`
- `.github/workflows/nse-momentum-scan.yml`
- `.github/workflows/nse-momentum-universe.yml`
- `.github/workflows/nse-momentum-summary.yml`
- `tests/test_final_strategy.py`
- `CRON_JOB_SETUP.md`
- `FINAL_CODE_GENERATION_PROMPT.md`

## Important deployment note

This is a **replacement-file bundle**, not a full clone of the repository. Existing infrastructure such as `app/dhan_client.py`, `app/calendar.py`, `app/nse_universe.py`, requirements, secrets, and other unchanged repository files must remain in place.

The external cron-job.org account is not modified by this ZIP. Configure its jobs according to `CRON_JOB_SETUP.md`.

## Verification performed on this bundle

- Python compilation: passed.
- Final strategy unit tests: 12 passed.
- The four supplied small Entry/SL examples were verified to return `STOP_DISTANCE_TOO_SMALL`.
