"""WaveTrend [LazyBear] + Squeeze filter, rebuilt from the public formulas.

The TradingView script 'Wave Trend Strategy [LazyBear] & Squeeze' has protected source,
so this tests the standard versions:
  WaveTrend: ap=hlc3, esa=ema(ap,n1), d=ema(|ap-esa|,n1), ci=(ap-esa)/(0.015*d),
             wt1=ema(ci,n2), wt2=sma(wt1,4)
  Signal:    long when wt1 crosses above wt2 below -level, short when it crosses below above +level
  Squeeze:   Bollinger(20,2) inside Keltner(20,1.5) = squeeze ON (flat market) -> skip entries
  Momentum:  LazyBear squeeze value (linreg) must agree with the trade direction (optional)
Signals can be computed on Heikin Ashi candles, but fills always use REAL prices
(TradingView's HA backtests fill at fake HA prices, which flatters results).

Run on the server:  .venv/bin/python -m research.wavetrend
"""
import math
import time

import requests

from . import backtest as bt


def fill_gap(rows, symbol):
    """Global API history may stop early; top up the latest months from Delta India."""
    end, last = int(time.time()), int(rows[-1][0]) if rows else 0
    if end - last < 2 * 86400:
        return rows
    got, t = {int(r[0]): r for r in rows}, last + 3600
    while t < end:
        t2 = min(t + 1900 * 3600, end)
        try:
            r = requests.get("https://api.india.delta.exchange/v2/history/candles", timeout=20,
                             params={"symbol": symbol, "resolution": "1h", "start": t, "end": t2})
            for c in r.json().get("result", []):
                got[int(c["time"])] = (int(c["time"]), float(c["open"]), float(c["high"]),
                                       float(c["low"]), float(c["close"]))
        except Exception:  # noqa: BLE001
            pass
        t = t2
    return sorted(got.values())


def heikin_ashi(bars):
    out, po, pc = [], None, None
    for t, o, h, l, c in bars:
        hc = (o + h + l + c) / 4
        ho = (o + c) / 2 if po is None else (po + pc) / 2
        out.append((t, ho, max(h, ho, hc), min(l, ho, hc), hc))
        po, pc = ho, hc
    return out


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / min(i + 1, n))
    return out


def stdev(xs, n):
    out = []
    for i in range(len(xs)):
        w = xs[max(0, i - n + 1):i + 1]
        m = sum(w) / len(w)
        out.append(math.sqrt(sum((x - m) ** 2 for x in w) / len(w)))
    return out


def linreg_last(ys):
    n = len(ys)
    xm = (n - 1) / 2
    ym = sum(ys) / n
    den = sum((i - xm) ** 2 for i in range(n)) or 1
    slope = sum((i - xm) * (y - ym) for i, y in enumerate(ys)) / den
    return ym + slope * (n - 1 - xm)


def wavetrend(bars, n1, n2):
    ap = [(b[2] + b[3] + b[4]) / 3 for b in bars]
    esa = bt.ema(ap, n1)
    d = bt.ema([abs(a - e) for a, e in zip(ap, esa)], n1)
    ci = [(a - e) / (0.015 * dd) if dd else 0 for a, e, dd in zip(ap, esa, d)]
    wt1 = bt.ema(ci, n2)
    return wt1, sma(wt1, 4)


def squeeze(bars, n=20, bb_mult=2.0, kc_mult=1.5):
    c = [b[4] for b in bars]
    basis, dev = sma(c, n), stdev(c, n)
    rng = sma([b[2] - b[3] for b in bars], n)
    on = [(basis[i] - bb_mult * dev[i] > basis[i] - kc_mult * rng[i]) and
          (basis[i] + bb_mult * dev[i] < basis[i] + kc_mult * rng[i]) for i in range(len(c))]
    mom = []
    for i in range(len(c)):
        if i < n:
            mom.append(0.0)
            continue
        hh = max(b[2] for b in bars[i - n + 1:i + 1])
        ll = min(b[3] for b in bars[i - n + 1:i + 1])
        src = [c[j] - ((hh + ll) / 2 + basis[j]) / 2 for j in range(i - n + 1, i + 1)]
        mom.append(linreg_last(src))
    return on, mom


_CACHE = {}


def _cached(key, fn):
    if key not in _CACHE:
        _CACHE[key] = fn()
    return _CACHE[key]


