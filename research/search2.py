"""Round 2: evidence-based strategies, same strict blind walk-forward.

  seasonal     long BTC 21:00-23:00 UTC daily (Quantpedia study), taker vs maker fees
  donch_ens    ensemble of Donchian breakouts (10..100 bars), volatility-sized (long-only / long-short)
  tsmom_vt     sign of past N-bar return, volatility-targeted size (cap 2x), long-only / long-short
  rotation     hold the stronger of BTC/ETH by N-day momentum, cash if both negative
  rsi2_trend   RSI(2) < x buys dips only when price > SMA(200); exit when RSI > 70
Run on the server:  .venv/bin/python -m research.search2
"""
import math
import time

from . import backtest as bt
from . import wavetrend as wt

TRAIN_DAYS, TEST_DAYS = 730, 182


def vol(c, i, n):
    r = [math.log(c[j] / c[j - 1]) for j in range(max(1, i - n + 1), i + 1)]
    if len(r) < 2:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / len(r))


def s_donch_ens(bars, target, longonly, hours):
    c = [b[4] for b in bars]
    looks = [10, 20, 40, 60, 100]
    per_year = 365 * 24 / hours
    out = []
    for i in range(len(c)):
        if i < 101:
            out.append(0)
            continue
        votes = []
        for n in looks:
            hi = max(b[2] for b in bars[i - n:i])
            lo = min(b[3] for b in bars[i - n:i])
            mid = (hi + lo) / 2
            votes.append(1 if c[i] > mid else (0 if longonly else -1))
        sig = sum(votes) / len(votes)
        v = vol(c, i, 30)
        size = min(2.0, target / (v * math.sqrt(per_year))) if v else 0
        out.append(round(sig * size * 4) / 4)
    return out


def s_tsmom(bars, look, target, longonly, hours):
    c = [b[4] for b in bars]
    per_year = 365 * 24 / hours
    out = []
    for i in range(len(c)):
        if i < look + 1:
            out.append(0)
            continue
        d = 1 if c[i] > c[i - look] else (0 if longonly else -1)
        v = vol(c, i, 30)
        size = min(2.0, target / (v * math.sqrt(per_year))) if v else 0
        out.append(round(d * size * 4) / 4)
    return out


def s_rsi2(bars, entry, sma_n=200):
    c = [b[4] for b in bars]
    out, pos, gain, loss = [], 0, 0.0, 0.0
    for i in range(len(c)):
        if i > 0:
            ch = c[i] - c[i - 1]
            gain = (gain + max(ch, 0)) / 2
            loss = (loss + max(-ch, 0)) / 2
        rsi = 100 - 100 / (1 + gain / loss) if loss else 100
        if i < sma_n:
            out.append(0)
            continue
        sma = sum(c[i - sma_n + 1:i + 1]) / sma_n
        if pos == 0 and rsi < entry and c[i] > sma:
            pos = 1
        elif pos == 1 and (rsi > 70 or c[i] < sma * 0.97):
            pos = 0
        out.append(pos)
    return out


def walk_forward(bars, hours, grid, fn, funding, atr_stop=None):
    per_day = 24 // hours
    tr, te, W = TRAIN_DAYS * per_day, TEST_DAYS * per_day, 300
    rets, trades, start = [], [], tr
    while start + te // 2 < len(bars):
        train, test = bars[start - tr:start], bars[max(0, start - W):start + te]
        best = None
        for p in grid:
            s = bt.stats(*bt.simulate(train, fn(train, p), funding, bar_hours=hours,
                                      atr_stop=atr_stop, start=W), bar_hours=hours)
            if s and s["trades"] >= 5 and (best is None or s["sharpe"] > best[1]):
                best = (p, s["sharpe"])
        if best:
            curve, tds = bt.simulate(test, fn(test, best[0]), funding, bar_hours=hours,
                                     atr_stop=atr_stop, start=min(W, start))
            rets += [curve[i] / curve[i - 1] - 1 for i in range(1, len(curve))]
            trades += tds
        start += te
    return rets, trades


def summarize(rets, trades, hours):
    eq, curve = 1.0, [1.0]
    for r in rets:
        eq *= max(1 + r, 0.0)
        curve.append(eq)
    return bt.stats(curve, trades, bar_hours=hours)


def line(name, s, bh_sharpe):
    ok = (s["sharpe"] > 0.8 and s["pf"] > 1.2 and s["maxdd"] > -0.35 and s["trades"] >= 20
          and s["sharpe"] > bh_sharpe)
    print(f"{name:34}{s['cagr']*100:7.1f}%{s['sharpe']:7.2f}{s['maxdd']*100:6.0f}%{s['trades']:7d}"
          f"{s['win']*100:5.0f}%{min(s['pf'], 99):6.2f}  {'PASS' if ok else 'fail'}", flush=True)


