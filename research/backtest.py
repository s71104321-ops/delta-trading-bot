"""Backtest futures strategies on Delta BTCUSD perpetual history.

Run on the server:  .venv/bin/python -m research.backtest
- Downloads 1h candles (+ funding if available), caches them in data/research/
- Tests strategies with fees, slippage and funding
- Picks parameters on the first 70% of history (in-sample) only,
  then reports how those exact parameters did on the last 30% (out-of-sample).
"""
import csv
import math
import os
import sys
import time

import requests

API = os.getenv("RESEARCH_API", "https://api.delta.exchange")  # global API has the longest history
SYMBOL = os.getenv("RESEARCH_SYMBOL", "BTCUSD")
YEARS = float(os.getenv("RESEARCH_YEARS", "5"))
FEE = 0.0005      # taker fee per side (0.05%)
SLIP = 0.0002     # slippage per side (0.02%)
CACHE = "data/research"


# ---------------- data ----------------
def fetch(symbol, resolution="1h", step=3600):
    path = f"{CACHE}/{symbol.replace(':', '_')}_{resolution}.csv"
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        with open(path) as f:
            return [tuple(map(float, r)) for r in csv.reader(f)]
    end = int(time.time())
    start = end - int(YEARS * 365 * 86400)
    rows, t = {}, start
    while t < end:
        t2 = min(t + 1900 * step, end)
        for attempt in range(3):
            try:
                r = requests.get(f"{API}/v2/history/candles", timeout=20,
                                 params={"symbol": symbol, "resolution": resolution, "start": t, "end": t2})
                for c in r.json().get("result", []):
                    rows[int(c["time"])] = (int(c["time"]), float(c["open"]), float(c["high"]),
                                            float(c["low"]), float(c["close"]))
                break
            except Exception as e:  # noqa: BLE001
                time.sleep(2)
        t = t2
    data = sorted(rows.values())
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(data)
    return data


