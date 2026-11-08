"""Complete weekly options plan matched to the market's mood, priced from Deribit DVOL.

Mood each Friday (4h EMA 50/150 + 100-day average):
  UP       fast EMA above slow                        -> bull put spread
  DOWN     fast below slow AND price below falling 100d avg -> bear call spread
  SIDEWAYS anything else                              -> iron condor (put spread + call spread)
Volatility filter (optional): only sell when DVOL > m x realised 30-day volatility.
Also tested: BUYING a 7-day at-the-money call in UP weeks (premium = r of capital).
Same conservative pricing as options.py: skew add-ons, 85% of model credit, buyers pay 115%, Delta fees.
Run on the server:  .venv/bin/python -m research.options2
"""
import math
import time

from . import backtest as bt
from . import options as op
from . import shorts as sh
from . import wavetrend as wt

T = 7 / 365


def call(S, K, t, iv):
    return op.put(S, K, t, iv) + S - K


def put_spread(S, iv, k, w):
    K1 = S - k * S * iv * math.sqrt(T)
    K2 = K1 - w * S
    p1, p2 = op.put(S, K1, T, iv + 0.05), op.put(S, K2, T, iv + 0.10)
    cr = (p1 - p2) * op.FILL - op.fee(S, p1) - op.fee(S, p2)
    return cr, K1 - K2, lambda ST: max(K1 - ST, 0) - max(K2 - ST, 0)


def call_spread(S, iv, k, w):
    K1 = S + k * S * iv * math.sqrt(T)
    K2 = K1 + w * S
    c1, c2 = call(S, K1, T, iv + 0.03), call(S, K2, T, iv + 0.05)
    cr = (c1 - c2) * op.FILL - op.fee(S, c1) - op.fee(S, c2)
    return cr, K2 - K1, lambda ST: max(ST - K1, 0) - max(ST - K2, 0)


def run(days, ivs, mood, rv, plan, risk, vrp, k=1.0, w=0.05, start=0):
    rets, wins, traded, maxl = [], 0, 0, 0
    for i, (ts, S) in enumerate(days):
        if ts < start or time.gmtime(ts).tm_wday != 4 or i + 7 >= len(days) or ts not in ivs:
            continue
        iv, m, ST = ivs[ts], mood.get(ts, "SIDE"), days[i + 7][1]
        if plan == "buy_calls":
            if m != "UP":
                rets.append(0.0)
                continue
            prem = call(S, S, T, iv) * 1.15 + op.fee(S, call(S, S, T, iv))
            r = risk * (max(ST - S, 0) - prem) / prem
            rets.append(r)
            traded += 1
            wins += r > 0
            continue
        legs = []
        if m == "UP" or (m == "SIDE" and plan in ("condor", "full")):
            legs.append(put_spread(S, iv, k, w))
        if (m == "SIDE" and plan in ("condor", "full")) or (m == "DOWN" and plan == "full"):
            legs.append(call_spread(S, iv, k, w))
        if not legs or (vrp and iv < vrp * rv.get(ts, 9)):
            rets.append(0.0)
            continue
        credit = sum(l[0] for l in legs)
        maxloss = max(l[1] for l in legs) - credit        # only one side of a condor can lose
        if credit <= 0 or maxloss <= 0:
            rets.append(0.0)
            continue
        pnl = credit - sum(l[2](ST) for l in legs)
        r = risk * pnl / maxloss
        rets.append(r)
        traded += 1
        wins += pnl > 0
        maxl += r <= -risk * 0.99
    return rets, traded, wins, maxl


def summary(rets):
    eq, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        eq *= 1 + r
        peak = max(peak, eq)
        dd = min(dd, eq / peak - 1)
    return eq ** (52 / max(len(rets), 1)) - 1, dd, min(rets) if rets else 0


def main():
    t0 = time.time()
    print("Options plan by market mood | blind period (last 3 years) | weekly | conservative pricing")
    for coin, cur in (("BTCUSD", "BTC"), ("ETHUSD", "ETH")):
        ivs = op.dvol(cur)
        raw = wt.fill_gap(bt.fetch(coin), coin)
        days = [(b[0] // 86400 * 86400, b[4]) for b in bt.resample(raw, 24)]
        bars4 = bt.resample(raw, 4)
        c4 = [b[4] for b in bars4]
        f4, s4 = bt.ema(c4, 50), bt.ema(c4, 150)
        bear = sh.bear_regime(bars4, 100)
        mood = {}
        for b, f, s, br in zip(bars4, f4, s4, bear):
            mood[b[0] // 86400 * 86400] = "UP" if f > s else ("DOWN" if br else "SIDE")
        dc = [d[1] for d in days]
        rv = {days[i][0]: math.sqrt(sum(math.log(dc[j] / dc[j - 1]) ** 2 for j in range(i - 29, i + 1)) / 30 * 365)
              for i in range(31, len(days))}
        start = days[0][0] + 730 * 86400
        moods = [mood.get(d[0], "SIDE") for d in days if d[0] >= start and time.gmtime(d[0]).tm_wday == 4]
        print(f"\n{coin}: weeks UP {moods.count('UP')}, SIDEWAYS {moods.count('SIDE')}, DOWN {moods.count('DOWN')}")
        print(f"  {'plan':44}{'return/yr':>10}{'worstDD':>9}{'trades':>8}{'win%':>6}{'maxloss wks':>12}{'worst wk':>9}")
        for plan, label in (("puts", "A put spreads in UP weeks"),
                            ("condor", "B + iron condors in SIDEWAYS weeks"),
                            ("full", "C + bear call spreads in DOWN weeks")):
            for risk in (0.05, 0.10):
                for vrp in (None, 1.0):
                    rets, tr, wn, ml = run(days, ivs, mood, rv, plan, risk, vrp, start=start)
                    cagr, dd, worst = summary(rets)
                    name = f"{label}, risk {int(risk*100)}%{', vol filter' if vrp else ''}"
                    print(f"  {name:44}{cagr*100:9.1f}%{dd*100:8.0f}%{tr:8d}{wn / max(tr, 1)*100:5.0f}%{ml:12d}{worst*100:8.1f}%")
        for risk in (0.02, 0.05):
            rets, tr, wn, ml = run(days, ivs, mood, rv, "buy_calls", risk, None, start=start)
            cagr, dd, worst = summary(rets)
            print(f"  {'D BUY 7-day ATM calls in UP weeks, premium ' + str(int(risk*100)) + '%':44}"
                  f"{cagr*100:9.1f}%{dd*100:8.0f}%{tr:8d}{wn / max(tr, 1)*100:5.0f}%{'-':>12}{worst*100:8.1f}%")
    print(f"OPTIONS2_DONE in {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()
