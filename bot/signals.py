"""Trend signals + 6-monthly tuning. Same logic as the research code that passed the blind tests.

  EMA trend (BTC): long when EMA(fast) > EMA(slow), else flat.
  Breakout (ETH):  go long when the close breaks above the highest high of the last N candles
                   (and fast > slow); exit when EMA(fast) < EMA(slow).
  Both: 3 x ATR(14) stop under the entry. After a stop-out, wait for a fresh signal.
Bars are tuples (time, open, high, low, close), 4-hour candles, oldest first.
"""
import math

FEE_SLIP = 0.0007          # 0.05% fee + 0.02% slippage per side, as in the backtests
TREND_GRID = [(f, s) for f in (20, 30, 50) for s in (100, 150, 200)]
BRK_GRID = [(N, f, s) for N in (20, 50, 100) for f, s in ((30, 150), (50, 150), (50, 200))]


def ema(xs, n):
    k, out, e = 2 / (n + 1), [], None
    for x in xs:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def atr(bars, n=14):
    trs, prev = [], None
    for _, _, h, l, c in bars:
        trs.append(h - l if prev is None else max(h - l, abs(h - prev), abs(l - prev)))
        prev = c
    return ema(trs, n)


def trend_raw(bars, f, s):
    c = [b[4] for b in bars]
    ef, es = ema(c, f), ema(c, s)
    return [1 if i >= s and ef[i] > es[i] else 0 for i in range(len(c))]


def breakout_raw(bars, N, f, s):
    c = [b[4] for b in bars]
    ef, es = ema(c, f), ema(c, s)
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


def raw_signal(bars, kind, p):
    return trend_raw(bars, *p) if kind == "trend" else breakout_raw(bars, *p)


def simulate(bars, sig, start=300, stop_mult=3.0):
    """Long-only backtest with ATR stop and 'wait after stop'. Returns (bar returns, n_trades, final_state)."""
    a = atr(bars)
    pos, entry_stop, blocked, rets, trades = 0, None, False, [], 0
    for i in range(max(start, 1), len(bars)):
        o, h, l, c = bars[i][1:]
        pc = bars[i - 1][4]
        r = 0.0
        if pos:
            px = entry_stop if l <= entry_stop else c
            r = px / pc - 1
            if px != c:                      # stopped out
                r -= FEE_SLIP
                pos, blocked, trades = 0, True, trades + 1
                rets.append(r)
                continue
        want = sig[i]
        if blocked:
            if want == 0:
                blocked = False
            want = 0
        if want != pos:
            r -= FEE_SLIP
            trades += pos            # count a trade when one closes
            pos = want
            entry_stop = c - stop_mult * a[i] if pos else None
        rets.append(r)
    return rets, trades, {"pos": pos, "blocked": blocked, "stop": entry_stop}


def sharpe(rets):
    if len(rets) < 2:
        return -9.0
    m = sum(rets) / len(rets)
    sd = math.sqrt(sum((x - m) ** 2 for x in rets) / len(rets)) or 1e-9
    return m / sd


def tune(bars, kind):
    """Pick the settings with the best Sharpe over the given (about 2 years of) bars."""
    grid = TREND_GRID if kind == "trend" else BRK_GRID
    best, best_s = grid[0], -1e9
    for p in grid:
        rets, n, _ = simulate(bars, raw_signal(bars, kind, p))
        s = sharpe(rets) if n >= 5 else -9.0
        if s > best_s:
            best, best_s = p, s
    return best


def target(bars, kind, p):
    """Desired position now (1 long / 0 flat), whether we're 'blocked' after a stop, and the ATR now."""
    sig = raw_signal(bars, kind, p)
    _, _, state = simulate(bars, sig)
    return {"want": sig[-1], "blocked": state["blocked"], "atr": atr(bars)[-1], "close": bars[-1][4]}
