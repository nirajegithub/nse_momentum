from __future__ import annotations
import json
from datetime import date
from pathlib import Path
STATE = Path(__file__).resolve().parents[1] / "state" / "runtime_state.json"

def blank(day): return {"date": day.isoformat(), "universe": [], "signals": {}}
def load(day):
    if not STATE.exists(): return blank(day)
    try: s=json.loads(STATE.read_text())
    except Exception: return blank(day)
    return s if s.get("date")==day.isoformat() else blank(day)
def save(s):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2, ensure_ascii=False))
def key(symbol, direction, setup, candle): return f"{symbol}|{direction}|{setup}|{candle}"
def active_for_symbol(s, symbol): return any(v.get("symbol")==symbol and v.get("status")=="ACTIVE" for v in s["signals"].values())
