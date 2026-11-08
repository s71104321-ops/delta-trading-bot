"""Paper-trading of the Nifty 'gap idea' with LIVE Angel One prices. Reads prices only - never places orders.

Rules (from research/intraday.py):
  * skip the weekly expiry day, skip if Nifty's opening gap is smaller than 0.2%
  * gap up -> buy ATM CALL, gap down -> buy ATM PUT, at about 9:16 (paper: we record the ASK price)
  * Version A: sell at 3:20 (we record the BID price)
  * Version B: same, but sell early if Nifty crosses back past yesterday's close (gap filled)
Each day's result goes to data/gap_paper.csv and to Telegram.

Run by a systemd timer at 9:14 IST on weekdays:   python -m bot.gap_paper run
Running totals any time:                          python -m bot.gap_paper report
Needs in .env: ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN, ANGEL_TOTP_SECRET (+ TELEGRAM_* for alerts)
"""
import csv
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

IST = dt.timezone(dt.timedelta(hours=5, minutes=30))
LOG = "data/gap_paper.csv"
CHAIN = "data/nifty_options.json"
MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
NIFTY_TOKEN = "99926000"
MIN_GAP = 0.002
COST_PCT = 0.0015          # STT + exchange + GST + stamp, per side, on premium
BROKERAGE = 20.0           # per order
FIELDS = ["date", "gap_pct", "side", "strike", "expiry", "lot", "entry_time", "entry",
          "exitA_time", "exitA", "pnlA_pct", "pnlA_rs", "exitB_time", "exitB", "pnlB_pct", "pnlB_rs", "note"]


def now():
    return dt.datetime.now(IST)


def tg(text):
    tok, chat = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text, flush=True)
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
        urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=15)
    except Exception as e:  # noqa: BLE001
        print("telegram failed:", e, flush=True)


def login():
    import pyotp
    from SmartApi import SmartConnect
    s = SmartConnect(api_key=os.environ["ANGEL_API_KEY"])
    totp = pyotp.TOTP(os.environ["ANGEL_TOTP_SECRET"].replace(" ", "")).now()
    r = s.generateSession(os.environ["ANGEL_CLIENT_CODE"], os.environ["ANGEL_PIN"], totp)
    if not r or not r.get("status"):
        raise RuntimeError(f"Angel One login failed: {r and r.get('message')}")
    return s


def nifty_options():
    """Nifty option contracts from Angel One's instrument file (refreshed once a day)."""
    today = now().date().isoformat()
    try:
        with open(CHAIN) as f:
            c = json.load(f)
        if c.get("date") == today:
            return c["rows"]
    except Exception:  # noqa: BLE001
        pass
    text = urllib.request.urlopen(MASTER_URL, timeout=120).read().decode()
    rows = []
    for m in re.finditer(r"\{[^{}]*\}", text):
        o = m.group(0)
        if '"NIFTY"' not in o or "OPTIDX" not in o:
            continue
        d = json.loads(o)
        if d.get("name") == "NIFTY" and d.get("instrumenttype") == "OPTIDX" and d.get("exch_seg") == "NFO":
            rows.append({"token": d["token"], "symbol": d["symbol"], "expiry": d["expiry"],
                         "strike": float(d["strike"]) / 100, "type": d["symbol"][-2:],
                         "lot": int(float(d.get("lotsize") or 0))})
    del text
    os.makedirs("data", exist_ok=True)
    with open(CHAIN, "w") as f:
        json.dump({"date": today, "rows": rows}, f)
    return rows


def quote(s, exch, token):
    r = s.getMarketData("FULL", {exch: [token]})
    got = (r or {}).get("data", {}).get("fetched") or []
    if not got:
        raise RuntimeError(f"no quote for {exch}:{token}: {r and r.get('message')}")
    return got[0]


def best(q, side):
    """side='buy' -> best ask (what we would pay), 'sell' -> best bid. Falls back to last price."""
    book = (q.get("depth") or {}).get("sell" if side == "buy" else "buy") or []
    px = [float(x.get("price") or 0) for x in book if float(x.get("price") or 0) > 0]
    if px:
        return min(px) if side == "buy" else max(px)
    return float(q.get("ltp") or 0)


def pnl(entry, exitp, lot):
    gross = (exitp - entry) * lot
    costs = 2 * BROKERAGE + COST_PCT * (entry + exitp) * lot
    return round(100 * (gross - costs) / (entry * lot), 2), round(gross - costs, 0)


