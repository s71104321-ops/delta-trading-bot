"""Long-history check of the final trend strategy (2016/2017 -> now) using Bitstamp's public 4h data.

Same walk-forward as portfolio.py (tune 2 years, trade the next 6 months blind, roll), so the blind
period covers the 2018 and 2022 bear markets. Bitstamp is spot data, so a funding cost of 0.01% per 8h
is charged while in a position (about 11%/yr while invested) to stay conservative.
Run on the server:  .venv/bin/python -m research.longhist
"""
import csv
import math
import os
import time

import requests

from . import backtest as bt
from . import portfolio as pf


class ConstFunding(dict):
    def get(self, k, d=None):
        return 0.0001


def bitstamp(pair, start):
    path = f"data/research/bitstamp_{pair}_4h.csv"
    if os.path.exists(path):
        with open(path) as f:
            return [tuple(map(float, r)) for r in csv.reader(f)]
    rows, t, end = {}, start, int(time.time())
    while t < end:
        for _ in range(3):
            try:
                r = requests.get(f"https://www.bitstamp.net/api/v2/ohlc/{pair}/", timeout=20,
                                 params={"step": 14400, "limit": 1000, "start": t}).json()
                for c in r.get("data", {}).get("ohlc", []):
                    ts = int(c["timestamp"])
                    rows[ts] = (ts, float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"]))
                break
            except Exception:  # noqa: BLE001
                time.sleep(2)
        t += 1000 * 14400
        time.sleep(0.3)
    data = sorted(rows.values())
    os.makedirs("data/research", exist_ok=True)
    with open(path, "w", newline="") as f:
        csv.writer(f).writerows(data)
    return data


def yearly(d):
    out = {}
    for t in sorted(d):
        y = time.gmtime(t).tm_year
        out[y] = out.get(y, 1.0) * (1 + d[t])
    return {y: v - 1 for y, v in out.items()}


def main():
    t0 = time.time()
    fund = ConstFunding()
    streams, holds = {}, {}
    for pair, kind, start in (("btcusd", "trend", 1451606400), ("ethusd", "breakout", 1502928000)):
        bars = bitstamp(pair, start)
        print(f"{pair.upper()}: {len(bars)} 4h candles {time.strftime('%Y-%m-%d', time.gmtime(bars[0][0]))} to "
              f"{time.strftime('%Y-%m-%d', time.gmtime(bars[-1][0]))}", flush=True)
        s = pf.wf_dated(bars, fund, kind)
        streams[pair] = s
        holds[pair] = {int(bars[i][0]): bars[i][4] / bars[i - 1][4] - 1 for i in range(1, len(bars))
                       if int(bars[i][0]) in s}
    print("\nBlind walk-forward results (tune 2y, trade 6m, roll), fees + assumed funding, 1x, long only")
    print(f"{'':16}{'return/yr':>10}{'sharpe':>8}{'worstDD':>9}{'worst month':>12}")
    for pair in streams:
        for label, d in (("strategy", streams[pair]), ("just holding", holds[pair])):
            r = [d[t] for t in sorted(d)]
            cagr, sh, dd, wm = pf.stats(r, 6 * 365)
            print(f"{pair.upper() + ' ' + label:16}{cagr*100:9.1f}%{sh:8.2f}{dd*100:8.0f}%{wm*100:11.0f}%")
    common = sorted(set(streams["btcusd"]) & set(streams["ethusd"]))
    port = {t: (streams["btcusd"][t] + streams["ethusd"][t]) / 2 for t in common}
    cagr, sh, dd, wm = pf.stats([port[t] for t in common], 6 * 365)
    print(f"{'50/50 strategy':16}{cagr*100:9.1f}%{sh:8.2f}{dd*100:8.0f}%{wm*100:11.0f}%")
    print("\nYear by year (strategy | holding):")
    yb, ye, yp = yearly(streams["btcusd"]), yearly(streams["ethusd"]), yearly(port)
    hb, he = yearly(holds["btcusd"]), yearly(holds["ethusd"])
    print(f"{'year':6}{'BTC strat':>11}{'BTC hold':>10}{'ETH strat':>11}{'ETH hold':>10}{'50/50':>9}")
    for y in sorted(set(yb) | set(ye)):
        f = lambda dct: f"{dct[y]*100:+.0f}%" if y in dct else "-"
        print(f"{y:<6}{f(yb):>11}{f(hb):>10}{f(ye):>11}{f(he):>10}{f(yp):>9}")
    print(f"LONGHIST_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
