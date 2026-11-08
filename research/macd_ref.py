"""MACD zero-cross + reference-price reversal strategy (user's rules).

  MACD line (EMA12 - EMA26) crosses above 0 -> go LONG, reference = that close.
  MACD line crosses below 0 -> go SHORT, reference = that close.
  Between crosses: if close falls below reference*(1-buf) -> reverse to SHORT;
                   if close rises above reference*(1+buf) -> reverse to LONG.
  Always in the market. Decisions on candle close; fills at that close (+ fees/slippage).
Run on the server:  .venv/bin/python -m research.macd_ref
"""
import time

from . import backtest as bt
from . import wavetrend as wt


def s_macd_ref(bars, fast=12, slow=26, buf=0.0, use_ref=True):
    c = [b[4] for b in bars]
    ef, es = bt.ema(c, fast), bt.ema(c, slow)
    macd = [a - b for a, b in zip(ef, es)]
    pos, ref, out = 0, None, []
    for i in range(len(c)):
        if i < slow * 3:
            out.append(0)
            continue
        if macd[i - 1] <= 0 < macd[i]:
            pos, ref = 1, c[i]
        elif macd[i - 1] >= 0 > macd[i]:
            pos, ref = -1, c[i]
        elif use_ref and ref is not None:
            if pos == 1 and c[i] < ref * (1 - buf):
                pos = -1
            elif pos == -1 and c[i] > ref * (1 + buf):
                pos = 1
        out.append(pos)
    return out


VARIANTS = [("MACD zero-cross only", 0.0, False),
            ("your rules, exact ref", 0.0, True),
            ("your rules, 0.5% buffer", 0.005, True),
            ("your rules, 1% buffer", 0.01, True),
            ("your rules, 2% buffer", 0.02, True)]


def main():
    print("MACD(12,26) zero-cross + reference-price reversal | costs/side "
          f"{100*(bt.FEE+bt.SLIP):.2f}% | funding charged | 1x, no extra stop")
    for coin in ("BTCUSD", "ETHUSD"):
        raw = wt.fill_gap(bt.fetch(coin), coin)
        fr = wt.fill_gap(bt.fetch(f"FUNDING:{coin}"), f"FUNDING:{coin}")
        fund = {int(r[0]) // 3600 * 3600: r[4] / 100 for r in fr} if len(fr) > 100 else None
        for hours in (1, 4, 24):
            bars = bt.resample(raw, hours) if hours > 1 else raw
            split = int(len(bars) * 0.7)
            ts = time.strftime("%Y-%m-%d", time.gmtime(bars[split][0]))
            tf = "1d" if hours == 24 else f"{hours}h"
            full_bh = bt.stats(*bt.simulate(bars, [1] * len(bars), fund, bar_hours=hours, start=300), bar_hours=hours)
            last_bh = bt.stats(*bt.simulate(bars, [1] * len(bars), fund, bar_hours=hours, start=split), bar_hours=hours)
            print(f"\n{coin} {tf}  (whole: {time.strftime('%Y-%m-%d', time.gmtime(bars[300][0]))}..now | last 30%: {ts}..now)")
            print(f"  {'variant':26}{'WHOLE cagr':>11}{'shp':>6}{'maxDD':>7}{'trades':>7}{'win%':>6}"
                  f"{'LAST30% cagr':>13}{'shp':>6}{'maxDD':>7}")
            print(f"  {'buy & hold':26}{full_bh['cagr']*100:10.1f}%{full_bh['sharpe']:6.2f}{full_bh['maxdd']*100:6.0f}%"
                  f"{'':13}{last_bh['cagr']*100:12.1f}%{last_bh['sharpe']:6.2f}{last_bh['maxdd']*100:6.0f}%")
            for name, buf, use_ref in VARIANTS:
                sig = s_macd_ref(bars, buf=buf, use_ref=use_ref)
                w = bt.stats(*bt.simulate(bars, sig, fund, bar_hours=hours, start=300), bar_hours=hours)
                l = bt.stats(*bt.simulate(bars, sig, fund, bar_hours=hours, start=split), bar_hours=hours)
                print(f"  {name:26}{w['cagr']*100:10.1f}%{w['sharpe']:6.2f}{w['maxdd']*100:6.0f}%{w['trades']:7d}"
                      f"{w['win']*100:5.0f}%{l['cagr']*100:12.1f}%{l['sharpe']:6.2f}{l['maxdd']*100:6.0f}%", flush=True)
    print("MACD_DONE")


if __name__ == "__main__":
    main()