def main():
    t0 = time.time()
    data = {}
    for coin in ("BTCUSD", "ETHUSD"):
        raw = wt.fill_gap(bt.fetch(coin), coin)
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        data[coin] = (raw, {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None)
    btc, bfund = data["BTCUSD"]
    blind_from = btc[0][0] + TRAIN_DAYS * 86400
    print(f"Blind period: {time.strftime('%Y-%m-%d', time.gmtime(blind_from))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(btc[-1][0]))} | 1x unless sized by volatility")
    print(f"{'strategy':34}{'cagr':>8}{'sharpe':>7}{'maxDD':>7}{'trades':>7}{'win%':>6}{'PF':>6}  verdict")
    print("-" * 86)

    # 1) seasonal: no parameters -> just score the blind period
    for coin in ("BTCUSD", "ETHUSD"):
        raw, fund = data[coin]
        bh = bt.stats(*bt.simulate(raw, [1] * len(raw), fund, bar_hours=1,
                                   start=TRAIN_DAYS * 24), bar_hours=1)
        print(f"{coin + ' buy&hold':34}{bh['cagr']*100:7.1f}%{bh['sharpe']:7.2f}{bh['maxdd']*100:6.0f}%")
        sig = [1 if time.gmtime(b[0]).tm_hour in (21, 22) else 0 for b in raw]
        for label, fee, slip in (("taker fee 0.05%", 0.0005, 0.0002), ("maker fee 0.02%", 0.0002, 0.0)):
            bt.FEE, bt.SLIP = fee, slip
            s = bt.stats(*bt.simulate(raw, sig, fund, bar_hours=1, start=TRAIN_DAYS * 24), bar_hours=1)
            line(f"{coin} seasonal 21-23 UTC, {label}", s, bh["sharpe"])
        bt.FEE, bt.SLIP = 0.0005, 0.0002

    # 2-4) walk-forward families on 4h and 1d
    for coin in ("BTCUSD", "ETHUSD"):
        raw, fund = data[coin]
        for hours in (4, 24):
            bars = bt.resample(raw, hours)
            tf = "1d" if hours == 24 else "4h"
            bh = summarize([bars[i][4] / bars[i - 1][4] - 1 for i in range(TRAIN_DAYS * 24 // hours, len(bars))],
                           [], hours)
            fams = {
                "donch_ens long":   ([0.3, 0.5, 0.8], lambda b, p: s_donch_ens(b, p, True, hours), None),
                "donch_ens long/short": ([0.3, 0.5, 0.8], lambda b, p: s_donch_ens(b, p, False, hours), None),
                "tsmom_vt long":    ([(l, t) for l in (20, 60, 120) for t in (0.3, 0.5)],
                                     lambda b, p: s_tsmom(b, p[0], p[1], True, hours), None),
                "tsmom_vt long/short": ([(l, t) for l in (20, 60, 120) for t in (0.3, 0.5)],
                                        lambda b, p: s_tsmom(b, p[0], p[1], False, hours), None),
                "rsi2 dip-buy in uptrend": ([5, 10, 20], lambda b, p: s_rsi2(b, p), 3),
            }
            for name, (grid, fn, stop) in fams.items():
                rets, trades = walk_forward(bars, hours, grid, fn, fund, stop)
                if len(rets) > 50:
                    line(f"{coin} {tf} {name}", summarize(rets, trades, hours), bh["sharpe"])

    # 5) BTC/ETH rotation on daily bars (no shorting)
    b1, e1 = bt.resample(btc, 24), bt.resample(data["ETHUSD"][0], 24)
    n = min(len(b1), len(e1))
    b1, e1 = b1[-n:], e1[-n:]
    for look in (20, 30, 60, 90):
        rets, held, switches = [], None, 0
        for i in range(max(look, TRAIN_DAYS), n):
            mb = b1[i - 1][4] / b1[i - 1 - look][4] - 1
            me = e1[i - 1][4] / e1[i - 1 - look][4] - 1
            pick = None if max(mb, me) <= 0 else ("B" if mb >= me else "E")
            r = 0.0
            if pick != held:
                switches += 1
                r -= (bt.FEE + bt.SLIP) * (2 if held and pick else 1)
                held = pick
            if held == "B":
                r += b1[i][4] / b1[i - 1][4] - 1
            elif held == "E":
                r += e1[i][4] / e1[i - 1][4] - 1
            rets.append(r)
        s = summarize(rets, [0.01] * switches, 24)
        print(f"{'BTC/ETH rotation, ' + str(look) + 'd momentum':34}{s['cagr']*100:7.1f}%{s['sharpe']:7.2f}"
              f"{s['maxdd']*100:6.0f}%{switches:7d}   (not tuned: all lookbacks shown)")
    print(f"SEARCH2_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
