# NSE Momentum — B1 Progressive TS Validation

This package validates the finalized B1 ORB entry against a progressive trailing-stop exit model over 2026-09-01 through 2026-09-11.

## Entry (unchanged)
First completed 15M candle whose close breaks the fixed 09:15 ORB. One first opportunity per symbol/day. Entry is the 15M breakout close. Initial SL is the 09:15 close.

## Exit comparison
- B1: existing fixed initial stop / T1-T3 / EOD outcome model.
- Progressive TS: same entry and initial stop; +1R -> BE; +1.5R -> +0.5R; +2R -> +1R; +3R -> +2R; above +3R -> latest completed 15M structure. Stop ratchets only upward for BUY / downward for SELL and becomes active on the next 5M candle.

## Run
From the repository root:

```bash
python scripts/historical_backtest_orb_ts_1_11.py --start-date 2026-09-01 --end-date 2026-09-11 --universe-json state/runtime_state.json
```

For cached candles only:

```bash
python scripts/historical_backtest_orb_ts_1_11.py --start-date 2026-09-01 --end-date 2026-09-11 --universe-json state/runtime_state.json --no-fetch
```

Outputs are written under `backtest_orb_ts_results/`.

**Important:** this is backtest-only. It does not modify the live scanner.
