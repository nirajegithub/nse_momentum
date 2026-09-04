from __future__ import annotations
from collections import defaultdict
import logging
import requests
from .config import SETTINGS
from .dhan_client import DhanClient

LOG = logging.getLogger(__name__)
BASE = "https://www.nseindia.com"
URL = f"{BASE}/api/heatmap-symbols"
INDEXES = {"M50": "NIFTY500MOMENTM50", "M30": "NIFTY200MOMENTM30"}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE + "/",
}

def extract_symbols(payload):
    found = set()
    keys = {"symbol", "tradingsymbol", "tradingSymbol", "symbolCode"}
    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k in keys and isinstance(v, str):
                    s = v.strip().upper()
                    if s and len(s) <= 40 and s.replace("-", "").isalnum():
                        found.add(s)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(payload)
    return sorted(found)

def fetch_index(session, code):
    r = session.get(URL, params={"type": "Strategy Indices", "indices": code}, timeout=20)
    r.raise_for_status()
    return extract_symbols(r.json())

def build_universe(dhan: DhanClient):
    s = requests.Session(); s.headers.update(HEADERS); s.get(BASE + "/", timeout=20)
    membership = defaultdict(set)
    for name, code in INDEXES.items():
        symbols = fetch_index(s, code)
        LOG.info("%s returned %d symbols", name, len(symbols))
        for symbol in symbols:
            membership[symbol].add(name)

    symbols = sorted(membership)
    mapping = dhan.build_symbol_map(symbols)
    result = []
    for symbol in symbols:
        meta = mapping.get(symbol)
        if not meta:
            LOG.warning("Missing Dhan security_id: %s", symbol)
            continue
        result.append({
            "symbol": symbol,
            **meta,
            "indices": sorted(membership[symbol]),
            "membership_count": len(membership[symbol]),
        })
    return result