def write(row):
    new = not os.path.exists(LOG)
    os.makedirs("data", exist_ok=True)
    with open(LOG, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        if new:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def feed_date(feed):
    for f in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(feed.strip()[:19 if "T" in feed or feed[:4].isdigit() else 20], f).date()
        except ValueError:
            continue
    return None


def wait_until(h, m):
    while now() < now().replace(hour=h, minute=m, second=0, microsecond=0):
        time.sleep(5)


def run():
    if now().weekday() >= 5:
        return
    wait_until(9, 16)
    s = login()
    today = now().date()
    nq = quote(s, "NSE", NIFTY_TOKEN)
    feed = str(nq.get("exchFeedTime") or "")
    if feed_date(feed) not in (None, today):
        tg(f"📅 Gap paper-trade: market closed today ({feed}). No trade.")
        return
    prev, opn, ltp = float(nq["close"]), float(nq["open"]), float(nq["ltp"])
    gap = opn / prev - 1
    row = {"date": today.isoformat(), "gap_pct": round(100 * gap, 2)}
    rows = nifty_options()
    exps = sorted({dt.datetime.strptime(r["expiry"], "%d%b%Y").date() for r in rows})
    exps = [e for e in exps if e >= today]
    if not exps:
        raise RuntimeError("no Nifty expiries found in instrument file")
    exp = exps[0]
    if exp == today:
        row["note"] = "expiry day - skipped"
        write(row)
        tg(f"⏭ Gap paper-trade: expiry day today, skipped (gap {100*gap:+.2f}%).")
        return
    if abs(gap) < MIN_GAP:
        row["note"] = "gap too small - skipped"
        write(row)
        tg(f"⏭ Gap paper-trade: gap only {100*gap:+.2f}% (< 0.2%), no trade today.")
        return
    side = "CE" if gap > 0 else "PE"
    strike = round(ltp / 50) * 50
    exp_s = exp.strftime("%d%b%Y").upper()
    c = [r for r in rows if r["expiry"].upper() == exp_s and r["type"] == side]
    if not c:
        raise RuntimeError(f"no {side} contracts for {exp_s}")
    opt = min(c, key=lambda r: abs(r["strike"] - strike))
    oq = quote(s, "NFO", opt["token"])
    if feed_date(str(oq.get("exchTradeTime") or "")) not in (None, today):
        tg(f"📅 Gap paper-trade: no option trades today (last {oq.get('exchTradeTime')}), market holiday. No trade.")
        return
    entry = best(oq, "buy")
    lot = opt["lot"] or 75
    row.update(side=side, strike=opt["strike"], expiry=exp.isoformat(), lot=lot,
               entry_time=now().strftime("%H:%M"), entry=entry)
    tg(f"📈 Gap paper-trade {today}: Nifty gap {100*gap:+.2f}% → BUY {opt['symbol']} at ₹{entry} "
       f"(1 lot = {lot}, cost ₹{entry*lot:,.0f}). Paper only, no real order.")
    # watch until 15:20 for version B (exit if Nifty crosses back past yesterday's close)
    exitB = None
    end = now().replace(hour=15, minute=20, second=0, microsecond=0)
    while now() < end:
        time.sleep(60)
        try:
            n = float(quote(s, "NSE", NIFTY_TOKEN)["ltp"])
            if exitB is None and ((side == "CE" and n < prev) or (side == "PE" and n > prev)):
                exitB = best(quote(s, "NFO", opt["token"]), "sell")
                row.update(exitB_time=now().strftime("%H:%M"), exitB=exitB)
                tg(f"↩️ Gap filled (Nifty {n:.0f} vs yesterday {prev:.0f}): version B sells at ₹{exitB}.")
        except Exception as e:  # noqa: BLE001
            print("watch error:", e, flush=True)
            try:
                s = login()
            except Exception:  # noqa: BLE001
                pass
    exitA = best(quote(s, "NFO", opt["token"]), "sell")
    row.update(exitA_time=now().strftime("%H:%M"), exitA=exitA)
    if exitB is None:
        row.update(exitB_time=row["exitA_time"], exitB=exitA)
        exitB = exitA
    row["pnlA_pct"], row["pnlA_rs"] = pnl(entry, exitA, lot)
    row["pnlB_pct"], row["pnlB_rs"] = pnl(entry, exitB, lot)
    write(row)
    tg(f"🔔 Gap paper-trade {today} closed. A (hold to 3:20): {row['pnlA_pct']:+.1f}% = ₹{row['pnlA_rs']:+,.0f}/lot. "
       f"B (exit on gap fill): {row['pnlB_pct']:+.1f}% = ₹{row['pnlB_rs']:+,.0f}/lot.\n" + report(quiet=True))


def report(quiet=False):
    try:
        with open(LOG) as f:
            rows = [r for r in csv.DictReader(f) if r.get("pnlA_pct")]
    except FileNotFoundError:
        rows = []
    if not rows:
        out = "No paper trades yet."
    else:
        lines = [f"Totals after {len(rows)} trades (per lot, after costs):"]
        for v in ("A", "B"):
            p = [float(r[f"pnl{v}_pct"]) for r in rows]
            rs = sum(float(r[f"pnl{v}_rs"]) for r in rows)
            lines.append(f"  {v}: win {100*sum(x > 0 for x in p)/len(p):.0f}%, avg {sum(p)/len(p):+.1f}% per trade, "
                         f"total ₹{rs:+,.0f}")
        lines.append("  (backtest expected about +7% average per trade)")
        out = "\n".join(lines)
    if not quiet:
        print(out)
    return out


def main(argv=None):
    argv = argv or sys.argv[1:]
    cmd = argv[0] if argv else "run"
    if cmd == "report":
        report()
        return
    if cmd == "test-login":
        s = login()
        q = quote(s, "NSE", NIFTY_TOKEN)
        print("login OK. Nifty", q.get("ltp"), "prev close", q.get("close"), "feed", q.get("exchFeedTime"))
        r = nifty_options()
        print("Nifty option contracts:", len(r), "e.g.", r[0]["symbol"] if r else "-")
        return
    try:
        run()
    except Exception as e:  # noqa: BLE001
        tg(f"⚠️ Gap paper-trade error: {e}")
        raise


if __name__ == "__main__":
    main()
