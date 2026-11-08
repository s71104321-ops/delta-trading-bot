"""Filtered shorts: add short trades ONLY in a confirmed long-term bear market.

Bear regime (daily view, computed on 4h bars):
  price below its R-day simple average AND that average lower than 20 days ago.
Short entry: 4h fast EMA below slow EMA while in a bear regime.
Short exit:  fast EMA back above slow, or the bear regime ends. 3xATR stop on every trade.
Long side unchanged: BTC = EMA cross, ETH = 50-candle breakout in an uptrend.
Run on the server:  .venv/bin/python -m research.shorts
"""
import time

from . import backtest as bt
from . import faster as fa
from . import search2 as s2
from . import wavetrend as wt

TRAIN, TEST = 730, 182


def sma(xs, n):
    out, s = [], 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        out.append(s / min(i + 1, n))
    return out


def bear_regime(bars, days):
    c = [b[4] for b in bars]
    n = days * 6
    m = sma(c, n)
    return [i > n + 120 and c[i] < m[i] and m[i] < m[i - 120] for i in range(len(c))]


def with_shorts(long_sig, bars, f, s, days):
    c = [b[4] for b in bars]
    ef, es = bt.ema(c, f), bt.ema(c, s)
    bear = bear_regime(bars, days)
    return [1 if l == 1 else (-1 if bear[i] and ef[i] < es[i] else 0) for i, l in enumerate(long_sig)]


def wf(bars, grid, fn, fund, W=1400):
    tr, te = TRAIN * 6, TEST * 6
    rets, trades, start = [], [], tr
    while start + te // 2 < len(bars):
        train, test = bars[start - tr:start], bars[max(0, start - W):start + te]
        best = max(grid, key=lambda p: (bt.stats(*bt.simulate(train, fn(train, p), fund, bar_hours=4, atr_stop=3,
                                                               start=min(W, len(train) // 2)), bar_hours=4)
                                        or {"sharpe": -9})["sharpe"])
        c, tds = bt.simulate(test, fn(test, best), fund, bar_hours=4, atr_stop=3, start=min(W, start))
        rets += [c[i] / c[i - 1] - 1 for i in range(1, len(c))]
        trades += tds
        start += te
    return s2.summarize(rets, trades, 4)


def main():
    t0 = time.time()
    trend = [(f, s) for f in (20, 30, 50) for s in (100, 150, 200)]
    brk = [(N, f, s) for N in (20, 50, 100) for f, s in ((30, 150), (50, 150), (50, 200))]
    versions = {
        "BTCUSD": {
            "long only (current)": (trend, lambda b, p: bt.s_ema_trend(b, *p, longonly=True)),
            "long + short, no filter": (trend, lambda b, p: bt.s_ema_trend(b, *p)),
            "long + filtered short 100d": (trend, lambda b, p: with_shorts(
                bt.s_ema_trend(b, *p, longonly=True), b, p[0], p[1], 100)),
            "long + filtered short 200d": (trend, lambda b, p: with_shorts(
                bt.s_ema_trend(b, *p, longonly=True), b, p[0], p[1], 200)),
        },
        "ETHUSD": {
            "long only (current)": (brk, lambda b, p: fa.s_breakout(b, *p)),
            "long + short, no filter": (brk, lambda b, p: [x if x else -1 for x in fa.s_breakout(b, *p)]),
            "long + filtered short 100d": (brk, lambda b, p: with_shorts(fa.s_breakout(b, *p), b, p[1], p[2], 100)),
            "long + filtered short 200d": (brk, lambda b, p: with_shorts(fa.s_breakout(b, *p), b, p[1], p[2], 200)),
        },
    }
    print("Filtered shorts test, 4h, blind walk-forward (tune 2y, trade 6m, roll), fees + funding, 1x")
    print(f"{'coin':7}{'version':28}{'cagr':>8}{'sharpe':>7}{'maxDD':>7}{'trades':>7}{'win%':>6}{'PF':>6}")
    print("-" * 76)
    for coin, vs in versions.items():
        raw = wt.fill_gap(bt.fetch(coin), coin)
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        fund = {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None
        bars = bt.resample(raw, 4)
        for name, (grid, fn) in vs.items():
            s = wf(bars, grid, fn, fund)
            print(f"{coin:7}{name:28}{s['cagr']*100:7.1f}%{s['sharpe']:7.2f}{s['maxdd']*100:6.0f}%"
                  f"{s['trades']:7d}{s['win']*100:5.0f}%{min(s['pf'], 99):6.2f}", flush=True)
        # whole history incl. the 2022 bear market, fixed middle settings (not blind; context only)
        p_trend, p_brk = (50, 150), (50, 50, 150)
        for name, sig in (("FULL 2022-26 long only", bt.s_ema_trend(bars, *p_trend, longonly=True) if coin == "BTCUSD"
                           else fa.s_breakout(bars, *p_brk)),
                          ("FULL 2022-26 + short 200d", with_shorts(bt.s_ema_trend(bars, *p_trend, longonly=True), bars, 50, 150, 200)
                           if coin == "BTCUSD" else with_shorts(fa.s_breakout(bars, *p_brk), bars, 50, 150, 200))):
            s = bt.stats(*bt.simulate(bars, sig, fund, bar_hours=4, atr_stop=3, start=300), bar_hours=4)
            print(f"{coin:7}{name:28}{s['cagr']*100:7.1f}%{s['sharpe']:7.2f}{s['maxdd']*100:6.0f}%"
                  f"{s['trades']:7d}{s['win']*100:5.0f}%{min(s['pf'], 99):6.2f}", flush=True)
    print(f"SHORTS_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
