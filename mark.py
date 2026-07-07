#!/usr/bin/env python3
"""Auto-marker for Private Book.
Marks crypto (Hyperliquid) on every run; marks equities (Yahoo) only during
US market hours (09:30-16:00 America/New_York, Mon-Fri). Pushes to JSONBin.
Env: JSONBIN_KEY (secret), JSONBIN_BIN.
"""
import json, os, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = os.environ["JSONBIN_KEY"]
BIN = os.environ.get("JSONBIN_BIN", "6a4d02a9da38895dfe3adc02")
BASE = f"https://api.jsonbin.io/v3/b/{BIN}"
H = {"X-Master-Key": KEY, "Content-Type": "application/json", "User-Agent": "curl/8.5.0"}
HL = {"Content-Type": "application/json", "User-Agent": "curl/8.5.0"}
YH = {"User-Agent": "Mozilla/5.0"}

def req(url, method="GET", body=None, headers=None, tries=3):
    for i in range(tries):
        try:
            r = urllib.request.Request(url, method=method, headers=headers or H,
                                       data=json.dumps(body).encode() if body else None)
            with urllib.request.urlopen(r, timeout=20) as resp:
                return json.loads(resp.read())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))

def us_market_open(now_ny):
    if now_ny.weekday() >= 5:
        return False
    t = now_ny.hour * 60 + now_ny.minute
    return 9 * 60 + 30 <= t <= 16 * 60  # ignores exchange holidays; harmless (stale-but-valid close prints)

def main():
    ny = datetime.now(ZoneInfo("America/New_York"))
    mark_equities = us_market_open(ny) or os.environ.get("FORCE") == "1"

    state = req(BASE + "/latest")["record"]
    positions = state.get("positions", [])
    if not positions:
        print("no positions; exiting")
        return

    crypto = [p for p in positions if p.get("venue") == "crypto"]
    equity = [p for p in positions if p.get("venue") == "equity"]
    if not crypto and not mark_equities:
        print("market closed and no crypto to mark; exiting")
        return

    now = int(time.time() * 1000)
    marked, errs = [], []

    if crypto:
        mids = req("https://api.hyperliquid.xyz/info", "POST", {"type": "allMids"}, HL)
        dex_cache = {}
        for p in crypto:
            t = p["ticker"]
            dex = t.split(":")[0].lower() if ":" in t else ""
            pool = mids
            if dex:
                if dex not in dex_cache:
                    dex_cache[dex] = req("https://api.hyperliquid.xyz/info", "POST",
                                         {"type": "allMids", "dex": dex}, HL)
                pool = dex_cache[dex]
            raw = pool.get(t) or pool.get(t.upper()) or pool.get(f"{t.upper()}/USDC")
            if raw:
                state["prices"][t] = {"px": float(raw), "ts": now,
                                      "src": f"Hyperliquid{' ' + dex + ' dex' if dex else ''} mid (auto)"}
                marked.append((t, float(raw)))
            else:
                errs.append(t)

    if mark_equities:
        for p in equity:
            t = p["ticker"]
            try:
                y = req(f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?interval=1m&range=1d",
                        headers=YH)
                m = y["chart"]["result"][0]["meta"]
                state["prices"][t] = {"px": m["regularMarketPrice"],
                                      "ts": m.get("regularMarketTime", 0) * 1000 or now,
                                      "src": "Yahoo live API (auto)"}
                marked.append((t, m["regularMarketPrice"]))
            except Exception as e:
                errs.append(f"{t}: {e}")

    if not marked:
        print("nothing marked; errors:", errs)
        sys.exit(1)

    # keep the changelog clean: one rolling auto-mark entry, not one per run
    state["changelog"] = [e for e in state.get("changelog", [])
                          if e.get("action") != "Auto mark"]
    state["changelog"].insert(0, {
        "ts": now, "actor": "system", "action": "Auto mark",
        "detail": " · ".join(f"{t} ${px:g}" for t, px in marked) +
                  (f" | failed: {', '.join(map(str, errs))}" if errs else "") +
                  " (runs every 5 min in US market hours; crypto every 30 min around the clock)"
    })
    state["lastWrite"] = now
    state["version"] = state.get("version", 0) + 1
    req(BASE, "PUT", state)
    print(f"pushed v{state['version']}: {len(marked)} marks; errors: {errs or 'none'}")

if __name__ == "__main__":
    main()