def resample(bars, hours):
    out, bucket = [], []
    for b in bars:
        bucket.append(b)
        if (int(b[0]) // 3600 + 1) % hours == 0:
            out.append((bucket[0][0], bucket[0][1], max(x[2] for x in bucket),
                        min(x[3] for x in bucket), bucket[-1][4]))
            bucket = []
    return out


# ---------------- indicators ----------------
def ema(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def atr(bars, n):
    trs, prev = [], None
    for _, _, h, l, c in bars:
        trs.append(h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    return ema(trs, n)


# ---------------- strategies: return target position per bar (-1, 0, 1) ----------------
def s_ema_trend(bars, fast, slow, longonly=False):
    c = [b[4] for b in bars]
    f, s = ema(c, fast), ema(c, slow)
    return [(1 if f[i] > s[i] else (0 if longonly else -1)) if i >= slow else 0 for i in range(len(c))]


def s_donchian(bars, entry, exit_, longonly=False):
    pos, out = 0, []
    for i in range(len(bars)):
        if i < entry:
            out.append(0)
            continue
        hi = max(b[2] for b in bars[i - entry:i])
        lo = min(b[3] for b in bars[i - entry:i])
        xhi = max(b[2] for b in bars[i - exit_:i])
        xlo = min(b[3] for b in bars[i - exit_:i])
        c = bars[i][4]
        if pos == 1 and c < xlo:
            pos = 0
        elif pos == -1 and c > xhi:
            pos = 0
        if pos == 0:
            if c > hi:
                pos = 1
            elif c < lo and not longonly:
                pos = -1
        out.append(pos)
    return out


def s_meanrev(bars, n, z):
    c = [b[4] for b in bars]
    out, pos = [], 0
    for i in range(len(c)):
        if i < n:
            out.append(0)
            continue
        w = c[i - n:i]
        m = sum(w) / n
        sd = math.sqrt(sum((x - m) ** 2 for x in w) / n) or 1
        zz = (c[i] - m) / sd
        if pos == 0 and zz < -z:
            pos = 1
        elif pos == 0 and zz > z:
            pos = -1
        elif pos == 1 and zz >= 0 or pos == -1 and zz <= 0:
            pos = 0
        out.append(pos)
    return out


# ---------------- simulator ----------------
def simulate(bars, sig, funding=None, bar_hours=4, atr_stop=None, start=1):
    """Signal at bar i close -> position held over bar i+1. Returns equity curve & trade stats.
    Bars before 'start' only warm up indicators (no trading). After a stop-out the strategy
    waits for its signal to change before re-entering."""
    eq, curve, trades, pos, entry = 1.0, [], [], 0, None
    a = atr(bars, 14) if atr_stop else None
    stop, blocked = None, None
    for i in range(max(start, 1), len(bars)):
        o, h, l, c = bars[i][1:]
        prev_c = bars[i - 1][4]
        # P&L of holding pos over this bar (with stop check)
        if pos:
            exit_px = c
            if stop is not None and ((pos == 1 and l <= stop) or (pos == -1 and h >= stop)):
                exit_px = stop
            eq *= 1 + pos * (exit_px / prev_c - 1)
            if funding:
                eq *= 1 - pos * funding.get(int(bars[i][0]) // 3600 * 3600, 0) * (bar_hours / 8)
            if exit_px != c:  # stopped out
                eq *= 1 - (FEE + SLIP)
                trades.append(pos * (exit_px / entry - 1) - 2 * (FEE + SLIP))
                blocked = pos
                pos, stop = 0, None
                curve.append(eq)
                continue
        target = sig[i]
        if blocked is not None:
            if target == blocked:
                target = 0
            else:
                blocked = None
        if target != pos:
            changes = abs(target - pos)
            eq *= 1 - changes * (FEE + SLIP)
            if pos:
                trades.append(pos * (c / entry - 1) - 2 * (FEE + SLIP))
            pos, entry = target, c
            stop = (c - pos * atr_stop * a[i]) if (pos and atr_stop) else None
        curve.append(eq)
    return curve, trades


def stats(curve, trades, bar_hours=4):
    if len(curve) < 2:
        return {}
    years = len(curve) * bar_hours / 24 / 365
    rets = [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - m) ** 2 for r in rets) / len(rets)) or 1e-9
    peak, mdd = 0, 0
    for e in curve:
        peak = max(peak, e)
        mdd = min(mdd, e / peak - 1)
    wins = [t for t in trades if t > 0]
    losses = [-t for t in trades if t <= 0]
    return {
        "cagr": (curve[-1] / curve[0]) ** (1 / years) - 1 if years > 0 and curve[-1] > 0 else -1,
        "sharpe": m / sd * math.sqrt(365 * 24 / bar_hours),
        "maxdd": mdd,
        "trades": len(trades),
        "win": len(wins) / len(trades) if trades else 0,
        "pf": (sum(wins) / sum(losses)) if losses and sum(losses) > 0 else float("inf"),
    }


# ---------------- experiment ----------------
GRID = {
    "ema_trend":      [(f, s) for f in (10, 20, 30) for s in (50, 100, 200) if f < s],
    "ema_trend_long": [(f, s) for f in (10, 20, 30) for s in (50, 100, 200) if f < s],
    "donchian":       [(e, x) for e in (20, 55, 100) for x in (10, 20, 40) if x < e],
    "donchian_long":  [(e, x) for e in (20, 55, 100) for x in (10, 20, 40) if x < e],
    "mean_reversion": [(n, z) for n in (20, 50) for z in (1.5, 2.0, 2.5)],
}


def run_strategy(name, bars, p):
    if name.startswith("ema_trend"):
        return s_ema_trend(bars, *p, longonly=name.endswith("long"))
    if name.startswith("donchian"):
        return s_donchian(bars, *p, longonly=name.endswith("long"))
    return s_meanrev(bars, *p)


def main():
    print(f"Downloading {SYMBOL} 1h candles ({YEARS} years) from {API} ...", flush=True)
    raw = fetch(SYMBOL)
    if len(raw) < 1000:
        sys.exit(f"Not enough data ({len(raw)} candles).")
    fund_raw = fetch(f"FUNDING:{SYMBOL}")
    funding = {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fund_raw} if len(fund_raw) > 100 else None
    bars = resample(raw, 4)
    t0 = time.strftime("%Y-%m-%d", time.gmtime(bars[0][0]))
    t1 = time.strftime("%Y-%m-%d", time.gmtime(bars[-1][0]))
    split = int(len(bars) * 0.7)
    ts = time.strftime("%Y-%m-%d", time.gmtime(bars[split][0]))
    print(f"Data: {len(raw)} 1h candles, {t0} to {t1}; funding data: {'yes' if funding else 'NO'}")
    print(f"In-sample: {t0} to {ts} | Out-of-sample: {ts} to {t1} | costs/side {100*(FEE+SLIP):.2f}%\n")

    W = 300                                       # warm-up bars for indicators
    ins, oos = bars[:split], bars[split - W:]
    bh_in = stats(*simulate(ins, [1] * len(ins), funding, start=W))
    bh_out = stats(*simulate(oos, [1] * len(oos), funding, start=W))
    hdr = f"{'strategy':16}{'params':12}{'IN cagr':>9}{'sharpe':>8}{'OUT cagr':>10}{'sharpe':>8}{'maxDD':>8}{'trades':>7}{'win%':>6}{'PF':>6}"
    print(hdr)
    print("-" * len(hdr))
    print(f"{'buy&hold':16}{'-':12}{bh_in['cagr']*100:8.1f}%{bh_in['sharpe']:8.2f}"
          f"{bh_out['cagr']*100:9.1f}%{bh_out['sharpe']:8.2f}{bh_out['maxdd']*100:7.0f}%{'-':>7}{'-':>6}{'-':>6}")
    results = []
    for name, grid in GRID.items():
        best = None
        for p in grid:
            s_in = stats(*simulate(ins, run_strategy(name, ins, p), funding, atr_stop=3, start=W))
            if s_in and (best is None or s_in["sharpe"] > best[1]["sharpe"]):
                best = (p, s_in)
        p, s_in = best
        s_out = stats(*simulate(oos, run_strategy(name, oos, p), funding, atr_stop=3, start=W))
        results.append((name, p, s_in, s_out))
        print(f"{name:16}{str(p):12}{s_in['cagr']*100:8.1f}%{s_in['sharpe']:8.2f}"
              f"{s_out['cagr']*100:9.1f}%{s_out['sharpe']:8.2f}{s_out['maxdd']*100:7.0f}%"
              f"{s_out['trades']:7d}{s_out['win']*100:5.0f}%{min(s_out['pf'], 99):6.2f}")
    print("\nPASS rule: out-of-sample Sharpe > 0.8, PF > 1.2, maxDD better than -35%, >= 20 trades, and beats buy&hold Sharpe")
    for name, p, _, s in results:
        ok = (s["sharpe"] > 0.8 and s["pf"] > 1.2 and s["maxdd"] > -0.35 and s["trades"] >= 20
              and s["sharpe"] > bh_out["sharpe"])
        print(f"  {name:16}{str(p):12}{'PASS' if ok else 'fail'}")
    print("RESEARCH_DONE")


if __name__ == "__main__":
    main()
