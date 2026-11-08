"""Weekly bull put credit spreads, priced from real historical implied volatility (Deribit DVOL).

Every Friday: sell a put k x (weekly expected move) below price, buy a put 'width' further below.
Hold to expiry 7 days later. Optional filter: only trade when the 4h trend strategy says uptrend.
Pricing: Black-Scholes with IV = DVOL plus a put-skew add-on (more for the further-out bought put,
which is conservative). We receive only 85% of the model credit (bid/ask), and pay Delta option fees.
Size: each week risk 'r' of capital (max loss of the spread = r x capital).
Run on the server:  .venv/bin/python -m research.options
"""
import math
import time

import requests

from . import backtest as bt
from . import wavetrend as wt

FILL = 0.85          # fraction of model credit we actually receive (bid/ask haircut)
FEE_PCT = 0.0003     # Delta options fee per leg, % of underlying notional...
FEE_CAP = 0.10       # ...capped at 10% of that leg's premium


def dvol(cur):
    """Daily DVOL closes {day_ts: iv} from Deribit's public API."""
    out, end = {}, int(time.time() * 1000)
    start = end - 6 * 365 * 86400 * 1000
    for _ in range(40):
        r = requests.get("https://www.deribit.com/api/v2/public/get_volatility_index_data", timeout=20,
                         params={"currency": cur, "start_timestamp": start, "end_timestamp": end,
                                 "resolution": "1D"}).json().get("result", {})
        for ts, o, h, l, c in r.get("data", []):
            out[int(ts // 1000) // 86400 * 86400] = c / 100
        cont = r.get("continuation")
        if not cont or cont <= start:
            break
        end = cont
    return out


def ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def put(S, K, T, iv):
    if T <= 0 or iv <= 0:
        return max(K - S, 0)
    d1 = (math.log(S / K) + 0.5 * iv * iv * T) / (iv * math.sqrt(T))
    return K * ncdf(-(d1 - iv * math.sqrt(T))) - S * ncdf(-d1)


def fee(S, prem):
    return min(FEE_PCT * S, FEE_CAP * prem)


def run(days, ivs, trend_up, k, width, risk, use_trend, start_ts):
    """days: list of (ts, close). Returns weekly returns, outcomes."""
    T = 7 / 365
    idx = {d[0]: i for i, d in enumerate(days)}
    rets, out, eq = [], [], 1.0
    for i, (ts, S) in enumerate(days):
        if ts < start_ts or time.gmtime(ts).tm_wday != 4 or i + 7 >= len(days):
            continue
        iv = ivs.get(ts)
        if not iv:
            continue
        if use_trend and not trend_up.get(ts, False):
            rets.append(0.0)
            out.append("skip")
            continue
        move = S * iv * math.sqrt(T)
        K1 = S - k * move
        K2 = K1 - width * S
        p1, p2 = put(S, K1, T, iv + 0.05), put(S, K2, T, iv + 0.10)
        credit = (p1 - p2) * FILL - fee(S, p1) - fee(S, p2)
        maxloss = (K1 - K2) - credit
        if credit <= 0 or maxloss <= 0:
            rets.append(0.0)
            out.append("skip")
            continue
        ST = days[i + 7][1]
        pnl = credit - (max(K1 - ST, 0) - max(K2 - ST, 0))
        r = risk * pnl / maxloss
        rets.append(r)
        out.append("win" if pnl > 0 else ("max loss" if ST <= K2 else "loss"))
    return rets, out


def report(name, rets, out):
    eq, peak, dd, years = 1.0, 1.0, 0.0, max(len(rets) / 52, 1e-9)
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    traded = [o for o in out if o != "skip"]
    wins = traded.count("win")
    worst = min(rets) if rets else 0
    cagr = eq ** (1 / years) - 1
    print(f"  {name:36}{cagr*100:7.1f}%/yr  maxDD {dd*100:5.0f}%  trades {len(traded):3d}  "
          f"win {wins / max(len(traded), 1)*100:3.0f}%  max-loss weeks {traded.count('max loss'):2d}  worst week {worst*100:5.1f}%")


def main():
    t0 = time.time()
    print("Weekly bull put spreads | IV = Deribit DVOL (+5/+10 vol skew) | 85% of model credit | Delta fees")
    for coin, cur in (("BTCUSD", "BTC"), ("ETHUSD", "ETH")):
        ivs = dvol(cur)
        raw = wt.fill_gap(bt.fetch(coin), coin)
        days = [(b[0] // 86400 * 86400, b[4]) for b in bt.resample(raw, 24)]
        bars4 = bt.resample(raw, 4)
        c4 = [b[4] for b in bars4]
        f4, s4 = bt.ema(c4, 50), bt.ema(c4, 150)
        trend_up = {}
        for b, f, s in zip(bars4, f4, s4):
            trend_up[b[0] // 86400 * 86400] = f > s      # last 4h bar of each day wins
        blind = days[0][0] + 730 * 86400
        print(f"\n{coin}: DVOL days {len(ivs)}, price days {len(days)}, blind period from "
              f"{time.strftime('%Y-%m-%d', time.gmtime(blind))}")
        for use_trend in (False, True):
            for k in (1.0, 1.5):
                for width in (0.05, 0.10):
                    for risk in (0.05,):
                        rets, out = run(days, ivs, trend_up, k, width, risk, use_trend, blind)
                        report(f"{'uptrend only' if use_trend else 'every week '} k={k} width={int(width*100)}% risk 5%",
                               rets, out)
    print(f"OPTIONS_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
