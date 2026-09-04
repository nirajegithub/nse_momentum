from __future__ import annotations
import logging, os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from .calendar import is_nse_trading_day
from .config import SETTINGS
from .dhan_client import DhanClient
from .indicators import add_indicators
from .nse_universe import build_universe
from .scoring import score_signal
from .state import load, save, key, active_for_symbol
from .strategy import evaluate
from .summary import build_summary
from .telegram import send, signal_message, exit_message

IST=ZoneInfo("Asia/Kolkata"); LOG=logging.getLogger(__name__)

def now(): return datetime.now(IST)

def ltp_batch(dhan, ids):
    if not ids: return {}
    response=dhan.dhan.ohlc_data(securities={"NSE_EQ":[int(x) if str(x).isdigit() else str(x) for x in ids]})
    data=response.get("data", response) if isinstance(response,dict) else {}
    block=data.get("NSE_EQ", {}) if isinstance(data,dict) else {}
    return block if isinstance(block,dict) else {}

def create_universe(dhan, state):
    if state["universe"]: return
    state["universe"]=build_universe(dhan); save(state)
    LOG.info("Universe size: %d",len(state["universe"]))

def scan(dhan,state,ts):
    start=ts-timedelta(days=7); changed=False
    ids=[x["security_id"] for x in state["universe"]]
    # Quotes are batched; only candidates need LTP for alert display.
    for item in state["universe"]:
        symbol=item["symbol"]
        try:
            df5=add_indicators(dhan.intraday_df(item["security_id"],5,start.strftime("%Y-%m-%d"),ts.strftime("%Y-%m-%d")),SETTINGS.rvol_lookback)
            df15=add_indicators(dhan.intraday_df(item["security_id"],15,start.strftime("%Y-%m-%d"),ts.strftime("%Y-%m-%d")),SETTINGS.rvol_lookback)
            result=evaluate(df5,df15)
            if not result: continue
            score,grade=score_signal(result["regime"],result)
            if grade not in {"A","A+"} or active_for_symbol(state,symbol): continue
            q=ltp_batch(dhan,[item["security_id"]]).get(str(item["security_id"]),{})
            ltp=q.get("last_price",q.get("ltp"));
            if ltp is None: continue
            s={"symbol":symbol,"security_id":item["security_id"],"direction":result["direction"],"setup":result["setup"],"signal_time":result["candle_time"],"signal_price":result["signal_price"],"ltp":float(ltp),"score":score,"grade":grade,"status":"ACTIVE","risk":result["risk"],"rvol":result["rvol"],"rsi":result["rsi"],"regime":result["regime"]}
            k=key(symbol,s["direction"],s["setup"],s["signal_time"])
            if k in state["signals"]: continue
            if send(signal_message(s)):
                state["signals"][k]=s; changed=True
        except Exception: LOG.exception("Scan failed: %s",symbol)
    if changed: save(state)

def monitor(dhan,state,ts):
    active=[s for s in state["signals"].values() if s.get("status")=="ACTIVE"]
    if not active: return
    quotes=ltp_batch(dhan,[s["security_id"] for s in active])
    changed=False
    for s in active:
        q=quotes.get(str(s["security_id"]),{}); px=q.get("last_price",q.get("ltp"));
        if px is None: continue
        px=float(px); reason=None
        if s["direction"]=="BUY" and px<=s["risk"]["sl"]: reason="Stop loss reached"
        if s["direction"]=="SELL" and px>=s["risk"]["sl"]: reason="Stop loss reached"
        if reason and send(exit_message(s,px,reason,ts.strftime("%H:%M:%S"))):
            s["status"]="EXITED"; s["exit_price"]=px; s["exit_time"]=ts.isoformat(); s["exit_reason"]=reason; changed=True
    if changed: save(state)

def summary(dhan,state,ts):
    active=[s for s in state["signals"].values() if s.get("status")=="ACTIVE"]
    quotes=ltp_batch(dhan,[s["security_id"] for s in active])
    prices={}
    for s in active:
        q=quotes.get(str(s["security_id"]),{}); px=q.get("last_price",q.get("ltp"))
        if px is not None:
            s["status"]="CLOSED_EOD"; s["exit_price"]=float(px); s["exit_time"]=ts.isoformat(); s["exit_reason"]="END_OF_DAY"; prices[s["symbol"]]=float(px)
    send(build_summary(state,prices)); save({"date":"","universe":[],"signals":{}})

def main():
    logging.basicConfig(level=os.getenv("LOG_LEVEL","INFO"))
    ts=now(); action=os.getenv("SCANNER_ACTION","auto").lower(); hhmm=ts.hour*100+ts.minute
    if not is_nse_trading_day(ts.date()): LOG.info("Not an NSE trading day"); return
    dhan=DhanClient(); state=load(ts.date())
    if action=="universe": create_universe(dhan,state); return
    if not state["universe"]: LOG.warning("Universe missing; refusing to scan"); return
    if action=="scan" or (action=="auto" and 925<=hhmm<=1505): scan(dhan,state,ts)
    elif action=="monitor" or (action=="auto" and 1510<=hhmm<=1525): monitor(dhan,state,ts)
    elif action=="summary" or (action=="auto" and hhmm==1530): summary(dhan,state,ts)

if __name__=="__main__": main()
