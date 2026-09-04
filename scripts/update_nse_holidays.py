from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

IST=ZoneInfo("Asia/Kolkata"); OUT=Path(__file__).resolve().parents[1]/"data"/"nse_holidays.json"
BASE="https://www.nseindia.com"; URL=f"{BASE}/api/holiday-master?type=trading"
HEADERS={"User-Agent":"Mozilla/5.0 Chrome/124 Safari/537.36","Accept":"application/json","Referer":BASE+"/"}

def main():
    s=requests.Session(); s.headers.update(HEADERS); s.get(BASE+"/",timeout=20)
    r=s.get(URL,timeout=20); r.raise_for_status(); payload=r.json()
    rows=payload.get("CM",[]) if isinstance(payload,dict) else []
    year=datetime.now(IST).year; holidays=[]; details=[]
    for row in rows:
        raw=row.get("tradingDate")
        if not raw: continue
        try: d=datetime.strptime(raw,"%d-%b-%Y").date()
        except ValueError: continue
        if d.year==year:
            holidays.append(d.isoformat()); details.append({"date":d.isoformat(),"day":row.get("weekDay"),"description":row.get("description")})
    OUT.parent.mkdir(parents=True,exist_ok=True); OUT.write_text(json.dumps({"year":year,"market":"CM","updated_at_ist":datetime.now(IST).isoformat(),"holidays":sorted(set(holidays)),"holiday_details":details},indent=2)+"\n")
if __name__=="__main__": main()
