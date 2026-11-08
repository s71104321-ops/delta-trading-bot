"""Whole-portfolio test: trend futures on several coins + uptrend put spreads, leverage, stress.

Each coin's futures strategy is walk-forward (tuned on 2y, traded blind 6m, rolled):
  BTC = EMA trend, ETH = breakout entry, SOL/XRP = EMA trend. Long only, 3xATR stop.
Portfolio = equal weight of available coins, then:
  fixed leverage 1x / 1.5x / 2x, or volatility-targeted leverage (0.5x..2.5x) from the last 30 days.
Options add-on: weekly bull put spreads on BTC & ETH in uptrend weeks, 5% risk each.
Everything is run twice: normal costs and DOUBLE costs (fees, slippage, worse option fills).
Run on the server:  .venv/bin/python -m research.portfolio
"""
import math
import time

from . import backtest as bt
from . import faster as fa
from . import options as op
from . import options2 as o2
from . import wavetrend as wt

TRAIN, TEST, W = 730 * 6, 182 * 6, 300
TREND = [(f, s) for f in (20, 30, 50) for s in (100, 150, 200)]
BRK = [(N, f, s) for N in (20, 50, 100) for f, s in ((30, 150), (50, 150), (50, 200))]
COINS = {"BTCUSD": "trend", "ETHUSD": "breakout", "SOLUSD": "trend", "XRPUSD": "trend"}


def wf_dated(bars, fund, kind):
    grid = TREND if kind == "trend" else BRK
    fn = (lambda b, p: bt.s_ema_trend(b, *p, longonly=True)) if kind == "trend" else (lambda b, p: fa.s_breakout(b, *p))
    out, start = {}, TRAIN
    while start + TEST // 2 < len(bars):
        train, test = bars[start - TRAIN:start], bars[max(0, start - W):start + TEST]
        best = max(grid, key=lambda p: (bt.stats(*bt.simulate(train, fn(train, p), fund, bar_hours=4,
                                                               atr_stop=3, start=W), bar_hours=4)
                                        or {"sharpe": -9})["sharpe"])
        k = min(W, start)
        c, _ = bt.simulate(test, fn(test, best), fund, bar_hours=4, atr_stop=3, start=k)
        for i in range(1, len(c)):
            out[int(test[k + i][0])] = c[i] / c[i - 1] - 1
        start += TEST
    return out


def opt_dated(coin, cur, raw):
    ivs = op.dvol(cur)
    days = [(b[0] // 86400 * 86400, b[4]) for b in bt.resample(raw, 24)]
    bars4 = bt.resample(raw, 4)
    c4 = [b[4] for b in bars4]
    f4, s4 = bt.ema(c4, 50), bt.ema(c4, 150)
    mood = {b[0] // 86400 * 86400: ("UP" if f > s else "SIDE") for b, f, s in zip(bars4, f4, s4)}
    out = {}
    for i, (ts, S) in enumerate(days):
        if time.gmtime(ts).tm_wday != 4 or i + 7 >= len(days) or ts not in ivs or mood.get(ts) != "UP":
            continue
        cr, width, loss = o2.put_spread(S, ivs[ts], 1.0, 0.05)
        if cr > 0:
            out[days[i + 7][0]] = 0.05 * (cr - loss(days[i + 7][1])) / (width - cr)
    return out


def stats(rets, per_year):
    eq, peak, dd, curve = 1.0, 1.0, 0.0, []
    for r in rets:
        eq *= max(1 + r, 0)
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
        curve.append(eq)
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - m) ** 2 for x in rets) / len(rets)) or 1e-9
    month = per_year // 12
    worst_m = min(curve[i] / curve[i - month] - 1 for i in range(month, len(curve)))
    return eq ** (per_year / len(rets)) - 1, m / sd * math.sqrt(per_year), dd, worst_m


def build(streams, opts, times, lev, vt=None, with_opts=False):
    rets, hist = [], []
    for t in times:
        rs = [s[t] for s in streams.values() if t in s]
        base = sum(rs) / len(rs) if rs else 0.0
        L = lev
        if vt:
            if len(hist) >= 180:
                v = math.sqrt(sum(x * x for x in hist[-180:]) / 180 * 6 * 365)
                L = max(0.5, min(2.5, vt / v)) if v > 0 else 1.0
            else:
                L = 1.0
        hist.append(base)
        r = L * base
        if with_opts:
            r += sum(o.get(t, 0.0) for o in opts.values())
        rets.append(r)
    return rets


def main():
    t0 = time.time()
    data = {}
    for coin in COINS:
        raw = wt.fill_gap(bt.fetch(coin), coin)
        if len(raw) < 24 * 365 * 3:
            print(f"{coin}: not enough history ({len(raw)} hours), skipped")
            continue
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        data[coin] = (raw, {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None)
    for costs in ("normal", "DOUBLE"):
        if costs == "DOUBLE":
            bt.FEE, bt.SLIP, op.FILL = 0.0010, 0.0004, 0.70
        streams, opts = {}, {}
        for coin, (raw, fund) in data.items():
            streams[coin] = wf_dated(bt.resample(raw, 4), fund, COINS[coin])
        for coin, cur in (("BTCUSD", "BTC"), ("ETHUSD", "ETH")):
            if coin in data:
                o = opt_dated(coin, cur, data[coin][0])
                # move each weekly option result onto the nearest 4h bar timestamp
                opts[coin] = {ts - ts % 14400: r for ts, r in o.items()}
        times = sorted(set().union(*[set(s) for s in streams.values()]))
        times = [t for t in times if t >= times[0]]
        two = {k: v for k, v in streams.items() if k in ("BTCUSD", "ETHUSD")}
        print(f"\n=== {costs} COSTS  | blind {time.strftime('%Y-%m-%d', time.gmtime(times[0]))} to "
              f"{time.strftime('%Y-%m-%d', time.gmtime(times[-1]))} | coins: {', '.join(c[:3] for c in streams)} ===")
        print(f"{'portfolio':44}{'return/yr':>10}{'sharpe':>7}{'worstDD':>8}{'worst month':>12}")
        rows = [("BTC+ETH futures 1x", two, 1, None, False),
                ("ALL coins futures 1x", streams, 1, None, False),
                ("ALL coins futures 1.5x", streams, 1.5, None, False),
                ("ALL coins futures 2x", streams, 2, None, False),
                ("ALL coins, vol-target 40%/yr (0.5-2.5x)", streams, 1, 0.40, False),
                ("ALL coins, vol-target 60%/yr (0.5-2.5x)", streams, 1, 0.60, False),
                ("ALL coins 1x + put spreads", streams, 1, None, True),
                ("ALL coins 1.5x + put spreads", streams, 1.5, None, True),
                ("ALL coins vol-target 40% + put spreads", streams, 1, 0.40, True),
                ("ALL coins vol-target 60% + put spreads", streams, 1, 0.60, True)]
        for name, ss, lev, vt, wo in rows:
            cagr, sh, dd, wm = stats(build(ss, opts, times, lev, vt, wo), 6 * 365)
            print(f"{name:44}{cagr*100:9.1f}%{sh:7.2f}{dd*100:7.0f}%{wm*100:11.0f}%", flush=True)
        for coin, s in streams.items():
            r = [s[t] for t in sorted(s)]
            cagr, sh, dd, wm = stats(r, 6 * 365)
            print(f"  single {coin:36}{cagr*100:9.1f}%{sh:7.2f}{dd*100:7.0f}%{wm*100:11.0f}%")
    print(f"PORTFOLIO_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
