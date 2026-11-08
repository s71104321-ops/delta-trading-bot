"""Wide walk-forward search for the most profitable strategy that survives blind testing.

For every strategy family x timeframe x coin:
  roll through history: tune settings on 2 years, trade the next 6 months blind, repeat.
Only the blind months are scored. Survivors are combined into an equal-weight portfolio.
Run on the server:  .venv/bin/python -m research.search
"""
import math
import time

from . import backtest as bt
from . import wavetrend as wt

COINS = ("BTCUSD", "ETHUSD")
TFS = (4, 24)
TRAIN_DAYS, TEST_DAYS = 730, 182


def s_trend_funding(bars, fast, slow, fund_max, funding):
    """EMA trend, but skip longs when funding is very positive (crowded longs) and vice versa."""
    base = bt.s_ema_trend(bars, fast, slow)
    out = []
    for i, p in enumerate(base):
        f = funding.get(int(bars[i][0]) // 3600 * 3600, 0) if funding else 0
        if p == 1 and f > fund_max:
            p = 0
        elif p == -1 and f < -fund_max:
            p = 0
        out.append(p)
    return out


FAMILIES = {
    "trend":        ([(f, s) for f in (10, 20, 30, 50) for s in (100, 150, 200)],
                     lambda b, p, fd: bt.s_ema_trend(b, *p)),
    "trend_long":   ([(f, s) for f in (10, 20, 30, 50) for s in (100, 150, 200)],
                     lambda b, p, fd: bt.s_ema_trend(b, *p, longonly=True)),
    "breakout":     ([(e, x) for e in (20, 40, 55, 100) for x in (10, 20) if x < e],
                     lambda b, p, fd: bt.s_donchian(b, *p)),
    "wavetrend":    ([(n1, 21, lvl) for n1 in (8, 9, 10, 12) for lvl in (0, 30, 53)],
                     lambda b, p, fd: wt.s_wavetrend(b, p[0], p[1], p[2], False, False, False)),
    "mean_rev":     ([(n, z) for n in (20, 50) for z in (1.5, 2.0, 2.5)],
                     lambda b, p, fd: bt.s_meanrev(b, *p)),
    "trend_funding": ([(f, s, m) for f in (20, 50) for s in (100, 200) for m in (0.0002, 0.0005)],
                      lambda b, p, fd: s_trend_funding(b, *p, fd)),
}


def walk_forward(bars, hours, family, funding):
    grid, fn = FAMILIES[family]
    per_day = 24 // hours
    tr, te, W = TRAIN_DAYS * per_day, TEST_DAYS * per_day, 300
    rets, trades, picks = [], [], []
    start = tr
    while start + te // 2 < len(bars):
        train = bars[start - tr:start]
        test = bars[max(0, start - W):start + te]
        best = None
        for p in grid:
            s = bt.stats(*bt.simulate(train, fn(train, p, funding), funding, bar_hours=hours,
                                      atr_stop=3, start=min(W, len(train) // 4)), bar_hours=hours)
            if s and s["trades"] >= 5 and (best is None or s["sharpe"] > best[1]):
                best = (p, s["sharpe"])
        if best:
            curve, tds = bt.simulate(test, fn(test, best[0], funding), funding, bar_hours=hours,
                                     atr_stop=3, start=min(W, start))
            rets += [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
            trades += tds
            picks.append(best[0])
        start += te
    return rets, trades, picks


def summarize(rets, trades, hours, lev=1.0):
    eq, curve = 1.0, [1.0]
    for r in rets:
        eq *= max(1 + lev * r, 0.0)
        curve.append(eq)
    return bt.stats(curve, [t * lev for t in trades], bar_hours=hours)


def main():
    t_start = time.time()
    data = {}
    for coin in COINS:
        raw = wt.fill_gap(bt.fetch(coin), coin)
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        fund = {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None
        data[coin] = (raw, fund)
        print(f"{coin}: {len(raw)} 1h candles {time.strftime('%Y-%m-%d', time.gmtime(raw[0][0]))} to "
              f"{time.strftime('%Y-%m-%d', time.gmtime(raw[-1][0]))}, funding {'yes' if fund else 'NO'}", flush=True)

    print(f"\nWalk-forward: tune {TRAIN_DAYS}d, trade next {TEST_DAYS}d blind, roll. Only blind results shown. 1x leverage.")
    hdr = f"{'coin':7}{'tf':4}{'strategy':14}{'cagr':>8}{'sharpe':>8}{'maxDD':>7}{'trades':>7}{'win%':>6}{'PF':>6}  verdict"
    print(hdr)
    print("-" * len(hdr))
    results = []
    for coin, (raw, fund) in data.items():
        for hours in TFS:
            bars = bt.resample(raw, hours)
            bh = summarize([bars[i][4] / bars[i - 1][4] - 1 for i in range(TRAIN_DAYS * 24 // hours, len(bars))],
                           [], hours)
            print(f"{coin:7}{('1d' if hours == 24 else f'{hours}h'):4}{'buy&hold':14}{bh['cagr']*100:7.1f}%"
                  f"{bh['sharpe']:8.2f}{bh['maxdd']*100:6.0f}%", flush=True)
            for fam in FAMILIES:
                if fam == "trend_funding" and not fund:
                    continue
                rets, trades, picks = walk_forward(bars, hours, fam, fund)
                if len(rets) < 50:
                    continue
                s = summarize(rets, trades, hours)
                ok = (s["sharpe"] > 0.8 and s["pf"] > 1.2 and s["maxdd"] > -0.35 and s["trades"] >= 20
                      and s["sharpe"] > bh["sharpe"])
                results.append((coin, hours, fam, rets, trades, s, ok))
                print(f"{coin:7}{('1d' if hours == 24 else f'{hours}h'):4}{fam:14}{s['cagr']*100:7.1f}%"
                      f"{s['sharpe']:8.2f}{s['maxdd']*100:6.0f}%{s['trades']:7d}{s['win']*100:5.0f}%"
                      f"{min(s['pf'], 99):6.2f}  {'PASS' if ok else 'fail'}", flush=True)

    passed = [r for r in results if r[6]]
    print(f"\nPassed blind walk-forward: {len(passed)} of {len(results)}")
    if passed:
        # equal-weight portfolio of all survivors, aligned on daily returns
        daily = []
        for coin, hours, fam, rets, trades, s, ok in passed:
            k = 24 // hours
            d = [math.prod(1 + r for r in rets[i:i + k]) - 1 for i in range(0, len(rets) - k + 1, k)]
            daily.append(d)
        n = min(len(d) for d in daily)
        port = [sum(d[-n + i] for d in daily) / len(daily) for i in range(n)]
        all_trades = sum(len(r[4]) for r in passed)
        print("PORTFOLIO of all survivors (equal weight), blind results:")
        for lev in (1, 2, 3):
            s = summarize(port, [0.0] * all_trades, 24, lev)
            print(f"  {lev}x leverage: cagr {s['cagr']*100:6.1f}%  sharpe {s['sharpe']:5.2f}  maxDD {s['maxdd']*100:5.0f}%")
        best = max(passed, key=lambda r: r[5]["sharpe"])
        print(f"BEST SINGLE: {best[0]} {best[1]}h {best[2]}")
        for lev in (1, 2, 3):
            s = summarize(best[3], best[4], best[1], lev)
            print(f"  {lev}x leverage: cagr {s['cagr']*100:6.1f}%  sharpe {s['sharpe']:5.2f}  maxDD {s['maxdd']*100:5.0f}%")
    print(f"SEARCH_DONE in {time.time() - t_start:.0f}s")


if __name__ == "__main__":
    main()
