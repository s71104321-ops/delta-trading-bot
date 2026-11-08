"""Faster-signal versions of the long-only trend strategy vs the baseline (same blind walk-forward).

  baseline     EMA fast/slow cross, 3xATR stop
  fast_exit    enter on EMA fast/slow cross, exit early when close < EMA(x) (short)
  kama         long when close > KAMA(n) and KAMA rising (adaptive speed)
  hull         long when Hull MA(n) is rising (low-lag average)
  breakout     buy on close above N-bar high, exit on EMA fast/slow cross down
  trailing     baseline entry, exit when close falls k x ATR below the highest close of the trade
Run on the server:  .venv/bin/python -m research.faster
"""
import math
import time

from . import backtest as bt
from . import search2 as s2
from . import wavetrend as wt


def wma(xs, n):
    out, den = [], n * (n + 1) / 2
    for i in range(len(xs)):
        if i < n - 1:
            out.append(xs[i])
            continue
        out.append(sum(xs[i - n + 1 + j] * (j + 1) for j in range(n)) / den)
    return out


def s_fast_exit(bars, f, s, x):
    c = [b[4] for b in bars]
    ef, es, ex = bt.ema(c, f), bt.ema(c, s), bt.ema(c, x)
    return [1 if i >= s and ef[i] > es[i] and c[i] > ex[i] else 0 for i in range(len(c))]


def s_kama(bars, n, fast=2, slow=30):
    c = [b[4] for b in bars]
    fs, ss = 2 / (fast + 1), 2 / (slow + 1)
    k, out = [c[0]], [0]
    for i in range(1, len(c)):
        if i < n:
            k.append(c[i])
            out.append(0)
            continue
        change = abs(c[i] - c[i - n])
        noise = sum(abs(c[j] - c[j - 1]) for j in range(i - n + 1, i + 1)) or 1e-9
        sc = (change / noise * (fs - ss) + ss) ** 2
        k.append(k[-1] + sc * (c[i] - k[-1]))
        out.append(1 if c[i] > k[-1] and k[-1] > k[-2] else 0)
    return out


def s_hull(bars, n):
    c = [b[4] for b in bars]
    raw = [2 * a - b for a, b in zip(wma(c, n // 2), wma(c, n))]
    h = wma(raw, int(math.sqrt(n)))
    return [1 if i > n and h[i] > h[i - 1] else 0 for i in range(len(c))]


def s_breakout(bars, N, f, s):
    c = [b[4] for b in bars]
    ef, es = bt.ema(c, f), bt.ema(c, s)
    pos, out = 0, []
    for i in range(len(c)):
        if i < max(N, s):
            out.append(0)
            continue
        if pos == 0 and c[i] > max(b[2] for b in bars[i - N:i]):
            pos = 1
        elif pos == 1 and ef[i] < es[i]:
            pos = 0
        out.append(pos)
    return out


def s_trailing(bars, f, s, k):
    c = [b[4] for b in bars]
    ef, es, a = bt.ema(c, f), bt.ema(c, s), bt.atr(bars, 14)
    pos, peak, wait, out = 0, 0, False, []
    for i in range(len(c)):
        trend = i >= s and ef[i] > es[i]
        if not trend:
            wait = False
        if pos and (not trend or c[i] < peak - k * a[i]):
            pos, wait = 0, trend          # trailed out: wait for a fresh cross
        elif not pos and trend and not wait:
            pos, peak = 1, c[i]
        if pos:
            peak = max(peak, c[i])
        out.append(pos)
    return out


TREND = [(f, s) for f in (20, 30, 50) for s in (100, 150, 200)]
VARIANTS = {
    "baseline (current)": (TREND, lambda b, p: bt.s_ema_trend(b, *p, longonly=True), 3),
    "1 fast exit":        ([(f, s, x) for f, s in TREND for x in (10, 20, 30)], lambda b, p: s_fast_exit(b, *p), 3),
    "2a adaptive KAMA":   ([10, 20, 40, 60], lambda b, p: s_kama(b, p), 3),
    "2b Hull MA":         ([20, 50, 100, 150], lambda b, p: s_hull(b, p), 3),
    "3 breakout entry":   ([(N, f, s) for N in (20, 50, 100) for f, s in ((30, 150), (50, 150), (50, 200))],
                           lambda b, p: s_breakout(b, *p), 3),
    "4 trailing stop":    ([(f, s, k) for f, s in TREND for k in (2, 3, 4)], lambda b, p: s_trailing(b, *p), None),
}


def main():
    t0 = time.time()
    print("Faster versions of long-only trend, 4h, blind walk-forward (tune 2y, trade 6m, roll), 1x")
    print(f"{'coin':7}{'version':22}{'cagr':>8}{'sharpe':>7}{'maxDD':>7}{'trades':>7}{'win%':>6}{'PF':>6}  verdict")
    print("-" * 80)
    for coin in ("BTCUSD", "ETHUSD"):
        raw = wt.fill_gap(bt.fetch(coin), coin)
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        fund = {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None
        bars = bt.resample(raw, 4)
        bh = s2.summarize([bars[i][4] / bars[i - 1][4] - 1 for i in range(730 * 6, len(bars))], [], 4)
        for name, (grid, fn, stop) in VARIANTS.items():
            rets, trades = s2.walk_forward(bars, 4, grid, fn, fund, stop)
            s = s2.summarize(rets, trades, 4)
            ok = (s["sharpe"] > 0.8 and s["pf"] > 1.2 and s["maxdd"] > -0.35 and s["trades"] >= 20
                  and s["sharpe"] > bh["sharpe"])
            print(f"{coin:7}{name:22}{s['cagr']*100:7.1f}%{s['sharpe']:7.2f}{s['maxdd']*100:6.0f}%"
                  f"{s['trades']:7d}{s['win']*100:5.0f}%{min(s['pf'], 99):6.2f}  {'PASS' if ok else 'fail'}", flush=True)
        print(f"{coin:7}{'(buy & hold)':22}{bh['cagr']*100:7.1f}%{bh['sharpe']:7.2f}{bh['maxdd']*100:6.0f}%")
    print(f"FASTER_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
