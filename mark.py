#!/usr/bin/env python3
"""Auto-marker for Private Book (v3, hardened for GitHub runners).
Crypto via Hyperliquid every run; equities during US market hours via
Yahoo with Nasdaq-API fallback (Yahoo sometimes blocks datacenter IPs).
Env: JSONBIN_KEY (secret), JSONBIN_BIN, FORCE.
"""
import csv, io, json, os, sys, time, urllib.request
from datetime import datetime
from zoneinfo import ZoneInfo

KEY = os.environ.get("JSONBIN_KEY", "")
if not KEY or not KEY.startswith("$2"):
    sys.exit("CONFIG ERROR: JSONBIN_KEY secret is missing or malformed. "
             "Repo Settings -> Secrets and variables -> Actions -> New repository secret, name JSONBIN_KEY.")
BIN = os.environ.get("JSONBIN_BIN", "6a4d02a9da38895dfe3adc02")
BASE = f"https://api.jsonbin.io/v3/b/{BIN}"
H  = {"X-Master-Key": KEY, "Content-Type": "application/json", "User-Agent": "curl/8.5.0"}
HL = {"Content-Type": "application/json", "User-Agent": "curl/8.5.0"}
YH = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

def fetch(url, method="GET", body=None, headers=None, tries=3, timeout=20):
    last = None
    for i in range(tries):
        try:
            r = urllib.request.Request(url, method=method, headers=headers or H,
                                       data=json.dumps(body).encode() if body else None)
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                return resp.read()
        except Exception as e:
            last = e
            time.sleep(2 * (i + 1))
    raise last

def jfetch(*a, **k):
    return json.loads(fetch(*a, **k))

def yahoo_quote(t):
    for host in ("query1", "query2"):
        try:
            d = jfetch(f"https://{host}.finance.yahoo.com/v8/finance/chart/{t}?interval=1m&range=1d",
                       headers=YH, tries=2, timeout=12)
            m = d["chart"]["result"][0]["meta"]
            px = m.get("regularMarketPrice")
            if px:
                return px, m.get("regularMarketTime", 0) * 1000, f"Yahoo live ({host}, auto)"
        except Exception as e:
            print(f"  yahoo {host} failed for {t}: {e}")
    return None

def nasdaq_quote(t):
    # Nasdaq's public quote API as a fallback when Yahoo blocks the runner
    try:
        d = jfetch(f"https://api.nasdaq.com/api/quote/{t}/info?assetclass=stocks",
                   headers={"User-Agent": YH["User-Agent"], "Accept": "application/json"},
                   tries=2, timeout=12)
        raw = (d.get("data") or {}).get("primaryData", {}).get("lastSalePrice", "")
        px = float(str(raw).replace("$", "").replace(",", ""))
        if px > 0:
            return px, 0, "Nasdaq API (auto)"
    except Exception as e:
        print(f"  nasdaq failed for {t}: {e}")
    return None

def us_market_open(now_ny):
    if now_ny.weekday() >= 5:
        return False
    t = now_ny.hour * 60 + now_ny.minute
    return 9 * 60 + 30 <= t <= 16 * 60

def main():
    ny = datetime.now(ZoneInfo("America/New_York"))
    mark_equities = us_market_open(ny) or os.environ.get("FORCE") == "1"
    print(f"NY time {ny:%a %H:%M} | equities: {'yes' if mark_equities else 'no (market closed)'}")

    state = jfetch(BASE + "/latest")["record"]
    positions = state.get("positions", [])
    crypto = [p for p in positions if p.get("venue") == "crypto"]
    equity = [p for p in positions if p.get("venue") == "equity"]
    if not positions or (not crypto and not mark_equities):
        print("nothing to do; exiting cleanly")
        return

    now = int(time.time() * 1000)
    marked, errs = [], []

    if crypto:
        try:
            mids = jfetch("https://api.hyperliquid.xyz/info", "POST", {"type": "allMids"}, HL)
            dex_cache = {}
            for p in crypto:
                t = p["ticker"]
                dex = t.split(":")[0].lower() if ":" in t else ""
                pool = mids
                if dex:
                    if dex not in dex_cache:
                        dex_cache[dex] = jfetch("https://api.hyperliquid.xyz/info", "POST",
                                                {"type": "allMids", "dex": dex}, HL)
                    pool = dex_cache[dex]
                raw = pool.get(t) or pool.get(t.upper()) or pool.get(f"{t.upper()}/USDC")
                if raw:
                    state["prices"][t] = {"px": float(raw), "ts": now,
                                          "src": f"Hyperliquid{' ' + dex + ' dex' if dex else ''} mid (auto)"}
                    marked.append((t, float(raw)))
                else:
                    errs.append(f"{t}: not found on Hyperliquid")
        except Exception as e:
            errs.append(f"Hyperliquid: {e}")

    if mark_equities:
        for p in equity:
            t = p["ticker"].upper()
            q = yahoo_quote(t) or nasdaq_quote(t)
            if q:
                px, qts, src = q
                state["prices"][p["ticker"]] = {"px": px, "ts": qts or now, "src": src}
                marked.append((t, px))
            else:
                errs.append(f"{t}: all quote sources failed")

    if not marked:
        sys.exit(f"nothing marked; errors: {errs}")

    state["changelog"] = [e for e in state.get("changelog", []) if e.get("action") != "Auto mark"]
    state["changelog"].insert(0, {
        "ts": now, "actor": "system", "action": "Auto mark",
        "detail": " · ".join(f"{t} ${px:g}" for t, px in marked) +
                  (f" | failed: {'; '.join(errs)}" if errs else "")
    })
    state["lastWrite"] = now
    state["version"] = state.get("version", 0) + 1
    fetch(BASE, "PUT", state)
    print(f"pushed v{state['version']}: {len(marked)} marks | errors: {errs or 'none'}")

if __name__ == "__main__":
    main()
