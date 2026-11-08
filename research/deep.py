"""Deep robustness tests for the two finalists.

A) BTC 4h long-only EMA trend (+ATR stop):  per-year results, settings grid, stop variants, ETH check
B) BTC/ETH daily rotation (perp funding charged): blind lookback re-tuning, per-year, weekly rebalance
Both: Monte Carlo reshuffle of returns to estimate a realistic worst drawdown.
Run on the server:  .venv/bin/python -m research.deep
"""
import math
import random
import time

from . import backtest as bt
from . import search as se
from . import wavetrend as wt

TRAIN, TEST = 730, 182


def curve_of(rets):
    eq, c = 1.0, [1.0]
    for r in rets:
        eq *= max(1 + r, 0)
        c.append(eq)
    return c


def maxdd(rets):
    peak, eq, m = 1.0, 1.0, 0.0
    for r in rets:
        eq *= max(1 + r, 0)
        peak = max(peak, eq)
        m = min(m, eq / peak - 1)
    return m


def monte_carlo(rets, block, runs=2000):
    """Shuffle blocks of returns (keeps short-term streaks), report 50th/95th percentile worst drop."""
    blocks = [rets[i:i + block] for i in range(0, len(rets), block)]
    dds = []
    rnd = random.Random(1)
    for _ in range(runs):
        rnd.shuffle(blocks)
        dds.append(maxdd([r for b in blocks for r in b]))
    dds.sort()
    return dds[len(dds) // 2], dds[int(len(dds) * 0.05)]


def per_year(rets, times):
    out = {}
    for r, t in zip(rets, times):
        y = time.gmtime(t).tm_year
        out[y] = out.get(y, 1.0) * (1 + r)
    return {y: v - 1 for y, v in sorted(out.items())}


def fmt_years(strat, bh):
    return "  ".join(f"{y}: {strat[y]*100:+.0f}% (hold {bh.get(y, 0)*100:+.0f}%)" for y in strat)


# ---------------- A) trend ----------------
def trend_wf(bars, fund, stop):
    grid = se.FAMILIES["trend_long"][0]
    per_day, W = 6, 300
    tr, te = TRAIN * per_day, TEST * per_day
    rets, times, trades, start = [], [], [], tr
    while start + te // 2 < len(bars):
        train, test = bars[start - tr:start], bars[max(0, start - W):start + te]
        best = max(grid, key=lambda p: (bt.stats(*bt.simulate(train, bt.s_ema_trend(train, *p, longonly=True),
                                                               fund, bar_hours=4, atr_stop=stop, start=W),
                                                 bar_hours=4) or {"sharpe": -9})["sharpe"])
        c, tds = bt.simulate(test, bt.s_ema_trend(test, *best, longonly=True), fund, bar_hours=4,
                             atr_stop=stop, start=min(W, start))
        k = min(W, start)
        rets += [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        times += [test[k + i][0] for i in range(1, len(c))]
        trades += tds
        start += te
    return rets, times, trades


def part_a(data):
    print("=" * 78)
    print("A) LONG-ONLY EMA TREND, 4h")
    for coin in ("BTCUSD", "ETHUSD"):
        raw, fund = data[coin]
        bars = bt.resample(raw, 4)
        for stop in (2, 3, 4, None):
            rets, times, trades = trend_wf(bars, fund, stop)
            s = bt.stats(curve_of(rets), trades, bar_hours=4)
            print(f"{coin} stop={str(stop) + 'xATR' if stop else 'none':8} blind: {s['cagr']*100:6.1f}%/yr  "
                  f"sharpe {s['sharpe']:.2f}  maxDD {s['maxdd']*100:.0f}%  trades {s['trades']}  PF {min(s['pf'], 99):.2f}")
            if coin == "BTCUSD" and stop == 3:
                keep = (rets, times)
        if coin == "BTCUSD":
            rets, times = keep
            start = len(bars) - len(rets)
            bh = per_year([bars[i][4] / bars[i - 1][4] - 1 for i in range(start, len(bars))],
                          [bars[i][0] for i in range(start, len(bars))])
            print("  BTC per year (3xATR):", fmt_years(per_year(rets, times), bh))
            med, bad = monte_carlo(rets, 6 * 30)
            print(f"  Monte Carlo worst drop: typical {med*100:.0f}%, bad case (1 in 20) {bad*100:.0f}%")
            # settings grid on the whole blind period, fixed settings (no re-tuning)
            blind = bars[start - 300:]
            bhs = bt.stats(*bt.simulate(blind, [1] * len(blind), fund, bar_hours=4, start=300), bar_hours=4)
            print(f"  Settings grid, fixed for the whole blind period (hold BTC: {bhs['cagr']*100:.0f}%/yr, "
                  f"DD {bhs['maxdd']*100:.0f}%):")
            print("   fast/slow" + "".join(f"{s:>9}" for s in (50, 100, 150, 200, 250)))
            good = total = 0
            for f in (10, 20, 30, 50):
                row = f"   {f:>9}"
                for sl in (50, 100, 150, 200, 250):
                    if f >= sl:
                        row += f"{'-':>9}"
                        continue
                    s = bt.stats(*bt.simulate(blind, bt.s_ema_trend(blind, f, sl, longonly=True), fund,
                                              bar_hours=4, atr_stop=3, start=300), bar_hours=4)
                    total += 1
                    good += s["sharpe"] > bhs["sharpe"]
                    row += f"{s['cagr']*100:7.0f}%{'*' if s['sharpe'] > bhs['sharpe'] else ' '}"
                print(row)
            print(f"   * = better risk-adjusted than holding: {good} of {total} settings")


# ---------------- B) rotation ----------------
def rotation(b1, e1, fb, fe, look, start, end, weekly=False, cash=True):
    rets, times, held, sw = [], [], None, 0
    for i in range(start, end):
        if not weekly or time.gmtime(b1[i][0]).tm_wday == 0 or held is None:
            mb = b1[i - 1][4] / b1[i - 1 - look][4] - 1
            me = e1[i - 1][4] / e1[i - 1 - look][4] - 1
            pick = (None if cash and max(mb, me) <= 0 else ("B" if mb >= me else "E"))
        else:
            pick = held
        r = 0.0
        if pick != held:
            sw += 1
            r -= (bt.FEE + bt.SLIP) * (2 if held and pick else 1)
            held = pick
        if held:
            bars, fd = (b1, fb) if held == "B" else (e1, fe)
            r += bars[i][4] / bars[i - 1][4] - 1
            if fd:   # perp longs pay funding: sum the 24 hourly rates of that day
                t0 = int(bars[i - 1][0])
                r -= sum(fd.get(t0 + h * 3600, 0) for h in range(24)) / 8
        rets.append(r)
        times.append(b1[i][0])
    return rets, times, sw


def part_b(data):
    print("=" * 78)
    print("B) BTC/ETH ROTATION, daily, perpetual funding charged")
    (br, bf), (er, ef) = data["BTCUSD"], data["ETHUSD"]
    b1, e1 = bt.resample(br, 24), bt.resample(er, 24)
    n = min(len(b1), len(e1))
    b1, e1 = b1[-n:], e1[-n:]
    looks = (10, 20, 30, 45, 60, 90)
    for weekly in (False, True):
        for look in looks:
            rets, _, sw = rotation(b1, e1, bf, ef, look, TRAIN, n, weekly)
            print(f"  fixed {look:>2}d {'weekly' if weekly else 'daily '}: {(curve_of(rets)[-1] ** (365 / len(rets)) - 1)*100:6.1f}%/yr"
                  f"  maxDD {maxdd(rets)*100:4.0f}%  switches {sw}")
    # blind re-tuning: every 6 months pick the lookback with best Sharpe over the past 2 years
    rets, times, sw, start = [], [], 0, TRAIN
    picks = []
    while start < n:
        def score(lk):
            r, _, _ = rotation(b1, e1, bf, ef, lk, start - TRAIN + 100, start)
            m = sum(r) / len(r)
            sd = math.sqrt(sum((x - m) ** 2 for x in r) / len(r)) or 1
            return m / sd
        lk = max(looks, key=score)
        picks.append(lk)
        r, t, s = rotation(b1, e1, bf, ef, lk, start, min(start + TEST, n))
        rets += r
        times += t
        sw += s
        start += TEST
    c = curve_of(rets)
    sh = (sum(rets) / len(rets)) / (math.sqrt(sum((x - sum(rets) / len(rets)) ** 2 for x in rets) / len(rets)) or 1) * math.sqrt(365)
    print(f"  BLIND re-tuned (picks {picks}): {(c[-1] ** (365 / len(rets)) - 1)*100:.1f}%/yr  sharpe {sh:.2f}"
          f"  maxDD {maxdd(rets)*100:.0f}%  switches {sw}")
    bh = per_year([b1[i][4] / b1[i - 1][4] - 1 for i in range(TRAIN, n)], [b1[i][0] for i in range(TRAIN, n)])
    print("  per year:", fmt_years(per_year(rets, times), bh))
    med, bad = monte_carlo(rets, 30)
    print(f"  Monte Carlo worst drop: typical {med*100:.0f}%, bad case (1 in 20) {bad*100:.0f}%")


def main():
    t0 = time.time()
    data = {}
    for coin in ("BTCUSD", "ETHUSD"):
        raw = wt.fill_gap(bt.fetch(coin), coin)
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        data[coin] = (raw, {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None)
    part_a(data)
    part_b(data)
    print(f"DEEP_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
