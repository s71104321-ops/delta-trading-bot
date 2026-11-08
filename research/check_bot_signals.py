"""Check: does the LIVE bot code (bot/signals.py) reproduce the research results?
Walk-forward with the bot's own functions: tune on 2 years, trade the next 6 months blind, roll.
Run on the server: .venv/bin/python -m research.check_bot_signals
"""
import math
import time
from types import SimpleNamespace

from bot import signals
from bot.trend_portfolio import TrendPortfolio

BAR = 6 * 365  # 4h bars per year


def main():
    s = SimpleNamespace(trend_coins="BTCUSD:trend,ETHUSD:breakout", dry_run=True)
    tp = TrendPortfolio(SimpleNamespace(s=s, c=None, j=None, n=None), state_path="/tmp/x.json")
    for sym, kind in tp.coins.items():
        bars = tp.candles(sym, days=2400)
        print(sym, kind, "bars", len(bars), time.strftime("%Y-%m-%d", time.gmtime(bars[0][0])))
        rets, i, train, test = [], 730 * 6, 730 * 6, 182 * 6
        while i < len(bars):
            p = signals.tune(bars[i - train:i], kind)
            win = bars[i - 400:i + test]                 # 400 bars warm-up
            r, _, _ = signals.simulate(win, signals.raw_signal(win, kind, p), start=400)
            rets += r
            print("  ", time.strftime("%Y-%m", time.gmtime(bars[i][0])), "settings", p,
                  "6m return %+.1f%%" % (100 * (math.prod(1 + x for x in r) - 1)))
            i += test
        eq, peak, dd = 1.0, 1.0, 0.0
        for x in rets:
            eq *= 1 + x
            peak = max(peak, eq)
            dd = min(dd, eq / peak - 1)
        yrs = len(rets) / BAR
        print("  %s blind: %.1f%%/yr over %.1f yrs, max drawdown %.0f%%" % (sym, 100 * (eq ** (1 / yrs) - 1), yrs, 100 * dd))


if __name__ == "__main__":
    main()
