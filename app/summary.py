def build_summary(state, final_prices):
    buys=[]; sells=[]; total=ap=aa=0
    for s in state["signals"].values():
        if s.get("status") not in {"ACTIVE","EXITED","CLOSED_EOD"}: continue
        total += 1; ap += s.get("grade")=="A+"; aa += s.get("grade")=="A"
        entry=float(s["risk"]["entry"]); px=float(s.get("exit_price", final_prices.get(s["symbol"], entry)))
        move=(px-entry)/entry*100 if s["direction"]=="BUY" else (entry-px)/entry*100
        line=f"{s['symbol']:<10} ₹{entry:,.0f} → ₹{px:,.0f}  {move:+.1f}%"
        (buys if s["direction"]=="BUY" else sells).append(line)
    return "\n".join(["📊 DAILY MOMENTUM SUMMARY", state["date"], "", "🚀 BUY", *(buys or ["None"]), "", "🔻 SELL", *(sells or ["None"]), "", f"Signals: {total}", f"A+: {ap} | A: {aa}"])