def s_wavetrend(bars, n1, n2, level, sq_filter, mom_filter, ha):
    k = (len(bars), bars[0][0], bars[-1][0], ha)
    sbars = _cached(k + ("ha",), lambda: heikin_ashi(bars) if ha else bars)
    wt1, wt2 = _cached(k + ("wt", n1, n2), lambda: wavetrend(sbars, n1, n2))
    on, mom = _cached(k + ("sq",), lambda: squeeze(sbars)) if (sq_filter or mom_filter) else (None, None)
    pos, out = 0, []
    for i in range(len(bars)):
        if i < max(n1, n2) * 3:
            out.append(0)
            continue
        up = wt1[i - 1] <= wt2[i - 1] and wt1[i] > wt2[i] and wt1[i] < -level
        dn = wt1[i - 1] >= wt2[i - 1] and wt1[i] < wt2[i] and wt1[i] > level
        if up or dn:
            new = 1 if up else -1
            ok = True
            if sq_filter and on[i]:
                ok = False
            if mom_filter and (mom[i] > 0) != (new == 1):
                ok = False
            if ok:
                pos = new
            elif pos == -new:   # opposite signal still closes the old trade
                pos = 0
        out.append(pos)
    return out


GRID = [(n1, n2, lvl, sq, mf, ha)
        for n1 in (9, 10, 12) for n2 in (21,) for lvl in (0, 53, 60)
        for sq in (False, True) for mf in (False, True) for ha in (False, True)]


def label(p):
    n1, n2, lvl, sq, mf, ha = p
    return f"{n1}/{n2} L{lvl}{' SQ' if sq else ''}{' MOM' if mf else ''}{' HA' if ha else ''}"


def main():
    raw = fill_gap(bt.fetch(bt.SYMBOL), bt.SYMBOL)
    fund_raw = fill_gap(bt.fetch(f"FUNDING:{bt.SYMBOL}"), f"FUNDING:{bt.SYMBOL}")
    funding = {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fund_raw} if len(fund_raw) > 100 else None
    t0 = time.strftime("%Y-%m-%d", time.gmtime(raw[0][0]))
    t1 = time.strftime("%Y-%m-%d", time.gmtime(raw[-1][0]))
    print(f"WaveTrend + Squeeze | data {t0} to {t1} ({len(raw)} 1h candles) | funding: {'yes' if funding else 'NO'}")
    hdr = f"{'TF':4}{'best in-sample setup':26}{'IN cagr':>8}{'shp':>6}{'OUT cagr':>9}{'shp':>6}{'maxDD':>7}{'trd':>5}{'win%':>5}{'PF':>6}  verdict"
    print(hdr)
    print("-" * len(hdr))
    for hours in (1, 4, 24):
        bars = bt.resample(raw, hours) if hours > 1 else raw
        W = 300
        split = int(len(bars) * 0.7)
        ins, oos = bars[:split], bars[split - W:]
        bh = bt.stats(*bt.simulate(oos, [1] * len(oos), funding, bar_hours=hours, start=W), bar_hours=hours)
        best = None
        for p in GRID:
            s = bt.stats(*bt.simulate(ins, s_wavetrend(ins, *p), funding, bar_hours=hours,
                                      atr_stop=3, start=W), bar_hours=hours)
            if s and s["trades"] >= 15 and (best is None or s["sharpe"] > best[1]["sharpe"]):
                best = (p, s)
        if not best:
            print(f"{hours}h  too few trades")
            continue
        p, si = best
        so = bt.stats(*bt.simulate(oos, s_wavetrend(oos, *p), funding, bar_hours=hours,
                                   atr_stop=3, start=W), bar_hours=hours)
        ok = (so["sharpe"] > 0.8 and so["pf"] > 1.2 and so["maxdd"] > -0.35 and so["trades"] >= 20
              and so["sharpe"] > bh["sharpe"])
        tf = f"{hours}h" if hours < 24 else "1d"
        print(f"{tf:4}{label(p):26}{si['cagr']*100:7.1f}%{si['sharpe']:6.2f}{so['cagr']*100:8.1f}%"
              f"{so['sharpe']:6.2f}{so['maxdd']*100:6.0f}%{so['trades']:5d}{so['win']*100:4.0f}%"
              f"{min(so['pf'], 99):6.2f}  {'PASS' if ok else 'fail'}   (buy&hold OUT {bh['cagr']*100:.1f}%)")
    # the plain default setup, as most people run it (no optimisation at all)
    print("\nDefault LazyBear settings (10/21, levels 53, squeeze filter, Heikin Ashi), no tuning:")
    for hours in (1, 4, 24):
        bars = bt.resample(raw, hours) if hours > 1 else raw
        s = bt.stats(*bt.simulate(bars, s_wavetrend(bars, 10, 21, 53, True, False, True), funding,
                                  bar_hours=hours, atr_stop=3, start=300), bar_hours=hours)
        tf = f"{hours}h" if hours < 24 else "1d"
        print(f"  {tf:4} whole period: cagr {s['cagr']*100:6.1f}%  sharpe {s['sharpe']:5.2f}  "
              f"maxDD {s['maxdd']*100:4.0f}%  trades {s['trades']}  win {s['win']*100:.0f}%  PF {min(s['pf'], 99):.2f}")
    print("WAVETREND_DONE")


if __name__ == "__main__":
    main()
